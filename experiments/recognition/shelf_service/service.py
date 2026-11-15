"""Appearance-only service; all state lives in the private store."""
import hashlib
import json
import threading
from time import perf_counter

from PIL import Image

from .calibration import calibrate, classify
from .detection import DetectionConfig, detect, surface_pixels
from .embedding import DinoEmbedder
from .off import OffClient
from .storage import Store, identifier, image_hash, write_json

NOTE = 'similarities are model scores, not measured accuracy'


class ShelfService:
    def __init__(self, store=None, embedder=None, config=None, off=None, use_off_images=False):
        self.store = store or Store()
        self.embed = embedder or DinoEmbedder(self.store.root/'model_cache')
        self.config = config or DetectionConfig()
        self.off = off or OffClient(self.store.root/'off_cache')
        self.use_off_images = use_off_images
        self.lock = threading.RLock()
        self._gallery_key = None
        self._gallery = None
        self.model_error = None

    def health(self):
        return {'ok': self.embed.loaded, 'model_loaded': self.embed.loaded, 'model': self.embed.info,
                'model_error': self.model_error, 'gallery': self.store.counts(),
                'background_present': (self.store.root/'background.jpg').exists(),
                'off_images_enabled': self.use_off_images}

    def set_background(self, image, surface, captured_at):
        box = surface_pixels(image, surface)
        if box[2]-box[0] < 8 or box[3]-box[1] < 8:
            raise ValueError('surface must be at least 8 x 8 pixels')
        image.save(self.store.root/'background.jpg', quality=100, subsampling=0)
        write_json(self.store.root/'background.json', {'surface': list(surface), 'captured_at': captured_at,
                                                      'fingerprint': image_hash(image), 'size': list(image.size)})
        self._gallery_key = None
        return {'ok': True}

    def background_for(self, surface):
        background, meta = self.store.background()
        if any(abs(a-b) > 1e-6 for a,b in zip(meta['surface'], surface)):
            raise ValueError('surface differs from background; capture a new empty-surface photo')
        return background, meta

    def enrol(self, identity, image, surface, role, captured_at, session_id=None):
        if identity not in self.store.identities or role not in {'reference', 'heldout'}:
            raise ValueError('identity or role is not eligible for enrolment')
        background, bg_meta = self.background_for(surface)
        areas = [p['area_fraction'] for enrolled_identity, p in self.store.references()
                 if enrolled_identity == identity and p['background_fingerprint'] == bg_meta['fingerprint']]
        found = detect(image, background, surface, self.config, max(areas) if areas else None)
        if found['reasons'] or len(found['objects']) != 1:
            raise ValueError(f"need exactly one separated object; found {len(found['objects'])}; " + '; '.join(found['reasons']))
        obj = found['objects'][0]
        if obj['status']:
            raise ValueError('cannot enrol an obstructed or ambiguous object')
        crop = image.crop(obj['box_px'])
        fingerprint = image_hash(crop)
        frame_hash = image_hash(image)
        for other in self.store.identities:
            for photo in self.store.meta(other)['photos']:
                if fingerprint == photo['fingerprint'] or frame_hash == photo['frame_fingerprint']:
                    raise ValueError('duplicate photo; reference and heldout must be distinct captures')
                if session_id and photo.get('session_id') == session_id and photo['role'] != role:
                    raise ValueError('recording session cannot cross reference/heldout split')
        uid = identifier()
        directory = self.store.root/'enrolment'/identity/role
        directory.mkdir(parents=True, exist_ok=True)
        frames = directory/'frames'
        frames.mkdir(exist_ok=True)
        crop.save(directory/f'{uid}.jpg', quality=100, subsampling=0)
        image.save(frames/f'{uid}.jpg', quality=100, subsampling=0)
        record = {'id': uid, 'role': role, 'source': 'manual', 'captured_at': captured_at,
                  'surface': list(surface), 'box': obj['box'], 'area_fraction': obj['area_fraction'],
                  'fingerprint': fingerprint, 'frame_fingerprint': frame_hash, 'session_id': session_id,
                  'background_fingerprint': bg_meta['fingerprint'],
                  'path': str((directory/f'{uid}.jpg').relative_to(self.store.root)),
                  'frame_path': str((frames/f'{uid}.jpg').relative_to(self.store.root))}
        meta = self.store.meta(identity)
        meta['photos'].append(record)
        self.store.save_meta(identity, meta)
        if role == 'reference':
            self._gallery_key = None
        return {'ok': True, 'identity': identity, 'role': role, 'photo': record, 'gallery': self.store.counts()}

    def gallery(self, background, bg_meta):
        references = self.store.references()
        off_refs = []
        if self.use_off_images:
            for identity in self.store.identities:
                item = self.store.meta(identity).get('off')
                if item and item.get('use_image') and item.get('image_path'):
                    off_refs.append((identity, item))
        key = hashlib.sha256(json.dumps([references, off_refs, bg_meta], sort_keys=True).encode()).hexdigest()
        if key == self._gallery_key:
            return self._gallery
        vectors, labels, fingerprints = [], [], []
        for identity, photo in references:
            vectors.append(self.embed(Image.open(self.store.root/photo['path']).convert('RGB')))
            labels.append(identity)
            fingerprints.append(photo['fingerprint'])
        # Background quadrants supply non-product negatives; never a heldout/unfamiliar image.
        negatives = []
        if vectors:
            patch = background.crop(surface_pixels(background, bg_meta['surface']))
            w, h = patch.size
            for box in [(0,0,w,h), (0,0,w//2,h//2), (w//2,0,w,h//2), (0,h//2,w//2,h), (w//2,h//2,w,h)]:
                negatives.append(self.embed(patch.crop(box)))
        thresholds = calibrate(vectors, labels, negatives, fingerprints)
        # Optional external references are appearance candidates, never threshold-training data.
        for identity, item in off_refs:
            vectors.append(self.embed(Image.open(self.store.root/'off_cache'/item['image_path']).convert('RGB')))
            labels.append(identity)
        thresholds['off_reference_count'] = len(off_refs)
        thresholds['off_used_for_calibration'] = False
        thresholds['external_references'] = [{'identity': identity, 'source': 'open-food-facts',
                                              'barcode': item['barcode'], 'source_url': item['source_url']}
                                             for identity, item in off_refs]
        self._gallery_key = key
        self._gallery = vectors, labels, thresholds
        return self._gallery

    def recognize(self, image, surface, captured_at):
        started = perf_counter()
        result = {'captured_at': captured_at, 'surface': list(surface), 'objects': [],
                  'visible_units': {i: 0 for i in self.store.identities}, 'uncertain': False, 'reasons': [],
                  'timing_ms': {'detect': 0.0, 'embed': 0.0, 'match': 0.0, 'total': 0.0},
                  'model': self.embed.info, 'gallery': self.store.counts(), 'note': NOTE,
                  'thresholds': calibrate([], [], []), 'off_images_enabled': self.use_off_images}
        start = perf_counter()
        try:
            background, bg_meta = self.background_for(surface)
            areas = [p['area_fraction'] for _, p in self.store.references()
                     if p['background_fingerprint'] == bg_meta['fingerprint']]
            detection = detect(image, background, surface, self.config, max(areas) if areas else None)
            if self.store.references() and not areas:
                result['reasons'].append('reference package sizes are from another background; re-enrol for touching-item checks')
            result['detection'] = {k:v for k,v in detection.items() if k != 'objects'}
            result['reasons'].extend(detection['reasons'])
        except ValueError as exc:
            detection = {'objects': [], 'reasons': [str(exc)]}
            result['reasons'].append(str(exc))
        result['timing_ms']['detect'] = (perf_counter()-start)*1000
        vectors, labels, thresholds = [], [], result['thresholds']
        start = perf_counter()
        pending = []
        try:
            if detection['objects'] and not detection['reasons']:
                vectors, labels, thresholds = self.gallery(background, bg_meta)
                result['thresholds'] = thresholds
                if not thresholds['ready']:
                    result['reasons'].append(thresholds['reason'])
            for obj in detection['objects']:
                decision = {'identity': None, 'candidates': [], 'status': obj['status'] or 'unknown'}
                if detection['reasons']:
                    decision['status'] = 'obstructed'
                elif not obj['status'] and labels:
                    pending.append((len(result['objects']), self.embed(image.crop(obj['box_px']))))
                result['objects'].append({'box': obj['box'], 'area_px': obj['area_px'], **decision,
                                          'label': decision['status']})
                if obj['reason']:
                    result['reasons'].append(obj['reason'])
        except Exception:
            # A dependency/download/model error must not become a confident empty shelf.
            result['reasons'].append('model unavailable; recognition requires manual review')
            self.model_error = 'model load/inference failed'
            pending = []
            for obj in detection['objects'][len(result['objects']):]:
                status = obj['status'] or 'unknown'
                result['objects'].append({'box': obj['box'], 'area_px': obj['area_px'],
                                          'status': status, 'identity': None, 'candidates': [], 'label': status})
        result['timing_ms']['embed'] = (perf_counter()-start)*1000
        start = perf_counter()
        for index, vector in pending:
            decision = classify(vector, vectors, labels, thresholds)
            result['objects'][index].update(decision)
            result['objects'][index]['label'] = self.store.identities.get(decision['identity'], decision['status'])
        for obj in result['objects']:
            if obj['status'] == 'known':
                result['visible_units'][obj['identity']] += 1
            else:
                result['reasons'].append(f"{obj['status']} component requires manual review")
        result['timing_ms']['match'] = (perf_counter()-start)*1000
        result['reasons'] = list(dict.fromkeys(result['reasons']))
        result['uncertain'] = bool(result['reasons'])
        uid = identifier()
        runs = self.store.root/'runs'
        runs.mkdir(exist_ok=True)
        image.save(runs/f'{uid}.jpg', quality=95)
        result['run_id'] = uid
        result['frame_path'] = f'runs/{uid}.jpg'
        result['timing_ms']['total'] = (perf_counter()-started)*1000
        write_json(runs/f'{uid}.json', result)
        return result

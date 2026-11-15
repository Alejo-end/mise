"""Private local artifacts. JSON replacement is atomic; callers serialize service operations."""
import base64
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from . import product_set as PS

ROOT = Path(__file__).resolve().parent
INTEGRATION = Path('/Users/bigboss/Ai/Claudecode/junction2026-int/valio-challenge')
# The props set of 14 Nov (archived as products/archive/kitchen-20261114-b.json) with its original identities: the
# synthetic evaluation and the regression history use it. The live service uses the active set (product_set.py).
ARCHIVED_IDENTITIES = {'exp:chips': 'chips', 'exp:energy-drink': 'energy drink', 'exp:milk': 'milk'}
IDENTITIES = ARCHIVED_IDENTITIES  # kept name for older imports; prefer Store.identities


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid4().hex[:8]


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    tmp.replace(path)


def decode_image(value):
    try:
        if value.startswith('data:image/jpeg;base64,'):
            value = value.split(',', 1)[1]
        if len(value) > 16_000_000:
            raise ValueError('JPEG exceeds 12 MB')
        raw = base64.b64decode(value, validate=True)
        image = Image.open(io.BytesIO(raw))
        if image.format != 'JPEG' or image.width * image.height > 16_000_000:
            raise ValueError('use a JPEG of at most 16 megapixels')
        image.load()
        return image.convert('RGB')
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError('invalid JPEG image_b64') from exc


def image_hash(image):
    return hashlib.sha256(str(image.size).encode() + image.convert('RGB').tobytes()).hexdigest()


class Store:
    def __init__(self, root=ROOT/'store', products_path=None, identities=None):
        self.root = Path(root)
        self._identities = dict(identities) if identities is not None else None
        # The shared product-set file (SHELF_PRODUCTS: the active set). Identities given explicitly (tests, the
        # archived-set evaluation) take precedence; otherwise they are the active set's.
        self.products_path = Path(products_path) if products_path else PS.active_path()
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def identities(self):
        if self._identities is not None:
            return self._identities
        return PS.read(self.products_path).identities if self.products_path.exists() else {}

    def products(self):
        ids = self.identities
        if not self.products_path.exists():
            return {i: {'identity': i, 'label': label, 'catalogue_match': None} for i, label in ids.items()}
        data = json.loads(self.products_path.read_text())
        if data.get('schema') == 'shelf-product-set/2':
            return {p['identity']: {'identity': p['identity'], 'label': p['label'], 'ean': p.get('ean'),
                                    'catalogue_match': {k: (p.get('catalogue') or {}).get(k) for k in ('status', 'product_id', 'sales_unit', 'count_units_per_sales_unit')}}
                    for p in data['products'] if p['identity'] in ids}
        return {v['identity']: v for v in data['slots'].values() if v['identity'] in ids}

    def meta(self, identity):
        path = self.root/'enrolment'/identity/'meta.json'
        return json.loads(path.read_text()) if path.exists() else {'identity': identity, 'photos': [], 'off': None}

    def save_meta(self, identity, meta):
        write_json(self.root/'enrolment'/identity/'meta.json', meta)

    def references(self):
        return [(identity, photo) for identity in self.identities for photo in self.meta(identity)['photos']
                if photo['role'] == 'reference' and photo.get('source') == 'manual']

    def enrolment(self):
        products = self.products()
        return {identity: {
            'identity': identity, 'label': products.get(identity, {}).get('label', label),
            'reference': sum(p['role'] == 'reference' for p in self.meta(identity)['photos']),
            'heldout': sum(p['role'] == 'heldout' for p in self.meta(identity)['photos']),
            'off_data_present': bool((self.meta(identity).get('off') or {}).get('found')),
            'off': self.meta(identity).get('off'),
            'catalogue_match': products.get(identity, {}).get('catalogue_match'),
            'unit_pack_note': 'One separated visible package = one visible unit; no hidden stock or pack-size inference. Catalogue mapping does not authorise purchasing.'
        } for identity, label in self.identities.items()}

    def counts(self):
        return {i: sum(p['role'] == 'reference' for p in self.meta(i)['photos']) for i in self.identities}

    def background(self):
        path = self.root/'background.jpg'
        meta = self.root/'background.json'
        if not path.exists() or not meta.exists():
            raise ValueError('background missing; capture an empty surface first')
        return Image.open(path).convert('RGB'), json.loads(meta.read_text())

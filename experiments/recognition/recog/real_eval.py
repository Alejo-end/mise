"""Human-validated session holdout evaluation. Never train on generated label proposals."""
import json
from pathlib import Path

from .core import crop_rgb, match
from .manifest import truth_at
from .score import score_predictions, score_requests
from .scale import digest


def discover(root):
    """Return validated references/evaluations; malformed/proposed files remain idle."""
    refs, evals, rejected = [], [], []
    for file in sorted(Path(root).glob('*/labels.json')):
        try:
            lab = json.loads(file.read_text())
            if lab.get('validated') is not True or lab.get('validated_by', '').strip() == '':
                raise ValueError('requires validated=true and a human validated_by')
            if lab.get('validation_source') != 'human':
                raise ValueError('validation_source must be human')
            if lab.get('role') not in ('reference', 'evaluation') or not lab.get('session_id'):
                raise ValueError('role and recording session_id required')
            if not lab.get('labels') or not lab.get('assigned'):
                raise ValueError('labels and assigned required')
            previous = []
            for window in lab['labels']:
                if window.get('validated', True) is not True:
                    raise ValueError('unvalidated window')
                a, b = window['from_t'], window['to_t']
                if a >= b or not window['slots']:
                    raise ValueError('invalid window')
                for start, end, slots in previous:
                    if a < end and start < b and set(slots) & set(window['slots']):
                        raise ValueError('overlapping labels')
                previous.append((a, b, window['slots']))
            if lab['role'] == 'evaluation' and 'expected_requests' not in lab:
                raise ValueError('expected_requests required')
            cfg = json.loads((file.parent / 'config.json').read_text())
            meta = [json.loads(s) for s in (file.parent / 'meta.jsonl').read_text().splitlines() if s.strip()]
            frames = [m for m in meta if m['kind'] == 'frame']
            if not frames or any(not (file.parent/'frames'/f['file']).is_file() for f in frames):
                raise ValueError('missing frames')
            entry = {**lab, 'recording': file.parent.name, 'directory': str(file.parent), 'config': cfg,
                     'frames': frames, 'fingerprint': digest([lab, cfg, meta,
                        [(f['file'], __import__('hashlib').sha256((file.parent/'frames'/f['file']).read_bytes()).hexdigest()) for f in frames]])}
            (refs if lab['role'] == 'reference' else evals).append(entry)
        except (ValueError, TypeError, KeyError, OSError) as error:
            rejected.append({'recording': file.parent.name, 'reason': str(error)[:160]})
    # A session may contain several recording folders. Never leak it across the split.
    sessions = {r['session_id'] for r in refs}
    overlapping = [e for e in evals if e['session_id'] in sessions]
    rejected += [{'recording': e['recording'], 'reason': 'session overlaps reference split'} for e in overlapping]
    evals = [e for e in evals if e['session_id'] not in sessions]
    return refs, evals, rejected


def crops(entry):
    from PIL import Image
    out = []
    for frame in entry['frames']:
        image = Image.open(Path(entry['directory'])/'frames'/frame['file']).convert('RGB')
        for slot in entry['config']['slots']:
            truth = truth_at(entry, frame['t'], slot['slot'])
            if truth is not None:
                out.append(({'recording': entry['recording'], 'frame': frame['file'], 't': frame['t'],
                             'slot': slot['slot'], 'truth': truth}, crop_rgb(image, slot['rect'])))
    return out


def fit_reference(refs, embed):
    """Frozen-embedding 1-NN per slot. Only validated reference sessions enter this function."""
    gallery = {}
    for entry in refs:
        if (entry.get('validated') is not True or entry['role'] != 'reference'
                or entry.get('validation_source') != 'human' or not entry.get('validated_by')):
            raise ValueError('training requires validated reference recordings')
        for row, im in crops(entry):
            if row['truth'] in ('unknown', 'obstructed'):
                continue
            vecs, labels = gallery.setdefault(row['slot'], ([], []))
            vecs.append(embed(im))
            labels.append(row['truth'])
    return gallery


def evaluate(entry, gallery, embed, model, threshold=.85):
    if (entry.get('validated') is not True or entry.get('role') != 'evaluation'
            or entry.get('validation_source') != 'human' or not entry.get('validated_by')):
        raise ValueError('scoring requires human-validated evaluation recordings')
    preds = []
    for row, im in crops(entry):
        vectors, labels = gallery.get(row['slot'], ([], []))
        pred = match(embed(im), vectors, labels, threshold, .03) if labels else {'label': 'unknown'}
        preds.append({**row, **pred, 'model': model})
    codes = {label for _, labels in gallery.values() for label in labels} - {'empty'}
    codes |= set(entry['assigned'].values())
    codes |= {v for w in entry['labels'] for v in w['slots'].values()} - {'empty', 'unknown', 'obstructed'}
    return {'predictions': preds, 'scores': score_predictions(preds, entry, entry['assigned'], codes),
            'downstream': downstream(entry, preds), 'threshold': threshold,
            'threshold_source': 'prespecified provisional; not selected on evaluation data'}


def downstream(entry, preds):
    """Board classify/observe with and without veto. Counts shortage decisions, not external orders."""
    import numpy as np
    from PIL import Image
    import shelf_detect as sd
    from .gate import gate_frame
    cfg = entry['config']
    slots = cfg['slots']
    refs = {}
    for s in slots:
        paths = [Path(entry['directory'])/'calibration'/f"slot{s['slot']}_{kind}.npy" for kind in ('empty', 'full')]
        if not all(p.exists() for p in paths):
            return {'status': 'idle: missing calibration', 'metric': 'confirmed shortage decisions'}
        refs[s['slot']] = tuple(np.load(p) for p in paths)
    by = {(p['frame'], p['slot']): p['label'] for p in preds}
    counts = {}
    for gated in (False, True):
        trackers = {s['slot']: sd.new_tracker() for s in slots}
        pending, actual, previous = set(), {}, None
        for i, frame in enumerate(entry['frames']):
            if previous is not None and frame['t'] - previous > 2:
                for s in slots:
                    sd.observe(trackers[s['slot']], 'uncertain', None, s['target_units'], None, frame['t'], cfg)
            previous = frame['t']
            image = Image.open(Path(entry['directory'])/'frames'/frame['file']).convert('RGB')
            readings = {s['slot']: sd.classify(sd.crop(image, s['rect']), *refs[s['slot']], cfg) for s in slots}
            if gated:
                readings = gate_frame(readings, {int(k):v for k,v in entry['assigned'].items()},
                                      {s['slot']: by.get((frame['file'], s['slot']), 'unknown') for s in slots})
            for s in slots:
                n = s['slot']
                st, fill, why = readings[n]
                changed = sd.observe(trackers[n], st, fill, s['target_units'], i, frame['t'], cfg, why=why)
                if changed and trackers[n]['state'] == 'full':
                    pending.discard(n)
                if changed and trackers[n]['state'] in ('low', 'empty') and n not in pending:
                    pending.add(n)
                    actual[n] = actual.get(n, 0) + 1
        counts['gated' if gated else 'baseline'] = score_requests(actual, entry['expected_requests'])
    return {'metric': 'confirmed shortage decisions; proxy, excludes gateway/restock policy', **counts}

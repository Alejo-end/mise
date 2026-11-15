"""Durable item identities and deterministic synthetic robustness inputs. No product truth."""
import hashlib
import json
from pathlib import Path

AUGMENTS = [("identity", 0), ("blur", 1), ("blur", 3), ("blur", 6),
            ("brightness", .3), ("brightness", .6), ("brightness", 1.5),
            ("occlusion", .2), ("occlusion", .5), ("occlusion", .8),
            ("rotation", -15), ("rotation", 15), ("rotation", 45)]
THRESHOLDS = [.5, .7, .85, .95]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2))
    tmp.replace(path)


def bias_items(model, catalogue, repeats=10, tiny=False):
    from .bias import units
    items = units(model, catalogue, only_configs=['base', 'W-two-step'] if tiny else None,
                  limit_images=1 if tiny else None)
    # Round-robin repeats: every configuration gets a first result early.
    return [{**u, 'repeat': r, 'item_id': digest([model, u, r]), 'model': model,
             'sampling': ('greedy' if r % 2 == 0 else 'sample-t0.7')
                         if model.startswith('qwen') else 'provider-default'}
            for r in range(1 if tiny else repeats) for u in items]


def augment(image, kind, value):
    from PIL import ImageEnhance, ImageFilter, ImageDraw
    if kind == 'identity':
        return image.copy()
    if kind == 'blur':
        return image.filter(ImageFilter.GaussianBlur(value))
    if kind == 'brightness':
        return ImageEnhance.Brightness(image).enhance(value)
    if kind == 'rotation':
        return image.rotate(value, resample=3)
    if kind == 'occlusion':
        out = image.copy()
        w, h = out.size
        side = value ** .5
        ImageDraw.Draw(out).rectangle((w*(1-side)/2, h*(1-side)/2, w*(1+side)/2, h*(1+side)/2), fill='grey')
        return out
    raise ValueError(kind)


def token_totals(records):
    total = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    for r in records:
        for response in r.get('responses', [r]):
            for key in total:
                total[key] += int(response.get('usage', {}).get(key, 0) or 0)
    return total

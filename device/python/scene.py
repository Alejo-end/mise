"""Has the shelf scene changed? A small brightness-normalised grey picture of a frame, compared cell by cell.

Used by the recognition coordinator (visible.py) to tell a settled scene from a moving one (two frames a moment
apart) and to reuse the whole-frame cloud check only while the scene stays the same as the one it checked. One
package added, removed or moved changes at least one cell; camera noise and a small exposure change do not (the
global brightness is removed first). Thresholds are provisional, measured on the box's own frames.
"""
from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image

SIZE = (64, 48)        # the grey picture, 8 x 8 pixels per cell
CELLS = (8, 6)
CELL_LIMIT = 6.0       # mean absolute difference (0-255 grey levels) of the most changed cell: above it, changed
# Measured on saved box frames (15 Nov): consecutive frames of a still scene 0.5-0.8; the same arrangement minutes
# apart 7-12 (light drift: a new check, the safe side); two shakes removed 78. A false "changed" costs one more cloud
# check; a false "unchanged" would reuse old identities, so the limit stays low.


def sig(jpeg_b64: str) -> np.ndarray:
    img = Image.open(io.BytesIO(base64.b64decode(jpeg_b64))).convert("L").resize(SIZE, Image.BILINEAR)
    a = np.asarray(img, dtype=np.float32)
    return a - a.mean()


def diff(a: np.ndarray, b: np.ndarray) -> float:
    d = np.abs(a - b)
    cw, ch = SIZE[0] // CELLS[0], SIZE[1] // CELLS[1]
    return float(d.reshape(CELLS[1], ch, CELLS[0], cw).mean(axis=(1, 3)).max())


def same(a, b, limit: float = CELL_LIMIT) -> bool:
    return a is not None and b is not None and diff(a, b) <= limit

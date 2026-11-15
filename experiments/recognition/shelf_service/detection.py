"""Fixed-camera foreground components, restricted to the monitored surface."""
from dataclasses import asdict, dataclass

import cv2
import numpy as np
from PIL import Image


@dataclass(frozen=True)
class DetectionConfig:
    difference: int = 25
    min_area_fraction: float = .002
    max_area_fraction: float = .45
    lighting_fraction: float = .70
    touching_ratio: float = 1.65
    entry_side: str = "top"
    border_px: int = 3

    def __post_init__(self):
        if self.entry_side not in {"top", "bottom", "left", "right", "any"}:
            raise ValueError("entry_side must be top, bottom, left, right or any")
        if not (0 < self.min_area_fraction < self.max_area_fraction <= 1):
            raise ValueError("invalid area fractions")
        if not (0 < self.lighting_fraction <= 1 and 0 < self.difference < 256):
            raise ValueError("invalid lighting/difference threshold")
        if self.touching_ratio <= 1 or self.border_px < 0:
            raise ValueError("invalid touching ratio or border tolerance")


def surface_pixels(image, surface):
    x, y, w, h = surface
    iw, ih = image.size
    return (round(x * iw), round(y * ih), round((x + w) * iw), round((y + h) * ih))


def detect(image: Image.Image, background: Image.Image, surface, config=DetectionConfig(), package_area=None):
    if image.size != background.size:
        raise ValueError("frame dimensions differ from background; capture a new background")
    left, top, right, bottom = surface_pixels(image, surface)
    if right - left < 8 or bottom - top < 8:
        raise ValueError("surface must be at least 8 x 8 pixels")
    a = np.asarray(image.convert("RGB"))[top:bottom, left:right]
    b = np.asarray(background.convert("RGB"))[top:bottom, left:right]
    a, b = (cv2.GaussianBlur(v, (5, 5), 0) for v in (a, b))
    diff = np.max(cv2.absdiff(a, b), axis=2)
    mask = (diff > config.difference).astype(np.uint8)
    changed = float(mask.mean())
    reasons = ["frame-wide change: lighting, camera movement or obstruction"] if changed >= config.lighting_fraction else []
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    sh, sw = mask.shape
    objects = []
    for i in range(1, count):
        x, y, w, h, area = map(int, stats[i])
        if area < max(16, sw * sh * config.min_area_fraction):
            continue
        sides = {"left": x <= config.border_px, "top": y <= config.border_px,
                 "right": x + w >= sw - config.border_px, "bottom": y + h >= sh - config.border_px}
        enters = any(sides.values()) if config.entry_side == "any" else sides[config.entry_side]
        status, reason = None, None
        if enters or area / (sw * sh) > config.max_area_fraction:
            status, reason = "obstructed", "component touches camera entry border or exceeds maximum area"
        elif package_area and area / (sw * sh) > package_area * config.touching_ratio:
            status, reason = "ambiguous", "possibly several touching items"
        box_px = [left + x, top + y, left + x + w, top + y + h]
        iw, ih = image.size
        objects.append({"box": [(left+x)/iw, (top+y)/ih, w/iw, h/ih], "box_px": box_px,
                        "area_px": area, "area_fraction": area/(sw*sh), "status": status, "reason": reason})
    return {"objects": sorted(objects, key=lambda o: o['area_px'], reverse=True),
            "reasons": reasons, "changed_fraction": changed, "config": asdict(config)}

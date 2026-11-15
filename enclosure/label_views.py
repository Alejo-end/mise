"""Finish the two enclosure pictures: a light wall, and part names on the exploded view.

Run render_views.py first. Then, from this folder:

    uv run --no-project --with pillow python label_views.py

It reads renders/*_raw.png and renders/exploded_labels.json, and writes renders/assembled.png,
renders/side.png, renders/back.png and renders/exploded.png.

The place of each part comes from the render. The offset of each balloon is set by hand for the
camera in render_views.py. If you move the camera or a part, the script stops when two balloons
touch or a balloon leaves the picture. Then change the offsets in LABELS.
"""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent / "renders"
GROUND, INK, INK2, LINE = (252, 252, 251), (11, 11, 11), (82, 81, 78), (137, 135, 129)

# number, name, part in assembly.json, which point of the part the balloon points at (left, right or top),
# balloon offset in pixels
LABELS = [
    (1, "Front cover, printed, snaps on", "front", "right", (60, -150)),
    (2, "Knob cap, printed", "knob_cap", "right", (70, 0)),
    (3, "Power button cap, printed", "button_cap", "right", (60, 0)),
    (4, "LED grille insert, printed", "led_grille", "right", (30, 45)),
    (5, "Guition JC4827W543C touch screen", "screen", "right", (58, -31)),
    (6, "Arduino UNO Q", "uno_q", "right", (55, 45)),
    (7, "Modulino Knob", "knob_parts", "right", (60, -20)),
    (8, "Modulino Buzzer", "buzzer_parts", "right", (55, 0)),
    (9, "Back shell, printed", "back", "left", (-70, 0)),
    (10, "Thermo pod cover, printed", "thermo_pod_cover", "right", (45, 60)),
    (11, "Modulino Thermo", "thermo", "left", (-10, 80)),
    (12, "Thermo pod base, printed", "thermo_pod_base", "left", (-70, 0)),
    (13, "Distance pod cover, printed", "tof_pod_cover", "right", (60, 20)),
    (14, "SparkFun ToF Imager VL53L5CX", "tof", "right", (10, 75)),
    (15, "Distance pod base, printed", "tof_pod_base", "top", (-90, 40)),
]


def font(size, bold=False):
    for path, index in (("/System/Library/Fonts/Helvetica.ttc", 1 if bold else 0),
                        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
                         else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0)):
        try:
            return ImageFont.truetype(path, size, index=index)
        except OSError:
            continue
    return ImageFont.load_default()


def on_ground(name):
    raw = Image.open(HERE / f"{name}_raw.png").convert("RGBA")
    out = Image.new("RGBA", raw.size, GROUND + (255,))
    out.alpha_composite(raw)
    return out.convert("RGB")


def main():
    on_ground("assembled").save(HERE / "assembled.png", optimize=True)
    on_ground("side").save(HERE / "side.png", optimize=True)
    on_ground("back").save(HERE / "back.png", optimize=True)
    img = on_ground("exploded")
    data = json.loads((HERE / "exploded_labels.json").read_text())
    at = {l["name"]: l for l in data["labels"]}
    w, h = img.size
    draw = ImageDraw.Draw(img)
    name_font, num_font = font(30), font(26, bold=True)
    r = 22
    placed = []
    for n, _, part, side, (dx, dy) in LABELS:
        px, py = at[part][side][0] * w, at[part][side][1] * h
        cx, cy = px + dx, py + dy
        if not (r <= cx <= w - r and r <= cy <= h - r):
            raise SystemExit(f"Balloon {n} is outside the picture. Change its offset in LABELS.")
        for m, ox, oy in placed:
            if (cx - ox) ** 2 + (cy - oy) ** 2 < (2 * r + 4) ** 2:
                raise SystemExit(f"Balloons {m} and {n} touch. Change an offset in LABELS.")
        placed.append((n, cx, cy))
        draw.line([(cx, cy), (px, py)], fill=LINE, width=2)
        draw.ellipse([px - 5, py - 5, px + 5, py + 5], fill=INK)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=INK)
        draw.text((cx, cy), str(n), font=num_font, fill=(255, 255, 255), anchor="mm")
    # The names stand below the picture, in columns, so no line crosses a part.
    crop_top, legend_h = 110, 270
    page = Image.new("RGB", (w, h - crop_top + legend_h), GROUND)
    page.paste(img.crop((0, crop_top, w, h)), (0, 0))
    img, draw = page, ImageDraw.Draw(page)
    rows, col_w, x0, y0 = 5, 560, 110, h - crop_top + 40
    for i, (n, text, *_rest) in enumerate(LABELS):
        x, y = x0 + (i // rows) * col_w, y0 + (i % rows) * 44
        draw.text((x, y), str(n), font=num_font, fill=INK, anchor="rm")
        draw.text((x + 16, y), text, font=name_font, fill=INK2, anchor="lm")
    img.save(HERE / "exploded.png", optimize=True)
    print("wrote", HERE / "assembled.png", HERE / "side.png", "and", HERE / "exploded.png")


main()

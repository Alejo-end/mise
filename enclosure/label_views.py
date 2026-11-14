"""Finish the two enclosure pictures: a light ground, and part names on the exploded view.

Run render_views.py first. Then, from this folder:

    uv run --no-project --with pillow python label_views.py

It reads renders/*_raw.png and renders/exploded_labels.json, and writes renders/assembled.png
and renders/exploded.png.
"""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent / "renders"
GROUND, INK, INK2, LINE = (252, 252, 251), (11, 11, 11), (82, 81, 78), (137, 135, 129)

# number, name, part in assembly.json, which end of the part the balloon points at, balloon offset in pixels
LABELS = [
    (1, "4 screws M3 x 10, lid", "lid_screws", "left", (-70, 0)),
    (2, "Lid, printed", "lid", "left", (-70, 0)),
    (3, "12 screws M3 x 6, boards", "uno_q_screws", "left", (-70, 0)),
    (4, "Arduino UNO Q", "uno_q", "left", (-70, 0)),
    (5, "Base, printed", "base", "left", (-70, 0)),
    (6, "Modulino Buzzer", "buzzer", "right", (85, 5)),
    (7, "Modulino Knob", "knob_parts", "right", (75, 20)),
    (8, "4 screws M3 x 10, pod", "pod_screws", "right", (70, 0)),
    (9, "Thermo pod cover, printed", "pod_cover", "right", (70, 0)),
    (10, "Modulino Thermo", "thermo", "right", (70, 0)),
    (11, "Thermo pod base, printed", "pod_base", "right", (70, 10)),
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
    img = on_ground("exploded")
    data = json.loads((HERE / "exploded_labels.json").read_text())
    at = {l["name"]: l for l in data["labels"]}
    w, h = img.size
    draw = ImageDraw.Draw(img)
    name_font, num_font = font(30), font(26, bold=True)
    r = 22
    for n, _, part, side, (dx, dy) in LABELS:
        px, py = at[part][side][0] * w, at[part][side][1] * h
        cx, cy = px + dx, py + dy
        draw.line([(cx, cy), (px, py)], fill=LINE, width=2)
        draw.ellipse([px - 5, py - 5, px + 5, py + 5], fill=INK)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=INK)
        draw.text((cx, cy), str(n), font=num_font, fill=(255, 255, 255), anchor="mm")
    # The names stand below the picture, in three columns, so no line crosses a part.
    crop_top, legend_h = 110, 220
    page = Image.new("RGB", (w, h - crop_top + legend_h), GROUND)
    page.paste(img.crop((0, crop_top, w, h)), (0, 0))
    img, draw = page, ImageDraw.Draw(page)
    rows, col_w, x0, y0 = 4, 560, 110, h - crop_top + 40
    for i, (n, text, *_rest) in enumerate(LABELS):
        x, y = x0 + (i // rows) * col_w, y0 + (i % rows) * 44
        draw.text((x, y), str(n), font=num_font, fill=INK, anchor="rm")
        draw.text((x + 16, y), text, font=name_font, fill=INK2, anchor="lm")
    img.save(HERE / "exploded.png", optimize=True)
    print("wrote", HERE / "assembled.png", "and", HERE / "exploded.png")


main()

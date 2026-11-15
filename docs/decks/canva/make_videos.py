"""Two short loops for the Canva deck, made from the same pictures as the HTML deck.

explode.mp4: the case with the screen comes apart into 16 numbered parts, then goes back together.
iterations.mp4: the eight versions of the case crossfade on a light screen.
Both open on their finished state, so a PDF export, which keeps the first frame, shows the useful picture.

uv run --with pillow python make_videos.py <layers dir from render_layers.py> <font dir with Inter.ttf>
"""
import json, os, subprocess, sys, tempfile, math
from PIL import Image, ImageDraw, ImageFont

LAY, FONTS = sys.argv[1], sys.argv[2]
HERE = os.path.dirname(os.path.abspath(__file__))
HTML_IMG = os.path.join(HERE, "..", "html", "img")
FPS = 30


def font(size, weight="Bold"):
    f = ImageFont.truetype(os.path.join(FONTS, "Inter.ttf"), size)
    try: f.set_variation_by_name(weight)
    except Exception: pass
    return f


def ease(t): return 4 * t ** 3 if t < .5 else 1 - (-2 * t + 2) ** 3 / 2


def encode(frames_dir, out):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", os.path.join(frames_dir, "%05d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", out], check=True)


# ---------- explode ----------
meta = json.load(open(os.path.join(LAY, "layers.json")))
W0, H = meta["width"], meta["height"]
OFF = 150                      # room for the numbers on both sides
W = W0 + 2 * OFF
bg = Image.new("RGB", (W, H), (255, 255, 255))     # the clean white of the renders
layers = [(l, Image.open(os.path.join(LAY, l["name"] + ".png")).convert("RGBA")) for l in meta["layers"]]
whole = Image.open(os.path.join(LAY, "assembled.png")).convert("RGBA")

# The numbers of the exploded view in valio-challenge/enclosure/renders/exploded.png (the wall unit)
ORDER = ["front", "knob_cap", "button_cap", "led_grille", "screen", "uno_q", "knob", "buzzer", "back",
         "thermo_pod_cover", "thermo", "thermo_pod_base", "tof_pod_cover", "tof", "tof_pod_base"]
NUM = {n: i + 1 for i, n in enumerate(ORDER)}
LEFT = {"back", "screen", "uno_q", "thermo_pod_base", "thermo", "thermo_pod_cover"}
# Blender wrote "right" and "left" anchors in pixels of the exploded picture; recompute them from the layer alpha.
calls = []
for l, im in layers:
    a = im.getchannel("A"); bb = a.getbbox()
    right = l["name"] not in LEFT
    # the outermost opaque pixel on that side, at the row through the middle of the part
    ys = range(bb[1], bb[3], 4)
    best = None
    for y in ys:
        row = [x for x in range(bb[0], bb[2], 3) if a.getpixel((x, y)) > 128]
        if not row: continue
        x = max(row) if right else min(row)
        if best is None or (x > best[0] if right else x < best[0]): best = (x, y)
    calls.append({"name": l["name"], "right": right, "ax": best[0] + OFF, "ay": best[1], "y": best[1]})
for side in (True, False):
    g = sorted([c for c in calls if c["right"] == side], key=lambda c: c["y"])
    for k in range(1, len(g)):
        if g[k]["y"] - g[k - 1]["y"] < 50: g[k]["y"] = g[k - 1]["y"] + 50
F_NUM = font(24)


def draw_calls(img, alpha):
    if alpha <= 0: return img
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
    ink, soft = (12, 21, 48, int(255 * alpha)), (138, 146, 166, int(255 * alpha))
    for c in calls:
        s = 1 if c["right"] else -1
        p = [(c["ax"], c["ay"]), (c["ax"] + 40 * s, c["y"]), (c["ax"] + 80 * s, c["y"])]
        d.line(p, fill=soft, width=3, joint="curve")
        d.ellipse((c["ax"] - 6, c["ay"] - 6, c["ax"] + 6, c["ay"] + 6), fill=soft)
        cx = c["ax"] + 102 * s
        d.ellipse((cx - 22, c["y"] - 22, cx + 22, c["y"] + 22), fill=ink)
        d.text((cx, c["y"]), str(NUM[c["name"]]), font=F_NUM, fill=(255, 255, 255, int(255 * alpha)), anchor="mm")
    return Image.alpha_composite(img, ov)


def explode_frame(t):
    """t = 0 exploded, 1 assembled. Each part starts a little later than the one before it."""
    img = bg.convert("RGBA")
    n = len(layers)
    for i, (l, im) in enumerate(layers):
        k = min(1, max(0, (t * 1.35 - i * 0.35 / n)))
        e = ease(k)
        x, y = int(round(l["dx"] * e)), int(round(l["dy"] * e))
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0)); layer.paste(im, (x + OFF, y), im)
        img = Image.alpha_composite(img, layer)
    return img


with tempfile.TemporaryDirectory() as tmp:
    seq = []
    hold = lambda img, s: seq.extend([img] * int(s * FPS))
    exploded = draw_calls(explode_frame(0), 1)
    hold(exploded, 2.5)                                             # first frame: exploded, numbered
    for k in range(int(.4 * FPS)): seq.append(draw_calls(explode_frame(0), 1 - k / (.4 * FPS)))
    for k in range(int(1.6 * FPS)): seq.append(explode_frame((k + 1) / (1.6 * FPS)))
    together = explode_frame(1)
    wf = Image.new("RGBA", (W, H), (0, 0, 0, 0)); wf.paste(whole, (OFF, 0), whole)
    wholeimg = Image.alpha_composite(bg.convert("RGBA"), wf)
    for k in range(int(.4 * FPS)): seq.append(Image.blend(together, wholeimg, (k + 1) / (.4 * FPS)))
    hold(wholeimg, 1.6)
    for k in range(int(.3 * FPS)): seq.append(Image.blend(wholeimg, together, (k + 1) / (.3 * FPS)))
    for k in range(int(1.6 * FPS)): seq.append(explode_frame(1 - (k + 1) / (1.6 * FPS)))
    for k in range(int(.4 * FPS)): seq.append(draw_calls(explode_frame(0), (k + 1) / (.4 * FPS)))
    hold(exploded, 1.0)
    cache = {}
    for i, im in enumerate(seq):
        key = id(im)
        if key not in cache: cache[key] = im.convert("RGB")
        cache[key].save(os.path.join(tmp, f"{i:05d}.png"), compress_level=1)
    encode(tmp, os.path.join(HERE, "explode.mp4"))
    exploded.convert("RGB").save(os.path.join(HERE, "explode_poster.png"))

# ---------- iterations ----------
LABELS = ["First fit check", "Labels and vents", "The name, a distance pod", "A knob cap",
          "First render, with screws", "Snap-fit lid, en place", "Knob cap, rendered", "A touch screen on a stand",
          "One wall unit, the screen in its face"]
VW, VH = 1600, 900
F_V, F_T = font(30, "SemiBold"), font(28, "Regular")
pics = [Image.open(os.path.join(HERE, "img", "iter", f"v{i + 1}.png")).convert("RGB").resize((VW, VH)) for i in range(len(LABELS))]


def labelled(i):
    im = pics[i].copy(); d = ImageDraw.Draw(im)
    d.text((40, 34), f"v{i + 1}", font=F_V, fill=(12, 21, 48))
    d.text((40 + d.textlength(f"v{i + 1}", font=F_V) + 16, 36), LABELS[i], font=F_T, fill=(89, 98, 122))
    # the version dots, bottom left, like the LED row on the box
    for k in range(len(LABELS)):
        x, y = 52 + k * 34, VH - 46
        col = (31, 79, 216) if k == i else ((12, 21, 48) if k < i else (214, 218, 228))
        d.ellipse((x - 9, y - 9, x + 9, y + 9), fill=col)
    return im


with tempfile.TemporaryDirectory() as tmp:
    seq = []
    order = [len(LABELS) - 1] + list(range(len(LABELS)))                    # open on the newest version, then play the history
    holds = [2.0] + [1.3] * (len(LABELS) - 1) + [2.5]
    frames = [labelled(i) for i in order]
    for j, im in enumerate(frames):
        seq += [im] * int(holds[j] * FPS)
        if j + 1 < len(frames):
            for k in range(int(.5 * FPS)): seq.append(Image.blend(im, frames[j + 1], (k + 1) / (.5 * FPS)))
    for i, im in enumerate(seq): im.save(os.path.join(tmp, f"{i:05d}.png"), compress_level=1)
    encode(tmp, os.path.join(HERE, "iterations.mp4"))
    frames[0].save(os.path.join(HERE, "iterations_poster.png"))
print("done")

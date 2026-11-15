"""Iteration frames on the mise panel colour, then an MP4 and a GIF of them."""
import os, subprocess
from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageFilter

S = os.path.dirname(os.path.abspath(__file__))
IT, OUT = os.path.join(S, "iter"), os.path.join(S, "frames")
os.makedirs(OUT, exist_ok=True)
W, H = 1920, 1080
BG = (246, 247, 250)       # --bg of the app, light
INK, DIM, ORANGE, OFF = (12, 21, 48), (89, 98, 122), (31, 79, 216), (214, 218, 228)
SEQ = ["preview_1", "preview_2", "preview_3", "preview_4", "assembled_1", "assembled_2", "assembled_3"]


def font(size, weight="SemiBold"):
    f = ImageFont.truetype(os.path.join(S, "fonts", "Inter.ttf"), size)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


def cutout(path):
    im = Image.open(path).convert("RGBA")
    rgb = im.convert("RGB")
    bg = rgb.getpixel((3, 3))
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg)).convert("L")
    # soft mask: pixels close to the ground fade out, so each frame sits on the same panel
    mask = diff.point(lambda v: 0 if v < 4 else min(255, (v - 4) * 18)).filter(ImageFilter.GaussianBlur(0.6))
    alpha = ImageChops.multiply(mask, im.getchannel("A"))
    im.putalpha(alpha)
    box = alpha.point(lambda v: 255 if v > 20 else 0).getbbox()
    return im.crop(box)


frames = []
for i, name in enumerate(SEQ):
    art = cutout(os.path.join(IT, name + ".png"))
    sc = min(1640 / art.width, 760 / art.height)
    art = art.resize((int(art.width * sc), int(art.height * sc)), Image.LANCZOS)
    f = Image.new("RGB", (W, H), BG)
    f.paste(art, ((W - art.width) // 2, 110 + (780 - art.height) // 2), art)
    d = ImageDraw.Draw(f)
    # LED row: one dot per iteration, lit up to this one
    for k in range(len(SEQ)):
        x, y = 140 + k * 46, 975
        col = ORANGE if k == i else (INK if k < i else OFF)
        d.ellipse((x - 10, y - 10, x + 10, y + 10), fill=col)
    d.text((140 + len(SEQ) * 46 + 20, 975), f"v{i + 1}", font=font(44), fill=INK, anchor="lm")
    p = os.path.join(OUT, f"iter_{i + 1}.png")
    f.save(p)
    frames.append(p)

# MP4: hold each frame, crossfade to the next, hold the last one longer
hold, fade, last = 1.4, 0.5, 3.0
inputs, durs = [], []
for k, p in enumerate(frames):
    d = (last if k == len(frames) - 1 else hold) + fade
    durs.append(d)
    inputs += ["-loop", "1", "-t", f"{d}", "-i", p]
chain, prev, off = [], "0:v", 0.0
for k in range(1, len(frames)):
    off += durs[k - 1] - fade
    out = f"x{k}"
    chain.append(f"[{prev}][{k}:v]xfade=transition=fade:duration={fade}:offset={off:.3f}[{out}]")
    prev = out
chain.append(f"[{prev}]format=yuv420p[v]")
mp4 = os.path.join(S, "mise_case_iterations.mp4")
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(chain),
                "-map", "[v]", "-r", "30", "-c:v", "libx264", "-crf", "18", mp4], check=True)
gif = os.path.join(S, "mise_case_iterations.gif")
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp4, "-vf",
                "fps=15,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer",
                gif], check=True)
print(mp4, gif)

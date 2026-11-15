"""Put each animation on a full slide, so its colours can never differ from the slide behind it.

A video is decoded with slightly other colours than the flat slide fill, so a video placed on a coloured
slide shows a visible box. Here every video is 1920 x 1080 and carries its own slide background
(fill, top bar and panels). The PowerPoint file puts it first on the slide, under all text.

uv run --with pillow python fullbleed.py
"""
import os, subprocess
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
K = 1.2  # 1600 px slide -> 1920 px video
WHITE = "#ffffff"
# name: (source video, slide fill, top bar, panels, video box (x, y, w, h)) in slide pixels, as in mise_white.py
SLIDES = {
    "s6_iterations": ("iterations", WHITE, WHITE, [], (240, 180, 1120, 1120 * 9 / 16)),
    "s7_explode": ("explode", WHITE, WHITE, [], (60, 190, 1000, 1000 * 1200 / 1900)),
}


def bg_image(fill, bar, panels):
    im = Image.new("RGB", (1920, 1080), fill); d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 1920, round(6 * K)), fill=bar)
    for x, y, w, h, f in panels:
        d.rounded_rectangle((round(x * K), round(y * K), round((x + w) * K), round((y + h) * K)), radius=round(12 * K), fill=f)
    return im


for name, (src, fill, bar, panels, (x, y, w, h)) in SLIDES.items():
    bg = bg_image(fill, bar, panels)
    bgp = os.path.join(HERE, f"_bg_{name}.png"); bg.save(bgp)
    X, Y, Wv, Hv = round(x * K), round(y * K), round(w * K) // 2 * 2, round(h * K) // 2 * 2
    out = os.path.join(HERE, "full", name + ".mp4"); os.makedirs(os.path.dirname(out), exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", bgp, "-i", os.path.join(HERE, src + ".mp4"),
                    "-filter_complex", f"[1:v]scale={Wv}:{Hv}:flags=lanczos[v];[0:v][v]overlay={X}:{Y}:shortest=1,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
                    "-r", "30", "-c:v", "libx264", "-crf", "18", "-colorspace", "bt709", "-color_primaries", "bt709",
                    "-color_trc", "bt709", "-movflags", "+faststart", out], check=True)
    poster = bg.copy(); poster.paste(Image.open(os.path.join(HERE, src + "_poster.png")).convert("RGB").resize((Wv, Hv), Image.LANCZOS), (X, Y))
    poster.save(os.path.join(HERE, "full", name + "_poster.png"))
    os.remove(bgp)
print("done")

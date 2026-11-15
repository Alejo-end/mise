"""Crop the Blender layers, save them as WebP and write their positions into index.html.

python build_layers.py <render dir>   (the dir render_layers.py wrote)
"""
import json, os, re, sys
from PIL import Image

SRC = sys.argv[1]; H = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(H, "img", "layers")
os.makedirs(OUT, exist_ok=True)
meta = json.load(open(os.path.join(SRC, "layers.json")))
for l in meta["layers"]:
    im = Image.open(os.path.join(SRC, l["name"] + ".png"))
    b = im.getchannel("A").point(lambda v: 255 if v > 4 else 0).getbbox()
    im.crop(b).save(os.path.join(OUT, l["name"] + ".webp"), quality=86)
    l.update(x=b[0], y=b[1], w=b[2] - b[0], h=b[3] - b[1], dx=round(l["dx"], 1), dy=round(l["dy"], 1),
             right=[round(v) for v in l["right"]], left=[round(v) for v in l["left"]])
whole = Image.open(os.path.join(SRC, "assembled.png")); whole.save(os.path.join(OUT, "assembled.webp"), quality=86)
page = os.path.join(H, "index.html"); t = open(page).read()
t = re.sub(r"/\*LAYERS\*/.*?/\*END\*/", "/*LAYERS*/window.LAYERS = " + json.dumps(meta, separators=(",", ":")).replace("\\", "\\\\") + ";/*END*/", t, flags=re.S)
open(page, "w").write(t)
print(len(meta["layers"]), "layers")

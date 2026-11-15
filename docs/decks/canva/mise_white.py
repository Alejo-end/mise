"""The mise deck for Canva, white and quiet: the look of the renders, few words, two looping videos.

uv run --with python-pptx --with pillow python mise_white.py
Coordinates are pixels of a 1600 x 900 slide; 120 px is one inch.
"""
import os
from pptx import Presentation
from pptx.util import Emu, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS, HIMG, IMG = os.path.join(HERE, "..", "assets"), os.path.join(HERE, "..", "html", "img"), os.path.join(HERE, "img")
OUT = os.path.join(HERE, "mise - Valio Aimo (white).pptx")


def c(h): return RGBColor.from_string(h.lstrip("#"))


WHITE, CARD, LINE = c("#ffffff"), c("#f5f6f8"), c("#e6e8ee")
INK, SOFT, FAINT = c("#0c1530"), c("#59627a"), c("#9aa1b2")
BLUE, BLUE_SOFT, RED = c("#1f4fd8"), c("#eaf0ff"), c("#d63a31")
OK, OK_SOFT, WARN, WARN_SOFT, PINK, PINK_SOFT = c("#0a7450"), c("#e2f4ec"), c("#8f5600"), c("#fdf0d9"), c("#d2175f"), c("#fde8f0")
FONT = "Inter"
PX = 914400 // 120


def px(v): return Emu(int(v * PX))


p = Presentation(); p.slide_width, p.slide_height = px(1600), px(900)
N = [0]


def png(path):
    if path.endswith(".png"): return path
    out = os.path.join(IMG, os.path.splitext(os.path.basename(path))[0] + ".png")
    Image.open(path).save(out); return out


def new():
    s = p.slides.add_slide(p.slide_layouts[6]); f = s.background.fill; f.solid(); f.fore_color.rgb = WHITE
    N[0] += 1; return s


def text(s, x, y, w, h, t, size, color=INK, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP):
    b = s.shapes.add_textbox(px(x), px(y), px(w), px(h)); tf = b.text_frame
    tf.word_wrap = True; tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, line in enumerate(t if isinstance(t, list) else [t]):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph(); para.alignment = align
        r = para.add_run(); r.text = line
        r.font.size = Pt(size * 0.75); r.font.bold = bold; r.font.name = FONT; r.font.color.rgb = color
    return b


def rect(s, x, y, w, h, fill=CARD, line=None, radius=14, shape=MSO_SHAPE.ROUNDED_RECTANGLE, lw=1):
    r = s.shapes.add_shape(shape, px(x), px(y), px(w), px(h))
    if fill is None: r.fill.background()
    else: r.fill.solid(); r.fill.fore_color.rgb = fill
    if line is None: r.line.fill.background()
    else: r.line.color.rgb = line; r.line.width = Pt(lw * 0.75)
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE: r.adjustments[0] = min(0.5, radius / min(w, h))
    r.shadow.inherit = False; return r


def pic(s, path, x, y, w=None, h=None):
    path = png(path); iw, ih = Image.open(path).size
    if w and h:
        k = min(w / iw, h / ih); ww, hh = iw * k, ih * k; x += (w - ww) / 2; y += (h - hh) / 2; w, h = ww, hh
    elif w: h = ih * w / iw
    else: w = iw * h / ih
    s.shapes.add_picture(path, px(x), px(y), px(w), px(h)); return w, h


def pill(s, x, y, label, fg, bg, size=18):
    w = 8.8 * len(label) * size / 15 + 40
    rect(s, x, y, w, 40, fill=bg, radius=18)
    text(s, x, y, w, 40, label, size, fg, True, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE); return w


def frame(s, title):
    pic(s, os.path.join(ASSETS, "mise_blue_light.png"), 80, 52, h=30)
    text(s, 80, 104, 1440, 64, title, 44, INK, True)
    pic(s, os.path.join(ASSETS, "enplace_light.png"), 80, 846, h=22)
    text(s, 1440, 846, 80, 22, f"{N[0]:02d}", 13, FAINT, align=PP_ALIGN.RIGHT)


def logos(s, x, y, h=40):
    for name, k in (("valio", 1.0), ("aimo", 1.0), ("arduino", 0.8)):
        w, _ = pic(s, os.path.join(ASSETS, name + ".png"), x, y + h * (1 - k) / 2, h=h * k)
        x += w + 44



def full(s, name):
    """The animation as the whole slide, so its white and the slide's white are the same picture."""
    s.shapes.add_movie(os.path.join(HERE, "full", name + ".mp4"), 0, 0, px(1600), px(900),
                       poster_frame_image=os.path.join(HERE, "full", name + "_poster.png"), mime_type="video/mp4")

# 1 Title
s = new()
pic(s, os.path.join(ASSETS, "mise_blue_light.png"), 90, 200, h=170)
pic(s, os.path.join(ASSETS, "enplace_light.png"), 330, 380, h=46)
text(s, 96, 480, 620, 120, "A shelf that orders from Valio Aimo by itself, inside the chef's rules.", 30, INK)
logos(s, 96, 760)
pic(s, os.path.join(IMG, "hero_white.png"), 760, 120, 780, 640)

# 2 The kitchen
s = new(); frame(s, "Kitchens still order by hand.")
text(s, 80, 250, 760, 150, "20,000+", 120, BLUE, True)
text(s, 86, 470, 640, 80, "professional kitchens rely on Valio Aimo.", 28, INK)
text(s, 86, 570, 640, 30, "Valio Aimo challenge brief, Junction 2026", 14, FAINT)
rect(s, 880, 260, 640, 360)
text(s, 930, 310, 540, 200, "\"This is not about replacing today's e-commerce UI with a chatbot.\"", 30, INK, True)
text(s, 930, 540, 540, 30, "Valio Aimo", 16, SOFT)

# 3 How it works
s = new(); frame(s, "A model searches. Code decides.")
cols = [("The kitchen", ["Shelf box", "Chef's phone"], CARD, INK),
        ("The gateway", ["Search and quote", "Rule check", "Audit log"], BLUE_SOFT, BLUE),
        ("Valio", ["Product file", "Price and stock"], CARD, INK)]
for k, (h, items, fill, col) in enumerate(cols):
    x = 80 + k * 500
    rect(s, x, 250, 440, 420, fill=fill)
    text(s, x + 36, 280, 380, 30, h.upper(), 15, col, True)
    for j, it in enumerate(items):
        rect(s, x + 36, 330 + j * 92, 368, 72, fill=WHITE)
        text(s, x + 60, 330 + j * 92, 330, 72, it, 22, INK, True, anchor=MSO_ANCHOR.MIDDLE)
    if k < 2:
        a = s.shapes.add_shape(MSO_SHAPE.CHEVRON, px(x + 452), px(442), px(36), px(36))
        a.fill.solid(); a.fill.fore_color.rgb = FAINT; a.line.fill.background()
x = 530
for lab, fg, bg in (("Allowed", OK, OK_SOFT), ("Needs approval", WARN, WARN_SOFT), ("Forbidden", PINK, PINK_SOFT)):
    x += pill(s, x, 700, lab, fg, bg) + 10

# 4 The real catalog
s = new(); frame(s, "The real catalog, under a school's bans.")
x = 80
for share, fill, fg, lab, n in ((11, PINK, PINK, "Forbidden", "1,962"), (46, c("#f0b65c"), WARN, "To a person", "8,149"), (42, c("#6fd3b0"), OK, "Clear", "7,435")):
    w = 1440 * share / 99 - 6
    rect(s, x, 300, w, 90, fill=fill, radius=12)
    text(s, x + 20, 300, w, 90, f"{share}%", 30, WHITE, True, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x, 420, w, 30, lab, 20, fg, True)
    text(s, x, 456, w, 40, n, 30, INK, True)
    x += w + 9
text(s, 80, 620, 1200, 40, "17,546 products. A person reads each unknown label once.", 26, SOFT)

# 5 The app
s = new(); frame(s, "One system, three people.")
for k, (img, name, sub) in enumerate((("head-chef", "Head chef", "approves"), ("kitchen", "Kitchen", "asks"), ("wholesaler", "Wholesaler", "admits agents"))):
    x = 80 + k * 490
    rect(s, x, 250, 460, 275, fill=WHITE, line=LINE)
    pic(s, os.path.join(HERE, "..", "shots", img + ".png"), x + 2, 252, 456, 271)
    text(s, x, 550, 460, 34, name, 26, INK, True)
    text(s, x, 590, 460, 30, sub, 20, SOFT)

# 6 What the camera sees
s = new(); frame(s, "The camera reads the shelf.")
rect(s, 80, 200, 560, 620, fill=c("#e9e8e3"), radius=16)
pic(s, os.path.join(IMG, "visible_items.png"), 100, 214, 520, 592)
text(s, 720, 300, 800, 70, "It names what it sees.", 40, INK, True)
x = 720
for lab in ("An observation", "Not stock", "Never orders"):
    x += pill(s, x, 400, lab, BLUE, BLUE_SOFT) + 12
text(s, 720, 490, 760, 120, "It is a trigger, not a stock count. An uncertain reading never starts an order.", 22, SOFT)

# 6 How the case grew
s = new(); full(s, "s6_iterations"); frame(s, "How the case grew.")

# 7 Inside
s = new(); full(s, "s7_explode"); frame(s, "Fifteen parts. No screw between them.")
vw = 1000; vh = vw * 1200 / 1900
parts = ["Front cover", "Knob cap", "Power button cap", "LED grille", "Touch screen", "Arduino UNO Q", "Modulino Knob",
         "Modulino Buzzer", "Back shell", "Thermo pod cover", "Modulino Thermo", "Thermo pod base", "Distance pod cover",
         "ToF imager VL53L5CX", "Distance pod base"]
for k, n in enumerate(parts):
    y = 200 + k * 40
    text(s, 1160, y, 40, 30, str(k + 1), 16, FAINT, True)
    text(s, 1200, y, 340, 30, n, 16, RED if "pod" in n else INK)

# 8 Demo
s = new(); frame(s, "Demo in five steps.")
steps = [("Admit an agent", "It passes the Driving Test."), ("The agent buys", "A signed quote, live catalog."),
         ("A person approves", "€389 is over the €150 limit."), ("The door", "25 °C against a limit of 6."),
         ("The record", "Valio ends the licence in one click.")]
for k, (h, t) in enumerate(steps):
    x = 80 + k * 292
    rect(s, x, 270, 272, 360, fill=BLUE_SOFT if k == 0 else CARD)
    text(s, x + 28, 300, 80, 70, str(k + 1), 56, BLUE, True)
    text(s, x + 28, 420, 220, 70, h, 24, INK, True)
    text(s, x + 28, 500, 220, 100, t, 18, SOFT)

# 9 Close
s = new()
pic(s, os.path.join(ASSETS, "mise_blue_light.png"), 90, 110, h=90)
text(s, 90, 260, 1100, 200, "Valio stays readable by agents on its own terms.", 56, INK, True)
x = 96
for lab, fg, bg in (("Live Valio API: works", OK, OK_SOFT), ("361 tests pass", OK, OK_SOFT), ("The camera estimates, it does not count", WARN, WARN_SOFT)):
    x += pill(s, x, 520, lab, fg, bg) + 12
logos(s, 96, 720)
text(s, 900, 740, 620, 30, "Team Kindergarten Engineering · Junction 2026", 16, FAINT, align=PP_ALIGN.RIGHT)

p.save(OUT)
print(OUT, N[0], "slides")

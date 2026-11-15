"""The HTML deck as a PowerPoint file for Canva: the same nine slides, editable text, and two looping videos.

Canva imports the .pptx, plays the videos when presenting, and exports a PDF with each video's first frame.
Coordinates are in pixels of the 1600 x 900 HTML stage; 120 px is one inch.

uv run --with python-pptx --with pillow python mise_canva.py
"""
import os
from pptx import Presentation
from pptx.util import Emu, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS, HIMG = os.path.join(HERE, "..", "assets"), os.path.join(HERE, "..", "html", "img")
IMG = os.path.join(HERE, "img"); os.makedirs(IMG, exist_ok=True)
OUT = os.path.join(HERE, "mise - Valio Aimo (Canva).pptx")


def c(h): return RGBColor.from_string(h.lstrip("#"))


BG, RAIL, PANEL, LINE, LINE2 = c("#0a0e19"), c("#0e1324"), c("#11172a"), c("#232b43"), c("#1a2136")
INK, SOFT, FAINT = c("#e9edf7"), c("#a2abc2"), c("#7480a0")
BLUE, BLUE_SOFT, PINK, PINK_SOFT, OK, OK_SOFT, WARN, WARN_SOFT = (c("#7f9fff"), c("#18244a"), c("#ff7fb0"), c("#3a1529"),
                                                                 c("#6fd3b0"), c("#0f3329"), c("#f0b65c"), c("#3a2a0c"))
VIEW, VIEW_INK = c("#f6f7fa"), c("#0c1530")
FONT = "Inter"
PX = 914400 // 120
X0, XW = 308, 1244


def px(v): return Emu(int(v * PX))


p = Presentation(); p.slide_width, p.slide_height = px(1600), px(900)
N = [0]


def png(path):
    """python-pptx reads no WebP or SVG: keep a PNG copy."""
    if path.endswith(".png"): return path
    out = os.path.join(IMG, os.path.splitext(os.path.basename(path))[0] + ".png")
    if not os.path.exists(out): Image.open(path).save(out)
    return out


def new():
    s = p.slides.add_slide(p.slide_layouts[6]); f = s.background.fill; f.solid(); f.fore_color.rgb = BG
    N[0] += 1
    return s


def text(s, x, y, w, h, t, size, color=INK, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, gap=0.35, italic=False):
    b = s.shapes.add_textbox(px(x), px(y), px(w), px(h)); tf = b.text_frame
    tf.word_wrap = True; tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, line in enumerate(t if isinstance(t, list) else [t]):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = align; para.space_after = Pt(size * 0.75 * gap)
        runs = line if isinstance(line, tuple) else ((line, color, bold),)
        for rt, rc, rb in runs:
            r = para.add_run(); r.text = rt
            r.font.size = Pt(size * 0.75); r.font.bold = rb; r.font.italic = italic; r.font.name = FONT; r.font.color.rgb = rc
    return b


def rect(s, x, y, w, h, fill=PANEL, line=LINE, lw=1, radius=12, shape=MSO_SHAPE.ROUNDED_RECTANGLE):
    r = s.shapes.add_shape(shape, px(x), px(y), px(w), px(h))
    if fill is None: r.fill.background()
    else: r.fill.solid(); r.fill.fore_color.rgb = fill
    if line is None: r.line.fill.background()
    else: r.line.color.rgb = line; r.line.width = Pt(lw * 0.75)
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE: r.adjustments[0] = min(0.5, radius / min(w, h))
    r.shadow.inherit = False
    return r


def dot(s, x, y, d, color): rect(s, x - d / 2, y - d / 2, d, d, fill=color, line=None, shape=MSO_SHAPE.OVAL)


def hline(s, x, y, w, color=LINE):
    ln = s.shapes.add_connector(1, px(x), px(y), px(x + w), px(y)); ln.line.color.rgb = color; ln.line.width = Pt(0.75)


def pic(s, path, x, y, w=None, h=None):
    path = png(path); iw, ih = Image.open(path).size
    if w and h:
        k = min(w / iw, h / ih); ww, hh = iw * k, ih * k; x += (w - ww) / 2; y += (h - hh) / 2; w, h = ww, hh
    elif w: h = ih * w / iw
    else: w = iw * h / ih
    s.shapes.add_picture(path, px(x), px(y), px(w), px(h))
    return w, h


def chip(s, x, y, label, color=SOFT, kind=None):
    w = 8.6 * len(label) + (44 if kind else 30)
    rect(s, x, y, w, 28, fill=PANEL, line=LINE if not kind else color, radius=14)
    if kind: dot(s, x + 15, y + 14, 7, color)
    text(s, x + (26 if kind else 13), y, w, 28, label, 13, color, True, anchor=MSO_ANCHOR.MIDDLE)
    return w


def chips_right(s, items, y=46):
    x = X0 + XW
    for label, color, kind in reversed(items):
        w = 8.6 * len(label) + (44 if kind else 30); x -= w + 8
        chip(s, x, y, label, color, kind)


SECTIONS = [("kitchen", "The kitchen", "why it matters"), ("gateway", "The gateway", "Valio's side"),
            ("box", "The box", "mise on the shelf"), ("demo", "The demo", "what works today")]


def shell(s, sec, ctx, title, chips=()):
    rect(s, 0, 0, 260, 900, fill=RAIL, line=None, shape=MSO_SHAPE.RECTANGLE)
    ln = s.shapes.add_connector(1, px(260), 0, px(260), px(900)); ln.line.color.rgb = LINE; ln.line.width = Pt(0.75)
    pic(s, os.path.join(ASSETS, "mise_blue.png"), 24, 38, h=42)
    text(s, 22, 122, 200, 20, "Sections", 13, FAINT, True)
    for k, (key, name, sub) in enumerate(SECTIONS):
        y = 150 + k * 68; on = key == sec
        if on: rect(s, 14, y, 232, 60, fill=BLUE_SOFT, line=None, radius=10)
        rect(s, 34, y + 13, 16, 16, fill=None, line=BLUE if on else SOFT, lw=1.5, shape=MSO_SHAPE.OVAL)
        text(s, 66, y + 8, 170, 22, name, 15, BLUE if on else INK, True)
        text(s, 66, y + 32, 170, 20, sub, 12.5, BLUE if on else FAINT)
    text(s, 22, 760, 216, 90, "One system, three people. The head chef uses a phone. The kitchen and the wholesaler use a screen.", 12.5, FAINT, gap=0)
    text(s, X0, 30, 900, 22, ctx, 14, SOFT)
    cw = sum(8.6 * len(l) + (44 if k else 30) + 8 for l, _, k in chips)
    text(s, X0, 52, XW - cw - 20, 44, title, 30 if len(title) < 46 else 27, INK, True)
    chips_right(s, chips)
    hline(s, X0, 116, XW)
    pic(s, os.path.join(ASSETS, "enplace_faint.png"), X0, 846, h=24)
    text(s, X0 + 104, 848, 800, 20, "Everything in its place before service: the stock, the rules and the orders.", 13, FAINT)
    text(s, X0 + XW - 60, 848, 60, 20, f"{N[0]:02d}", 13, FAINT, align=PP_ALIGN.RIGHT)


def panel(s, x, y, w, h, fill=PANEL, line=LINE): return rect(s, x, y, w, h, fill=fill, line=line)


def label(s, x, y, t, color=SOFT, w=400): text(s, x, y, w, 20, t, 13, color, True)


def logos(s, x, y):
    for name, w, extra in (("valio", 92, None), ("aimo", 130, None), ("arduino", 170, "ARDUINO")):
        rect(s, x, y, w, 56, fill=VIEW, line=None, radius=12)
        if extra:
            iw, _ = pic(s, os.path.join(ASSETS, name + ".png"), x + 16, y + 15, h=26)
            text(s, x + 22 + iw, y, w - iw - 22, 56, extra, 14, VIEW_INK, True, anchor=MSO_ANCHOR.MIDDLE)
        else:
            pic(s, os.path.join(ASSETS, name + ".png"), x + 14, y + 10, w - 28, 36)
        x += w + 12


TEAM = "Team Kindergarten Engineering"

# 1 Title
s = new()
chip(s, 90, 70, "Valio Aimo · Trusted Trade Agents")
pic(s, os.path.join(ASSETS, "mise_blue.png"), 84, 150, h=170)
pic(s, os.path.join(ASSETS, "enplace_faint.png"), 340, 340, h=44)
text(s, 90, 420, 560, 150, "A shelf that orders from Valio Aimo by itself, inside the rules the head chef signed.", 30, INK, gap=0)
x = 90
for lab, col, kind in (("Live Valio catalog", OK, True), ("Rule set 4", SOFT, None), ("Junction 2026", SOFT, None)):
    x += chip(s, x, 590, lab, col, kind) + 8
text(s, 90, 800, 500, 24, TEAM, 15, FAINT)
pic(s, os.path.join(HIMG, "hero.webp"), 700, 150, 840, 560)
logos(s, 1124, 780)

# 2 The kitchen
s = new(); shell(s, "kitchen", "The kitchen", "One school kitchen, one shelf, one order too late")
panel(s, X0, 140, 760, 680)
label(s, X0 + 24, 162, "Today")
text(s, X0 + 24, 196, 700, 120, "A buyer logs into the Valio Aimo webshop and searches the catalog by hand.", 30, INK, True)
text(s, X0 + 24, 350, 700, 90, "A school kitchen must also keep the bans its head chef set. In our rule set they are nuts, peanuts and sesame.", 19, INK)
text(s, X0 + 24, 450, 700, 200, "When a product runs short in the middle of the week, a person must notice it, find a substitute that breaks no rule and get it approved. The brief names this case: an allergen-safe substitution under pressure, in a school or hospital kitchen.", 19, INK)
text(s, X0 + 24, 770, 700, 20, "Our kitchen is a test case. No real kitchen data exists yet.", 13, FAINT)
panel(s, X0 + 784, 140, 460, 300)
label(s, X0 + 808, 162, "The number")
text(s, X0 + 808, 192, 420, 70, "20,000+", 56, INK, True)
text(s, X0 + 808, 270, 420, 80, "professional kitchens rely on Valio Aimo. Ordering is still largely manual or menu-driven.", 16, INK)
text(s, X0 + 808, 396, 420, 20, "Source: Valio Aimo challenge brief, Junction 2026", 13, FAINT)
panel(s, X0 + 784, 462, 460, 300)
label(s, X0 + 808, 484, "What Valio asks")
text(s, X0 + 808, 516, 420, 120, "\"This is not about replacing today's e-commerce UI with a chatbot.\"", 22, INK)
text(s, X0 + 808, 680, 420, 60, "Valio Aimo challenge brief. The question is how a wholesaler can be safely and openly readable by agents.", 13, FAINT)

# 3 How it works
s = new(); shell(s, "gateway", "The Aimo Agent Gateway", "A language model searches and explains. Plain code decides.", [("Blue is our code", SOFT, None)])
pic(s, os.path.join(HERE, "flow.png"), X0, 150, XW, 680)

# 4 The real catalog
s = new(); shell(s, "gateway", "The data", "The real catalog under a school's bans", [("17,546 products", SOFT, None), ("Nuts, peanuts, sesame", SOFT, None)])
x = X0
for share, col, lab in ((11, PINK, "11%"), (46, WARN, "46%"), (42, OK, "42%")):
    w = (XW - 6) * share / 99
    r = rect(s, x, 140, w, 64, fill=col, line=None, radius=8)
    text(s, x + 16, 140, w - 20, 64, lab, 18, BG, True, anchor=MSO_ANCHOR.MIDDLE)
    x += w + 3
panel(s, X0, 230, 780, 590)
rows = [("Forbidden", PINK, "1,962", "The allergen codes say the product contains or may contain a banned allergen."),
        ("To a person", WARN, "8,149", "8,110 have no allergen data, and about half of them are not food. \"Unknown\" is not \"none\"."),
        ("Clear", OK, "7,435", "The data lists allergens or free-from codes, and none is banned.")]
for k, (t, col, n, d) in enumerate(rows):
    y = 250 + k * 190
    rect(s, X0 + 24, y + 40, 10, 10, fill=col, line=None, radius=3)
    text(s, X0 + 44, y + 32, 160, 26, t, 16, col, True)
    text(s, X0 + 210, y + 24, 150, 44, n, 30, INK, True)
    text(s, X0 + 360, y + 30, 400, 120, d, 15, SOFT)
    if k < 2: hline(s, X0 + 24, y + 175, 732, LINE2)
panel(s, X0 + 804, 230, 440, 170, fill=PINK_SOFT, line=PINK)
label(s, X0 + 828, 250, "Trap in the data", PINK)
text(s, X0 + 828, 280, 390, 110, "69 products name a nut kind, for example almond, without the general nut code.", 16, INK)
panel(s, X0 + 804, 420, 440, 400)
label(s, X0 + 828, 440, "For Valio")
text(s, X0 + 828, 470, 390, 240, "Of the 8,110 products with no allergen data, 3,974 are not food: equipment, cleaning goods, disposables. The other 4,136 are food, 24% of the catalog. Each one waits for a person to read its label once.", 16, INK)
text(s, X0 + 828, 730, 390, 70, "The file is synthetic. How many of these a real kitchen orders in a week is a question for the Valio mentors.", 13, FAINT)

# 5 The app
s = new(); shell(s, "gateway", "The app", "One system, three people", [("Live Valio catalog", OK, True)])
rect(s, X0, 140, 900, 497, fill=c("#000000"), line=LINE)
pic(s, os.path.join(HIMG, "app-head-chef.webp"), X0 + 1, 141, 898, 495)
roles = [("Head chef", "approves orders, on a phone", "The card says who asked, what is on the shelf, how fast the kitchen uses it and when the shelf runs out. The PIN approves.", True),
         ("Kitchen", "asks for products", "Each shelf slot shows its count, its use per day and what is on order. A person can type a count, or ask the agent in plain words.", False),
         ("Wholesaler", "admits agents", "An outside agent takes the Driving Test. The head chef grants its licence with a PIN, and an approver can end it in one click.", False)]
for k, (n, sub, d, on) in enumerate(roles):
    y = 140 + k * 228
    panel(s, X0 + 924, y, 320, 212, fill=BLUE_SOFT if on else PANEL, line=None if on else LINE)
    rect(s, X0 + 944, y + 22, 14, 14, fill=BLUE if on else None, line=BLUE if on else SOFT, lw=1.5, shape=MSO_SHAPE.OVAL)
    text(s, X0 + 970, y + 16, 260, 24, n, 17, BLUE if on else INK, True)
    text(s, X0 + 970, y + 42, 260, 20, sub, 13.5, BLUE if on else FAINT)
    text(s, X0 + 944, y + 72, 284, 130, d, 13.5, INK if on else SOFT)
for k, (img, cap) in enumerate((("app-kitchen.webp", "Kitchen"), ("app-wholesaler.webp", "Wholesaler"))):
    x = X0 + k * 290
    rect(s, x, 660, 272, 163, fill=VIEW, line=LINE, radius=8)
    pic(s, os.path.join(HIMG, img), x + 1, 661, 270, 161)
text(s, X0 + 590, 670, 310, 140, "Top: the head chef on the live catalog. Below: the kitchen and the wholesaler, on the stand-in catalog with made-up shelf counts.", 13, FAINT)

# 6 Iterations
s = new(); shell(s, "box", "The box", "How the case grew in one night", [("9 versions", SOFT, None)])
rect(s, X0, 140, 900, 507, fill=VIEW, line=LINE)
s.shapes.add_movie(os.path.join(HERE, "iterations.mp4"), px(X0 + 1), px(141), px(898), px(505),
                   poster_frame_image=os.path.join(HERE, "iterations_poster.png"), mime_type="video/mp4")
VERS = ["First fit check", "Labels and vents", "The name, a distance pod", "A knob cap", "First render, with screws", "Snap-fit lid, en place", "Knob cap, rendered", "A touch screen on a stand", "One wall unit"]
cw = 900 / len(VERS)
hline(s, X0 + cw / 2, 680, 900 - cw, BLUE)
for k, v in enumerate(VERS):
    cx = X0 + cw * k + cw / 2
    dot(s, cx, 680, 16, BLUE)
    if k == len(VERS) - 1: rect(s, cx - 8, 672, 16, 16, fill=BG, line=BLUE, lw=2, shape=MSO_SHAPE.OVAL)
    text(s, cx - cw / 2, 696, cw, 18, f"v{k + 1}", 13, BLUE if k == len(VERS) - 1 else SOFT, True, PP_ALIGN.CENTER)
    text(s, cx - cw / 2 + 4, 716, cw - 8, 40, v, 12, INK if k == len(VERS) - 1 else FAINT, align=PP_ALIGN.CENTER, gap=0)
panel(s, X0 + 924, 140, 320, 680)
label(s, X0 + 944, 158, "What the prints taught us")
text(s, X0 + 944, 182, 285, 40, "CAD screenshots first, then Blender renders. The wall unit is the newest model and still in work.", 12.5, FAINT, gap=0)
learn = [("Labels", "Strokes of 0.4 mm did not print. They are now 0.8 to 1.2 mm wide."),
         ("Status lights", "Four small holes left walls too thin to print. One 1.8 mm slot replaced them."),
         ("Screws", "Gone. The boards sit on pins, rods in the lid hold them, and the lids snap on."),
         ("Distance pod", "The window widens at 34°, so its edge stays out of the sensor's 63° view."),
         ("One wall unit", "The screen moved into the face of one housing that hangs on the wall. Hooks hold the screen, and the covers snap on.")]
for k, (h, d) in enumerate(learn):
    y = 262 + k * 110
    text(s, X0 + 944, y, 285, 20, h, 14.5, INK, True)
    text(s, X0 + 944, y + 24, 285, 76, d, 13, SOFT, gap=0)
    if k < 4: hline(s, X0 + 944, y + 100, 280, LINE2)

# 7 Inside
s = new(); shell(s, "box", "Inside", "Fifteen parts, no screw between them", [("Covers snap on", SOFT, None)])
vw, vh = 930, 930 * 1200 / 1900
rect(s, X0, 140, vw, vh + 2, fill=PANEL, line=LINE)
s.shapes.add_movie(os.path.join(HERE, "explode.mp4"), px(X0 + 1), px(141), px(vw - 2), px(vh),
                   poster_frame_image=os.path.join(HERE, "explode_poster.png"), mime_type="video/mp4")
ORDER = [("Electronics", [(5, "Guition JC4827W543C touch screen"), (6, "Arduino UNO Q"), (7, "Modulino Knob"), (8, "Modulino Buzzer"), (11, "Modulino Thermo"), (14, "SparkFun ToF Imager VL53L5CX")]),
         ("Printed", [(1, "Front cover, snaps on"), (2, "Knob cap"), (3, "Power button cap"), (4, "LED grille insert"), (9, "Back shell"), (10, "Thermo pod cover"), (12, "Thermo pod base"), (13, "Distance pod cover"), (15, "Distance pod base")])]
y = 140; x = X0 + vw + 24
for head, items in ORDER:
    text(s, x, y, 280, 18, head.upper(), 12, FAINT, True); y += 24
    for n, name in items:
        text(s, x, y, 30, 20, str(n), 13.5, FAINT, True); text(s, x + 30, y, 260, 20, name, 13.5, INK); y += 23
    y += 10
panel(s, x, 668, 290, 152)
label(s, x + 16, 680, "The first print")
text(s, x + 16, 706, 260, 70, "Prusa CORE One, PLA: 72 g and 2 h 31 min, before the screen. It still used screws.", 13, INK, gap=0)
text(s, x + 16, 786, 260, 20, "0.2 mm layers, 3 walls, 20% infill, no supports.", 11.5, FAINT)

# 8 Demo
s = new(); shell(s, "demo", "Live", "Demo in five steps", [("Live Valio catalog", OK, True), ("3 minutes", SOFT, None)])
steps = [("Admit an agent", "An outside agent takes the Driving Test and passes 6 of 6. One scenario hides an order ten times too big in a product text."),
         ("The agent buys", "It searches the live catalog, compares four chicken products by price per kilogram and picks one. The quote is signed."),
         ("A person approves", "€389 is over the €150 limit, so the head chef approves on the phone with the PIN."),
         ("The door", "The touch screen shows the order with the air temperature: 25 °C against a limit of 6. A reject keeps the temperature in the record."),
         ("The record and the stop", "Every step has a time, a name and a rule set version. Valio ends the agent's licence in one click.")]
for k, (h, d) in enumerate(steps):
    y = 140 + k * 136
    panel(s, X0, y, XW, 122, fill=BLUE_SOFT if k == 0 else PANEL, line=BLUE if k == 0 else LINE)
    o = rect(s, X0 + 26, y + 39, 44, 44, fill=BLUE if k == 0 else None, line=BLUE if k == 0 else LINE, lw=1.5, shape=MSO_SHAPE.OVAL)
    text(s, X0 + 26, y + 39, 44, 44, str(k + 1), 18, BG if k == 0 else SOFT, True, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
    text(s, X0 + 96, y + 26, 1100, 30, h, 21, INK, True)
    text(s, X0 + 96, y + 60, 1100, 50, d, 17, SOFT)

# 9 Close
s = new(); shell(s, "demo", "Where it stands", "Valio stays readable by agents on its own terms", [("361 tests pass", OK, True)])
text(s, X0, 150, 640, 400, "Every agent has a mandate. Every answer has a signature. Every decision is in a log both sides can read.", 44, INK, True, gap=0)
logos(s, X0, 580)
panel(s, X0 + 680, 140, 564, 680)
status = [("The gateway on the live Valio API", "A signed quote, an order and an approval, 14 November", True),
          ("Rule check, approvals, audit log", "361 tests pass, 2 expected failures", True),
          ("The Agent Driving Test", "Six scenarios we wrote. No agent of another company took it yet", False),
          ("The shelf camera", "Estimates \"full\" or \"low\". It does not count", False),
          ("Orders to Valio", "Recorded by the gateway. The challenge has no order endpoint", False)]
for k, (h, d, ok) in enumerate(status):
    y = 160 + k * 130
    text(s, X0 + 704, y + 20, 400, 24, h, 16, INK, True)
    text(s, X0 + 704, y + 48, 400, 50, d, 14, SOFT)
    chip(s, X0 + 1125, y + 30, "Works" if ok else "Limit", OK if ok else PINK, True)
    if k < 4: hline(s, X0 + 704, y + 118, 516, LINE2)

p.save(OUT)
print(OUT, N[0], "slides")

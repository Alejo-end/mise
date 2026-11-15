"""The Valio deck in the style of the mise app: dark navy, hairline cards, one blue, one pink, a side rail.

Run from this folder:  uv run --with python-pptx --with pillow python mise_deck.py
The colours are the dark theme of valio-challenge/web/approve.html.
"""
import os
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.dml import MSO_LINE
from PIL import Image

S = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(S, "mise - Valio Aimo.pptx")


def rgb(h): return RGBColor.from_string(h.lstrip("#"))


BG, RAIL, PANEL, LINE, LINE2 = rgb("#0a0e19"), rgb("#0e1324"), rgb("#11172a"), rgb("#232b43"), rgb("#1a2136")
INK, SOFT, FAINT = rgb("#e9edf7"), rgb("#a2abc2"), rgb("#7480a0")
BLUE, BLUE_SOFT, PINK, PINK_SOFT, OK = rgb("#7f9fff"), rgb("#18244a"), rgb("#ff7fb0"), rgb("#3a1529"), rgb("#6fd3b0")
VIEW = rgb("#f6f7fa")  # light ground for renders and screenshots, like a screen inside the page
FONT = "Inter"
W, H = Inches(13.333), Inches(7.5)
I = Inches
X0, XW = 2.75, 10.0   # content column right of the rail

p = Presentation(); p.slide_width, p.slide_height = W, H
N = [0]
SECTIONS = [("kitchen", "The kitchen", "why it matters"), ("gateway", "The gateway", "Valio's side"),
            ("box", "The box", "mise on the shelf"), ("demo", "The demo", "what works today")]


def a(name): return os.path.join(S, "assets", name + ".png")


def new():
    s = p.slides.add_slide(p.slide_layouts[6])
    f = s.background.fill; f.solid(); f.fore_color.rgb = BG
    N[0] += 1
    return s


def text(s, x, y, w, h, t, size, color=INK, bold=False, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=0.35, italic=False):
    box = s.shapes.add_textbox(I(x), I(y), I(w), I(h)); tf = box.text_frame
    tf.word_wrap = True; tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    for i, line in enumerate(t if isinstance(t, list) else [t]):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = align; para.space_after = Pt(size * spacing)
        r = para.add_run(); r.text = line
        r.font.size = Pt(size); r.font.bold = bold; r.font.italic = italic; r.font.name = FONT; r.font.color.rgb = color
    return box


def rect(s, x, y, w, h, fill=PANEL, line=LINE, lw=0.75, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.035, dash=None):
    r = s.shapes.add_shape(shape, I(x), I(y), I(w), I(h))
    if fill is None: r.fill.background()
    else: r.fill.solid(); r.fill.fore_color.rgb = fill
    if line is None: r.line.fill.background()
    else:
        r.line.color.rgb = line; r.line.width = Pt(lw)
        if dash: r.line.dash_style = dash
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE: r.adjustments[0] = radius
    r.shadow.inherit = False
    return r


def card(s, x, y, w, h, fill=PANEL): return rect(s, x, y, w, h, fill=fill)


def dot(s, x, y, d, color): rect(s, x - d / 2, y - d / 2, d, d, fill=color, line=None, shape=MSO_SHAPE.OVAL)


def hline(s, x, y, w, color=LINE):
    ln = s.shapes.add_connector(1, I(x), I(y), I(x + w), I(y)); ln.line.color.rgb = color; ln.line.width = Pt(0.75)


def picture(s, path, x, y, maxw, maxh, align="center"):
    iw, ih = Image.open(path).size
    sc = min(maxw / iw, maxh / ih); w, h = iw * sc, ih * sc
    px = x + (maxw - w) / 2 if align == "center" else x
    s.shapes.add_picture(path, I(px), I(y + (maxh - h) / 2), I(w), I(h))
    return w, h


def viewer(s, path, x, y, w, h, pad=0.12):
    """A light screen inside the dark page, for renders, diagrams and screenshots."""
    rect(s, x, y, w, h, fill=VIEW, line=LINE)
    picture(s, path, x + pad, y + pad, w - 2 * pad, h - 2 * pad)


def chip(s, x, y, label, color=SOFT, live=False, w=None):
    w = w or (0.22 * live + 0.075 * len(label) + 0.3)
    r = rect(s, x, y, w, 0.3, fill=PANEL, line=LINE, radius=0.5)
    if live: dot(s, x + 0.17, y + 0.15, 0.07, color)
    text(s, x + (0.28 if live else 0.15), y, w, 0.3, label, 10.5, color, True, anchor=MSO_ANCHOR.MIDDLE)
    return w


def placeholder(s, x, y, w, h, prompt, size=13):
    r = rect(s, x, y, w, h, fill=None, line=BLUE, lw=1, dash=MSO_LINE.DASH)
    tf = r.text_frame; tf.word_wrap = True; tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = tf.margin_right = I(0.18)
    para = tf.paragraphs[0]; para.alignment = PP_ALIGN.LEFT
    run = para.add_run(); run.text = "[" + prompt + "]"
    run.font.size = Pt(size); run.font.name = FONT; run.font.color.rgb = BLUE; run.font.italic = True
    return r


def shell(s, section, title, meta=None, chips=()):
    """The app shell: a rail with the wordmark and the sections, a top bar, a footer."""
    rect(s, 0, 0, 2.35, 7.5, fill=RAIL, line=None, shape=MSO_SHAPE.RECTANGLE)
    ln = s.shapes.add_connector(1, I(2.35), 0, I(2.35), I(7.5)); ln.line.color.rgb = LINE; ln.line.width = Pt(0.75)
    s.shapes.add_picture(a("mise_blue"), I(0.35), I(0.38), height=I(0.42))
    text(s, 0.35, 1.25, 1.8, 0.3, "Sections", 10.5, FAINT, True)
    for k, (key, name, sub) in enumerate(SECTIONS):
        y = 1.6 + k * 0.68
        on = key == section
        if on: rect(s, 0.2, y, 1.98, 0.6, fill=BLUE_SOFT, line=None, radius=0.12)
        rect(s, 0.36, y + 0.17, 0.2, 0.2, fill=None, line=BLUE if on else SOFT, lw=1.25, shape=MSO_SHAPE.OVAL)
        text(s, 0.7, y + 0.07, 1.45, 0.3, name, 12, BLUE if on else INK, True)
        text(s, 0.7, y + 0.32, 1.45, 0.25, sub, 9.5, BLUE if on else FAINT)
    text(s, 0.35, 6.45, 1.8, 0.8, "One system, three people: the head chef, the kitchen, the wholesaler.", 9, FAINT, spacing=0.1)
    # top bar
    if meta: text(s, X0, 0.32, XW, 0.3, meta, 10.5, SOFT)
    text(s, X0, 0.62, 7, 0.55, title, 24, INK, True, anchor=MSO_ANCHOR.MIDDLE, spacing=0.05)
    cx = X0 + XW
    for label, color, live in reversed(chips):
        w = 0.22 * live + 0.075 * len(label) + 0.3
        cx -= w + 0.1
        chip(s, cx, 0.75, label, color, live, w)
    hline(s, X0, 1.3, XW)
    # footer
    s.shapes.add_picture(a("enplace_faint"), I(X0), I(6.95), height=I(0.26))
    text(s, X0 + 1.0, 6.98, 7.5, 0.3, "Everything in its place before service: the stock, the rules and the orders.", 10, FAINT)
    text(s, X0 + XW - 0.6, 6.98, 0.6, 0.3, f"{N[0]:02d}", 10, FAINT, True, PP_ALIGN.RIGHT)


def h2(s, x, y, t, w=4): text(s, x, y, w, 0.3, t, 11, SOFT, True)


def logo_row(s, x, y, h=0.5):
    """Valio, Valio Aimo and Arduino on light chips, so the dark logos read on the navy page."""
    for name, k, wid in (("valio", 0.95, 1.05), ("aimo", 1.0, 1.85), ("arduino", 0.8, 2.1)):
        rect(s, x, y, wid, h + 0.24, fill=VIEW, line=None, radius=0.15)
        lw, _ = picture(s, a(name), x + 0.15, y + 0.12 + h * (1 - k) / 2, wid - 0.3, h * k, align="left")
        if name == "arduino": text(s, x + 0.2 + lw, y, wid - lw - 0.2, h + 0.24, "ARDUINO", 13, rgb("#0c1530"), True, anchor=MSO_ANCHOR.MIDDLE)
        x += wid + 0.2


TEAM = "Team Kindergarten Engineering · Junction 2026"

# 1 Title
s = new()
text(s, 0.9, 0.8, 9, 0.35, "Valio Aimo · Trusted Trade Agents", 13, SOFT, True)
s.shapes.add_picture(a("mise_blue"), I(0.9), I(1.35), height=I(1.55))
s.shapes.add_picture(a("enplace_faint"), I(3.55), I(2.75), height=I(0.55))
text(s, 0.9, 3.75, 5.3, 1.2, "A shelf that orders from Valio Aimo by itself, inside the rules the head chef signed.", 20, INK)
chip(s, 0.9, 5.05, "Live Valio catalog", OK, True)
chip(s, 2.75, 5.05, "Rule set 4", SOFT)
text(s, 0.9, 6.8, 6, 0.35, TEAM, 11, FAINT)
picture(s, a("assembled_dark"), 6.3, 1.2, 6.7, 4.6)
logo_row(s, 7.75, 6.5, 0.42)

# 2 The problem (to write)
s = new(); shell(s, "kitchen", "One school kitchen, one shelf, one order too late", "The kitchen")
placeholder(s, X0, 1.6, 6.1, 5.0, "Your text: the story of the kitchen. Who runs out of what, and what it costs", 15)
for k, (lab, prompt) in enumerate((("The number", "A number that hurts, with its source"), ("The person", "A quote from a chef or a mentor"))):
    y = 1.6 + k * 2.6
    card(s, X0 + 6.35, y, 3.65, 2.4)
    h2(s, X0 + 6.6, y + 0.2, lab)
    placeholder(s, X0 + 6.6, y + 0.65, 3.15, 1.5, prompt, 12)

# 3 Statement
s = new(); shell(s, "gateway", "The idea", "The Aimo Agent Gateway")
text(s, X0, 2.0, XW, 2.4, "A language model can search, compare and explain. Plain code decides what is allowed.", 38, INK, True, spacing=0.1)
text(s, X0, 4.6, 9, 1.2, "The Aimo Agent Gateway is the interface on Valio's side, and it is our entry. mise, the shelf box, is one client of it.", 17, SOFT)

# 4 The brief
s = new(); shell(s, "gateway", "The three core requirements, and what our gateway gives", "The brief")
cards = [("Product, price and availability", "Search over the product file. Every answer keeps its source and time. Each quote and order answer is signed."),
         ("Price and stock, then an order", "Fixed prices. Price and stock again just before the order. One key per run, so a repeated call does not order twice."),
         ("Permission and approval", "A rule set the head chef signs once. Each decision is allowed, needs approval or forbidden, with a reason."),
         ("Optional: substitution", "If the usual product is short, the pipeline ranks up to three safe options."),
         ("Optional: traceability", "An append-only audit log. A story for the chef, the same entries as data for Valio."),
         ("Openness", "One OpenAPI file describes the calls: docs/valio-api/gateway-openapi.json")]
cw = (XW - 0.4) / 3
for k, (hd, b) in enumerate(cards):
    x, y = X0 + (k % 3) * (cw + 0.2), 1.6 + (k // 3) * 2.6
    card(s, x, y, cw, 2.4)
    text(s, x + 0.25, y + 0.22, 2, 0.25, "Core" if k < 3 else "Extra", 10, BLUE if k < 3 else FAINT, True)
    text(s, x + 0.25, y + 0.5, cw - 0.5, 0.5, hd, 14, INK, True)
    text(s, x + 0.25, y + 1.0, cw - 0.5, 1.3, b, 11.5, SOFT)

# 5-6 Diagrams
for t, img in (("Who talks to whom", "valio-system"), ("How one decision is made", "valio-decision")):
    s = new(); shell(s, "gateway", t, "The Aimo Agent Gateway")
    viewer(s, os.path.join(S, "png", img + ".png"), X0, 1.6, XW, 5.1)

# 7 One order, step by step
s = new(); shell(s, "gateway", "One order, step by step", "The pipeline")
steps = ["A trigger arrives: the shelf camera, a knob press or a text request.",
         "The pipeline searches the catalog and asks price and stock for each candidate.",
         "The rule check drops every forbidden product.",
         "If stock is short, it ranks up to three substitutes.",
         "Allowed: the agent orders. Not allowed: the chef approves on the phone with a PIN.",
         "Every step goes into the audit log, with the rule set version."]
card(s, X0, 1.6, XW, 5.1)
for k, t in enumerate(steps):
    y = 1.8 + k * 0.82
    text(s, X0 + 0.3, y, 0.8, 0.5, f"{k + 1:02d}", 13, FAINT, anchor=MSO_ANCHOR.MIDDLE)
    text(s, X0 + 1.0, y, XW - 1.4, 0.5, t, 15, INK, anchor=MSO_ANCHOR.MIDDLE)
    if k < 5: hline(s, X0 + 0.3, y + 0.66, XW - 0.6, LINE2)

# 8 Real catalog
s = new(); shell(s, "gateway", "The real catalog, under a school kitchen's allergen rules", "The data", [("17,546 products", SOFT, False)])
viewer(s, os.path.join(S, "png", "valio-allergen-data.png"), X0, 1.6, 6.6, 5.1)
card(s, X0 + 6.85, 1.6, 3.15, 2.6, fill=PINK_SOFT)
rect(s, X0 + 6.85, 1.6, 0.06, 2.6, fill=PINK, line=None, shape=MSO_SHAPE.RECTANGLE)
text(s, X0 + 7.1, 1.8, 2.75, 0.4, "46% have no allergen data", 13, PINK, True)
text(s, X0 + 7.1, 2.3, 2.75, 1.8, "Half of those are not food. \"Unknown\" goes to a person. The chef reads the label once for each product.", 11.5, INK)
placeholder(s, X0 + 6.85, 4.4, 3.15, 2.3, "Your takeaway for Valio", 12)

# 9 The app (screenshots)
s = new(); shell(s, "gateway", "One system, three people", "The app", [("Live Valio catalog", OK, True)])
for k, (img, name, sub) in enumerate((("head-chef", "Head chef", "approves orders"), ("kitchen", "Kitchen", "asks for products"), ("wholesaler", "Wholesaler", "admits agents"))):
    x = X0 + k * 3.4
    viewer(s, os.path.join(S, "shots", img + ".png"), x, 1.6, 3.2, 2.1, pad=0.06)
    text(s, x, 3.85, 3.2, 0.3, name, 13, INK, True)
    text(s, x, 4.15, 3.2, 0.3, sub, 10.5, FAINT)
    placeholder(s, x, 4.6, 3.2, 2.1, f"What the {name.lower()} sees and does", 11)

# 10 Section: the box
s = new()
text(s, 0.9, 0.8, 6, 0.35, "Part two · The box", 13, SOFT, True)
s.shapes.add_picture(a("mise_blue"), I(0.9), I(1.4), height=I(1.3))
text(s, 0.9, 3.15, 5.2, 1.6, "One client of the gateway: a box on the shelf that sees, counts and asks.", 22, INK, True, spacing=0.1)
placeholder(s, 0.9, 5.0, 5.2, 1.3, "Your text: why a physical box, and not an app", 13)
picture(s, a("assembled_dark"), 6.2, 1.2, 6.8, 5.2)

# 11 How the case grew: the animation
s = new(); shell(s, "box", "How the case grew in one night", "Iterations", [("7 versions", SOFT, False)])
rect(s, X0, 1.6, 6.9, 3.88 + 0.16, fill=VIEW, line=LINE)
s.shapes.add_movie(os.path.join(S, "mise_case_iterations.mp4"), I(X0 + 0.08), I(1.68), I(6.74), I(3.88),
                   poster_frame_image=os.path.join(S, "frames", "iter_7.png"), mime_type="video/mp4")
card(s, X0 + 7.15, 1.6, 2.85, 5.1)
h2(s, X0 + 7.4, 1.8, "From CAD to render")
for k, lab in enumerate(["first fit check", "labels and vents", "the name, a distance pod", "a knob cap", "first render, with screws", "snap-fit lid", "final render"]):
    y = 2.25 + k * 0.42
    text(s, X0 + 7.4, y, 0.45, 0.3, f"v{k + 1}", 11, BLUE if k == 6 else FAINT, True)
    text(s, X0 + 7.85, y, 2.1, 0.3, lab, 11, INK if k == 6 else SOFT)
placeholder(s, X0 + 7.35, 5.3, 2.45, 1.2, "What you learned between versions", 11)
placeholder(s, X0, 5.75, 6.9, 0.95, "Your caption for the video", 12)

# 12-18 One slide per iteration, same layout, for a Match and move transition
notes = ["what v1 tested", "what changed in v2", "what changed in v3", "what changed in v4",
         "what changed in v5", "what changed in v6", "why v7 is the one we printed"]
for k in range(7):
    s = new(); shell(s, "box", f"Iteration {k + 1} of 7", "How the case grew")
    rect(s, X0, 1.6, 7.2, 4.05 + 0.16, fill=VIEW, line=LINE)
    s.shapes.add_picture(os.path.join(S, "frames", f"iter_{k + 1}.png"), I(X0 + 0.08), I(1.68), I(7.04), I(3.96))
    placeholder(s, X0 + 7.45, 1.6, 2.55, 4.21, "Your text: " + notes[k], 12)

# 19 Exploded view
s = new(); shell(s, "box", "Twelve parts, no glue, no screws", "Inside", [("Lid snaps on", SOFT, False)])
viewer(s, os.path.join(S, "..", "..", "..", "valio-challenge", "enclosure", "renders", "exploded.png"), X0, 1.6, 6.3, 5.1, pad=0.08)
placeholder(s, X0 + 6.55, 1.6, 3.45, 2.45, "Your text: how it goes together", 12)
placeholder(s, X0 + 6.55, 4.25, 3.45, 2.45, "Your text: print time, material, cost", 12)


def parts_slide(t, items, cols, chips=()):
    s = new(); shell(s, "box", t, "The parts", chips)
    cw = (XW - (cols - 1) * 0.18) / cols
    for k, (img, name, sub) in enumerate(items):
        x = X0 + k * (cw + 0.18); y = 1.6
        card(s, x, y, cw, 5.1)
        picture(s, os.path.join(S, "parts", img + ".png"), x + 0.15, y + 0.2, cw - 0.3, 2.0)
        hline(s, x + 0.2, y + 2.4, cw - 0.4, LINE2)
        text(s, x + 0.2, y + 2.55, cw - 0.4, 0.5, name, 12.5, INK, True, spacing=0.05)
        text(s, x + 0.2, y + 3.05, cw - 0.4, 0.4, sub, 10, FAINT)
        placeholder(s, x + 0.15, y + 3.55, cw - 0.3, 1.35, "What it does", 10)
    return s


# 20 Electronics
parts_slide("The electronics", [
    ("uno_q", "Arduino UNO Q", "Linux and a microcontroller"),
    ("tof", "SparkFun ToF Imager", "VL53L5CX, 8 x 8 distances"),
    ("knob", "Modulino Knob", "the spend limit"),
    ("thermo", "Modulino Thermo", "cold room alarm"),
    ("buzzer", "Modulino Buzzer", "calls for approval")], 5, [("Arduino", BLUE, False), ("SparkFun", SOFT, False)])

# 21 Printed parts
parts_slide("The printed parts", [
    ("lid", "Lid and knob cap", "snaps on, LED window"),
    ("base", "Base", "holds the UNO Q, en place"),
    ("thermo_pod", "Thermo pod", "hangs in the cold room"),
    ("tof_pod", "Distance pod", "looks at the shelf")], 4, [("PLA, no supports", SOFT, False)])

# 22 What the box does
s = new(); shell(s, "box", "What the box does on the shelf", "The box", [("Shelf box online", OK, True)])
rows = [("Sees", "A camera on an Arduino UNO Q watches a kitchen shelf."),
        ("Orders", "When a product runs low, the agent orders it inside the kitchen's rules."),
        ("Listens", "Knob lowers the spend limit. Thermo raises a cold room alarm. Buzzer calls for approval."),
        ("Proves", "Agent Driving Test: an outside agent must pass six scenarios on a copy of the catalog before it gets a licence.")]
card(s, X0, 1.6, 6.0, 5.1)
for k, (hd, b) in enumerate(rows):
    y = 1.8 + k * 1.22
    text(s, X0 + 0.3, y, 1.3, 0.4, hd, 14, BLUE, True)
    text(s, X0 + 1.55, y, 4.2, 1.0, b, 12.5, INK)
    if k < 3: hline(s, X0 + 0.3, y + 1.07, 5.4, LINE2)
viewer(s, os.path.join(S, "frames", "iter_7.png"), X0 + 6.25, 1.6, 3.75, 2.25, pad=0.05)
placeholder(s, X0 + 6.25, 4.05, 3.75, 2.65, "Your text: a photo of the printed box on a real shelf", 12)

# 23 Demo
s = new(); shell(s, "demo", "Demo in six steps", "Live")
demo = ["The Agent Driving Test: the naive agent fails, our agent passes. The chef grants its licence with the PIN.",
        "With the licence, an outside agent asks for a quote: \"1,000 litres of lactose-free milk by Monday\". Change one digit, and the signature fails.",
        "The agent orders the quote. The order is over its limit, so the chef approves on the phone.",
        "The real catalog: 11% forbidden, 46% to a person, 42% clear. The chef reads one label once.",
        "The kitchen as one client: take the milk off the shelf. The camera triggers the agent.",
        "The audit log as a story and as data."]
cw = (XW - 0.4) / 3
for k, t in enumerate(demo):
    x, y = X0 + (k % 3) * (cw + 0.2), 1.6 + (k // 3) * 2.6
    card(s, x, y, cw, 2.4, fill=BLUE_SOFT if k == 4 else PANEL)
    text(s, x + 0.25, y + 0.2, 1, 0.5, f"{k + 1}", 26, BLUE, True)
    text(s, x + 0.25, y + 0.85, cw - 0.5, 1.5, t, 11.5, INK if k == 4 else SOFT)

# 24 What is true today
s = new(); shell(s, "demo", "What is true today", "Status", [("156 tests pass", OK, True)])
state = [("Rule check, pipeline, approvals, audit log", "Work on the stand-in catalog and on the real product file. 156 tests pass", "Works", OK),
         ("Shelf box on the UNO Q", "Built and runs on the board. The camera count is a rough estimate", "Limit", PINK),
         ("Real catalog adapter", "Built and tested on the real product file. Ran on the live API on 14 November", "Works", OK),
         ("Outside agent calls with a licence", "Built, with tests. Quotes and order answers are signed. Licences survive a restart", "Works", OK),
         ("Agent Driving Test", "Works, also over the network. No agent of a real company took it yet", "Limit", PINK)]
card(s, X0, 1.6, XW, 4.85)
for k, (x1, x2, st, col) in enumerate(state):
    y = 1.78 + k * 0.92
    text(s, X0 + 0.3, y, 3.6, 0.6, x1, 13, INK, True, anchor=MSO_ANCHOR.MIDDLE)
    text(s, X0 + 4.0, y, 4.7, 0.6, x2, 11.5, SOFT, anchor=MSO_ANCHOR.MIDDLE)
    chip(s, X0 + XW - 1.15, y + 0.15, st, col, True, 0.9)
    if k < 4: hline(s, X0 + 0.3, y + 0.78, XW - 0.6, LINE2)
text(s, X0, 6.55, XW, 0.3, "The allergen code map is our reading of the GS1 list. We ask the Valio mentors for the official list.", 10, FAINT)

# 25 Close
s = new()
s.shapes.add_picture(a("mise_blue"), I(0.9), I(0.9), height=I(1.15))
text(s, 0.9, 2.5, 11.5, 1.6, "Valio stays readable by agents on its own terms.", 40, INK, True, spacing=0.1)
text(s, 0.9, 4.1, 10.5, 1.2, "Every agent has a mandate. Every answer has a signature the agent can check. Every decision is in an audit trail both sides can read.", 17, SOFT)
logo_row(s, 0.9, 5.85, 0.42)
s.shapes.add_picture(a("enplace_faint"), I(9.6), I(6.65), height=I(0.3))
text(s, 9.0, 7.0, 3.5, 0.3, TEAM, 10, FAINT, align=PP_ALIGN.RIGHT)

p.save(OUT)
print(OUT, N[0], "slides")

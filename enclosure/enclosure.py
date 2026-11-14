"""SK-1, the Safe Kitchen Agent enclosure.

Main case: Arduino UNO Q + Modulino Knob + Modulino Buzzer.
Pod: a small vented box for the Modulino Thermo, on a long Qwiic cable.

Run:  uv run --no-project --with manifold3d --with numpy --with matplotlib \
        python enclosure.py [--preview] [--assembly]

All numbers are millimetres. Board geometry comes from the official Arduino
CAD files (docs.arduino.cc, "CAD files" download of each board):
  UNO Q    ABX00162: pad coordinates from the IPC-D-356 netlist and the NC
           drill file. Part heights come from the datasheet drawing.
  Knob     ABX00107: STEP model.
  Buzzer   ABX00108: STEP model.
  Thermo   ABX00103: STEP model.

Coordinates: x runs left to right (USB-C side is left), y runs front to back,
z is up. The bottom of the base is z = 0.
"""

import struct
import sys
from pathlib import Path

import numpy as np
from manifold3d import CrossSection, FillRule, Manifold, OpType

SEG = 72
HERE = Path(__file__).parent
OUT = HERE / "stl"

# ------------------------------------------------------------ UNO Q data ----
# Frame: x from the USB-C edge, y from the front edge, z from the PCB top.
UNO_X, UNO_Y, UNO_T = 68.58, 53.34, 1.6
UNO_HOLES = [(13.97, 2.54), (15.24, 50.80), (66.04, 7.62), (66.04, 35.56)]  # Ø3.2
UNO_BOTTOM = 5.0                       # JMISC / JMEDIA connectors under the PCB
UNO_HEADER_H = 8.5                     # tallest top part
UNO_USB_Y, UNO_USB_Z = 37.85, 1.6      # USB-C centre (JUSB1 pads)
UNO_USB_FACE = -0.7                    # the port sticks out past the board edge
UNO_BUTTON = (5.84, 49.40)             # JBTN1 centre
UNO_MATRIX = (31.29, 4.51, 58.43, 20.44)  # D27001..D27104 pad extents
UNO_RGB = [(65.02, 42.16), (65.02, 44.07), (65.02, 45.97), (65.02, 47.88)]  # 4 RGB LEDs
UNO_HEADERS = [                        # x0, y0, x1, y1 of each header body
    (17.5, 49.5, 64.8, 52.1),          # JDIGITAL
    (26.6, 1.25, 64.8, 3.85),          # JANALOG
    (62.4, 24.1, 67.5, 31.8),          # JSPI
    (12.7, 41.6, 24.3, 47.3),          # JCTL
]
UNO_BOTTOM_PARTS = [(9.2, 7.43, 14.2, 45.93), (56.1, 7.43, 61.0, 45.93)]  # JMEDIA, JMISC

# ---------------------------------------------------------- Modulino data ----
# Frame: x, y from the lower-left board corner, z from the PCB top.
MOD_X, MOD_Y, MOD_T, MOD_R = 41.0, 25.36, 1.6, 4.5
MOD_HOLES = [(4.5, 4.68), (36.5, 4.68), (4.5, 20.68), (36.5, 20.68)]  # Ø3.2
MOD_C = (20.5, 12.68)                  # encoder, buzzer and sensor centre
MOD_QWIIC = [(0.54, 9.68, 5.49, 15.68), (35.51, 9.68, 40.46, 15.68)]
MOD_QWIIC_H = 2.98
ENC_BODY = (11.68, 5.17, 29.32, 20.19, 4.5)   # x0, y0, x1, y1, height
ENC_BUSH_D, ENC_BUSH_TOP = 7.0, 11.5          # smooth bushing around the shaft
ENC_SHAFT_D, ENC_SHAFT_TOP = 6.0, 19.52
ENC_FLAT_FROM, ENC_FLAT_OFFSET = 14.5, 1.5    # D-flat: start height, plane offset from centre
BUZ_BODY = (14.5, 6.68, 26.5, 18.68, 3.03)

# ------------------------------------------------------------- case setup ----
OX, OY = 144.0, 68.0                   # outside footprint
WALL, FLOOR, LID_T = 2.0, 2.0, 2.5
CORNER_R, CHAMFER = 3.0, 0.6
STANDOFF_UNO = 6.0                     # > UNO_BOTTOM
GAP_QWIIC = 15.0                       # between UNO Q and Modulinos, for the plugs
SCREW_INSET = 3.8                      # lid screws, from both edges of each corner
POST_D = 6.0
PILOT_D = 2.5                          # M3 self-tapping pilot
SCREW_CLR_D, SCREW_HEAD_D, SCREW_HEAD_H = 3.4, 6.2, 1.2
STANDOFF_D = 5.0
LIP_W, LIP_H, LIP_CLR = 1.2, 2.0, 0.25
DOT_D, DOT_PITCH = 1.5, 2.5            # one dot grid for every grille and vent
ENGRAVE = 0.6
LABEL_XH = 2.4                         # x-height of the small labels

Z_IN = FLOOR
UNO_O = (WALL + 1.0, (OY - UNO_Y) / 2, Z_IN + STANDOFF_UNO)       # PCB lower-left-bottom
UNO_TOP = UNO_O[2] + UNO_T
Z_LID = UNO_TOP + UNO_HEADER_H + 0.6   # lid underside
Z_TOP = Z_LID + LID_T

WIN = (UNO_O[0] + UNO_MATRIX[0] - 1.0, UNO_O[1] + UNO_MATRIX[1] - 1.0,
       UNO_O[0] + UNO_MATRIX[2] + 1.0, UNO_O[1] + UNO_MATRIX[3] + 1.0)
WIN_CX = (WIN[0] + WIN[2]) / 2
VENT_NX = int((WIN[2] - WIN[0] - DOT_D) // DOT_PITCH) + 1          # vent as wide as the window
ROW_FRONT = (WIN[1] + WIN[3]) / 2      # the window and the knob share this line
ROW_BACK = OY - ROW_FRONT              # the vent and the speaker share this line

MOD_X0 = UNO_O[0] + UNO_X + GAP_QWIIC
KNOB_TOP = Z_TOP - 0.2 - ENC_BUSH_TOP                 # Knob PCB top: bushing ends flush
BUZZ_TOP = Z_LID - BUZ_BODY[4] - 0.6                  # Buzzer PCB top: buzzer just under the lid
KNOB_O = (MOD_X0, ROW_FRONT - MOD_C[1], KNOB_TOP - MOD_T)
BUZZ_O = (MOD_X0, ROW_BACK - MOD_C[1], BUZZ_TOP - MOD_T)
KNOB_XY = (KNOB_O[0] + MOD_C[0], ROW_FRONT)
BUZZ_XY = (BUZZ_O[0] + MOD_C[0], ROW_BACK)

POSTS = [(x, y) for x in (SCREW_INSET, OX - SCREW_INSET) for y in (SCREW_INSET, OY - SCREW_INSET)]
CABLE_NOTCH = (UNO_O[0] + UNO_X + 4.5, UNO_O[0] + UNO_X + 11.5, 3.0)  # x0, x1, depth

# Thermo pod.
P_WALL, P_FLOOR, P_CLR, P_STAND, P_COVER = 1.6, 1.6, 0.4, 2.4, 2.0
P_OX, P_OY = MOD_X + 2 * (P_CLR + P_WALL), MOD_Y + 2 * (P_CLR + P_WALL)
P_O = (P_WALL + P_CLR, P_WALL + P_CLR, P_FLOOR + P_STAND)
P_PCB_TOP = P_O[2] + MOD_T
P_H = P_PCB_TOP + MOD_QWIIC_H + 0.6
P_R = MOD_R + P_CLR + P_WALL


# --------------------------------------------------------------- helpers ----
def box(x0, y0, z0, x1, y1, z1):
    return Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


def cyl(x, y, z0, z1, d, seg=SEG):
    return Manifold.cylinder(z1 - z0, d / 2, circular_segments=seg).translate((x, y, z0))


def union(parts):
    return Manifold.batch_boolean(parts, OpType.Add) if parts else Manifold()


def rbox(x0, y0, z0, x1, y1, z1, r):
    """Box with rounded vertical edges."""
    pts = [(x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)]
    return Manifold.batch_hull([cyl(x, y, z0, z1, 2 * r) for x, y in pts])


def cbox(x0, y0, z0, x1, y1, z1, r, bottom=0.0, top=0.0):
    """Rounded box with a chamfer on the bottom and/or top outer edge."""
    e = 0.001
    layers = [rbox(x0, y0, z0 + bottom, x1, y1, z1 - top, r)]
    if bottom:
        layers.append(rbox(x0 + bottom, y0 + bottom, z0, x1 - bottom, y1 - bottom, z0 + e, r - bottom))
    if top:
        layers.append(rbox(x0 + top, y0 + top, z1 - e, x1 - top, y1 - top, z1, r - top))
    return Manifold.batch_hull(layers)


def dots(cx, cy, nx, ny, z0, z1, d=DOT_D, pitch=DOT_PITCH):
    """A grid of round holes centred on (cx, cy)."""
    return union([cyl(cx + (i - (nx - 1) / 2) * pitch, cy + (j - (ny - 1) / 2) * pitch, z0, z1, d, 24)
                  for i in range(nx) for j in range(ny)])


def wall_dot(x, y0, y1, z, d):
    """A round hole through a wall that faces y."""
    return Manifold.cylinder(y1 - y0, d / 2, circular_segments=24).rotate((90, 0, 0)).translate((x, y1, z))


def port_x(x0, x1, y, z, wy, wz, r):
    """Rounded rectangle opening through a wall that faces x."""
    pts = [(y + sy * (wy / 2 - r), z + sz * (wz / 2 - r)) for sy in (-1, 1) for sz in (-1, 1)]
    return Manifold.batch_hull([
        Manifold.cylinder(x1 - x0, r, circular_segments=32).rotate((0, 90, 0)).translate((x0, py, pz))
        for py, pz in pts])


_FONT = None


def text(s, x, y, z0, z1, xh=LABEL_XH, anchor="center"):
    """Extruded lowercase label. xh is the x-height in mm, y is the baseline."""
    global _FONT
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath
    if _FONT is None:
        _FONT = FontProperties(family=["DIN Alternate", "DejaVu Sans"], weight="bold")
    k = xh / TextPath((0, 0), "x", size=10, prop=_FONT).get_extents().height
    polys = [np.asarray(p[:-1]) * k for p in TextPath((0, 0), s, size=10, prop=_FONT).to_polygons()
             if len(p) > 3]
    cs = CrossSection(polys, FillRule.EvenOdd)
    bx0, _, bx1, _ = cs.bounds()
    dx = {"left": -bx0, "center": -(bx0 + bx1) / 2, "right": -bx1}[anchor]
    return cs.extrude(z1 - z0).translate((x + dx, y, z0))


# ------------------------------------------------------------------ base ----
def make_base():
    shell = cbox(0, 0, 0, OX, OY, Z_LID, CORNER_R, bottom=CHAMFER)
    cavity = rbox(WALL, WALL, Z_IN, OX - WALL, OY - WALL, Z_LID + 1, CORNER_R - WALL)
    add, cut = [], []

    for hx, hy in UNO_HOLES:
        x, y = UNO_O[0] + hx, UNO_O[1] + hy
        add.append(cyl(x, y, Z_IN - 0.01, UNO_O[2], STANDOFF_D))
        cut.append(cyl(x, y, Z_IN - 0.8, UNO_O[2] + 0.1, PILOT_D))
    for origin in (KNOB_O, BUZZ_O):
        for hx, hy in MOD_HOLES:
            x, y = origin[0] + hx, origin[1] + hy
            add.append(cyl(x, y, Z_IN - 0.01, origin[2], STANDOFF_D + 1.0))
            cut.append(cyl(x, y, Z_IN - 0.8, origin[2] + 0.1, PILOT_D))
    # Lid screw posts, one in each corner, merged into the walls.
    for x, y in POSTS:
        cx, cy = (0.5 if x < OX / 2 else OX - 0.5), (0.5 if y < OY / 2 else OY - 0.5)
        add.append(cyl(x, y, Z_IN - 0.01, Z_LID, POST_D))
        add.append(box(min(x, cx), min(y, cy), Z_IN - 0.01, max(x, cx), max(y, cy), Z_LID))
        cut.append(cyl(x, y, Z_LID - 11, Z_LID + 0.1, PILOT_D))
    base = ((shell - cavity) + union(add)) ^ shell

    # USB-C port.
    cut.append(port_x(-1, WALL + 1, UNO_O[1] + UNO_USB_Y, UNO_TOP + UNO_USB_Z, 13.0, 7.5, 2.0))
    # Thermo cable notch in the back wall. The lid closes it from above.
    cut.append(box(CABLE_NOTCH[0], OY - WALL - 1, Z_LID - CABLE_NOTCH[2], CABLE_NOTCH[1], OY + 1, Z_LID + 1))
    # One row of vent dots low on the front and back walls, in line with the lid vent.
    for i in range(VENT_NX):
        x = WIN_CX + (i - (VENT_NX - 1) / 2) * DOT_PITCH
        for y0, y1 in ((-1, WALL + 1), (OY - WALL - 1, OY + 1)):
            cut.append(wall_dot(x, y0, y1, Z_IN + 3.0, 1.8))
    # Recesses for four 10 mm rubber feet.
    for x in (10, OX - 10):
        for y in (10, OY - 10):
            cut.append(cyl(x, y, -0.1, 0.8, 10.6))
    return base - union(cut)


# ------------------------------------------------------------------- lid ----
def make_lid():
    lid = cbox(0, 0, Z_LID, OX, OY, Z_TOP, CORNER_R, top=CHAMFER)

    # Locating lip: four straight runs inside the walls, clear of the corner posts.
    lo, c = WALL + LIP_CLR, SCREW_INSET + POST_D / 2 + 0.6
    ring = (box(lo, lo, Z_LID - LIP_H, OX - lo, OY - lo, Z_LID + 0.01)
            - box(lo + LIP_W, lo + LIP_W, Z_LID - LIP_H - 1, OX - lo - LIP_W, OY - lo - LIP_W, Z_LID + 1))
    gaps = [box(x - c, y - c, Z_LID - LIP_H - 1, x + c, y + c, Z_LID)
            for x in (0, OX) for y in (0, OY)]
    gaps.append(box(CABLE_NOTCH[0] - 0.5, OY - WALL - 3, Z_LID - LIP_H - 1, CABLE_NOTCH[1] + 0.5, OY, Z_LID))
    lid = lid + (ring - union(gaps))

    zc0, zc1 = Z_LID - 1, Z_TOP + 1                 # through-cut range
    ze = Z_TOP - ENGRAVE                            # engraving floor
    cut = []
    for x, y in POSTS:
        cut.append(cyl(x, y, zc0, zc1, SCREW_CLR_D))
        cut.append(cyl(x, y, Z_TOP - SCREW_HEAD_H, zc1, SCREW_HEAD_D))

    # Front row: name, LED matrix window, knob.
    cut.append(rbox(WIN[0], WIN[1], zc0, WIN[2], WIN[3], zc1, 0.6))
    cut.append(rbox(WIN[0] - 1.5, WIN[1] - 1.5, Z_TOP - 0.5, WIN[2] + 1.5, WIN[3] + 1.5, zc1, 1.2))
    cut.append(cyl(*KNOB_XY, zc0, zc1, ENC_BUSH_D + 0.7))
    for i, word in enumerate(("safe", "kitchen", "agent")):
        cut.append(text(word, 8.0, ROW_FRONT + 4.0 - i * 5.5, ze, zc1, xh=3.0, anchor="left"))
    cut.append(text("limit", KNOB_XY[0], 4.6, ze, zc1))

    # Back row: power button, vent, status LEDs, speaker.
    cut.append(dots(WIN_CX, ROW_BACK, VENT_NX, 5, zc0, zc1))
    cut.append(dots(*BUZZ_XY, 5, 5, zc0, zc1))
    for lx, ly in UNO_RGB:
        cut.append(cyl(UNO_O[0] + lx, UNO_O[1] + ly, zc0, zc1, 1.2, 24))
    bx, by = UNO_O[0] + UNO_BUTTON[0], UNO_O[1] + UNO_BUTTON[1]
    cut.append(cyl(bx, by, zc0, zc1, 4.5))
    cut.append(text("power", bx + 4.6, by - LABEL_XH / 2, ze, zc1, anchor="left"))
    cut.append(text("alert", BUZZ_XY[0], OY - 4.6 - LABEL_XH * 1.4, ze, zc1))
    return lid - union(cut)


# ------------------------------------------------------------ thermo pod ----
def make_pod_base():
    pod = cbox(0, 0, 0, P_OX, P_OY, P_H, P_R, bottom=0.5) - rbox(
        P_WALL, P_WALL, P_FLOOR, P_OX - P_WALL, P_OY - P_WALL, P_H + 1, P_R - P_WALL)
    cy = P_O[1] + MOD_C[1]
    tab = (rbox(P_OX - 6, cy - 5.5, 0, P_OX + 8, cy + 5.5, P_FLOOR, 5.4)
           - cyl(P_OX + 3.2, cy, -1, P_FLOOR + 1, 4.0))
    add, cut = [tab], []
    for hx, hy in MOD_HOLES:
        add.append(cyl(P_O[0] + hx, P_O[1] + hy, P_FLOOR - 0.01, P_O[2], STANDOFF_D + 0.6))
        cut.append(cyl(P_O[0] + hx, P_O[1] + hy, -1, P_O[2] + 0.1, PILOT_D))
    # Cable slot at the left Qwiic socket, open at the top.
    cut.append(box(-1, cy - 3.6, P_PCB_TOP - 0.2, P_WALL + 1, cy + 3.6, P_H + 1))
    # One row of air dots on each long wall, above the PCB.
    for i in range(7):
        x = P_OX / 2 + (i - 3) * 2 * DOT_PITCH
        for y0, y1 in ((-1, P_WALL + 1), (P_OY - P_WALL - 1, P_OY + 1)):
            cut.append(wall_dot(x, y0, y1, P_PCB_TOP + 1.7, 1.8))
    return (pod + union(add)) - union(cut)


def make_pod_cover():
    z0, z1 = P_H, P_H + P_COVER
    cover = cbox(0, 0, z0, P_OX, P_OY, z1, P_R, top=0.5)
    add, cut = [], []
    for hx, hy in MOD_HOLES:
        x, y = P_O[0] + hx, P_O[1] + hy
        add.append(cyl(x, y, P_PCB_TOP + 0.1, z0 + 0.01, STANDOFF_D + 0.2))   # spacer down to the PCB
        cut.append(cyl(x, y, P_PCB_TOP - 1, z1 + 1, SCREW_CLR_D))
        cut.append(cyl(x, y, z1 - 1.0, z1 + 1, SCREW_HEAD_D))
    cx, cy = P_O[0] + MOD_C[0], P_O[1] + MOD_C[1]
    cut.append(dots(cx, cy, 9, 5, z0 - 1, z1 + 1))
    cut.append(text("temperature", cx, P_OY - 5.9, z1 - ENGRAVE, z1 + 1, xh=2.0))
    return (cover + union(add)) - union(cut)


# ------------------------------------------------------ board stand-ins ----
def modulino(origin, kind):
    ox, oy, oz = origin
    top = oz + MOD_T
    parts = [rbox(ox, oy, oz, ox + MOD_X, oy + MOD_Y, top, MOD_R)]
    parts += [box(ox + a, oy + b, top, ox + c, oy + d, top + MOD_QWIIC_H) for a, b, c, d in MOD_QWIIC]
    cx, cy = ox + MOD_C[0], oy + MOD_C[1]
    if kind == "knob":
        a, b, c, d, h = ENC_BODY
        parts += [box(ox + a, oy + b, top, ox + c, oy + d, top + h),
                  cyl(cx, cy, top, top + ENC_BUSH_TOP, ENC_BUSH_D),
                  cyl(cx, cy, top, top + ENC_SHAFT_TOP, ENC_SHAFT_D)
                  - box(cx - 5, cy + ENC_FLAT_OFFSET, top + ENC_FLAT_FROM, cx + 5, cy + 5, top + 30)]
    elif kind == "buzzer":
        a, b, c, d, h = BUZ_BODY
        parts.append(box(ox + a, oy + b, top, ox + c, oy + d, top + h))
    else:
        parts.append(box(cx - 1.25, cy - 1.5, top, cx + 1.25, cy + 1.5, top + 0.91))
    return union(parts)


def uno_q_connectors():
    """The parts of the UNO Q that the CAD data places: the headers, the parts below the board and the USB-C port."""
    ox, oy, oz = UNO_O
    parts = [box(ox + a, oy + b, UNO_TOP, ox + c, oy + d, UNO_TOP + UNO_HEADER_H) for a, b, c, d in UNO_HEADERS]
    parts += [box(ox + a, oy + b, oz - UNO_BOTTOM, ox + c, oy + d, oz) for a, b, c, d in UNO_BOTTOM_PARTS]
    parts.append(box(ox + UNO_USB_FACE, oy + UNO_USB_Y - 4.5, UNO_TOP, ox + 7.4, oy + UNO_USB_Y + 4.5, UNO_TOP + 3.2))
    return parts


def uno_q():
    """The UNO Q as one solid for the fit: the board with its low parts as a block, and the connectors."""
    ox, oy, oz = UNO_O
    return union([box(ox, oy, oz, ox + UNO_X, oy + UNO_Y, UNO_TOP + 4.0)] + uno_q_connectors())


def modulino_look(origin, kind):
    """A Modulino as two solids for pictures: the bare board, and what sits on it."""
    ox, oy, oz = origin
    top = oz + MOD_T
    pcb = rbox(ox, oy, oz, ox + MOD_X, oy + MOD_Y, top, MOD_R)
    for hx, hy in MOD_HOLES:
        pcb -= cyl(ox + hx, oy + hy, oz - 1, top + 1, 3.2, 32)
    return pcb, modulino(origin, kind) - box(ox - 1, oy - 1, oz - 1, ox + MOD_X + 1, oy + MOD_Y + 1, top + 0.001)


def uno_q_look():
    """The UNO Q as two solids for pictures: the bare board, and its headers, connectors and chips.

    Headers, the USB-C port and the LED matrix sit where the CAD data puts them. The two chips
    are drawn for the look only: their place and size are not from the CAD data.
    """
    ox, oy, oz = UNO_O
    pcb = box(ox, oy, oz, ox + UNO_X, oy + UNO_Y, UNO_TOP)
    for hx, hy in UNO_HOLES:
        pcb -= cyl(ox + hx, oy + hy, oz - 1, UNO_TOP + 1, 3.2, 32)
    parts = uno_q_connectors()
    a, b, c, d = UNO_MATRIX
    parts.append(box(ox + a, oy + b, UNO_TOP, ox + c, oy + d, UNO_TOP + 0.8))
    parts.append(cyl(ox + UNO_BUTTON[0], oy + UNO_BUTTON[1], UNO_TOP, UNO_TOP + 2.0, 3.0, 32))
    parts += [box(ox + 30, oy + 26, UNO_TOP, ox + 44, oy + 40, UNO_TOP + 1.4),   # chips, for the look only
              box(ox + 48, oy + 30, UNO_TOP, ox + 56, oy + 38, UNO_TOP + 1.0)]
    return pcb, union(parts)


def boards():
    return {"UNO Q": uno_q(), "Knob": modulino(KNOB_O, "knob"), "Buzzer": modulino(BUZZ_O, "buzzer")}


def check(base, lid, pod, cover):
    bad = []
    shell = base + lid
    for name, g in boards().items():
        v = (shell ^ g).volume()
        if v > 0.05:
            bad.append(f"{name} in case: {v:.2f} mm3")
    v = ((pod + cover) ^ modulino(P_O, "thermo")).volume()
    if v > 0.05:
        bad.append(f"Thermo in pod: {v:.2f} mm3")
    return bad


# ---------------------------------------------------------------- export ----
def flip(part, z_top, depth_y):
    """Turn a lid upside down so its top face sits on the bed. A rotation, not a mirror."""
    return part.rotate((180, 0, 0)).translate((0, depth_y, z_top))


def triangles(part):
    mesh = part.to_mesh()
    return np.asarray(mesh.vert_properties)[:, :3][np.asarray(mesh.tri_verts)]


def write_stl(part, path):
    tri = triangles(part)
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    data = np.zeros(len(tri), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    data["n"], data["v"] = n, tri
    with open(path, "wb") as f:
        f.write(b"sk-1 enclosure".ljust(80, b" "))
        f.write(struct.pack("<I", len(tri)))
        f.write(data.tobytes())


def write_template(path):
    """1:1 paper template of the UNO Q, to lay the real board on."""
    m, w, h = 10, UNO_X, UNO_Y

    def fy(y):
        return m + h - y

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w + 2 * m}mm" height="{h + 2 * m + 12}mm" '
         f'viewBox="0 0 {w + 2 * m} {h + 2 * m + 12}" font-family="sans-serif" fill="none" stroke-width="0.2">',
         f'<rect x="{m}" y="{m}" width="{w}" height="{h}" stroke="black"/>']
    for hx, hy in UNO_HOLES:
        s.append(f'<circle cx="{m + hx}" cy="{fy(hy)}" r="1.6" stroke="red"/>')
    for a, b, c, d in UNO_HEADERS:
        s.append(f'<rect x="{m + a}" y="{fy(d)}" width="{c - a}" height="{d - b}" stroke="gray"/>')
    a, b, c, d = UNO_MATRIX
    s.append(f'<rect x="{m + a}" y="{fy(d)}" width="{c - a}" height="{d - b}" stroke="green"/>')
    s.append(f'<rect x="{m + UNO_USB_FACE}" y="{fy(UNO_USB_Y + 4.47)}" width="8.1" height="8.94" stroke="blue"/>')
    s.append(f'<circle cx="{m + UNO_BUTTON[0]}" cy="{fy(UNO_BUTTON[1])}" r="2" stroke="blue"/>')
    for lx, ly in UNO_RGB:
        s.append(f'<circle cx="{m + lx}" cy="{fy(ly)}" r="0.5" stroke="green"/>')
    s.append(f'<text x="{m}" y="{m + h + 8}" font-size="3.2" fill="black">UNO Q, components up, USB-C left. '
             f'Print at 100 %. Outline {w} x {h} mm.</text></svg>')
    Path(path).write_text("".join(s))


def render(items, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import to_rgb
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    light = np.array([-0.35, -0.5, 0.8])
    light /= np.linalg.norm(light)
    fig = plt.figure(figsize=(18, 8), facecolor="#f3f3f1")
    for i, (elev, azim) in enumerate(((34, -62), (90, -90)), 1):
        ax = fig.add_subplot(1, 2, i, projection="3d", facecolor="#f3f3f1")
        tris, cols = [], []
        for part, colour in items:
            tri = triangles(part)
            n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
            n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
            shade = 0.5 + 0.5 * np.clip(n @ light, 0, 1)
            sunk = (n[:, 2] > 0.9) & (tri[:, :, 2].max(axis=1) < tri[:, :, 2].max() - 0.05)
            shade[sunk] *= 0.55
            keep = n[:, 2] > -0.2 if elev < 90 else n[:, 2] > 0.5      # drop faces that point away
            tris.append(tri[keep])
            cols.append(np.outer(shade, to_rgb(colour))[keep])
        ax.add_collection3d(Poly3DCollection(np.concatenate(tris), facecolors=np.concatenate(cols),
                                             edgecolors="none"))
        ax.set_xlim(0, OX)
        ax.set_ylim(-45, OY)
        ax.set_zlim(0, 40)
        ax.set_box_aspect((OX, OY + 45, 40))
        ax.view_init(elev, azim)
        ax.set_axis_off()
    fig.subplots_adjust(0, 0, 1, 1, 0, 0)
    fig.savefig(path, dpi=120)


def screw(x, y, z_head_top, length, head_d=5.6, head_h=2.0):
    """A plain stand-in for an M3 screw, head up. z_head_top is the top of the head."""
    return union([cyl(x, y, z_head_top - head_h, z_head_top, head_d, 32),
                  cyl(x, y, z_head_top - head_h - length, z_head_top - head_h + 0.01, 3.0, 24)])


def write_assembly(base, lid, pod, cover):
    """Every part in its assembled place, for the FreeCAD file and the exploded picture.

    Writes assembly/*.stl and assembly/assembly.json. Each entry of the JSON names a part, its
    colour, and how far it moves in the exploded view (mm, as x, y, z). The pod sits to the
    right of the case.
    """
    import json

    folder = HERE / "assembly"
    folder.mkdir(exist_ok=True)
    shift = (OX + 26, (OY - P_OY) / 2, 0)  # the pod sits to the right of the case
    move = lambda part: part.translate(shift)
    board_screws = lambda origin, top: union([screw(origin[0] + hx, origin[1] + hy, top + 2.0, 6.0) for hx, hy in MOD_HOLES])
    pod_top = P_H + P_COVER
    grey, white, dark, steel = "#cfd0cc", "#e4e4e0", "#2b2b2b", "#b9bcc0"
    teal, blue = "#00878f", "#1d3f8f"  # the UNO Q board and the Modulino boards
    uno_pcb, uno_parts = uno_q_look()
    knob_pcb, knob_parts = modulino_look(KNOB_O, "knob")
    buzz_pcb, buzz_parts = modulino_look(BUZZ_O, "buzzer")
    thermo_pcb, thermo_parts = modulino_look(P_O, "thermo")
    up, screws_up, lid_up, lid_screws_up = (0, 0, 34), (0, 0, 58), (0, 0, 98), (0, 0, 128)
    parts = [
        # name, solid, colour, exploded move, what it is, kind of surface
        ("base", base, grey, (0, 0, 0), "Printed base", "print"),
        ("uno_q", uno_pcb, teal, up, "Arduino UNO Q", "board"),
        ("uno_q_parts", uno_parts, dark, up, "Arduino UNO Q, headers and connectors", "part"),
        ("uno_q_screws", union([screw(UNO_O[0] + hx, UNO_O[1] + hy, UNO_TOP + 2.0, 6.0) for hx, hy in UNO_HOLES]),
         steel, screws_up, "4 screws M3 x 6", "metal"),
        ("knob", knob_pcb, blue, up, "Modulino Knob", "board"),
        ("knob_parts", knob_parts, steel, up, "Modulino Knob, encoder", "metal"),
        ("knob_screws", board_screws(KNOB_O, KNOB_O[2] + MOD_T), steel, screws_up, "4 screws M3 x 6", "metal"),
        ("buzzer", buzz_pcb, blue, up, "Modulino Buzzer", "board"),
        ("buzzer_parts", buzz_parts, dark, up, "Modulino Buzzer, buzzer", "part"),
        ("buzzer_screws", board_screws(BUZZ_O, BUZZ_O[2] + MOD_T), steel, screws_up, "4 screws M3 x 6", "metal"),
        ("lid", lid, white, lid_up, "Printed lid", "print"),
        ("lid_screws", union([screw(x, y, Z_TOP, 10.0) for x, y in POSTS]), steel, lid_screws_up, "4 screws M3 x 10", "metal"),
        ("pod_base", move(pod), grey, (0, 0, 0), "Printed Thermo pod base", "print"),
        ("thermo", move(thermo_pcb), blue, (0, 0, 18), "Modulino Thermo", "board"),
        ("thermo_parts", move(thermo_parts), dark, (0, 0, 18), "Modulino Thermo, sensor", "part"),
        ("pod_cover", move(cover), white, (0, 0, 40), "Printed Thermo pod cover", "print"),
        ("pod_screws", move(union([screw(P_O[0] + hx, P_O[1] + hy, pod_top, 10.0) for hx, hy in MOD_HOLES])),
         steel, (0, 0, 62), "4 screws M3 x 10", "metal"),
    ]
    manifest = []
    for name, solid, colour, explode, label, surface in parts:
        write_stl(solid, folder / f"{name}.stl")
        manifest.append({"name": name, "file": f"{name}.stl", "colour": colour, "explode": explode, "label": label,
                         "surface": surface})
    (folder / "assembly.json").write_text(json.dumps({"units": "mm", "parts": manifest}, indent=1) + "\n")
    print(f"assembly: {len(parts)} parts in {folder}")


def main():
    OUT.mkdir(exist_ok=True)
    base, lid = make_base(), make_lid()
    pod, cover = make_pod_base(), make_pod_cover()

    bad = check(base, lid, pod, cover)
    print("clash check:", "OK" if not bad else bad)

    write_stl(base, OUT / "base.stl")
    write_stl(flip(lid, Z_TOP, OY), OUT / "lid.stl")
    write_stl(pod, OUT / "thermo_pod_base.stl")
    write_stl(flip(cover, P_H + P_COVER, P_OY), OUT / "thermo_pod_cover.stl")
    write_template(HERE / "uno_q_template.svg")

    print(f"case: {OX:.1f} x {OY:.1f} x {Z_TOP:.1f} mm, pod: {P_OX:.1f} x {P_OY:.1f} x {P_H + P_COVER:.1f} mm")
    print(f"standoffs: UNO Q {STANDOFF_UNO:.2f}, Knob {KNOB_O[2] - Z_IN:.2f}, Buzzer {BUZZ_O[2] - Z_IN:.2f} mm")
    for name, p in (("base", base), ("lid", lid), ("pod", pod), ("cover", cover)):
        print(f"{name}: {p.status().name}, {p.volume() / 1000:.1f} cm3")

    if "--assembly" in sys.argv:
        write_assembly(base, lid, pod, cover)

    if "--preview" in sys.argv:
        grey, metal = "#d6d7d3", "#8a8d8f"
        shift = (OX / 2 - P_OX / 2 - 4, -P_OY - 12, 0)
        render([(base, grey), (lid, grey), (modulino(KNOB_O, "knob"), metal),
                (pod.translate(shift), grey), (cover.translate(shift), grey)], OUT / "preview.png")


if __name__ == "__main__":
    main()

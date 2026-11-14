"""SK-1, the Safe Kitchen Agent enclosure.

Main case: Arduino UNO Q + Modulino Knob + Modulino Buzzer.
Thermo pod: a small vented box for the Modulino Thermo, on a long Qwiic cable.
Distance pod: a small box for the SparkFun Qwiic ToF Imager VL53L5CX, with a window for its view.

No part needs a screw. Pins on the standoffs locate each board, rods under the lid or the cover
hold it down, and each lid snaps into its base with a tight lip and two bumps.

Run:  uv run --no-project --with manifold3d --with numpy --with matplotlib \
        python enclosure.py [--preview] [--assembly]

All numbers are millimetres. Board geometry comes from the official Arduino
CAD files (docs.arduino.cc, "CAD files" download of each board):
  UNO Q    ABX00162: pad coordinates from the IPC-D-356 netlist and the NC
           drill file. Part heights come from the datasheet drawing.
  Knob     ABX00107: STEP model.
  Buzzer   ABX00108: STEP model.
  Thermo   ABX00103: STEP model.
  ToF      SparkFun SEN-18642: Eagle board file "SparkFun Qwiic ToF Imager - VL53L5CX.brd"
           in github.com/sparkfun/SparkFun_Qwiic_ToF_Imager_VL53L5CX.

Coordinates: x runs left to right (USB-C side is left), y runs front to back,
z is up. The bottom of the base is z = 0.
"""

import math
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
HOLE_D = 3.2                           # UNO Q and Modulino mounting holes

# ------------------------------------------------ SparkFun VL53L5CX data ----
# Frame: x, y from the lower-left board corner as in the Eagle file, z from the PCB top.
TOF_X = TOF_Y = 25.4                   # 1 x 1 inch outline
TOF_T = 1.6                            # standard SparkFun board thickness, not in the file
TOF_HOLE_D = 3.302
TOF_HOLES = [(2.54, 2.54), (22.86, 2.54), (2.54, 22.86), (22.86, 22.86)]
TOF_C = (12.7, 12.7)                   # U1, the VL53L5CX module
TOF_CHIP = (6.4, 3.0, 1.5)             # module length (along x), width, height (ST datasheet)
TOF_QWIIC = [(0.6, 9.7, 5.6, 15.7), (19.8, 9.7, 24.8, 15.7)]  # J2, J4 bodies around their pads
TOF_QWIIC_H = 2.95
TOF_VIEW = 34.0                        # half angle of the window, degrees. The 63 deg diagonal view needs 31.5

# ------------------------------------------------------------- case setup ----
OX, OY = 144.0, 68.0                   # outside footprint
WALL, FLOOR, LID_T = 2.0, 2.0, 2.5
CORNER_R, CHAMFER = 3.0, 0.6
STANDOFF_UNO = 6.0                     # > UNO_BOTTOM
GAP_QWIIC = 15.0                       # between UNO Q and Modulinos, for the plugs
STANDOFF_D = 5.0
# Screwless fit. A pin on each standoff goes up through the board hole. A rod under the lid
# comes down over the pin and stops just above the board, so the board cannot move.
PIN_CLR = 0.35                         # the pin is this much thinner than the hole
PIN_ABOVE = 0.9                        # pin top above the PCB top
CLAMP_GAP = 0.2                        # rod end above the PCB top
BORE = 1.3                             # depth of the hole in the rod end that takes the pin
UNO_ROD_D, MOD_ROD_D = 4.2, 5.0        # the rods stand where a screw head stood before
# Snap lid. A lip under the lid goes into the walls. Bumps on the lip click into grooves.
LIP_W, LIP_H, LIP_CLR = 1.6, 3.5, 0.10   # LIP_CLR is per side. 0.10 gives a very snug fit
SNAP, SNAP_R, SNAP_LEN = 0.30, 0.6, 14.0  # bump height past the lip face, bump radius, bump length
SNAP_XS = (36.0, 108.0)                # bumps on the front and the back wall
PRY_W, PRY_H, PRY_D = 14.0, 1.6, 1.0   # finger notch at the top of each end wall, to lift the lid
DOT_D, DOT_PITCH = 1.5, 2.5            # one dot grid for every grille and vent
ENGRAVE = 0.6
LABEL_XH = 2.4                         # x-height of the small labels
NAME, NAME_XH = "mise", 4.4            # the name on the lid, left of the LED window
NAME_BASE, NAME_BASE_XH, NAME_BASE_Z = "en place", 3.8, 9.6   # on the front wall of the base, under the name

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

# Cable opening for the outside sensors, in the back wall between the UNO Q and the Modulinos.
# It is open at the top, so a cable with its plug on lies in from above. A tongue on the lid
# closes it down to a gap that holds the cables: two or three Qwiic cables side by side.
CABLE_X = (UNO_O[0] + UNO_X + 0.5, UNO_O[0] + UNO_X + 16.5)   # 16 mm wide
CABLE_DEPTH, CABLE_GAP = 5.0, 2.2

# Sensor pods. One shape for both boards. Each pod has a cable slot in both end walls, so the
# Qwiic chain can run from the case to the distance pod and on to the Thermo pod.
P_WALL, P_FLOOR, P_STAND, P_COVER = 1.6, 1.6, 2.4, 2.0
P_LIP_W, P_LIP_H, P_SNAP_LEN = 1.2, 3.0, 10.0
P_CLR = LIP_CLR + P_LIP_W + 0.3        # room for the cover lip beside the board
PODS = {
    "thermo": dict(bx=MOD_X, by=MOD_Y, br=MOD_R, t=MOD_T, holes=MOD_HOLES, hole_d=HOLE_D, centre=MOD_C,
                   qwiic_h=MOD_QWIIC_H, rod_d=MOD_ROD_D, label="temperature"),
    "tof": dict(bx=TOF_X, by=TOF_Y, br=0.6, t=TOF_T, holes=TOF_HOLES, hole_d=TOF_HOLE_D, centre=TOF_C,
                qwiic_h=TOF_QWIIC_H, rod_d=4.6, label="distance"),
}


def pod_geom(s):
    """Outside size, board origin, PCB top, wall height and corner radius of a pod."""
    ox, oy = s["bx"] + 2 * (P_CLR + P_WALL), s["by"] + 2 * (P_CLR + P_WALL)
    o = (P_WALL + P_CLR, P_WALL + P_CLR, P_FLOOR + P_STAND)
    top = o[2] + s["t"]
    return ox, oy, o, top, top + s["qwiic_h"] + 0.6, s["br"] + P_CLR + P_WALL


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


def rod_x(x0, x1, y, z, r):
    """A round bar along x."""
    return Manifold.cylinder(x1 - x0, r, circular_segments=32).rotate((0, 90, 0)).translate((x0, y, z))


def lip_ring(x0, y0, x1, y1, z0, z1, r, w):
    """A rounded rectangular wall of width w. Its outer face is the given box."""
    return rbox(x0, y0, z0, x1, y1, z1, r) - rbox(x0 + w, y0 + w, z0 - 1, x1 - w, y1 - w, z1 + 1, max(r - w, 0.2))


def snaps(xs, length, y_front, y_back, z, r):
    """Round bars along x beside the front and the back inner wall face, one pair per x.

    With r = SNAP_R they are the bumps on a lip. With a slightly larger r they are the grooves
    in the walls. The centre sits inside the lip, so a bump stands SNAP past the lip face.
    """
    inset = LIP_CLR + SNAP_R - SNAP
    return union([rod_x(x - length / 2, x + length / 2, y, z, r)
                  for x in xs for y in (y_front + inset, y_back - inset)])


def clamp_rod(x, y, pcb_top, z_top, d, hole_d):
    """A rod from under a lid down to just above a board, with a hole in its end for the pin."""
    z0 = pcb_top + CLAMP_GAP
    return cyl(x, y, z0, z_top, d, 40) - cyl(x, y, z0 - 0.1, z0 + BORE, hole_d - PIN_CLR + 0.4, 32)


def locating_pin(x, y, z0, pcb_top, hole_d):
    return cyl(x, y, z0 - 0.01, pcb_top + PIN_ABOVE, hole_d - PIN_CLR, 32)


_FONTS = {}
LABEL_FONT = ("DIN Alternate", "DejaVu Sans")
NAME_FONT = HERE / "fonts" / "Borel-Regular.ttf"   # cursive with one stroke width, SIL Open Font License


def text(s, x, y, z0, z1, xh=LABEL_XH, anchor="center", family=LABEL_FONT):
    """Extruded lowercase label. xh is the x-height in mm, y is the baseline."""
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath
    if family not in _FONTS:
        _FONTS[family] = (FontProperties(fname=str(family)) if isinstance(family, Path)
                          else FontProperties(family=list(family), weight="bold"))
    font = _FONTS[family]
    k = xh / TextPath((0, 0), "x", size=10, prop=font).get_extents().height
    polys = [np.asarray(p[:-1]) * k for p in TextPath((0, 0), s, size=10, prop=font).to_polygons()
             if len(p) > 3]
    cs = CrossSection(polys, FillRule.EvenOdd)
    bx0, _, bx1, _ = cs.bounds()
    dx = {"left": -bx0, "center": -(bx0 + bx1) / 2, "right": -bx1}[anchor]
    return cs.extrude(z1 - z0).translate((x + dx, y, z0))


# ------------------------------------------------------------------ base ----
def boards_held():
    """Each board of the main case: its origin, holes, PCB top, standoff and hold-down rod diameter."""
    return [(UNO_O, UNO_HOLES, UNO_TOP, STANDOFF_D, UNO_ROD_D),
            (KNOB_O, MOD_HOLES, KNOB_O[2] + MOD_T, STANDOFF_D + 1.0, MOD_ROD_D),
            (BUZZ_O, MOD_HOLES, BUZZ_O[2] + MOD_T, STANDOFF_D + 1.0, MOD_ROD_D)]


def make_base():
    shell = cbox(0, 0, 0, OX, OY, Z_LID, CORNER_R, bottom=CHAMFER)
    cavity = rbox(WALL, WALL, Z_IN, OX - WALL, OY - WALL, Z_LID + 1, CORNER_R - WALL)
    add, cut = [], []
    for origin, holes, pcb_top, standoff_d, _ in boards_held():
        for hx, hy in holes:
            x, y = origin[0] + hx, origin[1] + hy
            add.append(cyl(x, y, Z_IN - 0.01, origin[2], standoff_d))
            add.append(locating_pin(x, y, origin[2], pcb_top, HOLE_D))
    base = (shell - cavity) + union(add)

    # USB-C port.
    cut.append(port_x(-1, WALL + 1, UNO_O[1] + UNO_USB_Y, UNO_TOP + UNO_USB_Z, 13.0, 7.5, 2.0))
    # Cable opening for the outside sensors, open at the top. The lid tongue closes it to a gap.
    cut.append(box(CABLE_X[0], OY - WALL - 1, Z_LID - CABLE_DEPTH, CABLE_X[1], OY + 1, Z_LID + 1))
    # One row of vent dots low on the front and back walls, in line with the lid vent.
    for i in range(VENT_NX):
        x = WIN_CX + (i - (VENT_NX - 1) / 2) * DOT_PITCH
        for y0, y1 in ((-1, WALL + 1), (OY - WALL - 1, OY + 1)):
            cut.append(wall_dot(x, y0, y1, Z_IN + 3.0, 1.8))
    # Grooves for the lid bumps, and a finger notch at the top of each end wall.
    cut.append(snaps(SNAP_XS, SNAP_LEN + 1.0, WALL, OY - WALL, Z_LID - LIP_H / 2, SNAP_R + 0.05))
    for x0, x1 in ((-1, PRY_D), (OX - PRY_D, OX + 1)):
        cut.append(box(x0, OY / 2 - PRY_W / 2, Z_LID - PRY_H, x1, OY / 2 + PRY_W / 2, Z_LID + 1))
    # The second half of the name, engraved in the front wall under the name on the lid.
    cut.append(text(NAME_BASE, 8.0, 0, 0, ENGRAVE + 1, xh=NAME_BASE_XH, anchor="left", family=NAME_FONT)
               .rotate((90, 0, 0)).translate((0, ENGRAVE, NAME_BASE_Z)))
    # Recesses for four 10 mm rubber feet.
    for x in (10, OX - 10):
        for y in (10, OY - 10):
            cut.append(cyl(x, y, -0.1, 0.8, 10.6))
    return base - union(cut)


# ------------------------------------------------------------------- lid ----
def make_lid():
    lid = cbox(0, 0, Z_LID, OX, OY, Z_TOP, CORNER_R, top=CHAMFER)

    # Snap lip: one closed ring inside the walls, open only at the cable opening.
    lo = WALL + LIP_CLR
    ring = lip_ring(lo, lo, OX - lo, OY - lo, Z_LID - LIP_H, Z_LID + 0.01, CORNER_R - WALL - LIP_CLR, LIP_W)
    ring += snaps(SNAP_XS, SNAP_LEN, WALL, OY - WALL, Z_LID - LIP_H / 2, SNAP_R) ^ box(
        lo - 1, lo - 1, Z_LID - LIP_H, OX - lo + 1, OY - lo + 1, Z_LID)
    ring -= box(CABLE_X[0] - 0.5, OY - WALL - LIP_W - 1, Z_LID - LIP_H - 1, CABLE_X[1] + 0.5, OY, Z_LID)
    tongue = box(CABLE_X[0] + 0.15, OY - WALL, Z_LID - CABLE_DEPTH + CABLE_GAP, CABLE_X[1] - 0.15, OY, Z_LID + 0.01)
    rods = [clamp_rod(origin[0] + hx, origin[1] + hy, pcb_top, Z_LID + 0.01, rod_d, HOLE_D)
            for origin, holes, pcb_top, _, rod_d in boards_held() for hx, hy in holes]
    lid = lid + ring + tongue + union(rods)

    zc0, zc1 = Z_LID - 0.5, Z_TOP + 1               # through-cut range
    ze = Z_TOP - ENGRAVE                            # engraving floor
    cut = []

    # Front row: name, LED matrix window, knob.
    cut.append(rbox(WIN[0], WIN[1], zc0, WIN[2], WIN[3], zc1, 0.6))
    cut.append(rbox(WIN[0] - 1.5, WIN[1] - 1.5, Z_TOP - 0.5, WIN[2] + 1.5, WIN[3] + 1.5, zc1, 1.2))
    cut.append(cyl(*KNOB_XY, zc0, zc1, ENC_BUSH_D + 0.7))
    cut.append(text(NAME, 8.0, ROW_FRONT - NAME_XH / 2, ze, zc1, xh=NAME_XH, anchor="left", family=NAME_FONT))
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


# ------------------------------------------------------------- sensor pods ----
def tof_window(cx, cy, z_chip_top, z0, z1):
    """The window over the VL53L5CX. It widens upward at TOF_VIEW, so its edge stays out of the view."""
    k = math.tan(math.radians(TOF_VIEW))

    def outline(z):
        d = (z - z_chip_top) * k
        return box(cx - TOF_CHIP[0] / 2 - d, cy - TOF_CHIP[1] / 2 - d, z,
                   cx + TOF_CHIP[0] / 2 + d, cy + TOF_CHIP[1] / 2 + d, z + 0.001)
    return Manifold.batch_hull([outline(z0), outline(z1)])


def make_pod_base(kind):
    s = PODS[kind]
    ox, oy, o, top, h, r = pod_geom(s)
    pod = cbox(0, 0, 0, ox, oy, h, r, bottom=0.5) - rbox(P_WALL, P_WALL, P_FLOOR, ox - P_WALL, oy - P_WALL, h + 1, r - P_WALL)
    cx, cy = ox / 2, o[1] + s["centre"][1]
    tab = rbox(cx - 5.5, oy - 6, 0, cx + 5.5, oy + 8, P_FLOOR, 5.4) - cyl(cx, oy + 3.2, -1, P_FLOOR + 1, 4.0)
    add, cut = [tab], []
    for hx, hy in s["holes"]:
        add.append(cyl(o[0] + hx, o[1] + hy, P_FLOOR - 0.01, o[2], STANDOFF_D + 0.6))
        add.append(locating_pin(o[0] + hx, o[1] + hy, o[2], top, s["hole_d"]))
    # A cable slot in each end wall, at the Qwiic sockets, open at the top. The cover closes it.
    for x0, x1 in ((-1, P_WALL + 1), (ox - P_WALL - 1, ox + 1)):
        cut.append(box(x0, cy - 3.6, top - 0.2, x1, cy + 3.6, h + 1))
    if kind == "thermo":  # air comes in low on the long walls and leaves through the cover grille
        for i in range(7):
            x = ox / 2 + (i - 3) * 2 * DOT_PITCH
            for y0, y1 in ((-1, P_WALL + 1), (oy - P_WALL - 1, oy + 1)):
                cut.append(wall_dot(x, y0, y1, P_FLOOR + 1.3, 1.8))
    cut.append(snaps((ox / 2,), P_SNAP_LEN + 1.0, P_WALL, oy - P_WALL, h - P_LIP_H / 2, SNAP_R + 0.05))
    cut.append(box(ox / 2 - 5, -1, h - 1.2, ox / 2 + 5, 0.8, h + 1))   # finger notch in the front wall
    return (pod + union(add)) - union(cut)


def make_pod_cover(kind):
    s = PODS[kind]
    ox, oy, o, top, h, r = pod_geom(s)
    z0, z1 = h, h + P_COVER
    cover = cbox(0, 0, z0, ox, oy, z1, r, top=0.5)
    cx, cy = o[0] + s["centre"][0], o[1] + s["centre"][1]
    lo = P_WALL + LIP_CLR
    ring = lip_ring(lo, lo, ox - lo, oy - lo, z0 - P_LIP_H, z0 + 0.01, max(r - P_WALL - LIP_CLR, 0.3), P_LIP_W)
    ring += snaps((ox / 2,), P_SNAP_LEN, P_WALL, oy - P_WALL, z0 - P_LIP_H / 2, SNAP_R) ^ box(
        lo - 1, lo - 1, z0 - P_LIP_H, ox - lo + 1, oy - lo + 1, z0)
    for x0, x1 in ((-1, lo + P_LIP_W + 0.5), (ox - lo - P_LIP_W - 0.5, ox + 1)):
        ring -= box(x0, cy - 4.1, z0 - P_LIP_H - 1, x1, cy + 4.1, z0)
    rods = [clamp_rod(o[0] + hx, o[1] + hy, top, z0 + 0.01, s["rod_d"], s["hole_d"]) for hx, hy in s["holes"]]
    cover = cover + ring + union(rods)

    cut = []
    if kind == "thermo":
        cut.append(dots(cx, cy, 9, 5, z0 - 1, z1 + 1))
        cut.append(text(s["label"], cx, oy - 5.9, z1 - ENGRAVE, z1 + 1, xh=2.0))
    else:
        cut.append(tof_window(cx, cy, top + TOF_CHIP[2], z0 - 0.5, z1 + 0.5))
        cut.append(text(s["label"], cx, 3.4, z1 - ENGRAVE, z1 + 1, xh=2.0))
    return cover - union(cut)


# ------------------------------------------------------ board stand-ins ----
def modulino(origin, kind):
    ox, oy, oz = origin
    top = oz + MOD_T
    pcb = rbox(ox, oy, oz, ox + MOD_X, oy + MOD_Y, top, MOD_R)
    parts = [pcb - union([cyl(ox + hx, oy + hy, oz - 1, top + 1, HOLE_D, 32) for hx, hy in MOD_HOLES])]
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
    """The UNO Q as one solid for the fit: the board with its low parts as a block, and the connectors.

    The block leaves free each mounting hole and a ring around it, where a screw head stood in
    the first version of the case. The hold-down rods of the lid stand in that ring.
    """
    ox, oy, oz = UNO_O
    block = box(ox, oy, oz, ox + UNO_X, oy + UNO_Y, UNO_TOP + 4.0)
    for hx, hy in UNO_HOLES:
        block -= cyl(ox + hx, oy + hy, oz - 1, UNO_TOP + 0.001, HOLE_D, 32)
        block -= cyl(ox + hx, oy + hy, UNO_TOP, UNO_TOP + 5, UNO_ROD_D + 0.4, 40)
    return union([block] + uno_q_connectors())


def tof_look(origin):
    """The SparkFun VL53L5CX board as two solids: the PCB with its holes, and the parts on it."""
    ox, oy, oz = origin
    top = oz + TOF_T
    pcb = box(ox, oy, oz, ox + TOF_X, oy + TOF_Y, top)
    for hx, hy in TOF_HOLES:
        pcb -= cyl(ox + hx, oy + hy, oz - 1, top + 1, TOF_HOLE_D, 32)
    cx, cy = ox + TOF_C[0], oy + TOF_C[1]
    parts = [box(ox + a, oy + b, top, ox + c, oy + d, top + TOF_QWIIC_H) for a, b, c, d in TOF_QWIIC]
    parts.append(box(cx - TOF_CHIP[0] / 2, cy - TOF_CHIP[1] / 2, top, cx + TOF_CHIP[0] / 2, cy + TOF_CHIP[1] / 2,
                     top + TOF_CHIP[2]))
    return pcb, union(parts)


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


def pod_board(kind):
    o = pod_geom(PODS[kind])[2]
    return modulino(o, "thermo") if kind == "thermo" else union(tof_look(o))


def check(base, lid, pods):
    """Every board against every printed part, each lid against its base, and the view of the ToF."""
    bad = []
    for name, g in boards().items():
        v = ((base + lid) ^ g).volume()
        if v > 0.05:
            bad.append(f"{name} in case: {v:.2f} mm3")
    pairs = [("lid in base", base, lid)] + [(f"{k} cover in pod", pod, cover) for k, (pod, cover) in pods.items()]
    for kind, (pod, cover) in pods.items():
        v = ((pod + cover) ^ pod_board(kind)).volume()
        if v > 0.05:
            bad.append(f"{kind} board in pod: {v:.2f} mm3")
    for name, a, b in pairs:
        v = (a ^ b).volume()
        if v > 0.05:
            bad.append(f"{name}: {v:.2f} mm3")
    # Nothing printed may stand in the 63 deg diagonal view of the VL53L5CX, up to 30 mm out.
    ox, oy, o, top, h, r = pod_geom(PODS["tof"])
    cx, cy, z = o[0] + TOF_C[0], o[1] + TOF_C[1], top + TOF_CHIP[2]
    far = 30.0
    ends = [(dx * TOF_CHIP[0] / 2, dy * TOF_CHIP[1] / 2) for dx in (-1, 1) for dy in (-1, 1)]
    k = math.tan(math.radians(31.5))
    cone = Manifold.batch_hull([box(cx + a - 0.01, cy + b - 0.01, z + 0.05, cx + a + 0.01, cy + b + 0.01, z + 0.06)
                                for a, b in ends] + [cyl(cx, cy, z + far, z + far + 0.01, 2 * far * k, 64)])
    v = (cone ^ (pods["tof"][0] + pods["tof"][1])).volume()
    if v > 0.05:
        bad.append(f"ToF view blocked: {v:.2f} mm3")
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


def write_assembly(base, lid, pods):
    """Every part in its assembled place, for the FreeCAD file and the exploded picture.

    Writes assembly/*.stl and assembly/assembly.json. Each entry of the JSON names a part, its
    colour, and how far it moves in the exploded view (mm, as x, y, z). The two pods sit to the
    right of the case, the distance pod at the far right.
    """
    import json

    folder = HERE / "assembly"
    folder.mkdir(exist_ok=True)
    for old in folder.glob("*.stl"):
        old.unlink()
    grey, white, dark = "#cfd0cc", "#e4e4e0", "#2b2b2b"
    teal, blue, red = "#00878f", "#1d3f8f", "#c8102e"  # UNO Q, Modulinos, SparkFun
    uno_pcb, uno_parts = uno_q_look()
    knob_pcb, knob_parts = modulino_look(KNOB_O, "knob")
    buzz_pcb, buzz_parts = modulino_look(BUZZ_O, "buzzer")
    up, lid_up = (0, 0, 34), (0, 0, 70)
    parts = [
        # name, solid, colour, exploded move, what it is, kind of surface
        ("base", base, grey, (0, 0, 0), "Printed base", "print"),
        ("uno_q", uno_pcb, teal, up, "Arduino UNO Q", "board"),
        ("uno_q_parts", uno_parts, dark, up, "Arduino UNO Q, headers and connectors", "part"),
        ("knob", knob_pcb, blue, up, "Modulino Knob", "board"),
        ("knob_parts", knob_parts, "#b9bcc0", up, "Modulino Knob, encoder", "metal"),
        ("buzzer", buzz_pcb, blue, up, "Modulino Buzzer", "board"),
        ("buzzer_parts", buzz_parts, dark, up, "Modulino Buzzer, buzzer", "part"),
        ("lid", lid, white, lid_up, "Printed lid", "print"),
    ]
    for kind, x in (("thermo", OX + 26), ("tof", OX + 26 + pod_geom(PODS["thermo"])[0] + 16)):
        y = (OY - pod_geom(PODS[kind])[1]) / 2
        pod, cover = pods[kind]
        ox, oy, o, top, h, r = pod_geom(PODS[kind])
        def move(part, k=(x, y, 0)):
            return part.translate(k)
        if kind == "thermo":
            pcb, on = modulino_look(o, "thermo")
            colour, name = blue, "Modulino Thermo"
        else:
            pcb, on = tof_look(o)
            colour, name = red, "SparkFun ToF Imager VL53L5CX"
        title = "Thermo pod" if kind == "thermo" else "Distance pod"
        parts += [(f"{kind}_pod_base", move(pod), grey, (0, 0, 0), f"Printed {title} base", "print"),
                  (kind, move(pcb), colour, (0, 0, 16), name, "board"),
                  (f"{kind}_parts", move(on), dark, (0, 0, 16), f"{name}, parts", "part"),
                  (f"{kind}_pod_cover", move(cover), white, (0, 0, 34), f"Printed {title} cover", "print")]
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
    pods = {kind: (make_pod_base(kind), make_pod_cover(kind)) for kind in PODS}

    bad = check(base, lid, pods)
    print("clash check:", "OK" if not bad else bad)

    write_stl(base, OUT / "base.stl")
    write_stl(flip(lid, Z_TOP, OY), OUT / "lid.stl")
    for kind, (pod, cover) in pods.items():
        ox, oy, o, top, h, r = pod_geom(PODS[kind])
        write_stl(pod, OUT / f"{kind}_pod_base.stl")
        write_stl(flip(cover, h + P_COVER, oy), OUT / f"{kind}_pod_cover.stl")
        print(f"{kind} pod: {ox:.1f} x {oy:.1f} x {h + P_COVER:.1f} mm")
    write_template(HERE / "uno_q_template.svg")

    print(f"case: {OX:.1f} x {OY:.1f} x {Z_TOP:.1f} mm")
    print(f"standoffs: UNO Q {STANDOFF_UNO:.2f}, Knob {KNOB_O[2] - Z_IN:.2f}, Buzzer {BUZZ_O[2] - Z_IN:.2f} mm")
    solids = [("base", base), ("lid", lid)] + [(f"{k} {n}", s) for k, pc in pods.items() for n, s in zip(("pod", "cover"), pc)]
    for name, s in solids:
        print(f"{name}: {s.status().name}, {s.volume() / 1000:.1f} cm3")

    if "--assembly" in sys.argv:
        write_assembly(base, lid, pods)

    if "--preview" in sys.argv:
        grey, metal = "#d6d7d3", "#8a8d8f"
        items = [(base, grey), (lid, grey), (modulino(KNOB_O, "knob"), metal)]
        x = 4.0
        for kind, (pod, cover) in pods.items():
            ox, oy = pod_geom(PODS[kind])[:2]
            items += [(pod.translate((x, -oy - 12, 0)), grey), (cover.translate((x, -oy - 12, 0)), grey)]
            x += ox + 10
        render(items, OUT / "preview.png")


if __name__ == "__main__":
    main()

"""SK-1, the case of "mise". One device that hangs on a wall, and two sensor pods.

Housing: a back shell and a front cover for the Guition JC4827W543C touch screen, the Arduino
         UNO Q, the Modulino Knob and the Modulino Buzzer, a knob cap for the encoder and a
         button cap for the power button of the UNO Q.
Thermo pod: a small vented box for the Modulino Thermo, on a long Qwiic cable.
Distance pod: a small box for the SparkFun Qwiic ToF Imager VL53L5CX, with a window for its view.

No screw holds one part to another. Pins on the standoffs locate each board, rods under the
cover hold the UNO Q and the Modulinos down, hooks on the back shell hold the screen, and each
cover snaps into its base with a tight lip and bumps. The housing hangs on two wall screws in
keyhole slots, or on two adhesive strips.

The front cover does not depend on any estimated screen number. If the screen does not fit,
measure it, change the screen numbers, and print the back shell again. See place_screen().

Run:  uv run --no-project --with manifold3d --with numpy --with matplotlib \
        python enclosure.py [--preview] [--assembly] [--check]

--check runs the tests only and writes no file. The script stops with an error if a test fails
or if a label is too thin to print.

All numbers are millimetres. Board geometry comes from the official Arduino
CAD files (docs.arduino.cc, "CAD files" download of each board):
  UNO Q    ABX00162: pad coordinates from the IPC-D-356 netlist and the NC
           drill file. Part heights come from the datasheet drawing.
  Knob     ABX00107: STEP model.
  Buzzer   ABX00108: STEP model.
  Thermo   ABX00103: STEP model.
  ToF      SparkFun SEN-18642: Eagle board file "SparkFun Qwiic ToF Imager - VL53L5CX.brd"
           in github.com/sparkfun/SparkFun_Qwiic_ToF_Imager_VL53L5CX.
  Screen   Guition JC4827W543C: no drawing. See the evidence level of each number below.

Coordinates: the housing is built lying on its back. Look at its face: x runs left to right,
y runs from the bottom edge to the top edge, z runs from the wall toward you. The back of the
housing is z = 0. The pods keep their own frame: x left to right, y front to back, z up.
"""

import math
import struct
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
from manifold3d import CrossSection, FillRule, JoinType, Manifold, OpType

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
UNO_BUTTON = (5.84, 49.40)             # JBTN1 centre, the power button
UNO_BUTTON_H = 2.0                     # ESTIMATE: no height in the CAD data. The usual height of a small SMD push button
UNO_BUTTON_TRAVEL = 0.25               # ESTIMATE: the usual travel of a small SMD push button
UNO_MATRIX = (31.29, 4.51, 58.43, 20.44)  # D27001..D27104 pad extents: the 104 LEDs of the matrix
UNO_LED_N = (13, 8)                    # columns and rows of the matrix (the sketch draws on 13 x 8)
# The CAD data gives the extents of all LED pads, not the pads of one LED. The pitch below
# assumes that the pads of one LED cover UNO_LED_PAD in each direction (0402 and 0603 LEDs
# lie between 0.5 and 1.6). DERIVED: with a pad field from 0.4 to 1.6 the pitch changes by
# 0.05 mm (columns) and 0.09 mm (rows), and an outer LED moves by 0.3 mm at most.
UNO_LED_PAD = 1.0
UNO_LED_PITCH = ((UNO_MATRIX[2] - UNO_MATRIX[0] - UNO_LED_PAD) / (UNO_LED_N[0] - 1),
                 (UNO_MATRIX[3] - UNO_MATRIX[1] - UNO_LED_PAD) / (UNO_LED_N[1] - 1))
UNO_LED_C = ((UNO_MATRIX[0] + UNO_MATRIX[2]) / 2, (UNO_MATRIX[1] + UNO_MATRIX[3]) / 2)
UNO_LEDS = [(UNO_LED_C[0] + (i - (UNO_LED_N[0] - 1) / 2) * UNO_LED_PITCH[0],
             UNO_LED_C[1] + (j - (UNO_LED_N[1] - 1) / 2) * UNO_LED_PITCH[1])
            for j in range(UNO_LED_N[1]) for i in range(UNO_LED_N[0])]
UNO_LED_H = 0.8                        # ESTIMATE: LED height. 0402 and 0603 LEDs are 0.35 to 0.8
UNO_LED_KEEP = 1.2                     # ESTIMATE: no part taller than the LEDs within this distance of the LED pads,
                                       #           except the JANALOG header. Check it on the board
UNO_RGB = [(65.02, 42.16), (65.02, 44.07), (65.02, 45.97), (65.02, 47.88)]  # 4 RGB LEDs
UNO_HEADERS = [                        # x0, y0, x1, y1 of each header body
    (17.5, 49.5, 64.8, 52.1),          # JDIGITAL
    (26.6, 1.25, 64.8, 3.85),          # JANALOG
    (62.4, 24.1, 67.5, 31.8),          # JSPI
    (12.7, 41.6, 24.3, 47.3),          # JCTL
]
UNO_BOTTOM_PARTS = [(9.2, 7.43, 14.2, 45.93), (56.1, 7.43, 61.0, 45.93)]  # JMEDIA, JMISC
UNO_LOW = 4.0                          # ESTIMATE: all other parts on the top of the UNO Q are lower than this

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
ENC_PUSH = 0.5                         # ESTIMATE: travel of the push switch in the encoder. Not in the STEP model.
                                       #           0.5 is the usual value for a 6 mm D-shaft encoder with a switch
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
# Soldered pin headers, as x0, y0, x1, y1 of each plastic body: the row of six on the front edge
# (GND, 3V3, SDA, SCL, INT, RST) and the row of three on the back edge. The pins point down,
# away from the sensor. Set TOF_PINS to [] for a board without headers.
TOF_PINS = [(5.08, 0.0, 20.32, 2.54), (8.89, 22.86, 16.51, 25.4)]
PIN_BODY_H, PIN_BELOW, PIN_SLOT_CLR = 2.54, 6.1, 0.4   # body under the board, pin below the body, room per side
TOF_VIEW = 34.0                        # half angle of the window, degrees. The 63 deg diagonal view needs 31.5

# -------------------------------------------- Guition JC4827W543C data ----
# The touch screen: an ESP32-S3 board with a 4.3 inch, 480 x 272 capacitive touch display.
# Frame: look at the display with the USB-C port on the left. x runs from the left end of the
# outline (the tips of the two left ears), y from the bottom edge. The display is on the front
# of the PCB, all other parts on the back. With the USB-C port on the left, the wider border of
# the glass (the chin) is at the bottom, and the SD card socket and P2 to P5 are on the bottom edge.
#
# Source of most numbers: the vendor structure drawing of the JC4827W543 (folder
# 3-Structure_Diagram of the vendor package, mirrored at github.com/OldNKrusty/JC4827W543-ESP32S3-Guide).
# The drawing counts y from the top edge down. This file counts y from the bottom edge up, so
# y here = 70.2 - y in the drawing. x is the same in both. The levels are:
#   VENDOR DRAWING (dimensioned)  a dimension line of the drawing gives the number.
#   VENDOR DRAWING (measured)     measured on the render of the drawing, scaled from the dimensioned
#                                 numbers. About 0.3 mm.
#   VENDOR CLAIM                  the vendor schematic of the board (V0.2) says so. Not measured.
#   WEB                           a web page states the number. The page is named.
#   STANDARD PANEL VALUE          the usual number for a 4.3 inch, 480 x 272 panel.
#   ESTIMATE                      a guess. Measure it before you print.
# The drawing gives no height. All heights below are WEB or ESTIMATE.
# Only the back shell uses these numbers. The front cover uses SCR_AA alone (see place_screen).
SCR_X, SCR_Y = 120.0, 70.2     # VENDOR DRAWING (dimensioned): outline over the ear tips
SCR_BODY = (6.9, 113.1)        # VENDOR DRAWING (measured): x range of the PCB body. Four ears stand out past it
SCR_EAR_H = 8.2                # VENDOR DRAWING (measured): each ear reaches this far from the top or the bottom edge
SCR_HOLE_D = 3.2               # VENDOR DRAWING (dimensioned): R1.6
SCR_HOLE_DX, SCR_HOLE_DY = 112.6, 62.1   # VENDOR DRAWING (dimensioned): distance between the hole centres
SCR_TOTAL_T = 9.5              # WEB: "~(120mm x 70.2mm x 9.5mm)", make.net.za/product/3me3062 (a version without touch)
SCR_T = 1.6                    # ESTIMATE: PCB thickness, the usual value
SCR_BACK_H = 3.5               # ESTIMATE: tallest part on the back (USB-C port, sockets P1 to P5)
SCR_LCD_H = SCR_TOTAL_T - SCR_T - SCR_BACK_H   # ESTIMATE: display with touch glass, from the PCB front to the glass front
SCR_LCD = (106.1, 67.4)        # VENDOR DRAWING (measured): glass outline, x 6.9 to 113.0, y 1.5 to 68.9.
SCR_LCD_OFF = (-0.05, 0.1)     # VENDOR DRAWING (measured): centre of the glass from the PCB centre
SCR_AA = (95.04, 53.86)        # STANDARD PANEL VALUE, and WEB: "Effective Display Area 95.04 x 53.86", make.net.za/product/3me3062
SCR_AA_OFF = (0.0, 1.7)        # VENDOR DRAWING (measured): centre of the active area from the PCB centre. Borders:
                               # 4.6 at the top, 7.7 at the bottom (the chin), about 5 at the sides
SCR_USB_Y = 40.4               # VENDOR DRAWING (measured): USB-C port (USB1) centre on the left body edge, y 35.8 to 45.0
SCR_USB_Z = 1.6                # ESTIMATE: USB-C port centre behind the PCB back face (half of a 3.2 mm port)
SCR_USB_FACE = -SCR_BODY[0]    # VENDOR DRAWING (measured): the port mouth is flush with the body edge, 6.9 inside the ear tips
SCR_BOOT, SCR_RST = (111.1, 24.1), (111.1, 16.5)   # VENDOR DRAWING (measured): button centres, each about 3.4 x 5
SCR_ANT = (106.6, 37.0, 113.1, 57.7)   # VENDOR DRAWING (measured): antenna end of the ESP32-S3 module, x0, y0, x1, y1
# Parts on the back, as x0, y0, x1, y1 (VENDOR DRAWING, measured) and a height behind the PCB back face (ESTIMATE).
SCR_ESP = (88.0, 37.0, 113.1, 57.7, 3.1)        # ESP32-S3 module
SCR_P1 = (6.9, 22.4, 12.3, 33.2, 3.4)           # P1, 4-pin HC-1.25, power and UART0, left body edge
SCR_LCD1 = (24.5, 40.9, 50.3, 47.5, 1.0)        # LCD1, flex connector of the display
SCR_P6 = (13.9, 64.8, 21.7, 69.9, 3.4)          # P6, BAT, 2-pin 1.25, top edge
SCR_P7 = (36.0, 62.6, 42.6, 69.9, 3.4)          # P7, speaker, 2-pin 1.25, top edge
SCR_TF = (69.7, -1.7, 86.3, 15.5, 1.9)          # TF1, micro SD socket on the bottom edge. Its body stands 1.7 past
                                                # the edge. The card goes in at the bottom edge
SCR_SOCKETS = [(17.3, 0.0, 26.4, 6.1, 3.4), (27.5, 0.0, 37.9, 6.1, 3.4),   # P5, P4, P3, P2 on the bottom edge,
               (41.1, 0.0, 51.2, 6.1, 3.4), (54.8, 0.0, 65.2, 6.1, 3.4)]   # cable entry down, out of the edge
SCR_SW1 = (80.1, 66.9, 84.8, 70.2, 2.0)         # SW1, the battery power button (press), on the top edge. VENDOR CLAIM
                                                # (schematic): a 4-pin push button on the KEY pin of the charge chip U2
SCR_CABLE_ROOM = 6.0           # ESTIMATE: room below the bottom edge for a plug in P2 to P5 and the bend of its wires
# Screen hooks of the back shell. The glass is as wide as the PCB body, so the hooks catch the
# 1.3 mm strip of PCB above and below the glass, at places where the drawing shows no part on the
# edge: x of each hook, and +1 for the top edge or -1 for the bottom edge.
SCR_HOOKS = [(29.0, 1), (99.0, 1), (12.0, -1), (109.9, -1)]

# -------------------------------------------------------------- fit ----
# FIT is the one tolerance of the printer. Each clearance below is FIT plus a fixed allowance
# for its kind of fit, so one edit of FIT loosens or tightens every fit together. After a test
# print: if the parts are too tight, increase FIT by 0.05. If they are loose, reduce it by 0.05.
FIT = 0.10
LIP_CLR = FIT                          # per side, cover lip in the walls: 0.10
PIN_CLR = 2 * (FIT + 0.075)            # on the diameter, pin in a board hole: 0.35
SCR_PIN_CLR = 2 * (FIT + 0.175)        # on the diameter, pin in a screen hole. A little looser, as the hole places
                                       # come from a symmetric reading of the drawing: 0.55
SHAFT_CLR = 2 * FIT                    # on the diameter, knob bore on the encoder shaft: 0.20
KNOB_GAP = FIT + 0.40                  # per side, knob cap in its ring. The cap must turn freely: 0.50
BTN_CLR = FIT + 0.10                   # per side, button cap in its hole. It must slide freely: 0.20
TONGUE_CLR = FIT + 0.05                # per side, cover tongue in a cable opening: 0.15
HOOK_GAP = FIT + 0.10                  # from a screen hook to the PCB edge: 0.20
SNAP = 0.30                            # a bump stands this far past the lip face. Interference: SNAP - LIP_CLR = 0.20
SNAP_R, SNAP_LEN = 0.6, 14.0           # bump radius and length. Each groove is 0.05 larger in radius and 1 mm longer

# ------------------------------------------------------------- case setup ----
# The USB-C plugs of the UNO Q and of the screen. Both ports face left.
#   "straight": each plug goes through its own opening in the left wall. The cables leave to the left.
#   "angled":   plugs with a 90 degree side bend stay inside, in a room at the left. The cables
#               leave through an opening in the bottom wall.
PLUGS = "straight"
PLUG = (12.5, 6.5, 20.0)               # assumed plug body for the fit: width, thickness, length
OX = {"straight": 132.0, "angled": 160.0}[PLUGS]   # outside width. The height OY follows from the face layout
BAND = 68.0                            # height of the control band: the part of the face between the foot and the screen
FOOT = 23.7                            # height of the strip below the control band. It holds the Valio Aimo logo.
                                       # check() proves the room above the logo and under the knob
USB_ROOM = {"straight": 1.0, "angled": 18.0}[PLUGS]   # from the left wall to the UNO Q. Angled plugs need room inside
WALL, FLOOR, LID_T = 2.0, 2.0, 2.5
CORNER_R = 6.0                         # round vertical corners
EDGE = 1.5                             # 45 degree chamfer on the face edge and on the back edge. Both lie on the bed
SEAM = 0.5                             # 45 degree chamfer on each side of the joint between cover and back shell
STANDOFF_UNO = 6.0                     # > UNO_BOTTOM
GAP_QWIIC = 15.0                       # between UNO Q and Modulinos, for the plugs
STANDOFF_D = 5.0
# Screwless fit. A pin on each standoff goes up through the board hole. A rod under the cover
# comes down over the pin and stops just above the board, so the board cannot move.
PIN_ABOVE = 0.9                        # pin top above the PCB top
CLAMP_GAP = 0.2                        # rod end above the PCB top
BORE = 1.3                             # depth of the hole in the rod end that takes the pin
UNO_ROD_D, MOD_ROD_D = 4.2, 5.0        # the rods stand where a screw head stood before
# Snap cover. A lip under the cover goes into the walls. Bumps on the lip click into grooves.
LIP_W, LIP_H = 1.6, 3.5
PRY_W, PRY_H, PRY_D = 14.0, 1.6, 1.0   # finger notch at the top of each end wall, to lift the cover
DOT_D, DOT_PITCH = 1.5, 2.5            # one dot grid for every grille and vent
LAYER = 0.2                            # print layer height. Depths in the bed face are a whole number of layers
ENGRAVE = 3 * LAYER
# Text for a 0.4 mm nozzle. The rule: the thinnest stroke is 1.0 or more, the thinnest gap is
# 0.5 or more, and the engraving is 0.6 deep or more. The script measures every label and mark
# and stops if one breaks the rule. BOLD moves every stroke edge out by that much, the letters
# of a label stand at least MIN_GAP apart, and a hole in a letter narrower than MIN_GAP is filled.
RULE_STROKE, RULE_GAP, RULE_DEPTH = 1.0, 0.5, 0.6
MIN_GAP = 0.7
LABEL_XH, LABEL_BOLD = 5.0, 0.13       # x-height of every DIN label: cover and pods. The crossbars of t and e set it
NAME, NAME_XH, NAME_BOLD = "mise", 5.2, 0.10   # the name on the face, in Borel
# The second half of the name runs along the right wall, from bottom to top. It must fit the
# depth of the back shell with SIDE_MARGIN to the chamfers, so it is a little smaller and bolder.
NAME_BASE, NAME_BASE_XH, NAME_BASE_BOLD = "en place", 4.5, 0.13
SIDE_MARGIN = 2.0
# The Valio Aimo logo, traced from its image (logos/valio_aimo.png, black on white), in the foot.
# Its thinnest strokes are in the small capitals VALIO: 1.0 % of the logo width, and the gap
# between their L and I is 1.3 %. So the logo is LOGO_W wide and grows by LOGO_OFFSET on each
# edge. 68 mm with 0.16 is the smallest width that passes the rule, and 70 mm gives a little room.
LOGO_FILE = HERE / "logos" / "valio_aimo.png"
LOGO_W, LOGO_OFFSET = 70.0, 0.16
LOGO_Y0 = 6.5                          # bottom of the logo on the face. "limit" stands on the baseline of VALIO
# The Arduino mark on the cover: an infinity sign with a minus in the left loop and a plus in the
# right loop. Radius of each loop and distance of its centre from the middle (both to the
# middle of the stroke), stroke width, and length and width of a bar of the signs.
ARD_R, ARD_C, ARD_W, ARD_SIGN = 2.9, 4.2, 1.1, (2.4, 1.05)
# Knob cap for the 6 mm D-shaft. It stands in a round well of the cover, with room below it to press.
KNOB_D, KNOB_ROOF = 18.0, 1.8          # cap diameter, plastic above the shaft end
KNOB_RECESS = 1.6                      # well depth in the cover
KNOB_RIM = 0.8                         # 45 degree chamfer around the well: the visible ring
KNOB_PUSH = 0.8                        # from the well floor to the cap. The encoder button travels ENC_PUSH
KNOB_BOSS = 1.5                        # extra cover thickness under the well
KNOB_RING_R = KNOB_D / 2 + KNOB_GAP + KNOB_RIM   # outer radius of the ring on the face
# Button cap over the UNO Q power button. A stem goes through the cover, a cone and a flange
# under the cover keep it from falling out to the front, and a pusher reaches down to the button.
BTN_D, BTN_FLANGE, BTN_FLANGE_T, BTN_PUSH_D = 5.0, 8.0, 0.6, 2.4
BTN_HOLE = BTN_D + 2 * BTN_CLR         # 5.4
BTN_PROUD = 1.0                        # the cap stands this far in front of the face at rest
BTN_GAP = 0.4                          # from the pusher to the button at rest
BTN_EDGE = 0.4                         # chamfer on the cap top edge and on the hole mouth
# The LED matrix of the UNO Q. Three ways to show it:
#   "insert": a separate printed grille (stl/led_grille.stl). A plate with one hole over each LED
#             lies just above the LEDs. Three sleeve walls rise from it and lean out at 45 degrees,
#             and a rim at their top presses into a pocket under the cover. The cover has one open
#             window over it. The plate can be printed in a dark filament.
#   "grid":   one hole over each LED through the cover, with a light guide block under the cover
#             that reaches down to just above the LEDs. Each channel is 10.5 mm deep.
#   "window": one opening around the LED field, with a collar under the cover.
LED_FACE = "insert"
LED_HOLE_D = 1.3                       # hole over each LED. About 60 % of the pitch, and the wall stays 0.8 or more
LED_GAP = 0.3                          # from the plate or the light guide block to the top of the LEDs. The LED height
                                       # is an estimate, so the gap is not smaller
INSERT_T = 1.6                         # "insert": plate thickness, 8 layers
INSERT_WALL = 0.9                      # "insert": sleeve and rim wall
INSERT_MARGIN = 0.1                    # "insert": from the outer hole edges to the inside of the sleeve
INSERT_LEAN = (3.5, 3.5, 2.0)          # "insert": how far the left, right and top sleeve wall lean out at 45 degrees.
                                       # A rod of the cover limits the right side, the JSPI header the top
INSERT_POCKET = 1.0                    # "insert": depth of the pocket under the cover that takes the rim
INSERT_PRESS = FIT - 0.15              # "insert": per side, rim in the pocket: -0.05, a light press fit
LED_WALL = 1.0                         # block wall outside the outer holes, and collar wall in "window" mode
LED_WIN_MARGIN = 0.5                   # "window" mode: margin around the LED pads
LED_HEADER_CLR = 0.3                   # from the block to the JANALOG header
LED_SKIN = 0.0                         # 0: open holes. 2 * LAYER: the first two layers stay closed over each hole. Each
                                       # dot then glows from any angle and hides when off. Open holes show the centre
                                       # of an LED only within about 3 degrees of straight on (1.3 mm hole, 10.5 mm deep)

Z_IN = FLOOR
UNO_O = (WALL + USB_ROOM, FOOT + (BAND - UNO_Y) / 2, Z_IN + STANDOFF_UNO)   # PCB lower-left-back corner
UNO_TOP = UNO_O[2] + UNO_T
Z_LID = UNO_TOP + UNO_HEADER_H + 0.6   # cover underside
Z_TOP = Z_LID + LID_T

# The LED field on the face, and the rows of the control band.
LED_XY = [(UNO_O[0] + x, UNO_O[1] + y) for x, y in UNO_LEDS]
FIELD = (UNO_O[0] + UNO_MATRIX[0], UNO_O[1] + UNO_MATRIX[1], UNO_O[0] + UNO_MATRIX[2], UNO_O[1] + UNO_MATRIX[3])
HOLES_BOX = (min(x for x, _ in LED_XY) - LED_HOLE_D / 2, min(y for _, y in LED_XY) - LED_HOLE_D / 2,
             max(x for x, _ in LED_XY) + LED_HOLE_D / 2, max(y for _, y in LED_XY) + LED_HOLE_D / 2)
WIN_CX = (FIELD[0] + FIELD[2]) / 2
VENT_NX = int((FIELD[2] - FIELD[0] - DOT_D) // DOT_PITCH) + 1     # vent as wide as the LED field
ROW_FRONT = (FIELD[1] + FIELD[3]) / 2  # the LED field, the Arduino mark and the knob share this line
ROW_BACK = BAND + 2 * FOOT - ROW_FRONT   # the buzzer sits on this line
ROW_GRILLE = ROW_BACK - DOT_PITCH / 2  # the vent and the speaker grille share this line: 4 rows, over the buzzer

MOD_X0 = UNO_O[0] + UNO_X + GAP_QWIIC
KNOB_TOP = Z_TOP - 0.2 - ENC_BUSH_TOP                 # Knob PCB top: bushing ends flush
BUZZ_TOP = Z_LID - BUZ_BODY[4] - 0.6                  # Buzzer PCB top: buzzer just under the cover
KNOB_O = (MOD_X0, ROW_FRONT - MOD_C[1], KNOB_TOP - MOD_T)
BUZZ_O = (MOD_X0, ROW_BACK - MOD_C[1], BUZZ_TOP - MOD_T)
KNOB_XY = (KNOB_O[0] + MOD_C[0], ROW_FRONT)
KNOB_Z0 = Z_TOP - KNOB_RECESS + KNOB_PUSH             # cap underside
SHAFT_TIP = KNOB_TOP + ENC_SHAFT_TOP                  # the cap sits on the shaft end
KNOB_Z1 = SHAFT_TIP + KNOB_ROOF
BUZZ_XY = (BUZZ_O[0] + MOD_C[0], ROW_BACK)
BTN_XY = (UNO_O[0] + UNO_BUTTON[0], UNO_O[1] + UNO_BUTTON[1])
BTN_Z0 = UNO_TOP + UNO_BUTTON_H + BTN_GAP             # pusher end at rest
BTN_Z1 = Z_TOP + BTN_PROUD                            # cap top at rest

# The screen window. It is placed and sized from the active area (a standard panel value) and
# from the knob, never from an estimated screen number. Its right edge lines up with the ring of
# the knob, and the same margin goes all round the active area.
SCR_WIN_CHAMFER = 1.2                  # the window edge slopes at 45 degrees, so a finger reaches the edge of the picture
SCR_WIN_MARGIN = KNOB_XY[0] + KNOB_RING_R - OX / 2 - SCR_AA[0] / 2 - SCR_WIN_CHAMFER   # 2.66: the window is this much
                                       # larger than the active area on each side
SCR_WIN_C = (OX / 2, FOOT + 104.4)     # centre of the window. The back shell puts the active area here
X_L = OX / 2 - SCR_AA[0] / 2 - SCR_WIN_MARGIN - SCR_WIN_CHAMFER   # left edge of the window on the face: the left axis
WIN_TOP = SCR_WIN_C[1] + SCR_AA[1] / 2 + SCR_WIN_MARGIN + SCR_WIN_CHAMFER
OY = math.ceil(2 * (WIN_TOP + X_L)) / 2  # outside height: the margin above the window is the margin beside it
LABEL_LINE = BTN_XY[1] - LABEL_XH / 2  # baseline of "power" and "alert": the x-height is centred on the button

# Cable openings in the bottom wall. Each opening is open toward the front, so a cable with its
# plug on lies in before the cover goes on. A tongue on the cover closes the opening down to a
# gap that holds the cables. Each entry: x0, x1, depth of the opening, height of the gap.
CABLE_X = (UNO_O[0] + UNO_X + 0.5, UNO_O[0] + UNO_X + 16.5)   # between the UNO Q and the Modulinos, 16 mm wide
CABLE_DEPTH, CABLE_GAP = 5.0, 2.2                             # for two or three Qwiic cables side by side
OPENINGS = [(*CABLE_X, CABLE_DEPTH, CABLE_GAP)]
if PLUGS == "angled":                  # one more opening under the USB-C room, 12 mm wide, for two USB-C cables side by side
    OPENINGS.append((WALL + 3.0, WALL + USB_ROOM - 3.0, 9.5, 5.5))
# Openings in the left wall for straight plugs, as width along the wall and height. The place
# of the screen port comes from the drawing (about 0.3 mm), its height is an estimate.
UNO_PORT = (PLUG[0] + 2 * (FIT + 0.15), PLUG[1] + 2 * (FIT + 0.40))   # 13.0 x 7.5
SCR_PORT = (PLUG[0] + 2 * (FIT + 0.45), PLUG[1] + 2 * (FIT + 0.90))   # 13.6 x 8.5
SNAP_XS = (OX * 0.25, OX * 0.75)       # bumps on the bottom and the top wall
SNAP_YS = (OY * 0.25, OY * 0.75)       # bumps on the left and the right wall, the long sides

# Wall mount: two keyhole slots in the back, behind the screen. The head of a screw goes in
# through the round hole, and the housing then slides down on the shank.
KEY_SCREW_D, KEY_HEAD_D, KEY_HEAD_H = 4.0, 8.0, 3.5   # the assumed screw: 4 mm shank, head up to 8 mm wide and 3.5 mm high
KEY_HOLE_D = KEY_HEAD_D + 1.0                          # round hole for the head
KEY_XS = (OX / 2 - 40.0, OX / 2 + 40.0)                # 80 mm between the two screws
KEY_TRAVEL = 9.0                                       # how far the housing slides down
# Second way to hang it: two adhesive strips in two shallow fields of the back. The assumed
# strip is 20 x 50 mm (the large size of the usual removable mounting strips), up to 1.6 mm thick.
STRIP = (50.0, 20.0)
STRIP_FIELD = (STRIP[0] + 2.0, STRIP[1] + 2.0, 2 * LAYER)   # field: 1 mm room each side, 0.4 deep
STRIP_YS = (19.0, OY - 28.0)           # field centres, low and high on the back
# A small mark engraved in the back, between the low strip field and the SD opening. It is read
# from behind, with the housing off the wall. A chef's hat over three lines of DIN text.
BACK_LINES = ("made with care", "at Junction 2026", "Kindergarten Engineering")
BACK_PITCH = 9.6                       # from one baseline to the next
BACK_HAT = (16.0, 1.2)                 # width of the hat, and the width of its engraved line
BACK_CLEAR = 3.0                       # from the mark to every opening, field and edge of the back
# Screen hooks on the back shell, at the top and bottom PCB edges (places: SCR_HOOKS).
HOOK_W, HOOK_T, HOOK_LIP = 5.0, 1.6, 0.8   # width, arm thickness, catch over the PCB front (the arm bends this far)
SCR_GLASS_GAP = 1.5                    # from the glass to the underside of the cover. Glass 1 mm thicker still clears it
SCR_SHIFT = 2.0                        # the cover must clear the screen with the board this far off in x or y
SCR_GLASS_EXTRA = 1.0                  # and with the glass this much thicker
SCR_BTN_D = 5.0                        # hole in the back over each button, for a pen tip
SCR_CARD_ROOM = 6.0                    # the SD opening in the back is this much larger than TF1 along the edge, each way
SCR_SW1_HOLE = 3.0                     # height of the pen hole over SW1 in the top wall. It is 6 mm longer each way


def place_screen():
    """Place the screen board from the estimates. Only the back shell and the checks use this.

    The back shell puts the board so that its active area sits under the window centre
    SCR_WIN_C, with the glass SCR_GLASS_GAP under the cover. When a screen number changes, the
    board moves and the back shell changes. The front cover stays the same.
    """
    global S_O, S_TOP, SCR_HOLES, KEY_Y
    hx, hy = (SCR_X - SCR_HOLE_DX) / 2, (SCR_Y - SCR_HOLE_DY) / 2     # 3.7 and 4.05: the holes sit symmetric
    SCR_HOLES = [(x, y) for x in (hx, SCR_X - hx) for y in (hy, SCR_Y - hy)]
    S_O = (SCR_WIN_C[0] - SCR_X / 2 - SCR_AA_OFF[0], SCR_WIN_C[1] - SCR_Y / 2 - SCR_AA_OFF[1],
           Z_LID - SCR_GLASS_GAP - SCR_LCD_H - SCR_T)          # PCB lower-left-back corner
    S_TOP = S_O[2] + SCR_T                                    # PCB front face
    KEY_Y = S_O[1] + 52.0                                     # where each wall screw rests


place_screen()

# Sensor pods. One shape for both boards. Each pod has a cable slot in both end walls, so the
# Qwiic chain can run from the case to the distance pod and on to the Thermo pod.
P_WALL, P_FLOOR, P_STAND, P_COVER = 1.6, 1.6, 2.4, 2.0
P_LIP_W, P_LIP_H, P_SNAP_LEN = 1.2, 3.0, 10.0
P_CLR = LIP_CLR + P_LIP_W + 0.3        # room for the cover lip beside the board
P_EDGE = 1.0                           # 45 degree chamfer on the bed edges of the pods
P_LABEL_ROOM = 2.0                     # from the face chamfer of a pod cover to its label
PODS = {
    "thermo": dict(bx=MOD_X, by=MOD_Y, br=MOD_R, t=MOD_T, holes=MOD_HOLES, hole_d=HOLE_D, centre=MOD_C,
                   qwiic_h=MOD_QWIIC_H, rod_d=MOD_ROD_D, label="temperature"),
    "tof": dict(bx=TOF_X, by=TOF_Y, br=0.6, t=TOF_T, holes=TOF_HOLES, hole_d=TOF_HOLE_D, centre=TOF_C,
                qwiic_h=TOF_QWIIC_H, rod_d=4.6, label="distance", pins=TOF_PINS),
}
TAB = (11.0, 8.0, 4.0)                 # pod hanging tab: width, reach past the back wall, hole diameter


def pod_geom(s):
    """Outside size, board origin, PCB top, wall height and corner radius of a pod.

    A pod is long enough for its board and for its label at full weight.
    """
    ox = max(s["bx"] + 2 * (P_CLR + P_WALL), label_width(s["label"]) + 2 * (P_EDGE + P_LABEL_ROOM))
    oy = s["by"] + 2 * (P_CLR + P_WALL)
    o = ((ox - s["bx"]) / 2, P_WALL + P_CLR, P_FLOOR + P_STAND)
    top = o[2] + s["t"]
    return ox, oy, o, top, top + s["qwiic_h"] + 0.6, s["br"] + P_CLR + P_WALL


# --------------------------------------------------------------- helpers ----
def box(x0, y0, z0, x1, y1, z1):
    return Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


def cyl(x, y, z0, z1, d, seg=SEG):
    return Manifold.cylinder(z1 - z0, d / 2, circular_segments=seg).translate((x, y, z0))


def cone(x, y, z0, z1, d0, d1, seg=SEG):
    """A cylinder whose diameter goes from d0 at z0 to d1 at z1."""
    return Manifold.cylinder(z1 - z0, d0 / 2, d1 / 2, circular_segments=seg).translate((x, y, z0))


def union(parts):
    return Manifold.batch_boolean(parts, OpType.Add) if parts else Manifold()


def rbox(x0, y0, z0, x1, y1, z1, r):
    """Box with rounded vertical edges."""
    pts = [(x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)]
    return Manifold.batch_hull([cyl(x, y, z0, z1, 2 * r) for x, y in pts])


def cbox(x0, y0, z0, x1, y1, z1, r, bottom=0.0, top=0.0):
    """Rounded box with a 45 degree chamfer on the bottom and/or top outer edge."""
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


def port_y(y0, y1, x, z, wx, wz, r):
    """Rounded rectangle opening through a wall that faces y."""
    pts = [(x + sx * (wx / 2 - r), z + sz * (wz / 2 - r)) for sx in (-1, 1) for sz in (-1, 1)]
    return Manifold.batch_hull([wall_dot(px, y0, y1, pz, 2 * r) for px, pz in pts])


def rod_x(x0, x1, y, z, r):
    """A round bar along x."""
    return Manifold.cylinder(x1 - x0, r, circular_segments=32).rotate((0, 90, 0)).translate((x0, y, z))


def rod_y(y0, y1, x, z, r):
    """A round bar along y."""
    return Manifold.cylinder(y1 - y0, r, circular_segments=32).rotate((-90, 0, 0)).translate((x, y0, z))


def lip_ring(x0, y0, x1, y1, z0, z1, r, w):
    """A rounded rectangular wall of width w. Its outer face is the given box."""
    return rbox(x0, y0, z0, x1, y1, z1, r) - rbox(x0 + w, y0 + w, z0 - 1, x1 - w, y1 - w, z1 + 1, max(r - w, 0.2))


def bumps(x0, y0, x1, y1, xs, ys, length, z, r):
    """Round bars beside the inner faces of a box of walls x0..x1, y0..y1.

    Bars along x at each of xs on the two walls that face y, and bars along y at each of ys on
    the two walls that face x. With r = SNAP_R they are the bumps on a lip. With a slightly
    larger r they are the grooves in the walls. The centre sits inside the lip, so a bump
    stands SNAP past the lip face.
    """
    inset = LIP_CLR + SNAP_R - SNAP
    parts = [rod_x(x - length / 2, x + length / 2, y, z, r) for x in xs for y in (y0 + inset, y1 - inset)]
    parts += [rod_y(y - length / 2, y + length / 2, x, z, r) for y in ys for x in (x0 + inset, x1 - inset)]
    return union(parts)


def clamp_rod(x, y, pcb_top, z_top, d, hole_d):
    """A rod from under a cover down to just above a board, with a hole in its end for the pin."""
    z0 = pcb_top + CLAMP_GAP
    return cyl(x, y, z0, z_top, d, 40) - cyl(x, y, z0 - 0.1, z0 + BORE, hole_d - PIN_CLR + 0.4, 32)


def locating_pin(x, y, z0, pcb_top, hole_d, clr=PIN_CLR):
    return cyl(x, y, z0 - 0.01, pcb_top + PIN_ABOVE, hole_d - clr, 32)


# ------------------------------------------------------------------ text ----
_FONTS = {}
LABEL_FONT = ("DIN Alternate", "DejaVu Sans")
NAME_FONT = HERE / "fonts" / "Borel-Regular.ttf"   # cursive with one stroke width, SIL Open Font License


def _area(p):
    return 0.5 * float(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1]))


def printable(cs, bold, spaced):
    """Make the outline of a text fit for the nozzle. See MIN_GAP."""
    cs = cs.offset(bold, JoinType.Round, 2.0, 32) if bold else cs
    # Fill each hole that is narrower than MIN_GAP: it is gone after a shrink by half of that.
    keep = []
    for poly in cs.to_polygons():
        poly = np.asarray(poly)
        if _area(poly) > 0 or CrossSection([poly[::-1]]).offset(-MIN_GAP / 2, JoinType.Round, 2.0, 32).area() > 1e-6:
            keep.append(poly)
    cs = CrossSection(keep, FillRule.EvenOdd)
    if not spaced:
        return cs
    # Letters: pieces that stand over each other, like an "i" and its dot. Move each letter to
    # the right until it stands MIN_GAP from the letter before it. The true distance counts,
    # not the boxes: the arm of an "r" reaches over the next letter.
    letters = []
    for piece in sorted(cs.decompose(), key=lambda c: c.bounds()[0]):
        x0, _, x1, _ = piece.bounds()
        if letters and letters[-1][1] - x0 > 0.5 * min(x1 - x0, letters[-1][1] - letters[-1][0].bounds()[0]):
            letters[-1] = (letters[-1][0] + piece, max(letters[-1][1], x1))
        else:
            letters.append((piece, x1))
    placed, move = [], 0.0
    for letter, _ in letters:
        halo = letter.offset(MIN_GAP / 2, JoinType.Round, 2.0, 32)
        while placed and (placed[-1] ^ halo.translate((move, 0))).area() > 1e-6:
            move += 0.05
        placed.append(halo.translate((move, 0)))
        letters[len(placed) - 1] = letter.translate((move, 0))
    return CrossSection.batch_boolean(letters, OpType.Add)


@lru_cache(maxsize=None)
def outline(s, xh=LABEL_XH, family=LABEL_FONT, bold=LABEL_BOLD):
    """Flat outline of a lowercase label, baseline on y = 0, left end on x = 0. xh is the x-height in mm."""
    from matplotlib.font_manager import FontProperties
    from matplotlib.textpath import TextPath
    if family not in _FONTS:
        _FONTS[family] = (FontProperties(fname=str(family)) if isinstance(family, Path)
                          else FontProperties(family=list(family), weight="bold"))
    font = _FONTS[family]
    k = xh / TextPath((0, 0), "x", size=10, prop=font).get_extents().height
    polys = [np.asarray(p[:-1]) * k for p in TextPath((0, 0), s, size=10, prop=font).to_polygons()
             if len(p) > 3]
    cs = printable(CrossSection(polys, FillRule.EvenOdd), bold, spaced=family != NAME_FONT)  # the cursive letters join
    return cs.translate((-cs.bounds()[0], 0))


def label_width(s, xh=LABEL_XH, family=LABEL_FONT, bold=LABEL_BOLD):
    b = outline(s, xh, family, bold).bounds()
    return b[2] - b[0]


def text(s, x, y, z0, z1, xh=LABEL_XH, anchor="center", family=LABEL_FONT, bold=LABEL_BOLD):
    """Extruded label. y is the baseline. anchor says which end of the label stands at x."""
    cs = outline(s, xh, family, bold)
    w = cs.bounds()[2]
    dx = {"left": 0.0, "center": -w / 2, "right": -w}[anchor]
    return cs.extrude(z1 - z0).translate((x + dx, y, z0))


def name(s, x, y, z0, z1, anchor="left"):
    return text(s, x, y, z0, z1, xh=NAME_XH, anchor=anchor, family=NAME_FONT, bold=NAME_BOLD)


def arduino_outline():
    """The Arduino mark as a flat outline, centred on (0, 0). See ARD_R."""
    a = math.asin(ARD_R / ARD_C)                    # the straight lines touch the loops at this angle
    # The middle line: an arc around the right loop, a straight line through the middle, an arc
    # around the left loop, and a straight line back.
    right = [(ARD_C + ARD_R * math.cos(t), ARD_R * math.sin(t))
             for t in np.linspace(math.pi / 2 + a, -math.pi / 2 - a, 48)]
    line = right + [(-x, -y) for x, y in reversed(right)]
    dot = CrossSection.circle(ARD_W / 2, 24)
    parts = [CrossSection.batch_hull([dot.translate(p), dot.translate(q)]) for p, q in zip(line, line[1:] + line[:1])]
    n, w = ARD_SIGN
    parts += [CrossSection.square((n, w), True).translate((-ARD_C, 0)),
              CrossSection.square((n, w), True).translate((ARD_C, 0)),
              CrossSection.square((w, n), True).translate((ARD_C, 0))]
    return CrossSection.batch_boolean(parts, OpType.Add)


def arduino_mark(x, y, z0, z1):
    return arduino_outline().extrude(z1 - z0).translate((x, y, z0))


def stroke_and_gap(cs):
    """The thinnest stroke and the thinnest gap of a flat outline, to 0.02 mm.

    Stroke: one piece (a letter, a dot, a mark) loses a tenth of its area when all that is
    narrower goes away. Each piece counts alone, so a small thin letter beside large ones counts.
    Gap: two pieces join, or a hole closes or appears, when all gaps that are narrower fill up.
    """
    def grow(c, d):
        return c.offset(d, JoinType.Round, 2.0, 32)

    def shape(c):
        return sum(1 for p in c.to_polygons() if _area(np.asarray(p)) < 0), len(c.decompose())

    def opening(c, d):                     # mitred, so a square corner is not counted as a thin stroke
        return c.offset(-d / 2, JoinType.Miter, 10.0).offset(d / 2, JoinType.Miter, 10.0)

    form = shape(cs)
    stroke = min(next(d for d in np.arange(0.3, 40, 0.02) if opening(p, d).area() < 0.9 * p.area())
                 for p in cs.decompose()) - 0.02
    gap = next(g for g in np.arange(0.2, 5, 0.02) if shape(grow(grow(cs, g / 2), -g / 2)) != form) - 0.02
    return stroke, gap


@lru_cache(maxsize=None)
def logo_outline():
    """The Valio Aimo logo as a flat outline, LOGO_W wide plus LOGO_OFFSET, lower-left corner on (0, 0).

    The trace: the image is padded with white, its grey level is cut at 50 % along the pixel
    edges (marching squares, as matplotlib draws a contour), the contours become polygons with
    the even-odd rule (so each counter stays a hole), and points closer than 0.01 mm to the
    line through their neighbours are dropped. Nothing is redrawn.
    """
    import contourpy
    import matplotlib.image as mpimg
    img = mpimg.imread(str(LOGO_FILE)).astype(float)
    grey = img[..., :3].mean(axis=2) if img.ndim == 3 else img
    if img.ndim == 3 and img.shape[2] == 4:            # a transparent pixel counts as white
        grey = 1 - (1 - grey) * img[..., 3]
    pad = 4
    grey = np.pad(grey, pad, constant_values=1.0)
    h, w = grey.shape
    k = LOGO_W / (w - 2 * pad)
    polys = [np.column_stack([(c[:, 0] - pad) * k, (h - 1 - pad - c[:, 1]) * k])
             for c in contourpy.contour_generator(z=grey).lines(0.5) if len(c) > 3]
    cs = CrossSection(polys, FillRule.EvenOdd).simplify(0.01).offset(LOGO_OFFSET, JoinType.Round, 2.0, 32)
    b = cs.bounds()
    return cs.translate((-b[0], -b[1]))


def logo_valio():
    """The small capitals VALIO of the logo: the pieces in its upper right part (not the dot of the i)."""
    cs = logo_outline()
    _, _, right, top = cs.bounds()
    return [p for p in cs.decompose() if p.bounds()[1] > top / 2 and p.bounds()[0] > 0.6 * right]


def logo_valio_base():
    """Height of the baseline of VALIO above the bottom of the logo: the lowest point of the small capitals."""
    return min(p.bounds()[1] for p in logo_valio())


def foot_line():
    """Baseline of "limit": the baseline of VALIO in the logo."""
    return LOGO_Y0 + logo_valio_base()


def labels():
    """Every engraved text and mark: what, where, its flat outline and its depth."""
    out = [(s, "front cover", outline(s), ENGRAVE) for s in ("power", "alert", "limit")]
    out.append((NAME, "front cover", outline(NAME, NAME_XH, NAME_FONT, NAME_BOLD), ENGRAVE))
    out.append(("Valio Aimo logo", "front cover", logo_outline(), ENGRAVE))
    out.append((NAME_BASE, "right wall of the back shell", base_outline(), ENGRAVE))
    out += [(p["label"], f"{k} pod cover", outline(p["label"]), ENGRAVE) for k, p in PODS.items()]
    out.append(("Arduino mark", "front cover", arduino_outline(), ENGRAVE))
    out.append(("back mark", "back of the back shell", back_mark_outline(), ENGRAVE))
    return out


def text_report():
    """Measure every label and mark against the print rule. Return the table and the failures."""
    table, bad = [], []
    for s, where, cs, depth in labels():
        stroke, gap = stroke_and_gap(cs)
        table.append((s, where, stroke, gap, depth))
        if stroke < RULE_STROKE - 1e-9 or gap < RULE_GAP - 1e-9 or depth < RULE_DEPTH - 1e-9:
            bad.append(f"{s!r} on the {where}: stroke {stroke:.2f} (rule {RULE_STROKE}), gap {gap:.2f} "
                       f"(rule {RULE_GAP}), depth {depth:.2f} (rule {RULE_DEPTH})")
    return table, bad


# ------------------------------------------------------------ back shell ----
def boards_held():
    """Each board of the control band: its origin, holes, PCB top, standoff and hold-down rod diameter."""
    return [(UNO_O, UNO_HOLES, UNO_TOP, STANDOFF_D, UNO_ROD_D),
            (KNOB_O, MOD_HOLES, KNOB_O[2] + MOD_T, STANDOFF_D + 1.0, MOD_ROD_D),
            (BUZZ_O, MOD_HOLES, BUZZ_O[2] + MOD_T, STANDOFF_D + 1.0, MOD_ROD_D)]


def screen_hooks(ox, oy, z_floor, pcb_top):
    """Snap hooks at the top and the bottom edge of the screen PCB. They hold the board on its standoffs.

    Each hook is an arm that stands up from the floor beside the PCB edge, with a catch over the
    1.3 mm strip of PCB outside the glass and a 45 degree ramp above it. The board pushes the
    arms out as it goes down, and the catches close over it. The arms print upright, the catch
    is a 0.8 mm overhang.
    """
    zc = pcb_top + FIT
    parts = []
    for hx, side in SCR_HOOKS:
        edge = oy + (SCR_Y if side > 0 else 0.0)
        yf = edge + side * HOOK_GAP        # arm face toward the board
        ya = yf + side * HOOK_T            # arm back
        yt = yf - side * HOOK_LIP          # tip of the catch, over the PCB
        x0, x1 = ox + hx - HOOK_W / 2, ox + hx + HOOK_W / 2
        parts.append(box(x0, min(yf, ya), z_floor - 0.01, x1, max(yf, ya), zc + 0.4))
        parts.append(Manifold.batch_hull([
            box(x0, min(ya, yt), zc, x1, max(ya, yt), zc + 0.4),
            box(x0, min(ya, yf), zc + 0.4 + HOOK_LIP - 0.001, x1, max(ya, yf), zc + 0.4 + HOOK_LIP)]))
    return union(parts)


def keyhole(x):
    """A keyhole slot through the back: a round hole for the screw head, and a slot above it for the shank."""
    y0 = KEY_Y - KEY_TRAVEL
    slot = Manifold.batch_hull([cyl(x, y, -1, FLOOR + 1, KEY_SCREW_D + 0.6, 32) for y in (y0, KEY_Y)])
    return cyl(x, y0, -1, FLOOR + 1, KEY_HOLE_D, 48) + slot


def wall_screws(y=None):
    """The heads of the two wall screws inside the housing, for the fit. y: where they stand, KEY_Y at rest."""
    y = KEY_Y if y is None else y
    return union([cyl(x, y, FLOOR, FLOOR + KEY_HEAD_H, KEY_HEAD_D, 32) for x in KEY_XS])


def strip_fields(z0=-1.0):
    """The two shallow fields in the back for adhesive strips."""
    w, h, d = STRIP_FIELD
    return union([rbox(OX / 2 - w / 2, y - h / 2, z0, OX / 2 + w / 2, y + h / 2, d, 2.0) for y in STRIP_YS])


def card_opening():
    """An opening in the back under the mouth of the SD card socket TF1. Tweezers reach the card through it."""
    x0, _, x1, _, _ = SCR_TF
    return rbox(S_O[0] + x0 - SCR_CARD_ROOM, S_O[1] - 8.0, -1, S_O[0] + x1 + SCR_CARD_ROOM, S_O[1] + 6.0, FLOOR + 1, 2.0)


def sw1_openings():
    """A pen hole in the top wall at SW1, long along the edge, and a slot in the back over SW1."""
    x0, y0, x1, y1, h = SCR_SW1
    cx, w = S_O[0] + (x0 + x1) / 2, x1 - x0 + 12.0
    top = port_y(OY - WALL - 1, OY + 1, cx, S_O[2] - h / 2, w, SCR_SW1_HOLE, SCR_SW1_HOLE / 2 - 0.01)
    back = rbox(cx - w / 2, S_O[1] + y0 - 0.5, -1, cx + w / 2, S_O[1] + y1 + 0.5, FLOOR + 1, 1.5)
    return top + back


def base_outline():
    return outline(NAME_BASE, NAME_BASE_XH, NAME_FONT, NAME_BASE_BOLD)


SIDE_BAND = (EDGE + SIDE_MARGIN, Z_LID - SEAM - SIDE_MARGIN)   # z range for text on a side wall of the back shell


def chef_hat(width, line):
    """A chef's hat as an engraved line drawing: a puffy top of four circles on a band. Centred on x = 0, bottom on y = 0."""
    k = width / 16.0
    band = CrossSection.square((12.0 * k, 4.0 * k)).translate((-6.0 * k, 0))
    puff = CrossSection.batch_boolean([CrossSection.circle(r * k, 64).translate((x * k, y * k)) for x, y, r in
                                       ((-4.6, 8.0, 3.4), (4.6, 8.0, 3.4), (-1.9, 10.6, 3.6), (1.9, 10.6, 3.6))],
                                      OpType.Add)
    puff += CrossSection.square((11.0 * k, 4.0 * k)).translate((-5.5 * k, 4.0 * k))   # the puff sits on the band
    ring = puff - puff.offset(-line, JoinType.Round, 2.0, 32)
    ring = ring ^ CrossSection.square((width + 2, width)).translate((-width / 2 - 1, 4.0 * k + line))  # open at the band
    return CrossSection.batch_boolean([ring, band - band.offset(-line, JoinType.Miter, 10.0)], OpType.Add)


@lru_cache(maxsize=None)
def back_mark_outline():
    """The back mark as read from behind: the hat over the lines, all centred on x = 0, lowest point on y = 0."""
    parts = []
    n = len(BACK_LINES)
    for i, line in enumerate(BACK_LINES):
        cs = outline(line)
        parts.append(cs.translate((-cs.bounds()[2] / 2, (n - 1 - i) * BACK_PITCH)))
    top = max(p.bounds()[3] for p in parts)
    parts.append(chef_hat(*BACK_HAT).translate((0, top + 3.0)))
    cs = CrossSection.batch_boolean(parts, OpType.Add)
    return cs.translate((0, -cs.bounds()[1]))


def back_mark(depth=ENGRAVE):
    """The back mark in the back face, mirrored so it reads right from behind. It sits BACK_CLEAR above the low strip field."""
    cs = back_mark_outline().mirror((1, 0))
    y0 = STRIP_YS[0] + STRIP_FIELD[1] / 2 + BACK_CLEAR
    return cs.extrude(depth + 1).translate((OX / 2, y0, -1))


def side_name(depth=ENGRAVE):
    """NAME_BASE on the right wall, centred on it. It reads from bottom to top for a person to
    the right of the housing, with the tops of the letters toward the face."""
    cs = base_outline()
    b = cs.bounds()
    t = cs.extrude(depth + 1)
    z_base = (SIDE_BAND[0] + SIDE_BAND[1]) / 2 - (b[1] + b[3]) / 2
    m = [[0.0, 0.0, 1.0, OX - depth], [1.0, 0.0, 0.0, OY / 2 - (b[0] + b[2]) / 2], [0.0, 1.0, 0.0, z_base]]
    return t.transform(m)


def make_back():
    shell = cbox(0, 0, 0, OX, OY, Z_LID, CORNER_R, bottom=EDGE, top=SEAM)
    cavity = rbox(WALL, WALL, Z_IN, OX - WALL, OY - WALL, Z_LID + 1, CORNER_R - WALL)
    add, cut = [], []
    for origin, holes, pcb_top, standoff_d, _ in boards_held():
        for hx, hy in holes:
            x, y = origin[0] + hx, origin[1] + hy
            add.append(cyl(x, y, Z_IN - 0.01, origin[2], standoff_d))
            add.append(locating_pin(x, y, origin[2], pcb_top, HOLE_D))
    for hx, hy in SCR_HOLES:               # the screen: taller standoffs, thinner pins, and hooks
        x, y = S_O[0] + hx, S_O[1] + hy
        add.append(cyl(x, y, Z_IN - 0.01, S_O[2], STANDOFF_D + 0.6))
        add.append(locating_pin(x, y, S_O[2], S_TOP, SCR_HOLE_D, SCR_PIN_CLR))
    add.append(screen_hooks(S_O[0], S_O[1], Z_IN, S_TOP))
    back = (shell - cavity) + union(add)

    # The cable openings in the bottom wall, open toward the front. The cover tongues close them to a gap.
    for x0, x1, depth, _ in OPENINGS:
        cut.append(box(x0, -1, Z_LID - depth, x1, WALL + 1, Z_LID + 1))
    if PLUGS == "straight":                # an opening in the left wall at each USB-C port
        cut.append(port_x(-1, WALL + 1, UNO_O[1] + UNO_USB_Y, UNO_TOP + UNO_USB_Z, *UNO_PORT, 1.0))
        cut.append(port_x(-1, WALL + 1, S_O[1] + SCR_USB_Y, S_O[2] - SCR_USB_Z, *SCR_PORT, 1.0))
    # One row of vent dots in the bottom wall and one in the top wall. Warm air leaves at the top.
    for cx, y0, y1 in ((WIN_CX, -1, WALL + 1), (OX / 2, OY - WALL - 1, OY + 1)):
        for i in range(VENT_NX):
            cut.append(wall_dot(cx + (i - (VENT_NX - 1) / 2) * DOT_PITCH, y0, y1, Z_IN + 3.0, 1.8))
    # Grooves for the cover bumps on all four walls, and a finger notch at the top of each side wall.
    cut.append(bumps(WALL, WALL, OX - WALL, OY - WALL, SNAP_XS, SNAP_YS, SNAP_LEN + 1.0, Z_LID - LIP_H / 2,
                     SNAP_R + 0.05))
    for x0, x1 in ((-1, PRY_D), (OX - PRY_D, OX + 1)):
        cut.append(box(x0, OY / 2 - PRY_W / 2, Z_LID - PRY_H, x1, OY / 2 + PRY_W / 2, Z_LID + 1))
    # Wall mount: keyhole slots and the fields for adhesive strips.
    cut += [keyhole(x) for x in KEY_XS]
    cut.append(strip_fields())
    # A hole over each button of the screen, an opening behind its antenna, the SD card and SW1.
    for bx, by in (SCR_BOOT, SCR_RST):
        cut.append(cyl(S_O[0] + bx, S_O[1] + by, -1, FLOOR + 1, SCR_BTN_D, 32))
    a, b, c, d = SCR_ANT
    cut.append(rbox(S_O[0] + a, S_O[1] + b, -1, min(S_O[0] + c + 4.0, OX - WALL), S_O[1] + d, FLOOR + 1, 2.0))
    cut += [card_opening(), sw1_openings(), side_name(), back_mark()]
    return back - union(cut)


# ----------------------------------------------------------- front cover ----
# The front cover uses no estimated screen number. Each feature and the numbers it uses:
#   outline, lip, bumps, tongues  OX, OY, WALL, LIP_*, SNAP*, CORNER_R, EDGE, SEAM, OPENINGS (UNO Q data)
#   screen window                 SCR_AA (standard panel value), SCR_WIN_C, SCR_WIN_MARGIN, SCR_WIN_CHAMFER
#   rods                          UNO Q and Modulino holes (CAD data). The screen has no rod: hooks hold it
#   LED holes and block           UNO Q LED data, LED_*
#   knob well, button hole        Modulino and UNO Q data, KNOB_*, BTN_*
#   labels, logo, mark, vent      the rows above, X_L, LABEL_LINE, LOGO_* and the traced logo image
# check() proves it: it builds the cover again with every screen estimate changed and compares.
def screen_window(z0, z1):
    """The window over the active area. Its edge slopes outward at 45 degrees toward the front face z1."""
    cx, cy = SCR_WIN_C
    hx, hy, c = SCR_AA[0] / 2 + SCR_WIN_MARGIN, SCR_AA[1] / 2 + SCR_WIN_MARGIN, SCR_WIN_CHAMFER
    slope = Manifold.batch_hull([rbox(cx - hx, cy - hy, z1 - c, cx + hx, cy + hy, z1 - c + 0.001, 2.0),
                                 rbox(cx - hx - c, cy - hy - c, z1, cx + hx + c, cy + hy + c, z1 + 0.001, 2.0 + c)])
    return rbox(cx - hx, cy - hy, z0, cx + hx, cy + hy, z1 + 1, 2.0) + slope


def insert_rect(z, grow=0.0, bottom=None):
    """The inside of the insert sleeve at height z, as x0, y0, x1, y1, grown by `grow` on each side.

    The sleeve walls stand straight up to just above the low parts of the UNO Q (UNO_LOW), then
    lean out at 45 degrees until each reaches INSERT_LEAN. The side toward the JANALOG header has
    no wall: the plate ends LED_HEADER_CLR from the header, and the header body closes that side.
    """
    zf = UNO_TOP + UNO_LOW + 0.3
    dl, dr, dt = (min(max(z - zf, 0.0), lean) for lean in INSERT_LEAN)
    x0, y0, x1, y1 = HOLES_BOX
    m = INSERT_MARGIN
    y_open = UNO_O[1] + UNO_HEADERS[1][3] + LED_HEADER_CLR if bottom is None else bottom
    return x0 - m - dl - grow, y_open - grow, x1 + m + dr + grow, y1 + m + dt + grow


def slab(r, z0, z1):
    return box(r[0], r[1], z0, r[2], r[3], z1)


def make_led_insert():
    """The LED grille insert in its assembled place. Print it plate down: the holes and walls stand
    up, the walls lean out at 45 degrees, and nothing needs a support or a bridge."""
    z0 = UNO_TOP + UNO_LED_H + LED_GAP
    z1 = z0 + INSERT_T
    zf = UNO_TOP + UNO_LOW + 0.3
    w = INSERT_WALL
    levels = sorted({z1, zf} | {zf + lean for lean in INSERT_LEAN} | {Z_LID})
    y_open = insert_rect(z1)[1]

    def body(grow, below):
        """The sleeve outline grown by `grow`. It is straight, then leans out, so it is not convex:
        each band between two levels is a hull of its own."""
        def at(z):
            x0, _, x1, y1 = insert_rect(z, grow)
            return box(x0, y_open - below, z, x1, y1, z + 0.001)
        return union([Manifold.batch_hull([at(za), at(zb)]) for za, zb in zip(levels, levels[1:])])
    sleeve = body(w, 0.0) - body(0.0, 1.0)
    outer, inner = insert_rect(Z_LID, w), insert_rect(Z_LID)
    rim = (slab((outer[0], y_open - w, outer[2], outer[3]), Z_LID, Z_LID + INSERT_POCKET)
           - slab((inner[0], y_open - 1, inner[2], inner[3]), Z_LID - 1, Z_LID + INSERT_POCKET + 1))
    px0, _, px1, py1 = insert_rect(z1, w)
    plate = box(px0, y_open, z0, px1, py1, z1)
    holes = [cyl(x, y, z0 - 1, z1 + 1, LED_HOLE_D, 24) for x, y in LED_XY]
    # The bottom row comes too close to the plate edge for a wall, so those holes open to the edge.
    holes += [box(x - LED_HOLE_D / 2, y_open - 1, z0 - 1, x + LED_HOLE_D / 2, y, z1 + 1)
              for x, y in LED_XY if y - LED_HOLE_D / 2 - y_open < 0.8]
    return union([plate - union(holes), sleeve, rim])


def led_parts():
    """What the LED face adds under the cover, and what it cuts through it.

    "insert": nothing is added. The cut is the pocket for the rim of the insert and one window
    that continues the 45 degree sleeve walls to the face.
    "grid": the block reaches down to LED_GAP above the LEDs. On the side of the JANALOG header it
    stops LED_HEADER_CLR from the header. There the wall is too thin to print, so the outer
    channels stay open on that side and the black header body closes them.
    "window": a collar around one opening.
    """
    z_low = UNO_TOP + UNO_LED_H + LED_GAP
    header_y = UNO_O[1] + UNO_HEADERS[1][3] + LED_HEADER_CLR
    zc1 = Z_TOP + 1
    if LED_FACE == "insert":
        w, p = INSERT_WALL, INSERT_PRESS
        outer, inner = insert_rect(Z_LID, w), insert_rect(Z_LID)
        pocket = slab((outer[0] - p, inner[1] - w - p, outer[2] + p, outer[3] + p), Z_LID - 0.01, Z_LID + INSERT_POCKET)
        g = Z_TOP - (Z_LID + INSERT_POCKET)
        window = Manifold.batch_hull([slab(inner, Z_LID + INSERT_POCKET - 0.01, Z_LID + INSERT_POCKET),
                                      slab((inner[0] - g, inner[1] - g, inner[2] + g, inner[3] + g), Z_TOP, Z_TOP + 0.001)])
        return Manifold(), pocket + window
    if LED_FACE == "grid":
        x0, y0, x1, y1 = HOLES_BOX
        block = box(x0 - LED_WALL, max(y0 - LED_WALL, header_y), z_low, x1 + LED_WALL, y1 + LED_WALL, Z_LID + 0.01)
        holes = [cyl(x, y, z_low - 1, Z_TOP - LED_SKIN if LED_SKIN else zc1, LED_HOLE_D, 24) for x, y in LED_XY]
        open_side = [box(x - LED_HOLE_D / 2, header_y - 1, z_low - 1, x + LED_HOLE_D / 2, y, Z_LID)
                     for x, y in LED_XY if y - LED_HOLE_D / 2 - header_y < 0.8]
        return block, union(holes + open_side)
    m = LED_WIN_MARGIN
    x0, y0, x1, y1 = FIELD[0] - m, FIELD[1] - m, FIELD[2] + m, FIELD[3] + m
    w = LED_WALL
    collar = box(x0 - w, max(y0 - w, header_y), z_low, x1 + w, y1 + w, Z_LID + 0.01)
    opening = box(x0, header_y - 1, z_low - 1, x1, y1, Z_LID) + rbox(x0, y0, z_low - 1, x1, y1, zc1, 0.5)
    mouth = Manifold.batch_hull([rbox(x0, y0, Z_TOP - 0.6, x1, y1, Z_TOP - 0.599, 0.5),
                                 rbox(x0 - 0.6, y0 - 0.6, Z_TOP, x1 + 0.6, y1 + 0.6, Z_TOP + 0.001, 1.1)])
    return collar, opening + mouth


def led_face_right():
    """The right edge of the LED feature on the face."""
    if LED_FACE == "insert":
        return insert_rect(Z_LID)[2] + Z_TOP - (Z_LID + INSERT_POCKET)
    if LED_FACE == "window":
        return FIELD[2] + LED_WIN_MARGIN + 0.6
    return HOLES_BOX[2]


def led_face_bottom():
    """The lower edge of the LED feature on the face."""
    if LED_FACE == "insert":
        return insert_rect(Z_LID)[1] - (Z_TOP - (Z_LID + INSERT_POCKET))
    if LED_FACE == "window":
        return FIELD[1] - LED_WIN_MARGIN - 0.6
    return HOLES_BOX[1]


def led_view_angles():
    """Viewing angles of the "insert" window, in degrees from straight on.

    edge: an outer hole, toward its own side. The straight part of the sleeve wall above the plate
    limits it. header: a hole of the bottom row, toward the JANALOG header. centre: the middle of
    the field, sideways and upward. The edge of the window at the face limits it.
    """
    z1 = UNO_TOP + UNO_LED_H + LED_GAP + INSERT_T
    zf = UNO_TOP + UNO_LOW + 0.3
    r = LED_HOLE_D / 2 + INSERT_MARGIN
    low_y = min(y for _, y in LED_XY)
    header = (low_y - (UNO_O[1] + UNO_HEADERS[1][3])) / (UNO_TOP + UNO_HEADER_H - z1)
    face = insert_rect(Z_LID)
    g = Z_TOP - (Z_LID + INSERT_POCKET)
    cx, cy = (HOLES_BOX[0] + HOLES_BOX[2]) / 2, (HOLES_BOX[1] + HOLES_BOX[3]) / 2
    side = (face[2] + g - cx) / (Z_TOP - z1)
    up = (face[3] + g - cy) / (Z_TOP - z1)
    return {k: math.degrees(math.atan(v)) for k, v in
            (("edge", r / (zf - z1)), ("header", header), ("centre sideways", side), ("centre upward", up))}


def knob_well():
    """The round well for the knob cap: a 45 degree rim, the well, and the bridging steps around the shaft hole."""
    kx, ky = KNOB_XY
    well_d, hole_d = KNOB_D + 2 * KNOB_GAP, ENC_BUSH_D + 0.7
    zr, zc1 = Z_TOP - KNOB_RECESS, Z_TOP + 1
    rim = cone(kx, ky, Z_TOP - KNOB_RIM, Z_TOP + 0.001, well_d, well_d + 2 * KNOB_RIM - 0.002)
    # The cover prints with this face on the bed, so the well floor is a bridge with a hole in
    # it. Two steps let the printer lay straight lines there: first a slot as wide as the hole,
    # one layer on a square, then the round hole.
    slot = box(kx - well_d, ky - hole_d / 2, zr - LAYER, kx + well_d, ky + hole_d / 2, zr + 0.01)
    return union([cyl(kx, ky, zr, zc1, well_d), rim, slot ^ cyl(kx, ky, zr - LAYER, zr + 0.01, well_d),
                  box(kx - hole_d / 2, ky - hole_d / 2, zr - 2 * LAYER, kx + hole_d / 2, ky + hole_d / 2, zr),
                  cyl(kx, ky, Z_LID - KNOB_BOSS - 1, zc1, hole_d)])


def cover_rods():
    return union([clamp_rod(origin[0] + hx, origin[1] + hy, pcb_top, Z_LID + 0.01, rod_d, HOLE_D)
                  for origin, holes, pcb_top, _, rod_d in boards_held() for hx, hy in holes])


def make_front():
    front = cbox(0, 0, Z_LID, OX, OY, Z_TOP, CORNER_R, bottom=SEAM, top=EDGE)

    # Snap lip: one closed ring inside the walls, open only at the cable openings, with bumps on all four sides.
    lo = WALL + LIP_CLR
    ring = lip_ring(lo, lo, OX - lo, OY - lo, Z_LID - LIP_H, Z_LID + 0.01, CORNER_R - WALL - LIP_CLR, LIP_W)
    ring += bumps(WALL, WALL, OX - WALL, OY - WALL, SNAP_XS, SNAP_YS, SNAP_LEN, Z_LID - LIP_H / 2, SNAP_R) ^ box(
        lo - 1, lo - 1, Z_LID - LIP_H, OX - lo + 1, OY - lo + 1, Z_LID)
    tongues = []
    for x0, x1, depth, gap in OPENINGS:
        ring -= box(x0 - 0.5, 0, Z_LID - LIP_H - 1, x1 + 0.5, WALL + LIP_W + 1, Z_LID)
        tongues.append(box(x0 + TONGUE_CLR, 0, Z_LID - depth + gap, x1 - TONGUE_CLR, WALL, Z_LID + 0.01))
    kx, ky = KNOB_XY
    boss = cyl(kx, ky, Z_LID - KNOB_BOSS, Z_LID + 0.01, 2 * KNOB_RING_R + 3.0)   # keeps the cover thick under the well
    led_block, led_cut = led_parts()
    front = front + ring + union(tongues) + cover_rods() + boss + led_block

    zc0, zc1 = Z_LID - KNOB_BOSS - 0.5, Z_TOP + 1   # through-cut range
    ze = Z_TOP - ENGRAVE                            # engraving floor
    cut = [screen_window(zc0, Z_TOP), led_cut, knob_well()]

    # Upper row: power button, vent, status LEDs, speaker grille. "power" and "alert" share one baseline.
    bx, by = BTN_XY
    cut.append(cyl(bx, by, zc0, zc1, BTN_HOLE, 48))
    cut.append(cone(bx, by, Z_TOP - BTN_EDGE, Z_TOP + 0.001, BTN_HOLE, BTN_HOLE + 2 * BTN_EDGE, 48))
    cut.append(text("power", X_L, LABEL_LINE, ze, zc1, anchor="left"))
    cut.append(text("alert", kx, LABEL_LINE, ze, zc1))
    cut.append(dots(WIN_CX, ROW_GRILLE, VENT_NX, 4, zc0, zc1))
    cut.append(dots(kx, ROW_GRILLE, 5, 4, zc0, zc1))
    # One slot over the four status LEDs. Four holes that close together leave walls too thin to print.
    led_x, led_ys = UNO_O[0] + UNO_RGB[0][0], [UNO_O[1] + ly for _, ly in UNO_RGB]
    cut.append(Manifold.batch_hull([cyl(led_x, y, zc0, zc1, 1.8, 24) for y in (min(led_ys), max(led_ys))]))

    # Lower row: LED matrix, Arduino mark, knob. The mark stands halfway between the LED holes and the ring.
    cut.append(arduino_mark((led_face_right() + kx - KNOB_RING_R) / 2, ROW_FRONT, ze, zc1))
    # The name above the screen and the challenge partner in the foot, both on the left axis X_L.
    # "en place" runs along the right wall (see side_name).
    b = outline(NAME, NAME_XH, NAME_FONT, NAME_BOLD).bounds()
    cut.append(name(NAME, X_L, (WIN_TOP + OY) / 2 - (b[1] + b[3]) / 2, ze, zc1))
    cut.append(logo_outline().extrude(zc1 - ze).translate((X_L, LOGO_Y0, ze)))
    cut.append(text("limit", kx, foot_line(), ze, zc1))
    return front - union(cut)


# -------------------------------------------------------------- knob cap ----
def make_knob():
    """A cap for the encoder shaft, in its assembled place. Print it with its top face on the bed.

    The bore has three steps, each wider toward the open end, so it needs no support: a D for
    the flat part of the shaft, a round part, and room for the bushing. A slit behind the flat
    lets that wall bend, so the cap holds on a shaft that is a little thick or thin.
    """
    kx, ky = KNOB_XY
    r, c = KNOB_D / 2, 0.8
    body = Manifold.batch_hull([cyl(kx, ky, KNOB_Z0, KNOB_Z0 + 0.001, KNOB_D - 0.8),
                                cyl(kx, ky, KNOB_Z0 + 0.4, KNOB_Z1 - c, KNOB_D),
                                cyl(kx, ky, KNOB_Z1 - 0.001, KNOB_Z1, KNOB_D - 2 * c)])
    n = 30                                          # grip grooves. They stop below the top edge
    grooves = [cyl(kx + (r + 0.15) * math.cos(a), ky + (r + 0.15) * math.sin(a), KNOB_Z0 - 1, KNOB_Z1 - 2.4, 1.1, 16)
               for a in (2 * math.pi * i / n for i in range(n))]
    z_flat = KNOB_TOP + ENC_FLAT_FROM + 0.3         # the D starts just above the start of the flat
    y_flat = ky + ENC_FLAT_OFFSET
    bore = [cyl(kx, ky, KNOB_Z0 - 1, KNOB_TOP + ENC_BUSH_TOP + 1.0, ENC_BUSH_D + 1.4),
            cyl(kx, ky, KNOB_Z0 - 1, z_flat, ENC_SHAFT_D + SHAFT_CLR + 0.1),
            cyl(kx, ky, KNOB_Z0 - 1, SHAFT_TIP, ENC_SHAFT_D + SHAFT_CLR) ^ box(kx - 5, ky - 5, KNOB_Z0 - 1, kx + 5, y_flat, SHAFT_TIP),
            box(kx - 3.2, y_flat + 0.9, KNOB_Z0 - 1, kx + 3.2, y_flat + 1.7, SHAFT_TIP)]
    return body - union(grooves + bore)


# ------------------------------------------------------------ button cap ----
def make_button():
    """The cap over the UNO Q power button, at rest. Print it with its top face on the bed.

    From the top: a stem through the cover hole, a 45 degree cone that widens to the flange
    under the cover, the flange, and a thin pusher down to the button. At rest the cone sits on
    the lower edge of the hole, so the cap cannot fall out to the front. Printed top down, the
    cone and the pusher need no support.
    """
    x, y = BTN_XY
    rh, rs, rf = BTN_HOLE / 2, BTN_D / 2, BTN_FLANGE / 2
    z_cone_top = Z_LID + (rh - rs)                  # the cone has the hole radius at Z_LID
    z_cone_low = Z_LID - (rf - rh)
    z_flange = z_cone_low - BTN_FLANGE_T
    stem = Manifold.batch_hull([cyl(x, y, z_cone_top, BTN_Z1 - BTN_EDGE, BTN_D, 48),
                                cyl(x, y, BTN_Z1 - 0.001, BTN_Z1, BTN_D - 2 * BTN_EDGE, 48)])
    return union([stem, cone(x, y, z_cone_low, z_cone_top + 0.001, BTN_FLANGE, BTN_D, 48),
                  cyl(x, y, z_flange, z_cone_low + 0.001, BTN_FLANGE, 48),
                  cyl(x, y, BTN_Z0, z_flange + 0.01, BTN_PUSH_D, 32)])


# ------------------------------------------------------------- sensor pods ----
def tof_window(cx, cy, z_chip_top, z0, z1):
    """The window over the VL53L5CX. It widens upward at TOF_VIEW, so its edge stays out of the view."""
    k = math.tan(math.radians(TOF_VIEW))

    def outline_at(z):
        d = (z - z_chip_top) * k
        return box(cx - TOF_CHIP[0] / 2 - d, cy - TOF_CHIP[1] / 2 - d, z,
                   cx + TOF_CHIP[0] / 2 + d, cy + TOF_CHIP[1] / 2 + d, z + 0.001)
    return Manifold.batch_hull([outline_at(z0), outline_at(z1)])


def make_pod_base(kind):
    s = PODS[kind]
    ox, oy, o, top, h, r = pod_geom(s)
    pod = cbox(0, 0, 0, ox, oy, h, r, bottom=P_EDGE, top=SEAM) - rbox(P_WALL, P_WALL, P_FLOOR, ox - P_WALL, oy - P_WALL, h + 1, r - P_WALL)
    cx, cy = ox / 2, o[1] + s["centre"][1]
    tw, reach, hole = TAB
    tab = rbox(cx - tw / 2, oy - 6, 0, cx + tw / 2, oy + reach, P_FLOOR, tw / 2 - 0.1) - cyl(cx, oy + 3.2, -1, P_FLOOR + 1, hole)
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
    # A slot through the floor under each soldered pin header. The pins come out below the pod.
    for a, b, c, d in s.get("pins", []):
        cut.append(box(o[0] + a - PIN_SLOT_CLR, o[1] + b - PIN_SLOT_CLR, -1,
                       o[0] + c + PIN_SLOT_CLR, o[1] + d + PIN_SLOT_CLR, o[2] + 0.01))
    cut.append(bumps(P_WALL, P_WALL, ox - P_WALL, oy - P_WALL, (ox / 2,), (), P_SNAP_LEN + 1.0, h - P_LIP_H / 2,
                     SNAP_R + 0.05))
    cut.append(box(ox / 2 - 5, -1, h - 1.2, ox / 2 + 5, 0.8, h + 1))   # finger notch in the front wall
    return (pod + union(add)) - union(cut)


def make_pod_cover(kind):
    s = PODS[kind]
    ox, oy, o, top, h, r = pod_geom(s)
    z0, z1 = h, h + P_COVER
    cover = cbox(0, 0, z0, ox, oy, z1, r, bottom=SEAM, top=P_EDGE)
    cx, cy = o[0] + s["centre"][0], o[1] + s["centre"][1]
    lo = P_WALL + LIP_CLR
    ring = lip_ring(lo, lo, ox - lo, oy - lo, z0 - P_LIP_H, z0 + 0.01, max(r - P_WALL - LIP_CLR, 0.3), P_LIP_W)
    ring += bumps(P_WALL, P_WALL, ox - P_WALL, oy - P_WALL, (ox / 2,), (), P_SNAP_LEN, z0 - P_LIP_H / 2, SNAP_R) ^ box(
        lo - 1, lo - 1, z0 - P_LIP_H, ox - lo + 1, oy - lo + 1, z0)
    for x0, x1 in ((-1, lo + P_LIP_W + 0.5), (ox - lo - P_LIP_W - 0.5, ox + 1)):
        ring -= box(x0, cy - 4.1, z0 - P_LIP_H - 1, x1, cy + 4.1, z0)
    rods = [clamp_rod(o[0] + hx, o[1] + hy, top, z0 + 0.01, s["rod_d"], s["hole_d"]) for hx, hy in s["holes"]]
    cover = cover + ring + union(rods)

    cut = []
    b = outline(s["label"]).bounds()
    if kind == "thermo":   # the grille over the sensor, the label above it
        cut.append(dots(cx, cy - DOT_PITCH / 2, 9, 4, z0 - 1, z1 + 1))
        cut.append(text(s["label"], ox / 2, oy - P_EDGE - P_LABEL_ROOM - b[3], z1 - ENGRAVE, z1 + 1))
    else:                  # the window over the sensor, the label below it
        cut.append(tof_window(cx, cy, top + TOF_CHIP[2], z0 - 0.5, z1 + 0.5))
        cut.append(text(s["label"], ox / 2, P_EDGE + P_LABEL_ROOM - b[1], z1 - ENGRAVE, z1 + 1))
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


def uno_button():
    x, y = BTN_XY
    return cyl(x, y, UNO_TOP - 0.01, UNO_TOP + UNO_BUTTON_H, 3.0, 32)


def uno_q():
    """The UNO Q as one solid for the fit: the board with its low parts as a block, and the connectors.

    The block is UNO_LOW high. It leaves free each mounting hole and a ring around it, where a
    screw head stood in the first version of the case: the hold-down rods stand there. Over the
    LED field and UNO_LED_KEEP around it, the block is only as high as the LEDs. Over the power
    button it leaves room for the pusher of the button cap, and the button stands there.
    """
    ox, oy, oz = UNO_O
    block = box(ox, oy, oz, ox + UNO_X, oy + UNO_Y, UNO_TOP + UNO_LOW)
    for hx, hy in UNO_HOLES:
        block -= cyl(ox + hx, oy + hy, oz - 1, UNO_TOP + 0.001, HOLE_D, 32)
        block -= cyl(ox + hx, oy + hy, UNO_TOP, UNO_TOP + 5, UNO_ROD_D + 0.4, 40)
    k = UNO_LED_KEEP
    block -= box(FIELD[0] - k, FIELD[1] - k, UNO_TOP + UNO_LED_H, FIELD[2] + k, FIELD[3] + k, UNO_TOP + 5)
    block -= cyl(*BTN_XY, UNO_TOP + 0.001, UNO_TOP + 5, BTN_PUSH_D + 1.0, 32)
    return union([block, uno_button()] + uno_q_connectors())


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
    for a, b, c, d in TOF_PINS:                     # header body under the board, pins through it
        parts.append(box(ox + a, oy + b, oz - PIN_BODY_H, ox + c, oy + d, oz))
        for i in range(round((c - a) / 2.54)):
            px, py = ox + a + 1.27 + 2.54 * i, oy + (b + d) / 2
            parts.append(box(px - 0.32, py - 0.32, oz - PIN_BODY_H - PIN_BELOW, px + 0.32, py + 0.32, oz))
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
    """The UNO Q as two solids for pictures: the bare board, and its headers, connectors, LEDs and chips.

    Headers, the USB-C port, the button and the 104 LEDs sit where the CAD data puts them. The
    two chips are drawn for the look only: their place and size are not from the CAD data.
    """
    ox, oy, oz = UNO_O
    pcb = box(ox, oy, oz, ox + UNO_X, oy + UNO_Y, UNO_TOP)
    for hx, hy in UNO_HOLES:
        pcb -= cyl(ox + hx, oy + hy, oz - 1, UNO_TOP + 1, 3.2, 32)
    parts = uno_q_connectors()
    parts += [box(x - 0.5, y - 0.25, UNO_TOP, x + 0.5, y + 0.25, UNO_TOP + UNO_LED_H) for x, y in LED_XY]
    parts.append(uno_button())
    parts += [box(ox + 30, oy + 26, UNO_TOP, ox + 44, oy + 40, UNO_TOP + 1.4),   # chips, for the look only
              box(ox + 48, oy + 30, UNO_TOP, ox + 56, oy + 38, UNO_TOP + 1.0)]
    return pcb, union(parts)


# The limit page of the sketch, for the pictures: "25" and the four page dots, top row first.
LED_PICTURE = ["...111.111...",
               ".....1.1.....",
               "...111.111...",
               "...1.....1...",
               "...111.111...",
               ".............",
               ".............",
               "...1.1.1.1..."]


def uno_leds():
    """The lit LEDs of LED_PICTURE, for the assembled picture."""
    lit = [(i, len(LED_PICTURE) - 1 - j) for j, row in enumerate(LED_PICTURE) for i, c in enumerate(row) if c == "1"]
    return union([box(x - 0.45, y - 0.3, UNO_TOP + UNO_LED_H - 0.1, x + 0.45, y + 0.3, UNO_TOP + UNO_LED_H)
                  for i, j in lit for x, y in [LED_XY[j * UNO_LED_N[0] + i]]])


def screen_back_parts():
    """The parts on the back of the screen that the model names, as x0, y0, x1, y1, height."""
    back = [SCR_ESP, (SCR_BODY[0], SCR_USB_Y - 4.6, 14.6, SCR_USB_Y + 4.6, 2 * SCR_USB_Z),   # USB-C port USB1
            SCR_P1, SCR_LCD1, SCR_P6, SCR_P7, SCR_TF, SCR_SW1] + list(SCR_SOCKETS)
    back += [(x - 1.7, y - 2.5, x + 1.7, y + 2.5, 1.8) for x, y in (SCR_BOOT, SCR_RST)]
    return back


def screen_pcb_outline():
    """The PCB outline as a flat shape: the body and four round ears around the holes."""
    b0, b1 = SCR_BODY
    body = CrossSection.square((b1 - b0, SCR_Y)).translate((b0, 0))
    ears = []
    for hx, hy in SCR_HOLES:
        r = min(hx, SCR_X - hx)                     # the ear tip is the end of the outline
        edge = b0 if hx < SCR_X / 2 else b1
        y0, y1 = (0.0, SCR_EAR_H) if hy < SCR_Y / 2 else (SCR_Y - SCR_EAR_H, SCR_Y)
        ears.append(CrossSection.batch_hull([CrossSection.circle(r, 48).translate((hx, hy)),
                                             CrossSection.square((abs(edge - hx), y1 - y0)).translate((min(hx, edge), y0))]))
    shape = CrossSection.batch_boolean([body] + ears, OpType.Add)
    return shape - CrossSection.batch_boolean([CrossSection.circle(SCR_HOLE_D / 2, 32).translate(h) for h in SCR_HOLES],
                                              OpType.Add)


def screen_solid(dx=0.0, dy=0.0, glass_extra=0.0):
    """The screen as one solid for the fit: the PCB, the display, the named parts, and a block for the rest.

    The block covers the PCB body down to SCR_BACK_H, so every unnamed part on the back counts.
    dx and dy move the board, glass_extra makes the glass thicker, for the checks at the limits.
    """
    ox, oy, oz = S_O[0] + dx, S_O[1] + dy, S_O[2]
    top = oz + SCR_T
    pcb = screen_pcb_outline().extrude(SCR_T).translate((ox, oy, oz))
    block = box(ox + SCR_BODY[0], oy, oz - SCR_BACK_H, ox + SCR_BODY[1], oy + SCR_Y, oz + 0.01)
    parts = [box(ox + a, oy + b, oz - h, ox + c, oy + d, oz + 0.01) for a, b, c, d, h in screen_back_parts()]
    cx, cy = ox + SCR_X / 2 + SCR_LCD_OFF[0], oy + SCR_Y / 2 + SCR_LCD_OFF[1]
    glass = box(cx - SCR_LCD[0] / 2, cy - SCR_LCD[1] / 2, top - 0.01, cx + SCR_LCD[0] / 2, cy + SCR_LCD[1] / 2,
                top + SCR_LCD_H + glass_extra)
    return union([pcb, block, glass] + parts)


def screen_look():
    """The screen as four solids for pictures: the PCB, the parts on its back, the display and the active area."""
    ox, oy, oz = S_O
    pcb = screen_pcb_outline().extrude(SCR_T).translate((ox, oy, oz))
    parts = union([box(ox + a, oy + b, oz - h, ox + c, oy + d, oz) for a, b, c, d, h in screen_back_parts()])
    cx, cy = ox + SCR_X / 2 + SCR_LCD_OFF[0], oy + SCR_Y / 2 + SCR_LCD_OFF[1]
    z = S_TOP + SCR_LCD_H
    lcd = box(cx - SCR_LCD[0] / 2, cy - SCR_LCD[1] / 2, S_TOP, cx + SCR_LCD[0] / 2, cy + SCR_LCD[1] / 2, z)
    ax, ay = ox + SCR_X / 2 + SCR_AA_OFF[0], oy + SCR_Y / 2 + SCR_AA_OFF[1]
    picture = box(ax - SCR_AA[0] / 2, ay - SCR_AA[1] / 2, z, ax + SCR_AA[0] / 2, ay + SCR_AA[1] / 2, z + 0.1)
    return pcb, parts, lcd, picture


def plugs():
    """Each straight USB-C plug as a block, pushed into its port through the left wall. For the fit."""
    if PLUGS != "straight":
        return {}
    w, t, n = PLUG

    def block(x, y, z):
        return box(x - n, y - w / 2, z - t / 2, x, y + w / 2, z + t / 2)
    return {"UNO Q plug": block(UNO_O[0] + UNO_USB_FACE, UNO_O[1] + UNO_USB_Y, UNO_TOP + UNO_USB_Z),
            "screen plug": block(S_O[0] - SCR_USB_FACE, S_O[1] + SCR_USB_Y, S_O[2] - SCR_USB_Z)}


def boards():
    return {"UNO Q": uno_q(), "Knob": modulino(KNOB_O, "knob"), "Buzzer": modulino(BUZZ_O, "buzzer"),
            "Screen": screen_solid()}


def pod_board(kind):
    o = pod_geom(PODS[kind])[2]
    return modulino(o, "thermo") if kind == "thermo" else union(tof_look(o))


# --------------------------------------------------------------- checks ----
def cover_ignores_screen_estimates(front):
    """Build the cover again with every screen estimate changed. It must not change."""
    names = ("SCR_X", "SCR_Y", "SCR_BODY", "SCR_EAR_H", "SCR_HOLE_DX", "SCR_HOLE_DY", "SCR_T", "SCR_BACK_H",
             "SCR_LCD_H", "SCR_LCD", "SCR_LCD_OFF", "SCR_AA_OFF", "SCR_USB_Y", "SCR_USB_Z", "SCR_USB_FACE",
             "SCR_BOOT", "SCR_RST", "SCR_ANT", "SCR_ESP", "SCR_P1", "SCR_LCD1", "SCR_P6", "SCR_P7", "SCR_TF",
             "SCR_SOCKETS", "SCR_SW1", "SCR_HOOKS")

    def nudge(v):
        if isinstance(v, (int, float)):
            return v + 1.5
        if isinstance(v, list):
            return [nudge(e) for e in v]
        return tuple(nudge(e) for e in v)
    g = globals()
    saved = {k: g[k] for k in names}
    try:
        for k in names:
            g[k] = nudge(g[k])
        place_screen()
        other = make_front()
    finally:
        g.update(saved)
        place_screen()
    return (front - other).volume() + (other - front).volume()


def check(back, front, knob, button, insert, pods):
    """Run every test. Return a list of (group, test, passed, detail)."""
    out = []

    def test(group, what, ok, detail=""):
        out.append((group, what, bool(ok), detail))

    def vol(a, b):
        return (a ^ b).volume()

    housing = back + front + knob + button + insert
    bds = boards()
    # Boards, wall screws, plugs.
    for name_, g in bds.items():
        v = vol(housing, g)
        test("fit", f"{name_} touches no printed part", v < 0.05, f"{v:.2f} mm3")
        v = vol(wall_screws(), g)
        test("mount", f"wall screw heads clear the {name_}", v < 0.05, f"{v:.2f} mm3")
    fixed = [back + front] + list(bds.values())
    for name_, plug in plugs().items():
        v = sum(vol(plug, g) for g in fixed + [q for other, q in plugs().items() if other != name_])
        test("fit", f"{name_} goes in", v < 0.05, f"{v:.2f} mm3")
    for what, a, b in (("cover in back shell", back, front), ("knob cap in cover", front, knob),
                       ("button cap in cover", front, button), ("button cap and knob cap", button, knob),
                       ("button cap in back shell", back, button)):
        v = vol(a, b)
        test("fit", what, v < 0.05, f"{v:.2f} mm3")

    # The screen: held by the back shell alone, and clear of the cover at the limits.
    picture = screen_look()[3]
    cover_parts = front + knob + button
    worst_v, worst_view = 0.0, 0.0
    for dx in (-SCR_SHIFT, 0.0, SCR_SHIFT):
        for dy in (-SCR_SHIFT, 0.0, SCR_SHIFT):
            worst_v = max(worst_v, vol(cover_parts, screen_solid(dx, dy, SCR_GLASS_EXTRA)))
            p = picture.translate((dx, dy, 0))
            view = Manifold.batch_hull([p, p.translate((0, 0, Z_TOP + 5 - p.bounding_box()[5]))])
            worst_view = max(worst_view, vol(front, view))
    test("screen", f"cover clears the screen with the board {SCR_SHIFT} mm off and the glass {SCR_GLASS_EXTRA} mm thicker",
         worst_v < 0.05, f"worst {worst_v:.2f} mm3")
    test("screen", f"whole picture visible with the board {SCR_SHIFT} mm off", worst_view < 0.05, f"worst {worst_view:.2f} mm3")
    hooks = screen_hooks(S_O[0], S_O[1], Z_IN, S_TOP)
    v = vol(hooks, screen_solid(0, 0, 0).translate((0, 0, FIT + 0.05)))
    test("screen", "hooks of the back shell hold the screen down", v > 0.05, f"{v:.2f} mm3 of catch over the PCB")
    v = cover_ignores_screen_estimates(front)
    test("screen", "front cover unchanged when every screen estimate changes by 1.5", v < 1e-6, f"{v:.4f} mm3")
    x0 = S_O[0] + min(s[0] for s in SCR_SOCKETS) - 2.0
    room = box(x0, S_O[1] - SCR_CABLE_ROOM, FLOOR + 0.5, CABLE_X[1], S_O[1], S_O[2])
    v = vol(room, back + front + bds["UNO Q"] + bds["Knob"] + bds["Buzzer"] + wall_screws())
    test("screen", f"{SCR_CABLE_ROOM} mm of free room under P2 to P5 to the cable gap", v < 0.05, f"{v:.2f} mm3")
    tx0, _, tx1, _, th = SCR_TF
    reach = box(S_O[0] + tx0, S_O[1] - 6.0, -1, S_O[0] + tx1, S_O[1] + 2.0, S_O[2] - th)
    v = vol(reach, back)
    test("screen", "the SD card mouth can be reached through the back", v < 0.05, f"{v:.2f} mm3")
    sx0, sy0, sx1, sy1, sh = SCR_SW1
    pen = box(S_O[0] + (sx0 + sx1) / 2 - 0.6, S_O[1] + sy1, S_O[2] - sh / 2 - 0.6, S_O[0] + (sx0 + sx1) / 2 + 0.6,
              OY + 1, S_O[2] - sh / 2 + 0.6)
    v = vol(pen, back + front + hooks)
    test("screen", "a pen reaches SW1 through the top wall", v < 0.05, f"{v:.2f} mm3")

    lb = logo_outline().bounds()
    room = led_face_bottom() - (LOGO_Y0 + lb[3])
    test("labels", "3 mm or more between the logo and the LED window above it", room >= 3.0 - 1e-6, f"{room:.2f} mm")
    lim = outline("limit").bounds()
    room = (KNOB_XY[1] - KNOB_RING_R) - (foot_line() + lim[3])
    test("labels", "2.5 mm or more between 'limit' and the knob ring above it", room >= 2.5 - 1e-6, f"{room:.2f} mm")
    room = (KNOB_XY[0] - lim[2] / 2) - (X_L + lb[2])
    test("labels", "5 mm or more between the logo and 'limit'", room >= 5.0, f"{room:.2f} mm")
    bb = side_name().bounding_box()
    test("labels", f"{NAME_BASE!r} fits the right wall with {SIDE_MARGIN} mm to each chamfer",
         bb[2] >= SIDE_BAND[0] - 1e-6 and bb[5] <= SIDE_BAND[1] + 1e-6 and bb[1] > 0 and bb[4] < OY,
         f"z {bb[2]:.2f} to {bb[5]:.2f}, band {SIDE_BAND[0]:.2f} to {SIDE_BAND[1]:.2f}")

    # The back mark: BACK_CLEAR from every opening and field in the back, and from its edge.
    mark = back_mark()
    foot = mark.project().offset(BACK_CLEAR - 0.01, JoinType.Round, 2.0, 32).extrude(FLOOR + 2).translate((0, 0, -1))
    a, b, c, d = SCR_ANT
    holes = union([keyhole(x) for x in KEY_XS] + [strip_fields(), card_opening(), sw1_openings()] +
                  [cyl(S_O[0] + bx, S_O[1] + by, -1, FLOOR + 1, SCR_BTN_D, 32) for bx, by in (SCR_BOOT, SCR_RST)] +
                  [rbox(S_O[0] + a, S_O[1] + b, -1, min(S_O[0] + c + 4.0, OX - WALL), S_O[1] + d, FLOOR + 1, 2.0)])
    v = vol(foot, holes)
    mb = mark.bounding_box()
    inside = mb[0] - BACK_CLEAR >= EDGE and mb[3] + BACK_CLEAR <= OX - EDGE and mb[1] - BACK_CLEAR >= EDGE
    test("back", f"back mark {BACK_CLEAR} mm or more from every opening, field and edge of the back",
         v < 0.01 and inside, f"{v:.2f} mm3, x {mb[0]:.1f} to {mb[3]:.1f}, y {mb[1]:.1f} to {mb[4]:.1f}")
    v = vol(mark, wall_screws()) + vol(mark, union(list(bds.values())))
    test("back", "back mark stays in the floor", v < 0.01 and ENGRAVE < FLOOR, f"{v:.2f} mm3")

    # The LED matrix. In "insert" mode the insert is the light guide.
    guide = insert if LED_FACE == "insert" else led_parts()[0]
    v = vol(guide, uno_q())
    test("LED", f"{LED_FACE} clears the UNO Q, its headers and the LEDs", v < 0.05, f"{v:.2f} mm3")
    v = vol(guide.translate((0, 0, -(LED_GAP + 0.05))), uno_q())
    test("LED", f"{LED_FACE} comes within {LED_GAP} mm of the LEDs", v > 0.01, f"{v:.2f} mm3 when 0.05 lower")
    v = vol(guide, cover_rods()) + vol(guide, back) + vol(guide, knob) + vol(guide, button)
    test("LED", f"{LED_FACE} clears the rods, the standoffs and the caps", v < 0.05, f"{v:.2f} mm3")
    if LED_FACE == "insert":
        o = insert_rect(Z_LID, INSERT_WALL)
        press = 2 * ((o[2] - o[0]) + (o[3] - o[1] + INSERT_WALL)) * INSERT_POCKET * max(-INSERT_PRESS, 0.0)
        v = vol(insert, front)
        test("LED", f"insert touches the cover only by the press fit of its rim ({-INSERT_PRESS:.2f} per side)",
             0.5 * press <= v <= 1.2 * press + 0.05, f"{v:.2f} mm3, the rim alone gives {press:.2f}")

    # The knob in its ring.
    kx, ky = KNOB_XY
    ring_free = cyl(kx, ky, KNOB_Z0 - 0.5, Z_TOP + 0.5, KNOB_D + 2 * (KNOB_GAP - 0.02))
    v = vol(ring_free, front)
    test("knob", f"even gap of {KNOB_GAP:.2f} mm all round the cap", v < 0.01, f"{v:.3f} mm3")
    v = vol(knob.translate((0, 0, -(ENC_PUSH + 0.1))), front)
    test("knob", f"cap pressed {ENC_PUSH + 0.1:.1f} mm (encoder push {ENC_PUSH}) touches nothing", v < 0.05, f"{v:.2f} mm3")

    # The button cap.
    uno = bds["UNO Q"]
    rest = vol(button, uno_button())
    test("button", f"cap does not press the button at rest (gap {BTN_GAP})", rest < 1e-4, f"{rest:.4f} mm3")
    v = vol(button.translate((0, 0, 0.1)), front)
    test("button", "cap is captive: 0.1 mm toward the front it hits the cover", v > 1e-3, f"{v:.3f} mm3")
    v = vol(button.translate((0, 0, -(BTN_GAP + 0.05))), uno_button())
    test("button", "pushed, the cap reaches the button", v > 1e-3, f"{v:.3f} mm3")
    push = button.translate((0, 0, -(BTN_GAP + UNO_BUTTON_TRAVEL)))
    v = vol(push, front) + vol(push, uno - uno_button()) + vol(push, back)
    test("button", "pushed to the end of the button travel, the cap touches nothing else", v < 0.05, f"{v:.2f} mm3")
    left = BTN_PROUD - BTN_GAP - UNO_BUTTON_TRAVEL
    test("button", "pushed to the end of the travel, the cap still stands in front of the face", left > 0.2,
         f"{left:.2f} mm")

    # The wall mount.
    y0 = KEY_Y - KEY_TRAVEL
    path = Manifold.batch_hull([wall_screws(y0), wall_screws(KEY_Y)])
    entry = union([cyl(x, y0, -1, FLOOR + KEY_HEAD_H, KEY_HEAD_D, 32) for x in KEY_XS])
    shank = union([Manifold.batch_hull([cyl(x, y, -1, FLOOR + 0.5, KEY_SCREW_D, 32) for y in (y0, KEY_Y)]) for x in KEY_XS])
    v = vol(entry, back) + vol(shank, back) + vol(path, back)
    test("mount", "screw heads pass the round holes, and the shanks slide up the slots to their rest", v < 0.05,
         f"{v:.2f} mm3")
    v = vol(path, union(list(bds.values())) + front + hooks)
    test("mount", "the slide path of the screw heads is free inside", v < 0.05, f"{v:.2f} mm3")
    fields = strip_fields(0.0)
    others = union([keyhole(x) for x in KEY_XS] + [card_opening(), sw1_openings()] +
                   [cyl(S_O[0] + bx, S_O[1] + by, -1, FLOOR + 1, SCR_BTN_D + 2.0, 32) for bx, by in (SCR_BOOT, SCR_RST)])
    a, b, c, d = SCR_ANT
    others += box(S_O[0] + a - 1, S_O[1] + b - 1, -1, OX, S_O[1] + d + 1, FLOOR + 1)
    v = vol(fields, others)
    test("mount", "strip fields clear the keyholes and every opening in the back", v < 0.01, f"{v:.2f} mm3")
    inside = cbox(0, 0, -1, OX, OY, FLOOR, CORNER_R, bottom=EDGE + 1)
    v = (fields - inside).volume()
    test("mount", f"strip fields lie on the flat back, {STRIP_FIELD[2]} deep in a {FLOOR} mm floor",
         v < 0.01 and STRIP_FIELD[2] < FLOOR - 1.0, f"{v:.2f} mm3 outside")

    # The pods.
    for kind, (pod, cover) in pods.items():
        v = vol(pod + cover, pod_board(kind))
        test("pods", f"{kind} board touches no printed part", v < 0.05, f"{v:.2f} mm3")
        v = vol(pod, cover)
        test("pods", f"{kind} cover in pod", v < 0.05, f"{v:.2f} mm3")
    # Nothing printed may stand in the 63 deg diagonal view of the VL53L5CX, up to 30 mm out.
    ox, oy, o, top, h, r = pod_geom(PODS["tof"])
    cx, cy, z = o[0] + TOF_C[0], o[1] + TOF_C[1], top + TOF_CHIP[2]
    far = 30.0
    ends = [(dx * TOF_CHIP[0] / 2, dy * TOF_CHIP[1] / 2) for dx in (-1, 1) for dy in (-1, 1)]
    k = math.tan(math.radians(31.5))
    cone_ = Manifold.batch_hull([box(cx + a - 0.01, cy + b - 0.01, z + 0.05, cx + a + 0.01, cy + b + 0.01, z + 0.06)
                                 for a, b in ends] + [cyl(cx, cy, z + far, z + far + 0.01, 2 * far * k, 64)])
    v = vol(cone_, pods["tof"][0] + pods["tof"][1])
    test("pods", "ToF view free", v < 0.05, f"{v:.2f} mm3")
    return out


# ---------------------------------------------------------------- export ----
def flip(part, z_top, depth_y):
    """Turn a cover upside down so its top face sits on the bed. A rotation, not a mirror."""
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


def weakest_label():
    """The label of the front cover with the least margin to the print rule."""
    table, _ = text_report()
    rows = [r for r in table if r[1] == "front cover" and r[0] != "Arduino mark"]
    return min(rows, key=lambda r: (round(min(r[2] - RULE_STROKE, r[3] - RULE_GAP), 3), r[0] != "Valio Aimo logo"))[0]


def label_outline(label):
    """The flat outline of a face label for the fit test. For the logo: its VALIO capitals, the thinnest part."""
    if label == NAME:
        return outline(label, NAME_XH, NAME_FONT, NAME_BOLD)
    if label == "Valio Aimo logo":
        valio = CrossSection.batch_boolean(logo_valio(), OpType.Add)
        b = valio.bounds()
        return valio.translate((-b[0], -b[1]))
    return outline(label)


def snap_sample(label):
    """A short piece of the cover snap: a small shell with one groove, and a small cover with one bump.

    The small cover also carries `label`, engraved like the front cover, so the same print
    tests the text weight. Both have the cross sections of the housing. Returns the shell, the
    cover in its place on the shell, and its outside size.
    """
    cs = label_outline(label)
    b = cs.bounds()
    L, D = round(b[2] + 2 * (EDGE + 3.0)), 24.0
    z_rim = FLOOR + LIP_H + 1.0
    shell = cbox(0, 0, 0, L, D, z_rim, 4.0, bottom=EDGE, top=SEAM) - rbox(WALL, WALL, FLOOR, L - WALL, D - WALL, z_rim + 1, 2.0)
    shell -= bumps(WALL, WALL, L - WALL, D - WALL, (L / 2,), (), SNAP_LEN + 1.0, z_rim - LIP_H / 2, SNAP_R + 0.05) ^ box(
        -1, D / 2, 0, L + 1, D + 1, z_rim + 1)                                       # the groove on one side only
    lo = WALL + LIP_CLR
    cover = cbox(0, 0, z_rim, L, D, z_rim + LID_T, 4.0, bottom=SEAM, top=EDGE)
    cover += lip_ring(lo, lo, L - lo, D - lo, z_rim - LIP_H, z_rim + 0.01, 2.0 - LIP_CLR, LIP_W)
    cover += bumps(WALL, WALL, L - WALL, D - WALL, (L / 2,), (), SNAP_LEN, z_rim - LIP_H / 2, SNAP_R) ^ box(
        lo - 1, D / 2, z_rim - LIP_H, L - lo + 1, D - lo + 1, z_rim)                  # one bump
    z1 = z_rim + LID_T
    engraving = cs.extrude(ENGRAVE + 1).translate((L / 2 - (b[0] + b[2]) / 2, D / 2 - (b[1] + b[3]) / 2, z1 - ENGRAVE))
    return shell, cover - engraving, (L, D, z_rim, z1)


def make_fit_test(label):
    """One small test print: the screen pins and hooks on a frame, and a snap sample with a label.

    The frame has the four screen pins, the four hooks and a rim around the PCB outline. The rim
    is open at the USB-C port, as wide as the opening in the housing wall, and at each hook. The
    middle of the frame is open, so the parts on the back of the board hang free. The snap
    sample lies in that opening as two loose parts: after the print, take them off the bed.
    """
    plate, up, rim, inside = 1.2, 3.0, 1.2, 7.5     # frame thickness, standoff height, rim width, frame width inside the outline
    edge_clr = 0.6
    m = edge_clr + rim + HOOK_T + 0.6
    top = plate + up + SCR_T
    frame = rbox(-m, -m, 0, SCR_X + m, SCR_Y + m, plate, 1.5)
    r0 = edge_clr + rim
    frame += lip_ring(-r0, -r0, SCR_X + r0, SCR_Y + r0, 0, top, 1.5, rim)
    cut = [rbox(inside, inside, -1, SCR_X - inside, SCR_Y - inside, plate + 1, 2.0),
           box(-m - 1, SCR_USB_Y - SCR_PORT[0] / 2, plate, 1.0, SCR_USB_Y + SCR_PORT[0] / 2, top + 1)]
    for hx, side in SCR_HOOKS:
        y0, y1 = (SCR_Y - 1.0, SCR_Y + m + 1) if side > 0 else (-m - 1, 1.0)
        cut.append(box(hx - HOOK_W / 2 - 1.0, y0, plate, hx + HOOK_W / 2 + 1.0, y1, top + 1))
    add = [screen_hooks(0, 0, plate, plate + up + SCR_T)]
    for hx, hy in SCR_HOLES:
        add.append(cyl(hx, hy, 0, plate + up, STANDOFF_D + 0.6))
        add.append(locating_pin(hx, hy, plate + up, top, SCR_HOLE_D, SCR_PIN_CLR))
    frame = ((frame - union(cut)) + union(add)).translate((m, m, 0))
    shell, cover, (L, D, _, z1) = snap_sample(label)
    x0 = m + inside + 3.0
    shell = shell.translate((x0, m + inside + 3.0, 0))
    cover = flip(cover, z1, D).translate((x0, m + inside + 3.0 + D + 3.0, 0))
    # A patch of the LED grille: 4 x 3 holes with the pitch, hole and plate of the insert.
    px, py = UNO_LED_PITCH
    w, d = 4 * px + 2 * INSERT_WALL, 3 * py + 2 * INSERT_WALL
    patch = box(0, 0, 0, w, d, INSERT_T) - union([cyl(INSERT_WALL + (i + 0.5) * px, INSERT_WALL + (j + 0.5) * py, -1,
                                                       INSERT_T + 1, LED_HOLE_D, 24) for i in range(4) for j in range(3)])
    patch = patch.translate((x0 + L + 4.0, m + inside + 3.0, 0))
    return frame, shell, cover + patch


def write_screen_template(path):
    """1:1 paper template of the screen as the model assumes it, to lay the real board on.

    Two views: from the front with the USB-C port on the left, and from the back, mirrored, for
    the parts on the back. Short lines outside the outline continue each edge that the board hides.
    """
    m, w, h, gap = 16, SCR_X, SCR_Y, 6           # the page fits A4
    hx, hy = SCR_HOLES[0]
    key = [("black", f"PCB outline: {SCR_X} x {SCR_Y} over the ears, body x {SCR_BODY[0]} to {SCR_BODY[1]} (SCR_BODY)"),
           ("red", f"Holes: {SCR_HOLE_DX} x {SCR_HOLE_DY} apart, centres {hx:.2f} and {hy:.2f} from the edges, D {SCR_HOLE_D}"),
           ("gray", f"Glass outline: SCR_LCD = {SCR_LCD[0]} x {SCR_LCD[1]}, SCR_LCD_OFF = {SCR_LCD_OFF}"),
           ("green", f"Lit area: SCR_AA = {SCR_AA[0]} x {SCR_AA[1]}, SCR_AA_OFF = {SCR_AA_OFF} (chin at the bottom)"),
           ("blue", f"USB-C port USB1: SCR_USB_Y = {SCR_USB_Y}, mouth on the body edge"),
           ("darkorange", f"Buttons: SCR_BOOT = {SCR_BOOT}, SCR_RST = {SCR_RST}"),
           ("purple", f"Antenna zone: SCR_ANT = {SCR_ANT}"),
           ("teal", "Bottom edge: TF1 SD card socket (SCR_TF) and P5, P4, P3, P2 (SCR_SOCKETS)"),
           ("brown", "Top edge: SW1 battery power button (press), P6 BAT, P7 speaker. Left: P1"),
           ("magenta", "Hooks of the back shell (SCR_HOOKS): each catches the PCB edge here")]
    page_w, page_h = w + 2 * m, 2 * (h + 2 * m) + gap + 5 * len(key) + 22
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{page_w}mm" height="{page_h}mm" '
         f'viewBox="0 0 {page_w} {page_h}" font-family="sans-serif" font-size="3.2" fill="none" stroke-width="0.2">']

    def view(y0, back, title):
        def fx(x):
            return m + (w - x if back else x)

        def fy(y):
            return y0 + m + h - y

        def rect(a, b, c, d, colour, ticks=True, name_=None):
            x0, x1 = sorted((fx(a), fx(c)))
            s.append(f'<rect x="{x0:.2f}" y="{fy(d):.2f}" width="{x1 - x0:.2f}" height="{d - b:.2f}" stroke="{colour}"/>')
            if name_:
                s.append(f'<text x="{x0 + 0.5:.2f}" y="{(fy(d) + fy(b)) / 2 + 0.8:.2f}" font-size="2.2" fill="{colour}">{name_}</text>')
            if ticks:                               # continue each edge outside the board
                for x in (x0, x1):
                    s.append(f'<path d="M{x:.2f} {fy(0) + 2} v6 M{x:.2f} {fy(h) - 2} v-6" stroke="{colour}"/>')
                for y in (fy(b), fy(d)):
                    s.append(f'<path d="M{fx(0) - 2:.2f} {y:.2f} h-6 M{fx(w) + 2:.2f} {y:.2f} h6" stroke="{colour}"/>'
                             if not back else
                             f'<path d="M{fx(w) - 2:.2f} {y:.2f} h-6 M{fx(0) + 2:.2f} {y:.2f} h6" stroke="{colour}"/>')
        s.append(f'<text x="{m}" y="{y0 + 4.5}" fill="black">{title}</text>')
        for poly in screen_pcb_outline().to_polygons():
            pts = " ".join(f"{fx(x):.2f},{fy(y):.2f}" for x, y in poly)
            s.append(f'<polygon points="{pts}" stroke="black"/>')
        for hx_, hy_ in SCR_HOLES:
            s.append(f'<path d="M{fx(hx_) - 3:.2f} {fy(hy_):.2f} h6 M{fx(hx_):.2f} {fy(hy_) - 3:.2f} v6" stroke="red"/>')
        for hk, side in SCR_HOOKS:
            yy = SCR_Y if side > 0 else 0.0
            rect(hk - HOOK_W / 2, yy - HOOK_LIP + HOOK_GAP if side > 0 else yy, hk + HOOK_W / 2,
                 yy if side > 0 else yy + HOOK_LIP - HOOK_GAP, "magenta", ticks=False)
        if not back:
            for (sx, sy), (ox, oy), colour in ((SCR_LCD, SCR_LCD_OFF, "gray"), (SCR_AA, SCR_AA_OFF, "green")):
                cx, cy = w / 2 + ox, h / 2 + oy
                rect(cx - sx / 2, cy - sy / 2, cx + sx / 2, cy + sy / 2, colour)
        else:
            for bx, by in (SCR_BOOT, SCR_RST):
                s.append(f'<circle cx="{fx(bx):.2f}" cy="{fy(by):.2f}" r="{SCR_BTN_D / 2}" stroke="darkorange"/>')
            rect(*SCR_ANT, "purple", ticks=False)
            rect(*SCR_TF[:4], "teal", name_="TF1")
            for name_, sk in zip(("P5", "P4", "P3", "P2"), SCR_SOCKETS):
                rect(*sk[:4], "teal", ticks=False, name_=name_)
            rect(*SCR_SW1[:4], "brown", name_="SW1")
            rect(*SCR_P6[:4], "brown", ticks=False, name_="P6")
            rect(*SCR_P7[:4], "brown", ticks=False, name_="P7")
            rect(*SCR_P1[:4], "brown", ticks=False, name_="P1")
            rect(*SCR_ESP[:4], "gray", ticks=False, name_="ESP32-S3")
            rect(*SCR_LCD1[:4], "gray", ticks=False, name_="LCD1")
        # The USB-C port and the plug that goes in it.
        rect(SCR_BODY[0], SCR_USB_Y - 4.6, 14.6, SCR_USB_Y + 4.6, "blue", ticks=False)
        rect(SCR_BODY[0] - 8, SCR_USB_Y - PLUG[0] / 2, SCR_BODY[0], SCR_USB_Y + PLUG[0] / 2, "blue", ticks=False)

    view(0, False, "Front. Lay the board here, display up, USB-C port on the left. Print at 100 %.")
    y1 = h + 2 * m + gap
    view(y1, True, "Back, mirrored. Lay the board here, display down, USB-C port on the right.")
    y = y1 + h + 2 * m + 2
    s.append(f'<path d="M{m} {y} h100 M{m} {y - 2} v4 M{m + 50} {y - 1} v2 M{m + 100} {y - 2} v4" stroke="black"/>')
    s.append(f'<text x="{m}" y="{y + 6}" fill="black">This line is 100 mm long. Measure it to check the print scale.</text>')
    for i, (colour, label) in enumerate(key):
        yy = y + 13 + 5 * i
        s.append(f'<path d="M{m} {yy - 1} h8" stroke="{colour}" stroke-width="0.6"/>')
        s.append(f'<text x="{m + 11}" y="{yy}" fill="black">{label}</text>')
    s.append('</svg>')
    Path(path).write_text("".join(s))


def write_template(path):
    """1:1 paper template of the UNO Q, to lay the real board on. It shows the LED holes of the cover."""
    m, w, h = 10, UNO_X, UNO_Y

    def fy(y):
        return m + h - y

    px, py = UNO_LED_PITCH
    page_w = 150                                    # wide enough for the notes below the board
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{page_w}mm" height="{h + 2 * m + 24}mm" '
         f'viewBox="0 0 {page_w} {h + 2 * m + 24}" font-family="sans-serif" fill="none" stroke-width="0.2">',
         f'<rect x="{m}" y="{m}" width="{w}" height="{h}" stroke="black"/>']
    for hx, hy in UNO_HOLES:
        s.append(f'<circle cx="{m + hx}" cy="{fy(hy)}" r="1.6" stroke="red"/>')
    for a, b, c, d in UNO_HEADERS:
        s.append(f'<rect x="{m + a}" y="{fy(d)}" width="{c - a}" height="{d - b}" stroke="gray"/>')
    a, b, c, d = UNO_MATRIX
    s.append(f'<rect x="{m + a}" y="{fy(d)}" width="{c - a:.2f}" height="{d - b:.2f}" stroke="green"/>')
    for x, y in UNO_LEDS:                           # one circle per hole in the cover, centred on the assumed LED
        s.append(f'<circle cx="{m + x:.3f}" cy="{fy(y):.3f}" r="{LED_HOLE_D / 2}" stroke="green" stroke-width="0.1"/>')
    s.append(f'<rect x="{m + UNO_USB_FACE}" y="{fy(UNO_USB_Y + 4.47)}" width="8.1" height="8.94" stroke="blue"/>')
    s.append(f'<circle cx="{m + UNO_BUTTON[0]}" cy="{fy(UNO_BUTTON[1])}" r="2" stroke="blue"/>')
    s.append(f'<circle cx="{m + UNO_BUTTON[0]}" cy="{fy(UNO_BUTTON[1])}" r="{BTN_PUSH_D / 2}" stroke="blue" stroke-width="0.1"/>')
    for lx, ly in UNO_RGB:
        s.append(f'<circle cx="{m + lx}" cy="{fy(ly)}" r="0.5" stroke="green"/>')
    s.append(f'<text x="{m}" y="{m + h + 8}" font-size="3.2" fill="black">UNO Q, components up, USB-C left. '
             f'Print at 100 %. Outline {w} x {h} mm.</text>')
    s.append(f'<text x="{m}" y="{m + h + 13}" font-size="2.6" fill="black">Green: LED pad field and one {LED_HOLE_D} mm '
             f'hole per LED, pitch {px:.3f} x {py:.3f} mm (derived). Each LED must sit inside its circle.</text>')
    s.append(f'<text x="{m}" y="{m + h + 17}" font-size="2.6" fill="black">Blue: power button JBTN1 and the '
             f'{BTN_PUSH_D} mm pusher of the button cap.</text></svg>')
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


def hang(part):
    """Turn a part from the building frame to the wall: its back on the plane y = 0, its top up."""
    return part.rotate((90, 0, 0))


def write_assembly(back, front, knob, button, insert, pods):
    """Every part in its place on the wall, for the FreeCAD file and the two pictures.

    Writes assembly/*.stl and assembly/assembly.json. The wall is the plane y = 0 and z is up.
    Each entry of the JSON names a part, its colour, and how far it moves in the exploded view
    (mm, as x, y, z): away from the wall. The two pods hang to the right of the housing. In the
    exploded view they move below it, so the housing does not hide them.
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
    scr_pcb, scr_parts, scr_lcd, scr_picture = screen_look()
    scr = "Guition JC4827W543C"
    out, cover_out = (0, -70, 0), (0, -205, 0)
    parts = [
        # name, solid, colour, exploded move, what it is, kind of surface
        ("back", back, grey, (0, 0, 0), "Printed back shell", "print"),
        ("screen", scr_pcb, "#1c1c1e", out, scr, "board"),
        ("screen_parts", scr_parts, dark, out, f"{scr}, parts on the back", "part"),
        ("screen_display", scr_lcd, "#0e1013", out, f"{scr}, display", "part"),
        ("screen_picture", scr_picture, "#2c4258", out, f"{scr}, active area", "part"),
        ("uno_q", uno_pcb, teal, out, "Arduino UNO Q", "board"),
        ("uno_q_parts", uno_parts, dark, out, "Arduino UNO Q, headers and connectors", "part"),
        ("uno_q_leds", uno_leds(), "#7fb6ff", out, "Arduino UNO Q, LED matrix", "light"),
        ("knob", knob_pcb, blue, out, "Modulino Knob", "board"),
        ("knob_parts", knob_parts, "#b9bcc0", out, "Modulino Knob, encoder", "metal"),
        ("buzzer", buzz_pcb, blue, out, "Modulino Buzzer", "board"),
        ("buzzer_parts", buzz_parts, dark, out, "Modulino Buzzer, buzzer", "part"),
        ("front", front, white, cover_out, "Printed front cover", "print"),
        ("knob_cap", knob, white, (0, -250, 0), "Printed knob cap", "print"),
        ("button_cap", button, white, (0, -330, -35), "Printed power button cap", "print"),
        ("led_grille", insert, "#3a3b3d", (0, -135, 0), "Printed LED grille insert", "print"),
    ]
    for kind, x, below, low in (("thermo", OX + 30, 15.0, -44.0),
                                ("tof", OX + 30 + pod_geom(PODS["thermo"])[0] + 34, OX + 26.0, -64.0)):
        pod, cover = pods[kind]
        ox, oy, o, top, h, r = pod_geom(PODS[kind])
        y = OY / 2 - oy / 2

        def move(part, k=(x, y, 0)):
            return part.translate(k)

        def apart(d, dx=below - x, dz=low - oy - y):
            return (dx, -d, dz)
        if kind == "thermo":
            pcb, on = modulino_look(o, "thermo")
            colour, label = blue, "Modulino Thermo"
        else:
            pcb, on = tof_look(o)
            colour, label = red, "SparkFun ToF Imager VL53L5CX"
        title = "Thermo pod" if kind == "thermo" else "Distance pod"
        parts += [(f"{kind}_pod_base", move(pod), grey, apart(0), f"Printed {title} base", "print"),
                  (kind, move(pcb), colour, apart(30), label, "board"),
                  (f"{kind}_parts", move(on), dark, apart(30), f"{label}, parts", "part"),
                  (f"{kind}_pod_cover", move(cover), white, apart(60), f"Printed {title} cover", "print")]
    manifest = []
    for name_, solid, colour, explode, label, surface in parts:
        write_stl(hang(solid), folder / f"{name_}.stl")
        manifest.append({"name": name_, "file": f"{name_}.stl", "colour": colour, "explode": explode, "label": label,
                         "surface": surface})
    (folder / "assembly.json").write_text(json.dumps({"units": "mm", "parts": manifest}, indent=1) + "\n")
    print(f"assembly: {len(parts)} parts in {folder}")


BED = (250.0, 210.0, 220.0)            # Prusa CORE One: x, y, and a safe height


def fits_bed(part):
    b = part.bounding_box()
    w, d, h = b[3] - b[0], b[4] - b[1], b[5] - b[2]
    return (max(w, d) <= BED[0] and min(w, d) <= BED[1] and h <= BED[2]), (w, d, h)


def main():
    table, bad_text = text_report()
    print("labels (thinnest stroke, thinnest gap, depth, mm):")
    for s, where, stroke, gap, depth in table:
        print(f"  {s!r:15} {where:30} stroke {stroke:.2f}  gap {gap:.2f}  depth {depth:.2f}")
    if bad_text:
        raise SystemExit("A label breaks the print rule for a 0.4 mm nozzle (stroke 1.0 or more, gap 0.5 or more, "
                         "depth 0.6 or more). Make it larger, bolder or wider spaced:\n  " + "\n  ".join(bad_text))

    back, front, knob, button = make_back(), make_front(), make_knob(), make_button()
    insert = make_led_insert() if LED_FACE == "insert" else Manifold()
    pods = {kind: (make_pod_base(kind), make_pod_cover(kind)) for kind in PODS}
    label = weakest_label()
    frame, sample_shell, sample_cover = make_fit_test(label)
    fit_test = frame + sample_shell + sample_cover

    results = check(back, front, knob, button, insert, pods)
    print("checks:")
    for group, what, ok, detail in results:
        print(f"  {'ok  ' if ok else 'FAIL'} [{group}] {what}: {detail}")
    failed = [r for r in results if not r[2]]
    print("clash check:", "OK" if not failed else f"{len(failed)} FAILED")

    printed = {"back.stl": back, "front.stl": flip(front, Z_TOP, OY),
               "knob.stl": flip(knob.translate((-KNOB_XY[0], -KNOB_XY[1], 0)), KNOB_Z1, 0),
               "button.stl": flip(button.translate((-BTN_XY[0], -BTN_XY[1], 0)), BTN_Z1, 0),
               **({"led_grille.stl": insert.translate((-HOLES_BOX[0], -HOLES_BOX[1], -insert.bounding_box()[2]))}
                  if LED_FACE == "insert" else {}),
               "screen_fit_test.stl": fit_test}
    for kind, (pod, cover) in pods.items():
        ox, oy, o, top, h, r = pod_geom(PODS[kind])
        printed[f"{kind}_pod_base.stl"] = pod
        printed[f"{kind}_pod_cover.stl"] = flip(cover, h + P_COVER, oy)
        print(f"{kind} pod: {ox:.1f} x {oy:.1f} x {h + P_COVER:.1f} mm")
    for fname, part in printed.items():
        ok, (w, d, h) = fits_bed(part)
        status = part.status().name
        print(f"{fname}: {status}, {part.volume() / 1000:.1f} cm3, {w:.1f} x {d:.1f} x {h:.1f} mm, "
              f"{'fits' if ok else 'DOES NOT FIT'} the {BED[0]:.0f} x {BED[1]:.0f} bed")
        if status != "NoError" or not ok:
            failed.append(("print", fname, False, status))

    print(f"plugs: {PLUGS}, LED face: {LED_FACE}, FIT {FIT}")
    print(f"housing: {OX:.1f} wide x {OY:.1f} high x {Z_TOP:.1f} mm deep")
    print(f"knob cap: {KNOB_D:.1f} x {KNOB_Z1 - KNOB_Z0:.1f} mm, {KNOB_Z1 - Z_TOP:.1f} mm above the cover, "
          f"gap {KNOB_GAP:.2f}, ring {2 * KNOB_RING_R:.1f} mm")
    print(f"button cap: stem {BTN_D} in a {BTN_HOLE:.2f} hole, {BTN_PROUD} proud, {BTN_GAP} above the button, "
          f"travel to press {BTN_GAP + UNO_BUTTON_TRAVEL:.2f}, {BTN_Z1 - BTN_Z0:.1f} mm long")
    px, py = UNO_LED_PITCH
    if LED_FACE == "insert":
        b = insert.bounding_box()
        print(f"LED grille insert: {b[3] - b[0]:.1f} x {b[4] - b[1]:.1f} x {b[5] - b[2]:.1f} mm, plate {INSERT_T} at "
              f"{LED_GAP} above the LEDs, rim press {-INSERT_PRESS:.2f} per side, view angles: "
              + ", ".join(f"{k} {v:.0f} deg" for k, v in led_view_angles().items()))
    print(f"LED: pitch {px:.3f} x {py:.3f}, hole {LED_HOLE_D}, wall {px - LED_HOLE_D:.2f} x {py - LED_HOLE_D:.2f}, "
          f"block {Z_LID - (UNO_TOP + UNO_LED_H + LED_GAP):.1f} mm high, {LED_GAP} above the LEDs")
    print(f"screen window margin {SCR_WIN_MARGIN:.2f}, left axis x = {X_L:.2f}, glass gap {SCR_GLASS_GAP}")
    print(f"standoffs: UNO Q {STANDOFF_UNO:.2f}, Knob {KNOB_O[2] - Z_IN:.2f}, Buzzer {BUZZ_O[2] - Z_IN:.2f}, "
          f"screen {S_O[2] - Z_IN:.2f} mm")
    print(f"fit test: label {label!r}")
    if "--check" in sys.argv:
        if failed:
            raise SystemExit(f"{len(failed)} check(s) failed")
        return

    OUT.mkdir(exist_ok=True)
    for old in ("lid.stl", "base.stl"):
        (OUT / old).unlink(missing_ok=True)
    for fname, part in printed.items():
        write_stl(part, OUT / fname)
    write_template(HERE / "uno_q_template.svg")
    write_screen_template(HERE / "screen_template.svg")

    if "--assembly" in sys.argv:
        write_assembly(back, front, knob, button, insert, pods)

    if "--preview" in sys.argv:
        grey, metal = "#d6d7d3", "#8a8d8f"
        items = [(back, grey), (front, grey), (knob, metal), (button, metal), (insert, metal)]
        x = 4.0
        for kind, (pod, cover) in pods.items():
            ox, oy = pod_geom(PODS[kind])[:2]
            items += [(pod.translate((x, -oy - 12, 0)), grey), (cover.translate((x, -oy - 12, 0)), grey)]
            x += ox + 10
        render(items, OUT / "preview.png")
    if failed:
        raise SystemExit(f"{len(failed)} check(s) failed")


if __name__ == "__main__":
    main()

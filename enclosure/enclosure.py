"""Safe Kitchen Agent enclosure: Arduino UNO Q + Modulino Knob + Buzzer, and a
separate vented pod for the Modulino Thermo.

Run:  uv run --no-project --with manifold3d --with numpy --with matplotlib \
        python enclosure.py [--preview]

All numbers are millimetres. Board numbers come from the official datasheets:
  UNO Q   ABX00162 datasheet, section 12 (Mechanical Information)
  Knob    ABX00107 datasheet, section 7.4
  Buzzer  ABX00108 datasheet, section 7.4
  Thermo  ABX00103 datasheet, section 6.1
Values marked MEASURED were read off the datasheet drawings, not a dimension
label. Check them with calipers on the real boards before the final print.

Coordinates: x runs left to right (USB-C side is left), y runs front to back,
z is up. The base floor bottom is z = 0.
"""

import struct
import sys
from pathlib import Path

import numpy as np
from manifold3d import Manifold, OpType

SEG = 64
OUT = Path(__file__).parent / "stl"

# ---------------------------------------------------------------- boards ----
UNO_X, UNO_Y, UNO_T = 68.58, 53.34, 1.6
UNO_HOLES = [(13.97, 2.54), (15.24, 50.80), (66.04, 7.62), (66.04, 35.56)]  # Ø3.2
UNO_BOTTOM_CLEAR = 5.0        # JMISC/JMEDIA connectors stick 5 mm below the PCB
UNO_TOP_MAX = 8.5             # tallest top part (headers) above the PCB
UNO_USB_Y = (11.0, 20.2)      # MEASURED: USB-C span, measured down from the back edge
UNO_USB_Z = 1.6               # USB-C centre above the PCB top (3 mm tall part)
UNO_BUTTON = (5.7, 49.35)     # MEASURED: power button centre (board coords)
UNO_MATRIX = (31.2, 4.7, 58.4, 20.6)  # MEASURED: 8x13 LED matrix x0, y0, x1, y1
UNO_QWIIC_Y = (11.1, 17.7)    # MEASURED: Qwiic socket on the right edge

MOD_X, MOD_Y, MOD_T, MOD_R = 41.0, 25.36, 1.6, 4.5
MOD_HOLES = [(4.5, 4.68), (36.5, 4.68), (4.5, 20.68), (36.5, 20.68)]  # Ø3.2
MOD_CENTER = (20.5, 12.68)    # encoder, buzzer and sensor all sit on the centre
MOD_QWIIC_H = 4.58            # Qwiic socket height above the PCB
KNOB_COLLAR_TOP = 11.3        # MEASURED: top of the Ø7 shaft collar above the PCB
KNOB_COLLAR_D = 7.3           # MEASURED
BUZZER_H = 3.0                # PKLCS1212 buzzer, 12 x 12 x 3

# ------------------------------------------------------------- case setup ----
WALL, FLOOR, LID_T = 2.0, 2.0, 2.5
CLR = 1.0                     # gap between the UNO Q edge and the wall
GAP_QWIIC = 15.0              # room between UNO Q and Modulinos for the plugs
GAP_LOOP = 13.0               # room at the right wall for the Knob-Buzzer loop
STANDOFF_UNO = 6.5            # > UNO_BOTTOM_CLEAR
IH = 19.0                     # inner height, floor top to lid underside
PILOT_D = 2.5                 # M3 self-tapping (or M2.5 machine) screw pilot
SCREW_CLR_D, SCREW_HEAD_D = 3.4, 6.2
POST = 6.5                    # lid screw post, square
LIP_W, LIP_H, LIP_CLR = 1.2, 2.0, 0.3
CORNER_R = 3.0

IX = CLR + UNO_X + GAP_QWIIC + MOD_X + GAP_LOOP
IY = CLR + UNO_Y + CLR
OX, OY = IX + 2 * WALL, IY + 2 * WALL
Z_IN = FLOOR                  # floor top
Z_LID = FLOOR + IH            # lid underside
Z_TOP = Z_LID + LID_T

# Board placement, in global coordinates (lower-left corner of each PCB).
UNO_O = (WALL + CLR, WALL + CLR, Z_IN + STANDOFF_UNO)
MOD_X0 = WALL + CLR + UNO_X + GAP_QWIIC
KNOB_PCB_TOP = Z_TOP - 0.3 - KNOB_COLLAR_TOP     # collar ends 0.3 below the lid top
BUZZ_PCB_TOP = Z_LID - MOD_QWIIC_H - 0.4         # Qwiic socket must clear the lid
KNOB_O = (MOD_X0, WALL + 1.0, KNOB_PCB_TOP - MOD_T)
BUZZ_O = (MOD_X0, WALL + IY - 1.0 - MOD_Y, BUZZ_PCB_TOP - MOD_T)

POSTS = [  # centres of the four lid screw posts
    (UNO_O[0] + UNO_X + 0.6 + POST / 2, WALL + POST / 2),
    (UNO_O[0] + UNO_X + 0.6 + POST / 2, WALL + IY - POST / 2),
    (WALL + IX - POST / 2, WALL + POST / 2),
    (WALL + IX - POST / 2, WALL + IY - POST / 2),
]
CABLE_NOTCH_X = (POSTS[1][0] + POST / 2 + 1.5, MOD_X0 - 1.0)  # Thermo cable exit
CABLE_NOTCH_DEPTH = 4.5


# --------------------------------------------------------------- helpers ----
def box(x0, y0, z0, x1, y1, z1):
    return Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


def cyl(x, y, z0, z1, d, seg=SEG):
    return Manifold.cylinder(z1 - z0, d / 2, circular_segments=seg).translate((x, y, z0))


def rbox(x0, y0, z0, x1, y1, z1, r):
    """Box with rounded vertical edges."""
    pts = [(x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)]
    return Manifold.batch_hull([cyl(x, y, z0, z1, 2 * r) for x, y in pts])


def union(parts):
    return Manifold.batch_boolean(parts, OpType.Add) if parts else Manifold()


def slot(x, y, z0, z1, length, width, along_x=True):
    """Stadium-shaped slot centred on (x, y)."""
    h = (length - width) / 2
    a, b = ((x - h, y), (x + h, y)) if along_x else ((x, y - h), (x, y + h))
    return Manifold.batch_hull([cyl(*a, z0, z1, width), cyl(*b, z0, z1, width)])


# ------------------------------------------------------------------ base ----
def make_base():
    shell = rbox(0, 0, 0, OX, OY, Z_LID, CORNER_R)
    cavity = rbox(WALL, WALL, Z_IN, OX - WALL, OY - WALL, Z_LID + 1, max(CORNER_R - WALL, 0.5))
    base = shell - cavity

    add, cut = [], []
    # UNO Q standoffs.
    for hx, hy in UNO_HOLES:
        x, y = UNO_O[0] + hx, UNO_O[1] + hy
        add.append(cyl(x, y, Z_IN - 0.01, UNO_O[2], 5.5))
        cut.append(cyl(x, y, Z_IN - 1.0, UNO_O[2] + 0.1, PILOT_D))
    # Modulino standoffs on the floor. Their height puts the Knob collar and the
    # Buzzer at the right place under the lid holes.
    for origin in (KNOB_O, BUZZ_O):
        for hx, hy in MOD_HOLES:
            x, y = origin[0] + hx, origin[1] + hy
            add.append(cyl(x, y, Z_IN - 0.01, origin[2], 6.0))
            cut.append(cyl(x, y, Z_IN - 1.0, origin[2] + 0.1, PILOT_D))
    # Lid screw posts.
    for x, y in POSTS:
        add.append(box(x - POST / 2, y - POST / 2, Z_IN - 0.01, x + POST / 2, y + POST / 2, Z_LID))
        cut.append(cyl(x, y, Z_LID - 12, Z_LID + 0.1, PILOT_D))
    base = base + union(add)

    # USB-C opening, sized for a hub or cable overmold (14 x 9).
    uy = UNO_O[1] + UNO_Y - sum(UNO_USB_Y) / 2
    uz = UNO_O[2] + UNO_T + UNO_USB_Z
    cut.append(slot_yz(-1, WALL + 1, uy, uz, 14.0, 9.0))
    # Thermo cable notch in the back wall, open at the top so the lid traps the cable.
    cut.append(box(CABLE_NOTCH_X[0], OY - WALL - 1, Z_LID - CABLE_NOTCH_DEPTH,
                   CABLE_NOTCH_X[1], OY + 1, Z_LID + 1))
    # Side vents low on the front and back walls under the UNO Q.
    for i in range(7):
        x = UNO_O[0] + 10 + i * 7.5
        for y in (WALL / 2, OY - WALL / 2):
            cut.append(slot_xz(x, y, Z_IN + 3.0, 2.2, 8.0))
    # Recesses for four 10 mm rubber feet.
    for x in (12, OX - 12):
        for y in (12, OY - 12):
            cut.append(cyl(x, y, -0.1, 0.8, 10.6))
    return base - union(cut)


def slot_yz(x0, x1, y, z, wy, wz):
    """Rounded rectangle opening through a wall that faces x."""
    r = min(wy, wz) / 2 - 0.01
    pts = [(y + sy * (wy / 2 - r), z + sz * (wz / 2 - r)) for sy in (-1, 1) for sz in (-1, 1)]
    return Manifold.batch_hull([
        Manifold.cylinder(x1 - x0, r, circular_segments=SEG).rotate((0, 90, 0)).translate((x0, py, pz))
        for py, pz in pts
    ])


def slot_xz(x, y, z0, w, h):
    """Vertical stadium slot through a wall that faces y."""
    r = w / 2
    caps = [Manifold.cylinder(WALL + 2, r, circular_segments=32).rotate((90, 0, 0))
            .translate((x, y + WALL / 2 + 1, zz)) for zz in (z0 + r, z0 + h - r)]
    return Manifold.batch_hull(caps)


# ------------------------------------------------------------------- lid ----
def make_lid():
    lid = rbox(0, 0, Z_LID, OX, OY, Z_TOP, CORNER_R)

    # Locating lip inside the walls, cut away around the posts and the cable notch.
    lo = WALL + LIP_CLR
    outer = box(lo, lo, Z_LID - LIP_H, OX - lo, OY - lo, Z_LID + 0.01)
    inner = box(lo + LIP_W, lo + LIP_W, Z_LID - LIP_H - 1, OX - lo - LIP_W, OY - lo - LIP_W, Z_LID + 1)
    lip = outer - inner
    gaps = [box(x - POST / 2 - 0.4, y - POST / 2 - 0.4, Z_LID - LIP_H - 1,
                x + POST / 2 + 0.4, y + POST / 2 + 0.4, Z_LID) for x, y in POSTS]
    gaps.append(box(CABLE_NOTCH_X[0], OY - WALL - 3, Z_LID - LIP_H - 1, CABLE_NOTCH_X[1], OY, Z_LID))
    lid = lid + (lip - union(gaps))

    cut = []
    # Lid screw holes with counterbores.
    for x, y in POSTS:
        cut.append(cyl(x, y, Z_LID - 1, Z_TOP + 1, SCREW_CLR_D))
        cut.append(cyl(x, y, Z_TOP - 1.2, Z_TOP + 1, SCREW_HEAD_D))
    # Knob collar hole.
    kx, ky = KNOB_O[0] + MOD_CENTER[0], KNOB_O[1] + MOD_CENTER[1]
    cut.append(cyl(kx, ky, Z_LID - 1, Z_TOP + 1, KNOB_COLLAR_D + 0.9))
    # Buzzer grille: centre hole plus a ring of six.
    bx, by = BUZZ_O[0] + MOD_CENTER[0], BUZZ_O[1] + MOD_CENTER[1]
    cut.append(cyl(bx, by, Z_LID - 1, Z_TOP + 1, 2.2, 24))
    for k in range(6):
        a = np.radians(60 * k)
        cut.append(cyl(bx + 4.2 * np.cos(a), by + 4.2 * np.sin(a), Z_LID - 1, Z_TOP + 1, 2.2, 24))
    # LED matrix window, 1 mm margin, chamfered on top.
    m = UNO_MATRIX
    wx0, wy0 = UNO_O[0] + m[0] - 1, UNO_O[1] + m[1] - 1
    wx1, wy1 = UNO_O[0] + m[2] + 1, UNO_O[1] + m[3] + 1
    cut.append(box(wx0, wy0, Z_LID - 1, wx1, wy1, Z_TOP + 1))
    cut.append(Manifold.batch_hull([
        box(wx0, wy0, Z_TOP - 0.8, wx1, wy1, Z_TOP - 0.79),
        box(wx0 - 0.8, wy0 - 0.8, Z_TOP + 0.01, wx1 + 0.8, wy1 + 0.8, Z_TOP + 0.02),
    ]))
    # Power button access hole (press with a pen tip).
    cut.append(cyl(UNO_O[0] + UNO_BUTTON[0], UNO_O[1] + UNO_BUTTON[1], Z_LID - 1, Z_TOP + 1, 5.0))
    # Vent slots over the left half of the UNO Q.
    for i in range(4):
        cut.append(slot(UNO_O[0] + 14 + i * 4.0, UNO_O[1] + 22, Z_LID - 1, Z_TOP + 1, 16, 2.0, along_x=False))
    return lid - union(cut)


# ----------------------------------------------------------- thermo pod ----
P_WALL, P_FLOOR, P_CLR, P_STAND = 1.6, 1.6, 0.5, 3.0
P_IX, P_IY = MOD_X + 2 * P_CLR, MOD_Y + 2 * P_CLR
P_OX, P_OY = P_IX + 2 * P_WALL, P_IY + 2 * P_WALL
P_PCB_TOP = P_FLOOR + P_STAND + MOD_T
P_H = P_PCB_TOP + MOD_QWIIC_H + 1.2          # wall top
P_LID_T = 1.6


def make_pod_base():
    pod = rbox(0, 0, 0, P_OX, P_OY, P_H, MOD_R + P_WALL) - rbox(
        P_WALL, P_WALL, P_FLOOR, P_OX - P_WALL, P_OY - P_WALL, P_H + 1, MOD_R + P_CLR)
    ox, oy = P_WALL + P_CLR, P_WALL + P_CLR
    add, cut = [], []
    for hx, hy in MOD_HOLES:
        add.append(cyl(ox + hx, oy + hy, P_FLOOR - 0.01, P_FLOOR + P_STAND, 5.0))
        cut.append(cyl(ox + hx, oy + hy, P_FLOOR - 1.0, P_FLOOR + P_STAND + 0.1, PILOT_D))
    pod = pod + union(add)
    # Cable slot on the left end, open at the top, down to the PCB top.
    cy = oy + MOD_CENTER[1]
    cut.append(box(-1, cy - 3.5, P_PCB_TOP - 0.3, P_WALL + 1, cy + 3.5, P_H + 1))
    # Air slots on both long walls.
    for i in range(5):
        x = ox + 8 + i * 6.25
        for y in (P_WALL / 2, P_OY - P_WALL / 2):
            cut.append(box(x - 1.1, y - 2, P_FLOOR + 1.0, x + 1.1, y + 2, P_H - 1.5))
    # Hanging tab with a 4 mm hole on the right end, flush with the floor.
    tab = (rbox(P_OX - 4, cy - 6, 0, P_OX + 8, cy + 6, P_FLOOR, 5.9)
           - cyl(P_OX + 3, cy, -1, P_FLOOR + 1, 4.0))
    return (pod + tab) - union(cut)


def make_pod_cover():
    z0 = P_H
    cover = rbox(0, 0, z0, P_OX, P_OY, z0 + P_LID_T, MOD_R + P_WALL)
    lo = P_WALL + 0.25
    lip = (rbox(lo, lo, z0 - 1.5, P_OX - lo, P_OY - lo, z0 + 0.01, MOD_R + 0.75)
           - rbox(lo + 1.0, lo + 1.0, z0 - 3, P_OX - lo - 1.0, P_OY - lo - 1.0, z0 + 1, MOD_R))
    cy = P_WALL + P_CLR + MOD_CENTER[1]
    lip = lip - box(-1, cy - 4.0, z0 - 3, P_WALL + 3, cy + 4.0, z0 + 1)
    cover = cover + lip
    # Slot grid over the sensor.
    cx = P_WALL + P_CLR + MOD_CENTER[0]
    cuts = [slot(cx + dx, cy, z0 - 1, z0 + P_LID_T + 1, 14, 2.0, along_x=False)
            for dx in (-12, -8, -4, 0, 4, 8, 12)]
    return cover - union(cuts)


# ---------------------------------------------------------------- export ----
def flip_for_print(part, z_top, depth_y):
    """Turn a lid upside down so its flat top face sits on the bed.

    This is a rotation, not a mirror. A mirror would print a mirrored lid.
    """
    return part.rotate((180, 0, 0)).translate((0, depth_y, z_top))


def write_stl(part, path):
    mesh = part.to_mesh()
    v = np.asarray(mesh.vert_properties)[:, :3]
    t = np.asarray(mesh.tri_verts)
    tri = v[t]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    data = np.zeros(len(t), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    data["n"], data["v"] = n, tri
    with open(path, "wb") as f:
        f.write(b"safe-kitchen-enclosure".ljust(80, b" "))
        f.write(struct.pack("<I", len(t)))
        f.write(data.tobytes())


def ghosts():
    """Simple board stand-ins for the preview and the clash check."""
    ux, uy, uz = UNO_O
    parts = {
        "UNO Q": box(ux, uy, uz, ux + UNO_X, uy + UNO_Y, uz + UNO_T + UNO_TOP_MAX - 0.3),
        "UNO Q bottom connectors": box(ux + 10.2, uy + 7.43, uz - UNO_BOTTOM_CLEAR, ux + 60.1,
                                       uy + 45.93, uz),
    }
    for name, o, h in (("Knob", KNOB_O, KNOB_COLLAR_TOP), ("Buzzer", BUZZ_O, MOD_QWIIC_H)):
        board = box(o[0] + 6.0, o[1], o[2], o[0] + MOD_X - 6.0, o[1] + MOD_Y, o[2] + MOD_T)
        top = box(o[0] + 11.5, o[1] + 4.0, o[2] + MOD_T, o[0] + 29.5, o[1] + 21.4, o[2] + MOD_T + 4.4)
        qw = [box(o[0] + sx, o[1] + 9.5, o[2] + MOD_T, o[0] + sx + 5.5, o[1] + 16.0,
                  o[2] + MOD_T + MOD_QWIIC_H) for sx in (0.0, MOD_X - 5.5)]
        parts[name] = union([board, top] + qw)
    return parts


def check(base, lid):
    """Fail loudly if a board stand-in overlaps the printed parts."""
    shell = base + lid
    bad = []
    for name, g in ghosts().items():
        vol = (shell ^ g).volume()
        if vol > 0.5:
            bad.append(f"{name}: {vol:.1f} mm3")
    return bad


def preview(parts):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    fig = plt.figure(figsize=(16, 9))
    views = [(28, -60, "assembled"), (90, -90, "top"), (-35, -60, "from below")]
    for i, (elev, azim, title) in enumerate(views, 1):
        ax = fig.add_subplot(1, 3, i, projection="3d")
        for part, colour, alpha in parts:
            mesh = part.to_mesh()
            v = np.asarray(mesh.vert_properties)[:, :3]
            ax.add_collection3d(Poly3DCollection(v[np.asarray(mesh.tri_verts)],
                                                 facecolor=colour, edgecolor="none", alpha=alpha))
        ax.set_xlim(0, OX); ax.set_ylim(-40, OY + 40); ax.set_zlim(-30, 50)
        ax.set_box_aspect((OX, OY + 80, 80))
        ax.view_init(elev, azim); ax.set_title(title); ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(OUT / "preview.png", dpi=110)


def main():
    OUT.mkdir(exist_ok=True)
    base, lid = make_base(), make_lid()
    pod, cover = make_pod_base(), make_pod_cover()

    bad = check(base, lid)
    print("clash check:", "OK" if not bad else bad)

    write_stl(base, OUT / "base.stl")
    write_stl(flip_for_print(lid, Z_TOP, OY), OUT / "lid.stl")
    write_stl(pod, OUT / "thermo_pod_base.stl")
    write_stl(flip_for_print(cover, P_H + P_LID_T, P_OY), OUT / "thermo_pod_cover.stl")

    print(f"case outside: {OX:.1f} x {OY:.1f} x {Z_TOP:.1f} mm")
    print(f"thermo pod outside: {P_OX:.1f} x {P_OY:.1f} x {P_H + P_LID_T:.1f} mm (+ tab)")
    print(f"floor standoffs: knob {KNOB_O[2] - Z_IN:.2f} mm, buzzer {BUZZ_O[2] - Z_IN:.2f} mm")
    for name, p in (("base", base), ("lid", lid), ("pod", pod), ("cover", cover)):
        print(f"{name}: watertight={p.status().name}, genus={p.genus()}, volume={p.volume() / 1000:.1f} cm3")

    if "--preview" in sys.argv:
        pod_place = pod.translate((OX / 2 - P_OX / 2, -P_OY - 12, 0))
        cover_place = cover.translate((OX / 2 - P_OX / 2, -P_OY - 12, 0))
        g = ghosts()
        preview([(base, "#9aa5b1", 0.35), (lid, "#4c78a8", 0.25),
                 (pod_place, "#9aa5b1", 0.4), (cover_place, "#4c78a8", 0.3)]
                + [(gp, "#e45756", 0.9) for gp in g.values()])
        print("wrote", OUT / "preview.png")


if __name__ == "__main__":
    main()

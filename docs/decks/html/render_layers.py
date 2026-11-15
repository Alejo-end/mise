"""Render the mise case as one image per part group, all with one camera, for a CSS explode animation.

blender -b -P render_layers.py -- <assembly dir> <out dir>
Writes assembled.png (all parts in place), one <group>.png per group at its exploded place,
and layers.json with each group's offset in pixels from its exploded to its assembled place.
"""
import json, math, os, sys
import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

SRC, OUT = sys.argv[sys.argv.index("--") + 1:][:2]
WIDTH, HEIGHT = 1600, 1200
LOOK_FROM = Vector((-0.62, -1.0, 0.58)).normalized()
GROUPS = [  # back to front, the order the browser stacks them
    ("screen_stand", ["screen_stand"]), ("screen_base", ["screen_base"]),
    ("screen", ["screen", "screen_parts", "screen_display", "screen_picture"]), ("screen_bezel", ["screen_bezel"]),
    ("base", ["base"]), ("uno_q", ["uno_q", "uno_q_parts"]), ("buzzer", ["buzzer", "buzzer_parts"]),
    ("knob", ["knob", "knob_parts"]), ("lid", ["lid"]), ("knob_cap", ["knob_cap"]),
    ("thermo_pod_base", ["thermo_pod_base"]), ("thermo", ["thermo", "thermo_parts"]), ("thermo_pod_cover", ["thermo_pod_cover"]),
    ("tof_pod_base", ["tof_pod_base"]), ("tof", ["tof", "tof_parts"]), ("tof_pod_cover", ["tof_pod_cover"]),
]


def material(name, colour, surface):
    m = bpy.data.materials.new(name); m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    r, g, bl = (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    b.inputs["Base Color"].default_value = (r ** 2.2, g ** 2.2, bl ** 2.2, 1)
    b.inputs["Roughness"].default_value = 0.32 if surface == "metal" else 0.45 if surface != "print" else 0.62
    b.inputs["Metallic"].default_value = 0.9 if surface == "metal" else 0.0
    return m


bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
parts = json.load(open(os.path.join(SRC, "assembly.json")))["parts"]
objs = {}
for part in parts:
    bpy.ops.wm.stl_import(filepath=os.path.join(SRC, part["file"]))
    o = bpy.context.selected_objects[0]; o.name = part["name"]
    o.data.materials.append(material(part["name"], part["colour"], part.get("surface", "print")))
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.shade_auto_smooth(angle=math.radians(35))
    objs[part["name"]] = (part, o)


def place(exploded):
    for part, o in objs.values():
        o.location = Vector(part["explode"]) if exploded else Vector((0, 0, 0))
    bpy.context.view_layer.update()


# One camera that holds both states: frame the union of the assembled and the exploded bounds.
corners = []
for state in (False, True):
    place(state)
    corners += [o.matrix_world @ Vector(c) for _, o in objs.values() for c in o.bound_box]
lo = Vector([min(c[i] for c in corners) for i in range(3)]); hi = Vector([max(c[i] for c in corners) for i in range(3)])
centre = (lo + hi) / 2
cd = bpy.data.cameras.new("cam"); cd.type = "ORTHO"; cd.clip_end = 5000
cam = bpy.data.objects.new("cam", cd); scene.collection.objects.link(cam); scene.camera = cam
cam.rotation_euler = (-LOOK_FROM).to_track_quat("-Z", "Y").to_euler()
cam.location = centre + LOOK_FROM * 900
bpy.context.view_layer.update()
inv = cam.matrix_world.inverted(); loc = [inv @ c for c in corners]
w = max(q.x for q in loc) - min(q.x for q in loc); h = max(q.y for q in loc) - min(q.y for q in loc)
mid = Vector(((max(q.x for q in loc) + min(q.x for q in loc)) / 2, (max(q.y for q in loc) + min(q.y for q in loc)) / 2, 0))
cam.location += cam.matrix_world.to_3x3() @ mid
cd.ortho_scale = max(w, h * WIDTH / HEIGHT) * 1.06

for name, rot, energy, size in (("key", (math.radians(50), 0, math.radians(-40)), 2.2, 0.35),
                                ("fill", (math.radians(65), 0, math.radians(110)), 0.6, 0.8)):
    L = bpy.data.lights.new(name, "SUN"); L.energy, L.angle = energy, size
    lo_ = bpy.data.objects.new(name, L); lo_.rotation_euler = rot; scene.collection.objects.link(lo_)
world = bpy.data.worlds.new("w"); world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.97, 0.97, 0.96, 1)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.55
scene.world = world
scene.render.engine = "CYCLES"; scene.cycles.samples = 48; scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y = WIDTH, HEIGHT
scene.render.film_transparent = True
scene.view_settings.view_transform = "AgX"; scene.view_settings.exposure = 0.9
os.makedirs(OUT, exist_ok=True)


def shot(path, visible):
    for n, (_, o) in objs.items():
        o.hide_render = n not in visible
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def px(v):
    p = world_to_camera_view(scene, cam, v)
    return p.x * WIDTH, (1 - p.y) * HEIGHT


place(False)
shot(os.path.join(OUT, "assembled.png"), set(objs))
place(True)
layers = []
for gname, names in GROUPS:
    shot(os.path.join(OUT, gname + ".png"), set(names))
    part, o = objs[names[0]]
    ex = px(o.matrix_world.translation)
    asm = px(o.matrix_world.translation - Vector(part["explode"]))
    # label anchor: the rightmost visible point of the group in the exploded picture
    pts = [px(objs[n][1].matrix_world @ v.co) for n in names for v in list(objs[n][1].data.vertices)[::7]]
    right = max(pts, key=lambda q: q[0]); left = min(pts, key=lambda q: q[0])
    layers.append({"name": gname, "label": part["label"], "dx": asm[0] - ex[0], "dy": asm[1] - ex[1],
                   "right": right, "left": left})
json.dump({"width": WIDTH, "height": HEIGHT, "layers": layers}, open(os.path.join(OUT, "layers.json"), "w"), indent=1)

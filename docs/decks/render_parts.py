"""Render each component of the mise enclosure alone, same look as render_views.py."""
import json, math, os, sys
import bpy
from mathutils import Vector

SOURCE, OUT = sys.argv[sys.argv.index("--") + 1:][:2]
SIZE = 1200
LOOK_FROM = Vector((-0.62, -1.0, 0.75)).normalized()
GROUPS = {
    "uno_q": ["uno_q", "uno_q_parts"],
    "knob": ["knob", "knob_parts"],
    "buzzer": ["buzzer", "buzzer_parts"],
    "thermo": ["thermo", "thermo_parts"],
    "tof": ["tof", "tof_parts"],
    "lid": ["lid", "knob_cap"],
    "base": ["base"],
    "thermo_pod": ["thermo_pod_base", "thermo_pod_cover"],
    "tof_pod": ["tof_pod_base", "tof_pod_cover"],
}


def material(name, colour, surface):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    r, g, bl = (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    b.inputs["Base Color"].default_value = (r ** 2.2, g ** 2.2, bl ** 2.2, 1)
    b.inputs["Roughness"].default_value = 0.32 if surface == "metal" else 0.45 if surface != "print" else 0.62
    b.inputs["Metallic"].default_value = 0.9 if surface == "metal" else 0.0
    return m


parts = {p["name"]: p for p in json.load(open(os.path.join(SOURCE, "assembly.json")))["parts"]}
os.makedirs(OUT, exist_ok=True)
for gname, names in GROUPS.items():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    objs = []
    for n in names:
        p = parts[n]
        bpy.ops.wm.stl_import(filepath=os.path.join(SOURCE, p["file"]))
        o = bpy.context.selected_objects[0]
        o.data.materials.append(material(n, p["colour"], p.get("surface", "print")))
        bpy.context.view_layer.objects.active = o
        bpy.ops.object.shade_auto_smooth(angle=math.radians(35))
        objs.append(o)
    bpy.context.view_layer.update()
    corners = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    lo = Vector([min(c[i] for c in corners) for i in range(3)])
    hi = Vector([max(c[i] for c in corners) for i in range(3)])
    centre = (lo + hi) / 2
    bpy.ops.mesh.primitive_plane_add(size=4000, location=(centre.x, centre.y, lo.z - 0.02))
    bpy.context.object.is_shadow_catcher = True
    cd = bpy.data.cameras.new("cam"); cd.type = "ORTHO"; cd.clip_end = 5000
    cam = bpy.data.objects.new("cam", cd); scene.collection.objects.link(cam); scene.camera = cam
    cam.rotation_euler = (-LOOK_FROM).to_track_quat("-Z", "Y").to_euler()
    cam.location = centre + LOOK_FROM * 900
    bpy.context.view_layer.update()
    inv = cam.matrix_world.inverted()
    loc = [inv @ c for c in corners]
    w = max(p.x for p in loc) - min(p.x for p in loc); h = max(p.y for p in loc) - min(p.y for p in loc)
    mid = Vector(((max(p.x for p in loc) + min(p.x for p in loc)) / 2, (max(p.y for p in loc) + min(p.y for p in loc)) / 2, 0))
    cam.location += cam.matrix_world.to_3x3() @ mid
    cd.ortho_scale = max(w, h) * 1.15
    for name, rot, energy, size in (("key", (math.radians(50), 0, math.radians(-40)), 2.2, 0.35),
                                    ("fill", (math.radians(65), 0, math.radians(110)), 0.6, 0.8)):
        L = bpy.data.lights.new(name, "SUN"); L.energy, L.angle = energy, size
        o = bpy.data.objects.new(name, L); o.rotation_euler = rot; scene.collection.objects.link(o)
    world = bpy.data.worlds.new("w"); world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.97, 0.97, 0.96, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.55
    scene.world = world
    scene.render.engine = "CYCLES"; scene.cycles.samples = 64; scene.cycles.use_denoising = True
    scene.render.resolution_x = scene.render.resolution_y = SIZE
    scene.render.film_transparent = True
    scene.view_settings.view_transform = "AgX"; scene.view_settings.exposure = 0.9
    scene.render.filepath = os.path.join(OUT, gname + ".png")
    bpy.ops.render.render(write_still=True)

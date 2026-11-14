"""Render the SK-1 enclosure with Blender: one picture assembled, one exploded.

Run enclosure.py with --assembly first, then from this folder:

    /Applications/Blender.app/Contents/MacOS/Blender -b -P render_views.py -- assembly renders

It writes renders/assembled_raw.png, renders/exploded_raw.png and renders/exploded_labels.json.
The JSON says where each part is in the exploded picture. label_views.py then puts both
pictures on a light ground and adds the part names to the exploded one.
"""

import json
import math
import os
import sys

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

SOURCE, OUT = sys.argv[sys.argv.index("--") + 1:][:2]
WIDTH, HEIGHT = 1800, 1350
LOOK_FROM = Vector((-0.62, -1.0, 0.58)).normalized()  # from the front left, above


def material(name, colour, metal=False, board=False):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes["Principled BSDF"]
    r, g, b = (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    bsdf.inputs["Base Color"].default_value = (r ** 2.2, g ** 2.2, b ** 2.2, 1)
    bsdf.inputs["Roughness"].default_value = 0.32 if metal else 0.45 if board else 0.62
    bsdf.inputs["Metallic"].default_value = 0.9 if metal else 0.0
    return m


def build(exploded):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    with open(os.path.join(SOURCE, "assembly.json")) as f:
        parts = json.load(f)["parts"]
    objects = []
    for part in parts:
        bpy.ops.wm.stl_import(filepath=os.path.join(SOURCE, part["file"]))
        obj = bpy.context.selected_objects[0]
        obj.name = part["name"]
        if exploded:
            obj.location = Vector(part["explode"])
        surface = part.get("surface", "print")
        obj.data.materials.append(material(part["name"], part["colour"], metal=surface == "metal", board=surface != "print"))
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.shade_auto_smooth(angle=math.radians(35))
        objects.append((part, obj))
    bpy.context.view_layer.update()

    corners = [obj.matrix_world @ Vector(c) for _, obj in objects for c in obj.bound_box]
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    centre = (lo + hi) / 2

    if not exploded:  # the assembled case stands on a table; the exploded parts float
        bpy.ops.mesh.primitive_plane_add(size=4000, location=(centre.x, centre.y, -0.02))
        bpy.context.object.is_shadow_catcher = True

    cam_data = bpy.data.cameras.new("camera")
    cam_data.type = "ORTHO"
    cam_data.clip_end = 5000
    cam = bpy.data.objects.new("camera", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.rotation_euler = (-LOOK_FROM).to_track_quat("-Z", "Y").to_euler()
    cam.location = centre + LOOK_FROM * 900
    bpy.context.view_layer.update()
    inv = cam.matrix_world.inverted()
    local = [inv @ c for c in corners]
    w = max(p.x for p in local) - min(p.x for p in local)
    h = max(p.y for p in local) - min(p.y for p in local)
    mid = Vector(((max(p.x for p in local) + min(p.x for p in local)) / 2,
                  (max(p.y for p in local) + min(p.y for p in local)) / 2, 0))
    cam.location += cam.matrix_world.to_3x3() @ mid
    cam_data.ortho_scale = max(w, h * WIDTH / HEIGHT) * (1.38 if exploded else 1.12)

    for name, rot, energy, size in (("key", (math.radians(50), 0, math.radians(-40)), 2.2, 0.35),
                                    ("fill", (math.radians(65), 0, math.radians(110)), 0.6, 0.8)):
        light = bpy.data.lights.new(name, "SUN")
        light.energy, light.angle = energy, size
        o = bpy.data.objects.new(name, light)
        o.rotation_euler = rot
        scene.collection.objects.link(o)
    world = bpy.data.worlds.new("world")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.97, 0.97, 0.96, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.55
    scene.world = world

    scene.render.engine = "CYCLES"
    scene.cycles.samples = 96
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = WIDTH, HEIGHT
    scene.render.film_transparent = True  # label_views.py puts the picture on a light ground
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.exposure = 0.9
    scene.render.image_settings.file_format = "PNG"
    name = "exploded" if exploded else "assembled"
    scene.render.filepath = os.path.join(OUT, name + "_raw.png")
    bpy.ops.render.render(write_still=True)

    if exploded:
        labels = []
        for part, obj in objects:
            # The leftmost and the rightmost point of the part in the picture: a label line ends there.
            seen = [world_to_camera_view(scene, cam, obj.matrix_world @ v.co) for v in obj.data.vertices]
            left, right = min(seen, key=lambda v: v.x), max(seen, key=lambda v: v.x)
            labels.append({"name": part["name"], "label": part["label"],
                           "left": [left.x, 1 - left.y], "right": [right.x, 1 - right.y]})
        with open(os.path.join(OUT, "exploded_labels.json"), "w") as f:
            json.dump({"width": WIDTH, "height": HEIGHT, "labels": labels}, f, indent=1)


os.makedirs(OUT, exist_ok=True)
build(exploded=False)
build(exploded=True)

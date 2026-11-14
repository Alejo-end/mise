"""Build the FreeCAD file of the SK-1 enclosure: the assembled case and an exploded view.

Run enclosure.py with --assembly first. It writes every part, in its assembled place, to
assembly/. Then run this file with FreeCAD's command-line program, from this folder:

    SK1_DIR="$PWD" /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd freecad_assembly.py

It writes cad/sk1_assembly.FCStd with two groups, "Assembled" and "Exploded", and
cad/sk1_exploded.step. enclosure.py stays the one source of the geometry: this file only
turns its meshes into solids, so the printed parts and the CAD file cannot drift apart.
The solids keep the facets of the meshes, so a round hole is a polygon with many sides.
"""

import json
import os

import FreeCAD as App
import Import
import Mesh
import Part

HERE = os.environ.get("SK1_DIR") or os.getcwd()
SOURCE = os.path.join(HERE, "assembly")
OUT = os.path.join(HERE, "cad")


def solid_from_stl(path):
    """A solid from a closed mesh. Flat faces that the mesh split into triangles are merged again."""
    mesh = Mesh.Mesh(path)
    shape = Part.Shape()
    shape.makeShapeFromMesh(mesh.Topology, 0.01)
    shape = shape.removeSplitter()
    try:
        return Part.makeSolid(shape)
    except Exception:
        return shape  # an open mesh stays a shell; the view still works


def rgb(colour):
    return tuple(int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))


def main():
    with open(os.path.join(SOURCE, "assembly.json")) as f:
        manifest = json.load(f)
    os.makedirs(OUT, exist_ok=True)
    doc = App.newDocument("SK1")
    assembled = doc.addObject("App::DocumentObjectGroup", "Assembled")
    exploded = doc.addObject("App::DocumentObjectGroup", "Exploded")
    exploded_parts = []
    for part in manifest["parts"]:
        solid = solid_from_stl(os.path.join(SOURCE, part["file"]))
        for group, suffix, move in ((assembled, "", (0, 0, 0)), (exploded, "_exploded", part["explode"])):
            obj = doc.addObject("Part::Feature", part["name"] + suffix)
            obj.Label = part["label"] + (" (exploded)" if suffix else "")
            obj.Shape = solid
            obj.Placement.Base = App.Vector(*move)
            group.addObject(obj)
            if App.GuiUp:  # colours live in the view, which exists only with the window open
                obj.ViewObject.ShapeColor = rgb(part["colour"])
            if suffix:
                exploded_parts.append(obj)
        print(part["name"], "solid" if solid.ShapeType == "Solid" else solid.ShapeType, len(solid.Faces), "faces")
    doc.recompute()
    doc.saveAs(os.path.join(OUT, "sk1_assembly.FCStd"))
    Import.export(exploded_parts, os.path.join(OUT, "sk1_exploded.step"))
    print("wrote", OUT)


main()

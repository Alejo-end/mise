# SK-1 enclosure

This folder holds a 3D-printable case for the Safe Kitchen Agent. The main case holds the Arduino UNO Q, the Modulino Knob and the Modulino Buzzer. A separate vented pod holds the Modulino Thermo. The pod lets the Thermo sit in the cold room or in the delivery crate, away from the heat of the UNO Q.

`enclosure.py` builds all parts. Change a number at the top of the file and run it again to get new STL files.

```
uv run --no-project --with manifold3d --with numpy --with matplotlib python enclosure.py --preview
```

The script also runs a clash check. If no board model touches a printed part, it reports "OK".

## Exploded view

![The parts of the case and the Thermo pod, pulled apart](renders/exploded.png)

`renders/assembled.png` shows the closed case. `cad/sk1_assembly.FCStd` is the same assembly as a FreeCAD file, with one group for the assembled case and one for the exploded view.

The board models in the pictures are simple stand-ins. The headers, the USB-C port and the LED matrix sit where the Arduino CAD data puts them. The two chips on the UNO Q are drawn for the look only.

To make the pictures and the FreeCAD file again, run these four commands from this folder. You need FreeCAD and Blender.

```
uv run --no-project --with manifold3d --with numpy --with matplotlib python enclosure.py --assembly
/Applications/Blender.app/Contents/MacOS/Blender -b -P render_views.py -- "$PWD/assembly" "$PWD/renders"
uv run --no-project --with pillow python label_views.py
SK1_DIR="$PWD" /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd freecad_assembly.py
```

`enclosure.py` stays the one source of the geometry. The FreeCAD file is built from its meshes, so a round hole in it is a polygon with many sides. The last command also writes `cad/sk1_exploded.step`. Git ignores that file because of its size (22 MB).

## Where the dimensions come from

The board geometry comes from the official Arduino CAD files, not from drawings. Each board page on docs.arduino.cc has a "CAD files" download.

| Board | Source |
|---|---|
| UNO Q (ABX00162) | Pad coordinates from the IPC-D-356 netlist and the drill file |
| Modulino Knob (ABX00107) | STEP model |
| Modulino Buzzer (ABX00108) | STEP model |
| Modulino Thermo (ABX00103) | STEP model |

The UNO Q download has no 3D model. The heights of the UNO Q parts come from the datasheet drawing: 8.5 mm for the headers and 5 mm for the connectors under the board.

## Parts

| File | Size (mm) | Print orientation |
|---|---|---|
| `stl/base.stl` | 144 x 68 x 18.7 | Floor on the bed |
| `stl/lid.stl` | 144 x 68 x 4.5 | Top face on the bed |
| `stl/thermo_pod_base.stl` | 53 x 29.4 x 9.2 (with hanging tab) | Floor on the bed |
| `stl/thermo_pod_cover.stl` | 45 x 29.4 x 5.5 | Top face on the bed |

No part needs supports. Use 0.2 mm layers, 3 walls and 20 % infill. Print the case and the pod in a matte light gray or white filament. The labels are 0.6 mm deep in the face that lies on the bed, so use a smooth or satin print sheet.

`print/` holds the sliced files for a Prusa Core One with the 0.4 mm high-flow nozzle and PLA. The plate takes 2 h 30 min and 72 g.

## Design

The look follows small desktop audio hardware: one flat slab, with every feature on a grid.

- The front row holds the name, the LED matrix window and the knob on one line.
- The back row holds the power button, the vent, the status lights and the speaker on one line.
- The vent, the speaker grille and the pod grille use the same dots: 1.5 mm holes on a 2.5 mm grid.
- The vent is as wide as the LED window and sits directly behind it.
- Four M3 screws sit in the four corners of the lid.
- The labels are lowercase full words, engraved: `safe kitchen agent`, `power`, `limit`, `alert` and `temperature`.

## Layout

The UNO Q sits on the left on 6 mm standoffs. The standoffs clear the 5 mm connectors under the board. The USB-C port faces a 13 x 7.5 mm opening in the left wall. You need a USB-C hub with power pass-through because the camera and the power share the one USB-C port.

The lid has a window over the LED matrix, with 1 mm of margin on each side. Four 1.2 mm holes sit over the four RGB status LEDs of the UNO Q. A 4.5 mm hole sits over the power button. Press the button with a pen tip.

The Knob and the Buzzer sit on standoffs on the base floor. The Knob standoffs are 5.9 mm tall. At this height, the smooth bushing of the encoder ends 0.2 mm below the lid surface. The Buzzer standoffs are 11.5 mm tall. At this height, the buzzer sits 0.6 mm under the lid.

The encoder shaft comes through the lid. The case has no knob cap. Any cap for a 6 mm D-shaft fits.

The Thermo pod closes with four screws. Each screw goes through the cover, a spacer, the Thermo board and into the pod base.

## Assembly

1. Screw the UNO Q, the Knob and the Buzzer to their standoffs.
2. Connect the UNO Q Qwiic port to the left port of the Knob.
3. Connect the right port of the Knob to the right port of the Buzzer.
4. Connect the left port of the Buzzer to the Thermo with a 500 mm cable.
5. Lay the Thermo cable in the notch at the top of the back wall.
6. Put the lid on and fasten the four corner screws. The lid holds the cable in place.
7. Put the Thermo in the pod base with the cable in the slot, then fasten the cover.

## Hardware

| Item | Quantity |
|---|---|
| M3 x 6 self-tapping screw (UNO Q, Knob, Buzzer) | 12 |
| M3 x 10 self-tapping screw (lid, pod) | 8 |
| Rubber foot, 10 mm diameter | 4 |
| Qwiic cable, 50 mm or 100 mm | 2 |
| Qwiic cable, 500 mm | 1 |

The pilot holes are 2.5 mm.

## Before you print many

The positions are exact, but a printer is not. Make sure that these two fits are correct on the first print:

1. The lid in the base. If the lid is too tight, increase `LIP_CLR`.
2. The USB-C plug of your hub in the port opening.

`uno_q_template.svg` is a full-size drawing of the UNO Q. Print it at 100 % and lay the board on it to compare the positions.

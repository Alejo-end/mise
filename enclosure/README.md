# SK-1 enclosure

This folder holds a 3D-printable case for the Safe Kitchen Agent. The main case holds the Arduino UNO Q, the Modulino Knob and the Modulino Buzzer. Two small pods hold the outside sensors:

1. The Thermo pod holds the Modulino Thermo. The Thermo can then sit in the cold room or in the delivery crate, away from the heat of the UNO Q.
2. The distance pod holds the SparkFun Qwiic ToF Imager VL53L5CX (SEN-18642, the 1 x 1 inch board). It looks at the shelf through a window in its cover.

No part needs a screw. The boards sit on pins, and each lid snaps on.

`enclosure.py` builds all parts. Change a number at the top of the file and run it again to get new STL files.

```
uv run --no-project --with manifold3d --with numpy --with matplotlib python enclosure.py --preview
```

The script also runs a clash check with three tests. No board may touch a printed part. No lid may touch its base. No printed part may stand in the view of the distance sensor. If all three pass, the check reports "OK".

## Exploded view

![The parts of the case and the two pods, pulled apart](renders/exploded.png)

`renders/assembled.png` shows the closed case. `cad/sk1_assembly.FCStd` is the same assembly as a FreeCAD file, with one group for the assembled case and one for the exploded view.

The board models in the pictures are simple stand-ins. The headers, the USB-C port and the LED matrix sit where the Arduino CAD data puts them. The two chips on the UNO Q are drawn for the look only.

To make the pictures and the FreeCAD file again, run these four commands from this folder. You need FreeCAD and Blender.

```
uv run --no-project --with manifold3d --with numpy --with matplotlib python enclosure.py --assembly
/Applications/Blender.app/Contents/MacOS/Blender -b -P render_views.py -- "$PWD/assembly" "$PWD/renders"
uv run --no-project --with pillow python label_views.py
SK1_DIR="$PWD" /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd freecad_assembly.py
```

`enclosure.py` stays the one source of the geometry. The FreeCAD file is built from its meshes, so a round hole in it is a polygon with many sides. The last command also writes `cad/sk1_exploded.step`. Git ignores that file because of its size.

## Where the dimensions come from

The board geometry comes from the CAD files of the makers, not from drawings.

| Board | Source |
|---|---|
| Arduino UNO Q (ABX00162) | Pad coordinates from the IPC-D-356 netlist and the drill file |
| Modulino Knob (ABX00107) | STEP model |
| Modulino Buzzer (ABX00108) | STEP model |
| Modulino Thermo (ABX00103) | STEP model |
| SparkFun Qwiic ToF Imager VL53L5CX (SEN-18642) | Eagle board file `Hardware/SparkFun Qwiic ToF Imager - VL53L5CX.brd` in [the SparkFun repository](https://github.com/sparkfun/SparkFun_Qwiic_ToF_Imager_VL53L5CX) |

Each Arduino board page on docs.arduino.cc has a "CAD files" download. The UNO Q download has no 3D model. The heights of the UNO Q parts come from the datasheet drawing: 8.5 mm for the headers and 5 mm for the connectors under the board.

The SparkFun file gives the outline of 25.4 x 25.4 mm and four 3.302 mm holes, 2.54 mm from each edge. It also puts the VL53L5CX in the centre and the two Qwiic sockets on the left and right edges. It does not give the board thickness, so the script uses the usual 1.6 mm. The module size, 6.4 x 3.0 x 1.5 mm, comes from the ST datasheet. The Mini board (SEN-19013, 0.5 x 1 inch) has another shape and does not fit this pod.

## Parts

| File | Size (mm) | Print orientation |
|---|---|---|
| `stl/base.stl` | 144 x 68 x 18.7 | Floor on the bed |
| `stl/lid.stl` | 144 x 68 x 2.5, with a lip and rods under it | Top face on the bed |
| `stl/thermo_pod_base.stl` | 47.4 x 31.8 x 9.2, with a hanging tab | Floor on the bed |
| `stl/thermo_pod_cover.stl` | 47.4 x 31.8 x 2.0, with a lip under it | Top face on the bed |
| `stl/tof_pod_base.stl` | 31.8 x 31.8 x 9.2, with a hanging tab | Floor on the bed |
| `stl/tof_pod_cover.stl` | 31.8 x 31.8 x 2.0, with a lip under it | Top face on the bed |

No part needs supports. Use 0.2 mm layers, 3 walls and 20 % infill. Print the case and the pods in a matte light gray or white filament. A matte surface also sends less stray light from the window edge into the distance sensor. The labels are 0.6 mm deep in the face that lies on the bed, so use a smooth or satin print sheet.

`print/` holds the sliced files of the first version, which used screws. Slice the new STL files before you print.

## Design

The look follows small desktop audio hardware: one flat slab, with every feature on a grid.

- The front row holds the name, the LED matrix window and the knob on one line.
- The back row holds the power button, the vent, the status lights and the speaker on one line.
- The vent, the speaker grille and the pod grille use the same dots: 1.5 mm holes on a 2.5 mm grid.
- The vent is as wide as the LED window and sits directly behind it.
- The lid has no screws. It snaps on.
- The name `mise` stands left of the LED window in cursive letters with one stroke width. The font is Borel, in `fonts/` under the SIL Open Font License. The front wall of the base says `en place` below it, in the same letters. All other labels use DIN.
- The labels are lowercase full words, engraved: `mise`, `power`, `limit`, `alert`, `temperature` and `distance`.

## Layout

The UNO Q sits on the left on 6 mm standoffs. The standoffs clear the 5 mm connectors under the board. The USB-C port faces a 13 x 7.5 mm opening in the left wall. You need a USB-C hub with power pass-through because the camera and the power share the one USB-C port.

The lid has a window over the LED matrix, with 1 mm of margin on each side. Four 1.2 mm holes sit over the four RGB status LEDs of the UNO Q. A 4.5 mm hole sits over the power button. Press the button with a pen tip.

The Knob and the Buzzer sit on standoffs on the base floor. The Knob standoffs are 5.9 mm tall. At this height, the smooth bushing of the encoder ends 0.2 mm below the lid surface. The Buzzer standoffs are 11.5 mm tall. At this height, the buzzer sits 0.6 mm under the lid.

The encoder shaft comes through the lid. The case has no knob cap. Any cap for a 6 mm D-shaft fits.

## How the parts hold without screws

1. A pin on each standoff goes up through the mounting hole of the board. The pin is 0.35 mm thinner than the hole, so the board drops on.
2. Under the lid, a rod comes down over each pin and stops 0.2 mm above the board. So a closed lid holds every board in place. The rods stand where the screw heads stood in the first version.
3. A lip under the lid goes 3.5 mm into the walls, with 0.1 mm of clearance on each side. Two bumps on the front and two on the back click into grooves in the walls.
4. A finger notch at the top of each end wall lets you lift the lid.

The pods work the same way, with a 3 mm lip, one bump on each long side and a finger notch in the front wall.

## Openings for the sensor cables

The back wall of the case has a 16 mm wide opening between the UNO Q and the Modulinos. It is open at the top, so you lay a cable in with its plug on. A tongue under the lid closes the opening to a 2.2 mm gap. The gap holds two or three Qwiic cables side by side and stops them from pulling out.

Each pod has a cable slot in both end walls, at the two Qwiic sockets. So one cable from the case can feed both pods: the case, then the distance pod, then the Thermo pod.

## The window of the distance pod

The VL53L5CX sees a 63 degree cone on the diagonal. The window above it widens upward at 34 degrees from the axis, so its edge stays out of that cone. If a printed part stands in the cone up to 30 mm out, the clash check fails.

Do not put glass or film over the window. A cover glass needs the crosstalk calibration of the ST driver.

The VL53L5CX has the I2C address 0x29. The Modulinos use other addresses.

## Assembly

1. Put the UNO Q, the Knob and the Buzzer on their pins.
2. Connect the UNO Q Qwiic port to the left port of the Knob.
3. Connect the right port of the Knob to the right port of the Buzzer.
4. Connect the left port of the Buzzer to the distance pod with a 500 mm cable. Lay the cable in the opening of the back wall.
5. Press the lid down until the four bumps click.
6. Remove the tape from the VL53L5CX. Put the board on the pins of its pod, sensor up.
7. Connect the cable from the case to one socket. Connect the other socket to the Thermo with a second 500 mm cable. Lay both cables in the slots, then press the cover on.
8. Put the Thermo on the pins of its pod, connect the cable, and press the cover on.

## Hardware

| Item | Quantity |
|---|---|
| Rubber foot, 10 mm diameter | 4 |
| Qwiic cable, 50 mm or 100 mm | 2 |
| Qwiic cable, 500 mm | 2 |

## Before you print many

The positions are exact, but a printer is not. Print the base and the lid once, and make sure that these fits are correct:

1. The lid in the base. The lip has 0.1 mm of clearance on each side, which is very snug. If the lid does not go in, increase `LIP_CLR` to 0.15. If the bumps click too hard, reduce `SNAP` from 0.30 to 0.20.
2. The boards on their pins. If a board does not drop on, increase `PIN_CLR`.
3. The USB-C plug of your hub in the port opening.

`uno_q_template.svg` is a full-size drawing of the UNO Q. Print it at 100 % and lay the board on it to compare the positions.

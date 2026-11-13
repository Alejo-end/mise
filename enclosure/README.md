# Safe Kitchen Agent enclosure

This folder holds a 3D-printable case for the Arduino UNO Q with the Modulino Knob and the Modulino Buzzer. It also holds a separate vented pod for the Modulino Thermo. The pod lets the Thermo sit in the cold room or in the delivery crate, away from the heat of the UNO Q.

`enclosure.py` builds all parts from the official datasheet dimensions. Change a number at the top of the file and run it again to get new STL files.

```
uv run --no-project --with manifold3d --with numpy --with matplotlib python enclosure.py --preview
```

The script also runs a clash check. If no board stand-in touches a printed part, it reports "OK".

## Parts

| File | Size (mm) | Print orientation |
|---|---|---|
| `stl/base.stl` | 142.6 x 59.3 x 21.0 | Floor on the bed |
| `stl/lid.stl` | 142.6 x 59.3 x 4.5 | Top face on the bed |
| `stl/thermo_pod_base.stl` | 53.2 x 29.6 x 12.0 (with hanging tab) | Floor on the bed |
| `stl/thermo_pod_cover.stl` | 45.2 x 29.6 x 3.1 | Top face on the bed |

No part needs supports. Use PLA or PETG, 0.2 mm layers, 3 walls, 20 % infill. If the case stays in a cold, damp room, use PETG.

## Layout

The UNO Q sits on the left on 6.5 mm standoffs. The standoffs clear the 5 mm connectors on the bottom of the board. The USB-C port faces a 14 x 9 mm opening in the left wall. The opening is large enough for a USB-C hub plug. You need a hub with power pass-through because the camera and the power share the one USB-C port.

The lid has a window over the LED matrix and a 5 mm hole over the power button. Press the button with a pen tip.

The Knob and the Buzzer sit on screw standoffs on the base floor, 8.3 mm and 12.4 mm tall. You can wire them with the lid off. The Knob collar comes through an 8.2 mm hole in the lid. Push the knob cap onto the shaft after you close the lid. The Buzzer sits under a seven-hole grille.

## Qwiic wiring

1. Connect the UNO Q Qwiic port to the left port of the Knob.
2. Connect the right port of the Knob to the right port of the Buzzer with a 50 mm cable.
3. Connect the left port of the Buzzer to the Thermo with a 500 mm cable.
4. Lay the Thermo cable in the notch at the top of the back wall. The lid holds the cable in place.

## Hardware

| Item | Quantity |
|---|---|
| M3 x 6 self-tapping screw (UNO Q, Modulinos, pod) | 16 |
| M3 x 8 self-tapping screw (lid) | 4 |
| Rubber foot, 10 mm diameter | 4 |
| Qwiic cable, 50 mm | 2 |
| Qwiic cable, 500 mm | 1 |

The pilot holes are 2.5 mm. M2.5 machine screws also fit.

## Before the final print

Some values come from measurements on the datasheet drawings, not from labeled dimensions. The script marks them `MEASURED`. Measure them on the real boards with calipers. The most important values are these:

1. `KNOB_COLLAR_TOP`, the height of the knob collar top above the Knob PCB. It sets the Knob standoff length.
2. `UNO_USB_Y`, the position of the USB-C port.
3. `UNO_MATRIX`, the position of the LED matrix.

Print the lid first. It is the part that depends most on these values.

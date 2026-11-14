# The touch screen of the box

A Guition JC4827W543C: an ESP32-S3 with a 4.3 inch touch display of 480 by 272 pixels. It answers one question that the knob cannot: what am I accepting?

The screen joins the Wi-Fi and asks the backend what is at the door (`GET /box/screen`). It shows the order, its lines, the air temperature at the box and the storage limit of the order's coldest product. A person taps "Accept" or "Reject", and then "Yes". The backend writes the decision into the log and the order record, with the temperature. The screen can also confirm the cold alarm.

The screen has no PIN. It never approves an order and never spends money. That stays on the phone.

## Build and flash

1. Copy `secrets.example.h` to `secrets.h`. Fill in the Wi-Fi name, the password and the address of the backend. Git ignores `secrets.h`.
2. Connect the screen to the laptop with a USB-C data cable.
3. Compile and upload. Replace the port with the one that `arduino-cli board list` shows.

```
arduino-cli compile --fqbn esp32:esp32:esp32s3:FlashSize=4M,PSRAM=opi,PartitionScheme=huge_app,CDCOnBoot=cdc screen
arduino-cli upload  --fqbn esp32:esp32:esp32s3:FlashSize=4M,PSRAM=opi,PartitionScheme=huge_app,CDCOnBoot=cdc -p /dev/cu.usbmodem101 screen
```

The libraries are "GFX Library for Arduino" 1.6.9 and ArduinoJson 7.

## Hardware facts that the sketch relies on

These come from a bring-up of this exact board with other firmware.

- The panel is an NV3041A on four data lines. It needs the start-up table of the factory firmware, which is in `src/factory_init_table.h`. The table of the display library differs.
- Colour inversion is off. The picture is upright with USB-C on the left.
- The touch chip is a GT911 at address 0x5D on pins 8 and 4. The sketch reads it and never configures it.
- Pin 1 switches the backlight.

## Touch calibration

At its first start the screen shows four crosses, one after another. Tap the centre of each one. The screen works out how a touch reading becomes a place on the display, also for swapped or mirrored axes, and keeps the result in its flash. On this board both axes came out mirrored, with a scale within 5 % of 1.

To calibrate again, hold a finger on the name "mise" for 3 seconds. The backend can also ask for it: `POST /box/screen/recalibrate`. The screen then shows the crosses within 2 seconds.

## What is tested

On 14 November the sketch ran on the board. The display showed the right colours. The screen joined the Wi-Fi and fetched its view every 2 seconds. After the calibration, taps landed on the buttons, and four decisions from the screen reached the backend's log with the temperature. The backend view and the decisions have tests.

Not tested: a long run, the battery connector, and the screen in a cold room.

## Wiring

None for data. Power the screen from its own USB-C. Do not put a Qwiic cable into the small 4-pin socket: it joins the 3.3 V lines of the two boards.

The fonts in `src/fonts` are from the Adafruit GFX Library (BSD License).

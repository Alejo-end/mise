# USB camera on the UNO Q: preview tearing diagnosis (14 Nov 2026, 10:56 to 11:14 EET)

Done by the integration session over SSH (Wi-Fi), after an Arduino mentor replaced the faulty USB hub/dongle.
No firmware change, no rewrite. Raw files, contact sheets and logs (not in git):
`/Users/bigboss/Ai/Claudecode/valio-board-backups/2026-11-14/camera-diag-1059/`; app data backup before any change:
`.../pre-camera-diag-1056/apps-data.tgz`.

## Hardware and modes

| Item | Value |
|---|---|
| Camera | "Web Camera", USB 1c45:6200 (HHWei Technology), UVC 1.00, `/dev/video2` (plus `/dev/video3`, metadata) on port 1-1.1 |
| Hub / dongle | two hub chips 343c:0210, RTL8152 Ethernet 0bda:8152; a Mac (05ac:1905) attached at 1-1.3 at times |
| Modes | MJPG 320x240 to 4656x3496 at 5 to 30 fps; YUYV 640x480 and 320x240 at 5 to 30 fps |
| Mode left by the Arduino example | YUYV 640x480, 10 fps |
| Mode used by our app (`Camera(resolution=(640,480), fps=15)`) | YUYV 640x480, 15 fps |

## Observations (what was measured, not causes)

1. **Minimal capture is clean.** `v4l2-ctl --stream-mmap` straight from `/dev/video2`, no app, no AI, no browser:
   YUYV 640x480 at 10 fps (30 frames, all complete), MJPG 640x480 at 10 fps (30 frames), MJPG at 30 fps requested
   (90 frames, delivered at about 24 fps), YUYV at 30 fps requested (about 24 fps, 3 dropped buffers). No decode
   errors, no horizontal seam in any frame (largest row-to-row jump 3.3 x the median; a tear would stand out), no
   new kernel USB errors during capture.
2. **The Arduino example's own frames are clean.** "Detect Objects on Camera"
   (`examples:inspirational/video-generic-object-detection`, bricks `video_object_detection` and `web_ui`) at the
   same mode: its socket.io `image` messages on port 4912 (the frames it sends to the page, before any browser):
   149 JPEG frames in 15 s at 10.0 fps, no decode errors, no seams, no frame made of two neighbours. Delivery is
   uneven: gaps from 0.001 s to 0.319 s (median 0.096 s), so frames sometimes arrive in pairs (**choppy playback**).
   Load: inference container about 152 % CPU (yolo about 59 %, node 39 %, gstreamer 12 %); 942 MiB available.
3. **In a browser** (headless Brave, example page on port 7000): the video iframe `/embed` was loaded 3 times in the
   first 12 s and then stayed; the image changed about 8 times per second. Headless screenshots cannot show
   display-level tearing, so **a browser or display-only artifact was not ruled out and not reproduced**.
4. **Our shelf app (USB variant `safe-kitchen-agent`) works with this camera:** camera OK, frame age 0.0 to 0.1 s,
   YUYV 640x480 at 15 fps, about 53 % of one core, 94 MiB; its preview frames (port 7000, before the browser): 90
   frames in 15 s at 6 fps (set to 8), clean, no seams.
5. **Board stability:** four boots between 10:31 and 10:57 local time. The boot before the current one ended
   without shutdown messages 23 s after a Mac appeared on USB as a CDC-NCM network device (`bind() failure`), which
   looks like a power loss or an unplug; the logs cannot tell which. At boot the camera port once failed
   enumeration ("device not accepting address, error -71") and succeeded on retry.

## Reading

No corruption or tearing was found in any captured frame: not from the camera, not from the Arduino example's
pipeline, not from our app. What remains possible: choppy delivery (measured), a browser or display artifact (not
measurable headless), or an effect of the unstable power/USB period around the reboots. If the tearing shows again,
note the time, the browser and the device showing it, and whether it also shows in another browser.

## For the Arduino mentor (reproduction)

Board "Junction", app-bricks images 0.13.2; example `examples:inspirational/video-generic-object-detection`
started with `arduino-app-cli app start ...`; camera 1c45:6200 at YUYV 640x480 10 fps through the replaced hub.
Captured frames at every stage are clean; the visible artifact was not reproduced in captured data. Open points:
the reboots without shutdown, and the single boot-time enumeration error (-71) on the camera port.

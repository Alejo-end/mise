# Board measurement: template baseline on the Arduino UNO Q (14 Nov 2026, 07:41 EET)

Measured by the integration session over SSH, read-only (`top -b -n 2 -d 5`, `docker stats --no-stream`,
`free -m`), while the deployed app `safe-kitchen-test` (main.py c60b68271b87, shelf_detect.py 7095fd9ead9a)
ran the two-photo template detector on frames streamed from the laptop camera page.

| Quantity | Value |
|---|---|
| Device | Arduino UnoQ, 4 CPU cores, 1,737 MiB RAM (1,179 MiB available) |
| App process CPU | 13.5 % of one core (python, 5 s sample) |
| App container | 13.2 % CPU, 77.5 MiB memory (docker stats) |
| App process resident memory | 131,664 KiB |
| Container network since start | 53.6 MB in (camera frames from the laptop), 1.95 MB out |

Not measured yet: per-frame classification time, frames per second actually processed, power. No
learned model has run on the board; any local-feasibility claim for one needs its own measurement here,
after its accuracy is established on real recordings.

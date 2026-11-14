# Demo shelf setup: one camera, one visible row per product

**Proposed by Anatoly, 14 November 2026 — pending team agreement.**

Not implemented beyond documentation; the procedure has not been performed yet. The capture steps are in
`../CAPTURE_GUIDE.md`, sections 3a and 6.

## Proposed setup

- One stationary camera.
- Three product zones as today: chips (slot 1), energy drink (slot 2), milk (slot 3).
- Several units of one product stand **side by side in one visible row** inside its zone, with small gaps: no
  stacking, no unit behind another.
- The camera is placed so that every unit and its identifying packaging are visible.
- At least one product is tested with 2 or 3 identical units, depending on what is available.

## Why: a visibility constraint for the prototype

The board's current detector compares each zone with two saved photos (empty and full). It estimates a fill level,
not a number of units, and it is **not a validated object counter**. Keeping every unit visible in one row is a
constraint that makes counting by camera possible to evaluate at all; it is not a claim that counting works.
Stock that is fully hidden (behind another unit, stacked, outside the view) may be unobservable: the system cannot
know it is there and cannot be relied on to notice that something is hidden. Depth estimation and a second camera
remain future options; no hardware integration is planned now.

## What it changes

- Capture: clean-layout clips (one visible row, counts typed by the person) are evaluated separately from stress
  clips (hand over a zone, unfamiliar item, relocation, light).
- No change to the device configuration, the calibration, the purchasing behaviour or the recording mode.

## Decision for the team

Do we demo the shelf with this one-row constraint stated out loud, and which product carries the multi-unit test?

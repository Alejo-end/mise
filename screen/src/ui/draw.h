// The look of the screen: colours, type, and the functions that draw a whole frame from the model.
#pragma once
#include "canvas.h"
#include "model.h"

namespace ui {

// The palette. One accent blue for action and selection, pink only for the cold alarm and "too warm",
// green only for "fine", amber only for a slot that runs low.
namespace col {
const uint16_t BG = rgb(0x0A0F1C);        // the page
const uint16_t SURFACE = rgb(0x121A2B);   // cards
const uint16_t RAISED = rgb(0x1A2438);    // a selected row, the sheet, the active tab
const uint16_t HAIRLINE = rgb(0x24304A);  // the one line colour
const uint16_t EDGE = rgb(0x2C3A58);      // the edge of a raised surface
const uint16_t TEXT = rgb(0xF2F5FA);
const uint16_t TEXT2 = rgb(0x8B97AD);     // captions: at least 4.5 to 1 on every surface
const uint16_t TEXT3 = rgb(0x5B6780);     // decoration only, never needed to read
const uint16_t BLUE = rgb(0x4F7DFF);      // accent strokes and text
const uint16_t BLUE_FILL = rgb(0x3A66EA); // filled buttons: white text at 4.9 to 1
const uint16_t BLUE_DOWN = rgb(0x2F55C8);
const uint16_t PINK = rgb(0xFF4D8D);
const uint16_t PINK_FILL = rgb(0xD42C65); // filled pink: white text at 4.8 to 1
const uint16_t PINK_DOWN = rgb(0xB82558);
const uint16_t GREEN = rgb(0x2FD39A);
const uint16_t AMBER = rgb(0xFFB547);
const uint16_t BLACK = rgb(0x000000);
const uint16_t WHITE = rgb(0xFFFFFF);
}  // namespace col

// The degree sign of the atlases, as a string to join with others: "6" DEG.
#define DEG "\x80"

// What a frame needs next: another frame soon (a motion runs), or a slow loop (the breathing dots).
struct Frame {
  bool animating = false;
  bool looping = false;
};

// The whole screen for the model at the clock time now (milliseconds).
Frame render(Canvas &c, const Model &m, uint32_t now);
// The start picture while the screen joins the network.
void renderSplash(Canvas &c, const char *line);
// One target of the touch calibration, at a place on the display.
void renderCalibration(Canvas &c, int step, int x, int y);

}  // namespace ui

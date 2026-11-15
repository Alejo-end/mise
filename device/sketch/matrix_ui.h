// The face of the box: what the 13 x 8 LED matrix of the UNO Q shows.
//
// Plain C++ with no Arduino calls, so the same code runs in the sketch and in
// tools/matrix_preview.cpp, which draws every screen as a picture for review.
//
// The matrix does two jobs and leaves the data to the touch screen:
//   1. The spend limit that the knob sets: "40€", and a level bar from 0 to 300 euro in row 7.
//   2. One status symbol when something needs a person, by priority:
//        cold alarm              a thermometer whose mercury blinks, a heat mark, and the °C
//        a delivery at the door  a parcel that bobs
//        an order to approve     a ring with "!" that breathes
//      With no status, the face stays on the limit.
// A turn of the knob shows the limit at once, for a few seconds. A hold starts a delivery check:
// a thermometer and the 60 seconds left. A tap does nothing, so a knock never triggers an action.
// Brightness runs from 0 (off) to 7 (full).

#pragma once
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace ui {

const int ROWS = 8, COLS = 13;
typedef uint8_t Frame[ROWS][COLS];

// Timing, in milliseconds.
const unsigned long HOLD_SHOW_MS = 300;    // a press longer than this shows the hold bar
const unsigned long HOLD_MS = 1500;        // a hold this long starts the delivery check
const unsigned long ROLL_MS = 120;         // a changed digit rolls in
const unsigned long SHOW_LIMIT_MS = 4000;  // after a touch the limit stays this long, also over a status
const unsigned long DONE_MS = 900;         // the answer to a completed hold
const unsigned long CHECK_MS = 60000;      // a delivery check measures this long (DELIVERY_SECONDS in main.py)
const unsigned long BOOT_MS = 700;
const unsigned long IDLE_DIM_MS = 60000;   // after a minute without a touch the limit dims

struct State {
  int limitEur = 40, prevLimitEur = 40;
  unsigned long limitAt = 0;                // when the limit changed
  bool tempKnown = false;
  float tempC = 0;
  bool alarm = false, approval = false;     // from the buzzer mode that main.py sets
  bool delivery = false;                    // from main.py: a delivery waits for a decision at the door
  bool holding = false;
  unsigned long holdAt = 0;
  bool done = false;
  unsigned long doneAt = 0;
  bool checking = false;
  unsigned long checkAt = 0;
  unsigned long touchAt = 0, bootAt = 0;
};

// ------------------------------------------------------------------ glyphs ----
// Each row is a bit pattern, the leftmost pixel in the highest used bit.
const uint8_t DIGIT[10][5] = {
    {7, 5, 5, 5, 7}, {2, 6, 2, 2, 7}, {7, 1, 7, 4, 7}, {7, 1, 3, 1, 7}, {5, 5, 7, 1, 1},
    {7, 4, 7, 1, 7}, {7, 4, 7, 5, 7}, {7, 1, 2, 2, 2}, {7, 5, 7, 5, 7}, {7, 5, 7, 1, 7}};
const uint8_t EURO[5] = {3, 2, 3, 2, 3};                                  // 2 wide
const uint8_t THERMO[7] = {2, 5, 5, 5, 5, 7, 7};                          // 3 wide, the bulb at the bottom
const uint8_t PARCEL[6] = {0x7F, 0x49, 0x7F, 0x41, 0x41, 0x7F};           // 7 wide: lid with tape, then the box
const uint8_t ATTENTION[7] = {0x1C, 0x22, 0x49, 0x49, 0x41, 0x2A, 0x1C};  // 7 wide: a ring with "!"

inline void put(Frame f, int x, int y, int level) {
  if (x < 0 || x >= COLS || y < 0 || y >= ROWS || level <= 0) return;
  if (level > 7) level = 7;
  if (level > f[y][x]) f[y][x] = (uint8_t)level;
}

inline void glyph(Frame f, const uint8_t *rows, int h, int w, int x, int y, int level) {
  for (int r = 0; r < h; r++)
    for (int c = 0; c < w; c++)
      if (rows[r] & (1 << (w - 1 - c))) put(f, x + c, y + r, level);
}

inline void digit(Frame f, int d, int x, int y, int level) { glyph(f, DIGIT[d], 5, 3, x, y, level); }

inline float pulse(unsigned long t, unsigned long period) {   // 0..1, smooth
  return 0.5f + 0.5f * sinf(6.2832f * (float)(t % period) / (float)period);
}

// ------------------------------------------------------------------- faces ----
// The spend limit, right aligned without leading zeros, the euro sign dimmer than the amount, and
// the level bar in row 7. A digit that changed rolls in from above (higher) or below (lower).
inline void limitFace(const State &s, unsigned long now, Frame f) {
  char cur[4], old[4];
  int n = snprintf(cur, sizeof cur, "%d", s.limitEur);
  int on = snprintf(old, sizeof old, "%d", s.prevLimitEur);
  int width = n * 4 - 1 + (n < 3 ? 1 : 0) + 2;
  int x0 = (COLS - width) / 2;
  unsigned long age = now - s.limitAt;
  bool roll = age < ROLL_MS && on == n;
  int dir = s.limitEur > s.prevLimitEur ? 1 : -1;
  for (int i = 0; i < n; i++) {
    int x = x0 + i * 4;
    if (roll && cur[i] != old[i]) {
      int off = (int)((1.0f - (float)age / ROLL_MS) * 6.0f + 0.5f);
      digit(f, cur[i] - '0', x, 1 - dir * off, 7);
      digit(f, old[i] - '0', x, 1 + dir * (6 - off), 3);
    } else {
      digit(f, cur[i] - '0', x, 1, 7);
    }
  }
  glyph(f, EURO, 5, 2, x0 + n * 4 - 1 + (n < 3 ? 1 : 0), 1, 3);
  int lit = (s.limitEur * COLS + 150) / 300;
  for (int x = 0; x < COLS; x++) f[7][x] = x < lit ? 3 : 1;
}

// The temperature in whole degrees, right aligned at column `right`, with a small degree block.
inline void temperature(const State &s, Frame f, int right, int level) {
  if (!s.tempKnown) {                                     // "--"
    for (int x = right - 5; x <= right; x++) if (x != right - 2) put(f, x, 3, level);
    return;
  }
  int t = (int)lroundf(s.tempC);
  if (t < -9) t = -9;
  if (t > 99) t = 99;
  if (t >= 10) {
    digit(f, t / 10, right - 6, 1, level);
    digit(f, t % 10, right - 2, 1, level);
  } else if (t >= 0) {
    digit(f, t, right - 5, 1, level);
    put(f, right - 1, 1, level - 3); put(f, right, 1, level - 3);
    put(f, right - 1, 2, level - 3); put(f, right, 2, level - 3);
  } else {
    put(f, right - 7, 3, level); put(f, right - 6, 3, level);
    digit(f, -t, right - 4, 1, level);
  }
}

// Cold alarm: a thermometer with its mercury blinking, a heat mark rising beside it, and the °C.
inline void alarmFace(const State &s, unsigned long now, Frame f) {
  bool on = (now / 350) % 2;
  glyph(f, THERMO, 7, 3, 0, 0, 5);
  for (int r = 1; r <= 4; r++) put(f, 1, r, on ? 7 : 3);
  put(f, 4, 5 - (int)((now / 250) % 4), 5);
  temperature(s, f, COLS - 1, on ? 7 : 4);
}

// A delivery waits at the door: a parcel that bobs a little over its floor.
inline void deliveryFace(unsigned long now, Frame f) {
  glyph(f, PARCEL, 6, 7, 3, (int)((now / 450) % 2), 7);
  for (int x = 1; x <= 11; x += 2) put(f, x, 7, 1);
}

// An order waits for the head chef: a ring with "!" that breathes.
inline void approvalFace(unsigned long now, Frame f) {
  glyph(f, ATTENTION, 7, 7, 3, 0, 2 + (int)(5 * pulse(now, 1600)));
}

// A delivery check: a thermometer whose mercury rises, the temperature now, and the time left.
inline void checkFace(const State &s, unsigned long now, Frame f) {
  glyph(f, THERMO, 7, 3, 0, 0, 3);
  int fill = 1 + (int)((now / 300) % 4);
  for (int r = 0; r < fill; r++) put(f, 1, 5 - r, 7);
  put(f, 0, 6, 7); put(f, 1, 6, 7); put(f, 2, 6, 7);
  temperature(s, f, COLS - 1, 6);
  float left = 1.0f - (float)(now - s.checkAt) / (float)CHECK_MS;
  int lit = (int)(left * COLS + 0.5f);
  for (int x = 0; x < COLS; x++) f[7][x] = x < lit ? 4 : 0;
}

// ---------------------------------------------------------- the whole face ----
inline void render(const State &s, unsigned long now, Frame out) {
  memset(out, 0, sizeof(Frame));
  if (now - s.bootAt < BOOT_MS) {                         // boot: a soft diagonal wave crosses the face once
    float front = (float)(now - s.bootAt) / (float)BOOT_MS * 24.0f;
    for (int y = 0; y < ROWS; y++)
      for (int x = 0; x < COLS; x++) {
        float d = front - (x + y * 1.5f);
        int level = d < 0 ? 0 : 7 - (int)(d * 1.4f);
        out[y][x] = (uint8_t)(level > 0 ? level : 0);
      }
    return;
  }
  if (s.done && now - s.doneAt < DONE_MS) {               // the hold completed: a spark, then a thermometer
    unsigned long t = now - s.doneAt;
    if (t < 120) {
      int ring = (int)(t / 30);
      for (int y = 0; y < ROWS; y++)
        for (int x = 0; x < COLS; x++)
          if ((abs(x - 6) + abs(y - 3) * 2) / 2 == ring) put(out, x, y, 5);
    } else {
      glyph(out, THERMO, 7, 3, 5, 0, 7);
    }
    return;
  }
  bool touched = now - s.touchAt < SHOW_LIMIT_MS;
  if (s.checking && !touched) checkFace(s, now, out);
  else if (touched || s.holding) limitFace(s, now, out);
  else if (s.alarm) alarmFace(s, now, out);
  else if (s.delivery) deliveryFace(now, out);
  else if (s.approval) approvalFace(now, out);
  else limitFace(s, now, out);
  if (s.holding && now - s.holdAt >= HOLD_SHOW_MS) {      // a hold fills row 7; let go early and nothing happens
    float k = (float)(now - s.holdAt - HOLD_SHOW_MS) / (float)(HOLD_MS - HOLD_SHOW_MS);
    int lit = (int)(k * COLS + 0.999f);
    for (int x = 0; x < COLS; x++) out[7][x] = x < lit ? 7 : 1;
  }
  if (now - s.touchAt > IDLE_DIM_MS && !s.alarm && !s.delivery && !s.approval && !s.checking) {
    for (int y = 0; y < ROWS; y++)                        // a quiet minute with nothing to report: dim
      for (int x = 0; x < COLS; x++)
        if (out[y][x]) out[y][x] = (uint8_t)(out[y][x] > 3 ? 2 : 1);
  }
}

}  // namespace ui

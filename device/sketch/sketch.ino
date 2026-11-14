// Safe Kitchen Agent: microcontroller side of the UNO Q.
// Reads the Modulino Knob and Thermo, plays Modulino Buzzer tones on request.
// Talks to python/main.py through the Bridge, as in the App Lab examples.

#include <Modulino.h>
#include <Arduino_RouterBridge.h>
#include <ArduinoGraphics.h>
#include <Arduino_LED_Matrix.h>

ModulinoKnob knob;
ModulinoThermo thermo;
ModulinoBuzzer buzzer;
Arduino_LED_Matrix matrix;

bool haveKnob = false, haveThermo = false, haveBuzzer = false;
bool haveMatrix = false;

// The knob sets the auto-order limit in steps of 10 euro, from 0 to 300.
const int EUR_PER_STEP = 10;
const int MAX_STEPS = 30;
const int START_STEPS = 4;  // 40 euro at power on

// Buzzer modes set by Python: 0 off, 1 approval waiting, 2 temperature alarm, 3 one short confirm beep.
volatile int buzzMode = 0;

unsigned long lastThermo = 0, lastKnob = 0, lastBuzz = 0, pressStart = 0;
int lastSteps = -1;
bool lastPressed = false, longSent = false;
const unsigned long LONG_PRESS_MS = 1500;  // hold the knob this long to start a delivery check
int alarmPhase = 0;

// The knob menu. A short press steps to the next page. A turn changes the value of the page.
// A hold confirms it. After 15 seconds without a touch the knob is back on the limit page.
//   LIMIT     turn: the spend limit            hold: start a delivery check (60 s of temperature)
//   DELIVERY  turn: accept or reject           hold: send the decision, with the temperature
//   ALARM     no value                          hold: "I have seen the cold alarm"
//   SLOT      turn: slot 1, 2 or 3              hold: report that slot as low
enum { PAGE_LIMIT, PAGE_DELIVERY, PAGE_ALARM, PAGE_SLOT, PAGE_COUNT };
const int CHOICES[PAGE_COUNT] = {0, 2, 1, 3};
const int MENU_KNOB_ZERO = 100;             // the knob count on a menu page, far from both ends
const unsigned long MENU_IDLE_MS = 15000;
int page = PAGE_LIMIT, choice = 0, limitSteps = START_STEPS;
unsigned long lastTouch = 0;

// The matrix shows the knob's euro limit on the limit page, and a letter with a sign on the other pages.
// It updates on every encoder detent: no animation and no interpolation.
const int MATRIX_ROWS = 8, MATRIX_COLS = 13;

void draw_matrix(const uint8_t pixels[MATRIX_ROWS][MATRIX_COLS]) {
  if (!haveMatrix) return;
  uint8_t frame[MATRIX_ROWS * MATRIX_COLS];
  for (int y = 0; y < MATRIX_ROWS; y++) {
    for (int x = 0; x < MATRIX_COLS; x++) frame[y * MATRIX_COLS + x] = pixels[y][x];
  }
  matrix.draw(frame);
}

void render_limit_matrix(int steps) {
  uint8_t pixels[MATRIX_ROWS][MATRIX_COLS] = {};
  // Three 3×5 digits and a compact € glyph fit across the 13-column display.
  // It reads as 040€ at a glance, rather than an unexplained quantity.
  const uint8_t digits[10][5] = {
    {7,5,5,5,7}, {2,6,2,2,7}, {7,1,7,4,7}, {7,1,7,1,7}, {5,5,7,1,1},
    {7,4,7,1,7}, {7,4,7,5,7}, {7,1,2,2,2}, {7,5,7,5,7}, {7,5,7,1,7}};
  int eur = steps * EUR_PER_STEP;
  int shown[3] = {eur / 100, (eur / 10) % 10, eur % 10};
  for (int digit = 0; digit < 3; digit++) {
    int left = digit * 4;
    for (int row = 0; row < 5; row++) {
      for (int col = 0; col < 3; col++) {
        if (digits[shown[digit]][row] & (1 << (2 - col))) pixels[row + 1][left + col] = 7;
      }
    }
  }
  // The tiny 2×5 glyph is intentionally dimmer than the amount: it is a unit,
  // not a competing value. This is a readable € marker at matrix scale.
  const uint8_t euro[5] = {3, 2, 3, 2, 3};
  for (int row = 0; row < 5; row++) {
    for (int col = 0; col < 2; col++) {
      if (euro[row] & (1 << (1 - col))) pixels[row + 1][11 + col] = 3;
    }
  }
  draw_matrix(pixels);
}

// One 3×5 glyph, its bits from left to right, drawn with its top left corner at (left, 1).
void put_glyph(uint8_t pixels[MATRIX_ROWS][MATRIX_COLS], const uint8_t rows[5], int width, int left, uint8_t level) {
  for (int row = 0; row < 5; row++) {
    for (int col = 0; col < width; col++) {
      if (rows[row] & (1 << (width - 1 - col))) pixels[row + 1][left + col] = level;
    }
  }
}

void render_menu_matrix() {
  uint8_t pixels[MATRIX_ROWS][MATRIX_COLS] = {};
  const uint8_t letterD[5] = {6, 5, 5, 5, 6}, letterA[5] = {2, 5, 7, 5, 5}, letterS[5] = {3, 4, 2, 1, 6};
  const uint8_t tick[5] = {0x01, 0x02, 0x14, 0x08, 0x00}, cross[5] = {0x11, 0x0A, 0x04, 0x0A, 0x11};
  const uint8_t bang[5] = {2, 2, 2, 0, 2};
  const uint8_t digits[3][5] = {{2, 6, 2, 2, 7}, {7, 1, 7, 4, 7}, {7, 1, 7, 1, 7}};
  if (page == PAGE_DELIVERY) {
    put_glyph(pixels, letterD, 3, 1, 3);
    put_glyph(pixels, choice == 0 ? tick : cross, 5, 6, 7);
  } else if (page == PAGE_ALARM) {
    put_glyph(pixels, letterA, 3, 1, 3);
    put_glyph(pixels, bang, 3, 7, 7);
  } else if (page == PAGE_SLOT) {
    put_glyph(pixels, letterS, 3, 1, 3);
    put_glyph(pixels, digits[choice], 3, 7, 7);
  }
  // A row of dots at the bottom: one for each page, the present page bright.
  for (int i = 0; i < PAGE_COUNT; i++) pixels[7][2 + i * 3] = (i == page) ? 7 : 1;
  draw_matrix(pixels);
}

void enter_page(int next) {
  page = next;
  choice = 0;
  lastTouch = millis();
  if (page == PAGE_LIMIT) {
    knob.set(limitSteps);
    lastSteps = limitSteps;
    render_limit_matrix(limitSteps);
  } else {
    knob.set(MENU_KNOB_ZERO);
    lastSteps = MENU_KNOB_ZERO;
    render_menu_matrix();
  }
  Bridge.notify("knob_menu", page, choice);
}

void set_buzzer(int mode) {
  buzzMode = mode;
}

int modulino_status() {
  return (haveKnob ? 1 : 0) | (haveThermo ? 2 : 0) | (haveBuzzer ? 4 : 0);
}

void setup() {
  Bridge.begin();
  Modulino.begin(Wire1);  // The UNO Q Qwiic connector is on Wire1.

  haveKnob = knob.begin();
  haveThermo = thermo.begin();
  haveBuzzer = buzzer.begin();
  haveMatrix = matrix.begin();
  if (haveMatrix) matrix.setGrayscaleBits(3);
  if (haveKnob) {
    knob.set(START_STEPS);
    render_limit_matrix(START_STEPS);
  }

  Bridge.provide("set_buzzer", set_buzzer);
  Bridge.provide("modulino_status", modulino_status);
}

void loop() {
  unsigned long now = millis();

  if (haveKnob && now - lastKnob >= 50) {
    lastKnob = now;
    int steps = knob.get();
    bool pressed = knob.isPressed();
    if (page == PAGE_LIMIT) {
      if (steps < 0) { steps = 0; knob.set(0); }
      if (steps > MAX_STEPS) { steps = MAX_STEPS; knob.set(MAX_STEPS); }
      if (steps != lastSteps) {
        lastSteps = steps;
        limitSteps = steps;
        render_limit_matrix(steps);  // render first: the local feedback is never delayed by networking
        Bridge.notify("knob_limit", steps * EUR_PER_STEP);
      }
    } else if (steps != lastSteps) {
      // On a menu page a turn steps through the choices of the page. The limit does not change.
      int n = CHOICES[page];
      choice = (((choice + steps - lastSteps) % n) + n) % n;
      lastSteps = steps;
      lastTouch = now;
      render_menu_matrix();
      Bridge.notify("knob_menu", page, choice);
    }
    if (pressed && !lastPressed) {
      pressStart = now;
      longSent = false;
      lastTouch = now;
    }
    if (pressed && !longSent && now - pressStart >= LONG_PRESS_MS) {
      longSent = true;
      if (page == PAGE_LIMIT) {
        Bridge.notify("knob_long_press");            // delivery check
      } else {
        Bridge.notify("knob_confirm", page, choice);  // the decision of this page
        enter_page(PAGE_LIMIT);
      }
    }
    if (!pressed && lastPressed && !longSent && now - pressStart > 30) {
      enter_page((page + 1) % PAGE_COUNT);            // short press: the next page
    }
    if (page != PAGE_LIMIT && !pressed && now - lastTouch >= MENU_IDLE_MS) {
      enter_page(PAGE_LIMIT);
    }
    lastPressed = pressed;
  }

  if (haveThermo && now - lastThermo >= 2000) {
    lastThermo = now;
    float celsius = thermo.getTemperature();
    float humidity = thermo.getHumidity();
    // A failed read gives NaN. Send only real readings; the Linux side treats silence as "unknown".
    if (!isnan(celsius)) {
      Bridge.notify("thermo", celsius, humidity);
    }
  }

  if (haveBuzzer) {
    int mode = buzzMode;
    if (mode == 1 && now - lastBuzz >= 2000) {          // approval waiting: one short beep every 2 s
      lastBuzz = now;
      buzzer.tone(1047, 120);
    } else if (mode == 2 && now - lastBuzz >= 300) {    // alarm: low and high tones in turn
      lastBuzz = now;
      alarmPhase = !alarmPhase;
      buzzer.tone(alarmPhase ? 440 : 880, 280);
    } else if (mode == 3) {                             // confirm: one beep, then off
      buzzMode = 0;
      buzzer.tone(1568, 80);
    }
  }
}

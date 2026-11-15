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
int alarmPhase = 0;

// The face on the LED matrix is in matrix_ui.h. This file reads the knob, tells python/main.py
// what the person did, and plays the small sounds. The knob has two gestures only:
//   turn  the spend limit, 10 euro a step, from 0 to 300
//   hold  a delivery check of 60 s (the bar in row 7 fills first; let go early and nothing happens)
// A tap does nothing, so a knock never triggers an action. Decisions with context (accept or reject
// a delivery, "I have seen the alarm", report a slot low) are made on the touch screen.
#include "matrix_ui.h"
#include "src/vl53l5cx/SparkFun_VL53L5CX_Library.h"   // SparkFun library, MIT licence (src/vl53l5cx/LICENSE.md). App Lab compiles only src/ besides the sketch

// The distance sensor (SparkFun VL53L5CX, 8 x 8 zones) on the same Qwiic bus. It loads its own firmware at
// start, which takes a few seconds once. Each frame goes to python/main.py as eight rows of eight distances in
// millimetres, -1 where a zone has no valid target (status 5 and 9 are valid).
SparkFun_VL53L5CX imager;
VL53L5CX_ResultsData tofFrame;
int tofState = 0;                 // 0 not started, 1 ranging, -1 not found (tried again later), -2 failed to start
unsigned long tofTry = 0, tofSent = 0, tofFrames = 0;
int tofValid = 0;                 // valid zones in the newest frame

// For python/main.py's log: the state, the frames read, and the valid zones of the newest frame.
int tof_info() { return (tofState + 10) * 10000000 + (int)(tofFrames % 100000) * 100 + tofValid; }

void tof_loop(unsigned long now) {
  if (tofState == -1 && now - tofTry > 10000) tofState = 0;
  if (tofState == 0) {
    tofTry = now;
    Wire1.beginTransmission(0x29);
    if (Wire1.endTransmission() != 0) { tofState = -1; return; }
    if (!imager.begin(0x29, Wire1) || !imager.setResolution(64) || !imager.setRangingFrequency(5) || !imager.startRanging()) {
      tofState = -2;
      return;
    }
    tofState = 1;
  }
  if (tofState == 1 && now - tofSent >= 400 && imager.isDataReady() && imager.getRangingData(&tofFrame)) {
    tofSent = now;
    tofFrames++;
    tofValid = 0;
    for (int i = 0; i < 64; i++) if (tofFrame.target_status[i] == 5 || tofFrame.target_status[i] == 9) tofValid++;
    for (int r = 0; r < 8; r++) {
      int v[8];
      for (int c = 0; c < 8; c++) {
        int i = r * 8 + c, st = tofFrame.target_status[i];
        v[c] = (st == 5 || st == 9) ? tofFrame.distance_mm[i] : -1;
      }
      Bridge.notify("tof_row", r, v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7]);
    }
  }
}

const unsigned long FRAME_MS = 33;          // about 30 pictures a second
ui::State face;
unsigned long lastFrame = 0, chimeAt = 0;
int chimeFreq = 0;

// Called by python/main.py with what the face must show besides the limit: bit 0 is set while a
// delivery waits for a decision at the door. The alarm and the waiting approval come from the buzzer mode.
void set_status(int flags) {
  face.delivery = flags & 1;
}

void draw_face(unsigned long now) {
  if (!haveMatrix) return;
  ui::Frame f;
  ui::render(face, now, f);
  uint8_t frame[ui::ROWS * ui::COLS];
  for (int y = 0; y < ui::ROWS; y++)
    for (int x = 0; x < ui::COLS; x++) frame[y * ui::COLS + x] = f[y][x];
  matrix.draw(frame);
}

// Short sounds for the knob. They stay quiet during a temperature alarm, which owns the buzzer.
void chirp(int freq, int ms) {
  if (haveBuzzer && buzzMode != 2) buzzer.tone(freq, ms);
}
void chime(int first, int second, unsigned long now) {   // two notes, the second 70 ms later
  chirp(first, 60);
  chimeFreq = second;
  chimeAt = now + 70;
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
  unsigned long now = millis();
  face.limitEur = face.prevLimitEur = START_STEPS * EUR_PER_STEP;
  face.bootAt = face.touchAt = now;
  face.limitAt = now - 10000;
  if (haveKnob) knob.set(START_STEPS);
  lastSteps = START_STEPS;

  Bridge.provide("set_buzzer", set_buzzer);
  Bridge.provide("set_status", set_status);
  Bridge.provide("tof_info", tof_info);
  Bridge.provide("modulino_status", modulino_status);
}

void loop() {
  unsigned long now = millis();

  if (haveKnob && now - lastKnob >= 20) {
    lastKnob = now;
    int steps = knob.get();
    bool pressed = knob.isPressed();
    if (steps < 0) { steps = 0; knob.set(0); }
    if (steps > MAX_STEPS) { steps = MAX_STEPS; knob.set(MAX_STEPS); }
    if (steps != lastSteps) {
      if ((steps == 0 || steps == MAX_STEPS)) chirp(523, 20);   // the end of the range
      lastSteps = steps;
      face.prevLimitEur = face.limitEur;
      face.limitEur = steps * EUR_PER_STEP;
      face.limitAt = face.touchAt = now;
      Bridge.notify("knob_limit", face.limitEur);
    }
    if (pressed && !lastPressed) {                         // a press starts; only a full hold acts
      pressStart = now;
      longSent = false;
      face.holding = true;
      face.holdAt = face.touchAt = now;
    }
    if (pressed && !longSent && now - pressStart >= ui::HOLD_MS) {
      longSent = true;
      face.holding = false;
      face.done = true;
      face.doneAt = now;
      face.checking = true;
      face.checkAt = now;
      chime(1047, 1568, now);
      Bridge.notify("knob_long_press");                    // a delivery check of 60 s
    }
    if (!pressed && lastPressed && !longSent) {
      face.holding = false;
      face.touchAt = now;
      if (now - pressStart >= ui::HOLD_SHOW_MS) chirp(392, 25);   // let go before the bar was full
    }
    lastPressed = pressed;
  }
  tof_loop(now);
  if (face.checking && now - face.checkAt >= ui::CHECK_MS) face.checking = false;
  face.alarm = buzzMode == 2;
  face.approval = buzzMode == 1;
  if (chimeFreq && now >= chimeAt) { chirp(chimeFreq, 90); chimeFreq = 0; }
  if (now - lastFrame >= FRAME_MS) {
    lastFrame = now;
    draw_face(now);
  }

  if (haveThermo && now - lastThermo >= 2000) {
    lastThermo = now;
    float celsius = thermo.getTemperature();
    float humidity = thermo.getHumidity();
    // A failed read gives NaN. Send only real readings; the Linux side treats silence as "unknown".
    if (!isnan(celsius)) {
      face.tempKnown = true;
      face.tempC = celsius;
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

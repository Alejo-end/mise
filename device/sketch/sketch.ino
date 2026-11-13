// Safe Kitchen Agent: microcontroller side of the UNO Q.
// Reads the Modulino Knob and Thermo, plays Modulino Buzzer tones on request.
// Talks to python/main.py through the Bridge, as in the App Lab examples.

#include <Modulino.h>
#include <Arduino_RouterBridge.h>

ModulinoKnob knob;
ModulinoThermo thermo;
ModulinoBuzzer buzzer;

bool haveKnob = false, haveThermo = false, haveBuzzer = false;

// The knob sets the auto-order limit in steps of 10 euro, from 0 to 300.
const int EUR_PER_STEP = 10;
const int MAX_STEPS = 30;
const int START_STEPS = 4;  // 40 euro at power on

// Buzzer modes set by Python: 0 off, 1 approval waiting, 2 temperature alarm, 3 one short confirm beep.
volatile int buzzMode = 0;

unsigned long lastThermo = 0, lastKnob = 0, lastBuzz = 0, lastPress = 0;
int lastSteps = -1;
bool lastPressed = false;
int alarmPhase = 0;

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
  if (haveKnob) {
    knob.set(START_STEPS);
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
    if (steps < 0) { steps = 0; knob.set(0); }
    if (steps > MAX_STEPS) { steps = MAX_STEPS; knob.set(MAX_STEPS); }
    if (steps != lastSteps) {
      lastSteps = steps;
      Bridge.notify("knob_limit", steps * EUR_PER_STEP);
    }
    if (pressed && !lastPressed && now - lastPress > 400) {
      lastPress = now;
      Bridge.notify("knob_pressed");
    }
    lastPressed = pressed;
  }

  if (haveThermo && now - lastThermo >= 2000) {
    lastThermo = now;
    float celsius = thermo.getTemperature();
    float humidity = thermo.getHumidity();
    Bridge.notify("thermo", celsius, humidity);
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

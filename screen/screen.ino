// mise: the touch screen of the box.
//
// Board: Guition JC4827W543C (ESP32-S3, 4.3 inch, 480 x 272, NV3041A over QSPI, GT911 touch).
// It joins the Wi-Fi, asks the backend what is at the door (GET /box/screen) and draws it.
// A person accepts or rejects a delivery here, or confirms the cold alarm. The backend adds the
// temperature of the box to the record. The screen has no PIN, so it never approves an order.
//
// The hardware facts come from a bring-up of this exact board (the owner's notes, 14 November 2026):
// the panel is right with the factory's own start-up table, colour inversion off and USB-C on the
// left. The touch chip gives display coordinates as they are. Both were verified on the board with
// other firmware. This sketch uses them through the Arduino display library.
//
// Build: arduino-cli compile --fqbn esp32:esp32:esp32s3:FlashSize=4M,PSRAM=opi,PartitionScheme=huge_app,CDCOnBoot=cdc screen

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Arduino_GFX_Library.h>
#include <Wire.h>
#include <Preferences.h>
#include "src/fonts/FreeSans9pt7b.h"
#include "src/fonts/FreeSansBold9pt7b.h"
#include "src/fonts/FreeSansBold12pt7b.h"
#include "src/fonts/FreeSansBold18pt7b.h"
#include "src/factory_init_table.h"
#include "src/wordmark.h"
#include "secrets.h"

// A touch button. It is declared before the first function, as the Arduino build needs.
struct Button { int x, y, w, h; const char *label; uint16_t fill, text; };

// Display and touch pins of this board.
#define GFX_BL 1
Arduino_DataBus *bus = new Arduino_ESP32QSPI(45 /* CS */, 47 /* SCK */, 21 /* D0 */, 48 /* D1 */, 40 /* D2 */, 39 /* D3 */);

// The library's start-up table for this panel is not the one the factory firmware sends. This class
// sends the factory's table (src/factory_init_table.h, 97 register writes), in the library's own format.
class FactoryPanel : public Arduino_NV3041A {
public:
  using Arduino_NV3041A::Arduino_NV3041A;
  void tftInit() override {
    _bus->sendCommand(0x01);  // software reset; the board has no reset pin for the panel
    delay(120);
    _bus->batchOperation(kFactoryInitTable, sizeof(kFactoryInitTable));
    invertDisplay(false);
  }
};
Arduino_GFX *gfx = new FactoryPanel(bus, GFX_NOT_DEFINED /* RST */, 0 /* rotation: USB-C on the left */, false /* IPS: inversion off */);

// The touch chip: a GT911 on SDA 8 and SCL 4. It answers at 0x5D on this board; 0x14 is its other address.
// Its reset and interrupt pins stay untouched.
uint8_t touchAddr = 0;   // 0: no chip found
int touchReads = 0, touchTaps = 0, touchErrors = 0;  // counters for the diagnosis line and the backend's log
bool touchRead(uint16_t reg, uint8_t *out, size_t len) {
  if (!touchAddr) return false;
  // The register address goes out with a stop, then a separate read. This chip accepts both ways,
  // and the form with a stop does not depend on how the core handles a repeated start.
  Wire.beginTransmission(touchAddr);
  Wire.write(reg >> 8);
  Wire.write(reg & 0xFF);
  if (Wire.endTransmission(true) != 0) { touchErrors++; return false; }
  if (Wire.requestFrom((int)touchAddr, (int)len) != (int)len) { touchErrors++; return false; }
  for (size_t i = 0; i < len; i++) out[i] = Wire.read();
  touchReads++;
  return true;
}
void touchFind() {
  for (uint8_t addr : {0x5D, 0x14}) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) { touchAddr = addr; return; }
  }
}
// The calibration: how a raw touch reading becomes a place on the display. It is measured once with four
// targets and kept in the flash. Hold a finger on the name "mise" for 3 seconds to measure it again.
struct Calibration { bool set = false, swap = false; float ax = 1, bx = 0, ay = 1, by = 0; } cal;
Preferences store;
int rawX = 0, rawY = 0;  // the last raw reading
int calStep = -1;        // -1: not calibrating; 0 to 3: the target that waits for a tap
int calAsked = -1;       // the backend's count of calibration requests, as last seen; -1: not seen yet
bool fingerDown = false; // a finger is on the glass; a tap is the moment it lands
unsigned long lastDown = 0;

// One finger on the glass: its place in display coordinates. The chip is never configured, only read.
bool touched(int &x, int &y) {
  uint8_t status = 0, p[8];
  if (!touchRead(0x814E, &status, 1) || !(status & 0x80)) return false;
  bool down = (status & 0x0F) > 0 && touchRead(0x814F, p, 8);
  if (!down) fingerDown = false;  // the chip reports the lift as a reading with no points
  Wire.beginTransmission(touchAddr);  // tell the chip that the reading is taken
  Wire.write(0x81); Wire.write(0x4E); Wire.write(0x00);
  Wire.endTransmission();
  if (!down) return false;
  rawX = p[1] | (p[2] << 8);
  rawY = p[3] | (p[4] << 8);
  float u = cal.swap ? rawY : rawX, v = cal.swap ? rawX : rawY;
  x = constrain((int)lroundf(cal.ax * u + cal.bx), 0, 479);
  y = constrain((int)lroundf(cal.ay * v + cal.by), 0, 271);
  return true;
}

const int W = 480, H = 272;
// The colours of the page, as RGB565.
const uint16_t BG = 0xF7BF, PANEL = 0xFFFF, INK = 0x08A6, SOFT = 0x5B2F, LINE = 0xDF1D, BLUE = 0x1A7B, PINK = 0xD0AB, GREEN = 0x0BC8;

const uint16_t PINK_SOFT = 0xFF3D, GREEN_SOFT = 0xE7BD, BLUE_SOFT = 0xEF7F, FAINT = 0x9CF5;

const Button ACCEPT = {16, 206, 216, 56, "Accept", BLUE, PANEL};
const Button REJECT = {248, 206, 216, 56, "Reject", PANEL, PINK};
const Button YES = {16, 206, 216, 56, "Yes", BLUE, PANEL};
const Button BACK = {248, 206, 216, 56, "Back", PANEL, INK};
const Button SEEN = {250, 4, 222, 36, "Seen", PINK, PANEL};  // the alarm pill in the top bar

// What the backend said last.
struct View {
  bool reached = false, online = false, alarm = false, hasTemp = false, hasDelivery = false, tooWarm = false, hasLimit = false;
  float temp = 0, limit = 0;
  int approvals = 0, more = 0;
  String approval, orderId, total, lines[3];
  int lineCount = 0;
} view;

enum Ask { ASK_NONE, ASK_ACCEPT, ASK_REJECT };
Ask asking = ASK_NONE;       // the decision that waits for "Yes"
String toast;                // one line shown after a decision
String toastSub;             // the smaller line under it
bool toastGood = true;       // a tick or a cross above it
unsigned long toastUntil = 0, lastFetch = 0, lastTouch = 0, holdStart = 0;
String drawn, drawnBar;      // what the body and the top bar show now, so an unchanged answer draws nothing

void text(int x, int y, const String &s, const GFXfont *font, uint16_t colour) {
  gfx->setFont(font);
  gfx->setTextColor(colour);
  gfx->setCursor(x, y);
  gfx->print(s);
}

int textWidth(const String &s, const GFXfont *font) {
  int16_t x1, y1; uint16_t w, h;
  gfx->setFont(font);
  gfx->getTextBounds(s, 0, 0, &x1, &y1, &w, &h);
  return w;
}

void textCentre(int y, const String &s, const GFXfont *font, uint16_t colour) { text((W - textWidth(s, font)) / 2, y, s, font, colour); }
void textRight(int right, int y, const String &s, const GFXfont *font, uint16_t colour) { text(right - textWidth(s, font), y, s, font, colour); }

// A temperature as "25.3 °C". The font has no degree sign, so a small ring is drawn. It gives back the next x.
int celsius(int x, int y, float value, int digits, const GFXfont *font, uint16_t colour) {
  String number = String(value, digits);
  text(x, y, number, font, colour);
  x += textWidth(number, font) + 6;
  gfx->drawCircle(x, y - 10, 2, colour);
  if (font != &FreeSans9pt7b) gfx->drawCircle(x, y - 10, 3, colour);
  text(x + 5, y, "C", font, colour);
  return x + 5 + textWidth("C", font);
}

// A line 3 pixels thick, for the tick and the cross.
void stroke(int x1, int y1, int x2, int y2, uint16_t colour) {
  for (int d = -1; d <= 1; d++) {
    gfx->drawLine(x1, y1 + d, x2, y2 + d, colour);
    gfx->drawLine(x1 + d, y1, x2 + d, y2, colour);
  }
}
void tick(int cx, int cy, int size, uint16_t colour) {
  stroke(cx - size, cy, cx - size / 3, cy + size * 2 / 3, colour);
  stroke(cx - size / 3, cy + size * 2 / 3, cx + size, cy - size * 2 / 3, colour);
}
void cross(int cx, int cy, int size, uint16_t colour) {
  stroke(cx - size, cy - size, cx + size, cy + size, colour);
  stroke(cx - size, cy + size, cx + size, cy - size, colour);
}

// A button: its label with a small sign to the left of it.
void button(const Button &b, char sign = 0) {
  gfx->fillRoundRect(b.x, b.y, b.w, b.h, 10, b.fill);
  if (b.fill == PANEL) {
    uint16_t edge = b.text == INK ? LINE : b.text;
    gfx->drawRoundRect(b.x, b.y, b.w, b.h, 10, edge);
    gfx->drawRoundRect(b.x + 1, b.y + 1, b.w - 2, b.h - 2, 9, edge);
  }
  int w = textWidth(b.label, &FreeSansBold12pt7b) + (sign ? 26 : 0), x = b.x + (b.w - w) / 2, mid = b.y + b.h / 2;
  if (sign == 'v') tick(x + 8, mid, 7, b.text);
  if (sign == 'x') cross(x + 8, mid, 6, b.text);
  text(x + (sign ? 26 : 0), mid + 8, b.label, &FreeSansBold12pt7b, b.text);
}

// A touch counts for a button up to 8 pixels to its sides and 18 pixels above and below it. A fingertip lands
// a little off its aim.
bool inside(const Button &b, int x, int y) { return x >= b.x - 8 && x < b.x + b.w + 8 && y >= b.y - 18 && y < b.y + b.h + 18; }

String temperature() { return view.hasTemp ? String(view.temp, 1) + " C" : String("no reading"); }

// The top bar: the name on the left, and on the right the air at the box or what is wrong.
void drawBar() {
  String state = WiFi.status() != WL_CONNECTED ? "No Wi-Fi" : !view.reached ? "No backend" : !view.online ? "Box offline" : "";
  String key = state + view.alarm + temperature() + view.tooWarm + view.hasDelivery + view.more + touchAddr;
  if (key == drawnBar) return;
  drawnBar = key;
  gfx->fillRect(0, 0, W, 44, BG);
  gfx->draw16bitRGBBitmap(12, (44 - WORDMARK_H) / 2, (uint16_t *)WORDMARK, WORDMARK_W, WORDMARK_H);
  gfx->drawFastHLine(0, 44, W, LINE);
  if (!touchAddr) text(12 + WORDMARK_W + 12, 28, "no touch", &FreeSans9pt7b, PINK);
  else if (view.hasDelivery && view.more > 0 && !view.alarm) text(12 + WORDMARK_W + 14, 28, String(view.more + 1) + " at the door", &FreeSans9pt7b, SOFT);

  if (view.alarm) {  // the pill is the button: a tap says "I have seen it"
    gfx->fillRoundRect(SEEN.x, SEEN.y, SEEN.w, SEEN.h, 18, PINK);
    String label = "Cold alarm. Tap when seen";
    text(SEEN.x + (SEEN.w - textWidth(label, &FreeSansBold9pt7b)) / 2, SEEN.y + 24, label, &FreeSansBold9pt7b, PANEL);
    return;
  }
  if (state.length()) {
    int w = textWidth(state, &FreeSansBold9pt7b) + 40;
    gfx->fillRoundRect(W - 12 - w, 8, w, 28, 14, PINK_SOFT);
    gfx->fillCircle(W - 12 - w + 15, 22, 4, PINK);
    text(W - 12 - w + 26, 28, state, &FreeSansBold9pt7b, PINK);
    return;
  }
  // The air at the box, in a pill with a dot: green when the order at the door is cold enough, pink when it is too warm.
  bool warm = view.hasDelivery && view.tooWarm;
  String number = view.hasTemp ? String(view.temp, 1) : String("no reading");
  int w = textWidth("Air " + number, &FreeSansBold9pt7b) + (view.hasTemp ? 28 : 0) + 44;
  int x = W - 12 - w;
  gfx->fillRoundRect(x, 8, w, 28, 14, warm ? PINK_SOFT : PANEL);
  if (!warm) gfx->drawRoundRect(x, 8, w, 28, 14, LINE);
  gfx->fillCircle(x + 15, 22, 4, warm ? PINK : view.hasTemp ? GREEN : FAINT);
  text(x + 26, 28, "Air", &FreeSansBold9pt7b, warm ? PINK : INK);
  int nx = x + 26 + textWidth("Air", &FreeSansBold9pt7b) + 8;
  if (view.hasTemp) celsius(nx, 28, view.temp, 1, &FreeSansBold9pt7b, warm ? PINK : INK);
  else text(nx, 28, number, &FreeSansBold9pt7b, SOFT);
}

// The body under the bar. Only a change draws it again, so the picture does not flicker with every reading.
void drawBody() {
  String key = String(view.hasDelivery) + view.orderId + view.total + view.tooWarm + view.hasLimit + view.approvals + view.approval + asking +
               toast + toastSub + (view.hasDelivery ? temperature() : String(""));
  for (int i = 0; i < view.lineCount; i++) key += view.lines[i];
  if (key == drawn) return;
  drawn = key;
  gfx->fillRect(0, 45, W, H - 45, BG);

  if (toast.length()) {  // the answer to a decision: a sign, what happened, and where it went
    gfx->fillCircle(W / 2, 108, 30, toastGood ? BLUE_SOFT : PINK_SOFT);
    if (toastGood) tick(W / 2, 108, 13, BLUE); else cross(W / 2, 108, 11, PINK);
    textCentre(176, toast, &FreeSansBold12pt7b, INK);
    textCentre(204, toastSub, &FreeSans9pt7b, SOFT);
    return;
  }
  if (!view.hasDelivery) {
    gfx->fillCircle(W / 2, 100, 28, GREEN_SOFT);
    tick(W / 2, 100, 12, GREEN);
    textCentre(166, "Nothing at the door", &FreeSansBold12pt7b, INK);
    if (view.approvals > 0) {
      textCentre(194, String(view.approvals) + (view.approvals == 1 ? " order waits for the head chef" : " orders wait for the head chef"), &FreeSans9pt7b, SOFT);
      textCentre(218, view.approval, &FreeSansBold9pt7b, INK);
      textCentre(244, "Approve it on the phone, with a PIN.", &FreeSans9pt7b, FAINT);
    } else {
      textCentre(194, "Every delivery is checked.", &FreeSans9pt7b, SOFT);
    }
    return;
  }

  // The order to decide at the door: a card with the order, and a band with the temperature against the limit.
  gfx->fillRoundRect(16, 54, W - 32, 142, 10, PANEL);
  gfx->drawRoundRect(16, 54, W - 32, 142, 10, LINE);
  String title = asking == ASK_ACCEPT ? "Accept this delivery?" : asking == ASK_REJECT ? "Reject this delivery?" : "At the door";
  text(30, 78, title, &FreeSansBold9pt7b, asking == ASK_REJECT ? PINK : asking == ASK_ACCEPT ? BLUE : SOFT);
  text(30 + textWidth(title, &FreeSansBold9pt7b) + 10, 78, view.orderId, &FreeSans9pt7b, FAINT);
  textRight(W - 30, 78, view.total, &FreeSansBold9pt7b, INK);
  for (int i = 0, y = 106; i < view.lineCount && i < 2; i++, y += 22) text(30, y, view.lines[i], &FreeSansBold9pt7b, INK);
  if (view.lineCount > 2) textRight(W - 30, 128, "+" + String(view.lineCount - 2), &FreeSans9pt7b, FAINT);

  bool known = view.hasLimit && view.hasTemp;
  uint16_t band = !known ? BG : view.tooWarm ? PINK_SOFT : GREEN_SOFT;
  uint16_t ink = !known ? SOFT : view.tooWarm ? PINK : GREEN;
  gfx->fillRoundRect(24, 150, W - 48, 38, 8, band);
  gfx->fillCircle(42, 169, 5, ink);
  int x = 56;
  if (view.hasTemp) {
    text(x, 175, "Air", &FreeSansBold9pt7b, INK);
    x = celsius(x + textWidth("Air", &FreeSansBold9pt7b) + 8, 175, view.temp, 1, &FreeSansBold9pt7b, INK) + 16;
  } else {
    text(x, 175, "No temperature reading", &FreeSansBold9pt7b, SOFT);
  }
  if (known) {
    text(x, 175, "limit", &FreeSans9pt7b, SOFT);
    celsius(x + textWidth("limit", &FreeSans9pt7b) + 8, 175, view.limit, 0, &FreeSans9pt7b, SOFT);
    textRight(W - 38, 175, view.tooWarm ? "Too warm" : "Cold enough", &FreeSansBold9pt7b, ink);
  }

  if (asking == ASK_NONE) {
    button(ACCEPT, 'v');
    button(REJECT, 'x');
  } else {
    // A second tap, so a wet sleeve on the glass does not decide a delivery.
    Button yes = YES;
    yes.label = asking == ASK_ACCEPT ? "Yes, accept" : "Yes, reject";
    yes.fill = asking == ASK_ACCEPT ? BLUE : PINK;
    button(yes, asking == ASK_ACCEPT ? 'v' : 'x');
    button(BACK);
  }
}

void draw() {
  drawBar();
  drawBody();
}

void fetch() {
  View next;
  HTTPClient http;
  http.setConnectTimeout(2500);
  http.setTimeout(4000);
  if (WiFi.status() == WL_CONNECTED && http.begin(String(BACKEND) + "/box/screen?touch=" + String(touchAddr, HEX) + "&reads=" + touchReads + "&errors=" + touchErrors + "&taps=" + touchTaps + "&cal=" + (cal.set ? String(cal.swap) + "," + String(cal.ax, 3) + "," + String(cal.bx, 1) + "," + String(cal.ay, 3) + "," + String(cal.by, 1) : String("none"))) && http.GET() == 200) {
    JsonDocument doc;
    if (!deserializeJson(doc, http.getString())) {
      next.reached = true;
      next.online = doc["online"] | false;
      next.alarm = doc["alarm"] | false;
      next.hasTemp = !doc["temperature_c"].isNull();
      next.temp = doc["temperature_c"] | 0.0f;
      next.approvals = doc["approvals"] | 0;
      next.approval = doc["approval"] | "";
      int asked = doc["recalibrate"] | 0;  // the backend counts requests for a new calibration
      if (calAsked >= 0 && asked != calAsked && calStep < 0) startCalibration();
      calAsked = asked;
      JsonObject d = doc["delivery"];
      if (!d.isNull()) {
        next.hasDelivery = true;
        next.orderId = d["order_id"] | "";
        next.total = d["total"] | "";
        next.more = d["more"] | 0;
        next.tooWarm = d["too_warm"] | false;
        next.hasLimit = !d["limit_c"].isNull();
        next.limit = d["limit_c"] | 0.0f;
        for (JsonVariant line : d["lines"].as<JsonArray>()) {
          if (next.lineCount < 3) next.lines[next.lineCount++] = line.as<String>();
        }
      }
    }
  }
  http.end();
  if (next.orderId != view.orderId) asking = ASK_NONE;  // the question was about another order
  view = next;
}

// Send one decision. The answer of the backend becomes the line on the screen.
void send(const String &body, const String &done, const String &sub, bool good) {
  HTTPClient http;
  http.setTimeout(4000);
  bool ok = WiFi.status() == WL_CONNECTED && http.begin(String(BACKEND) + "/events");
  if (ok) {
    http.addHeader("Content-Type", "application/json");
    ok = http.POST(body) == 200;
  }
  http.end();
  toast = ok ? done : "Not sent";
  toastSub = ok ? sub : "The backend did not answer. Try again.";
  toastGood = ok && good;
  toastUntil = millis() + 2500;
  asking = ASK_NONE;
  lastFetch = 0;  // ask the backend again at once
}

void tap(int x, int y) {
  if (toast.length()) return;
  if (view.alarm && inside(SEEN, x, y)) {
    send("{\"type\":\"alarm_seen\",\"source\":\"screen\"}", "Alarm confirmed", "It is in the log.", true);
    return;
  }
  if (!view.hasDelivery) return;
  if (asking == ASK_NONE) {
    if (inside(ACCEPT, x, y)) asking = ASK_ACCEPT;
    else if (inside(REJECT, x, y)) asking = ASK_REJECT;
  } else if (inside(BACK, x, y)) {
    asking = ASK_NONE;
  } else if (inside(YES, x, y)) {
    bool accepted = asking == ASK_ACCEPT;
    send("{\"type\":\"delivery_decision\",\"accepted\":" + String(accepted ? "true" : "false") + ",\"order_id\":\"" + view.orderId +
         "\",\"source\":\"screen\"}", String(accepted ? "Accepted" : "Rejected") + (view.hasTemp ? " at " + String(view.temp, 1) + " degrees" : String("")),
         view.orderId + " is in the log.", accepted);
  }
}

// The four targets of the calibration, 40 pixels in from each corner: top left, top right, bottom right, bottom left.
const int TARGET[4][2] = {{40, 40}, {440, 40}, {440, 232}, {40, 232}};
int calRaw[4][2];

void drawTarget() {
  gfx->fillScreen(BG);
  text(90, 120, "Tap the centre of the cross", &FreeSansBold12pt7b, INK);
  text(90, 150, String("Target ") + (calStep + 1) + " of 4. Use a fingertip.", &FreeSans9pt7b, SOFT);
  int x = TARGET[calStep][0], y = TARGET[calStep][1];
  gfx->drawFastHLine(x - 18, y, 37, BLUE);
  gfx->drawFastVLine(x, y - 18, 37, BLUE);
  gfx->drawCircle(x, y, 10, BLUE);
}

void startCalibration() {
  calStep = 0;
  drawn = "";
  drawnBar = "";
  drawTarget();
}

// One tap on a target. After the fourth, the two straight lines that map raw readings to the display are solved.
void calibrationTap() {
  calRaw[calStep][0] = rawX;
  calRaw[calStep][1] = rawY;
  Serial.printf("calibration %d raw %d %d\n", calStep, rawX, rawY);
  if (++calStep < 4) { drawTarget(); return; }
  calStep = -1;
  // If a move along the top edge changes the raw Y more than the raw X, the axes of the panel are swapped.
  bool swap = abs(calRaw[1][1] - calRaw[0][1]) > abs(calRaw[1][0] - calRaw[0][0]);
  int a = swap ? 1 : 0, b = swap ? 0 : 1;
  float left = (calRaw[0][a] + calRaw[3][a]) / 2.0f, right = (calRaw[1][a] + calRaw[2][a]) / 2.0f;
  float top = (calRaw[0][b] + calRaw[1][b]) / 2.0f, bottom = (calRaw[2][b] + calRaw[3][b]) / 2.0f;
  float ax = 400.0f / (right - left), ay = 192.0f / (bottom - top);
  // This panel reports about one unit for each pixel. A scale far from that means the taps were not
  // on the four targets, so the measurement starts again.
  if (fabsf(ax) < 0.6f || fabsf(ax) > 1.7f || fabsf(ay) < 0.6f || fabsf(ay) > 1.7f) {
    startCalibration();
    return;
  }
  cal.set = true;
  cal.swap = swap;
  cal.ax = ax;
  cal.bx = 40 - cal.ax * left;
  cal.ay = ay;
  cal.by = 40 - cal.ay * top;
  store.putBytes("cal", &cal, sizeof(cal));
  Serial.printf("calibration swap %d x %.3f %+.1f y %.3f %+.1f\n", cal.swap, cal.ax, cal.bx, cal.ay, cal.by);
  drawn = "";
  drawnBar = "";
}

void setup() {
  Serial.begin(115200);
  gfx->begin(20000000);  // 20 MHz is the speed that was verified on this panel
  gfx->fillScreen(BG);     // a frame first, then the light
  gfx->draw16bitRGBBitmap((W - WORDMARK_W) / 2, 96, (uint16_t *)WORDMARK, WORDMARK_W, WORDMARK_H);
  textCentre(170, "Joining the network", &FreeSans9pt7b, SOFT);
  pinMode(GFX_BL, OUTPUT);
  digitalWrite(GFX_BL, HIGH);
  Wire.begin(8, 4, 100000);
  touchFind();
  Serial.printf("touch chip at 0x%02X\n", touchAddr);
  store.begin("mise", false);
  if (store.getBytesLength("cal") == sizeof(cal)) store.getBytes("cal", &cal, sizeof(cal));
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  // A stored calibration with a scale that cannot be right (an older build accepted those) is dropped.
  if (cal.set && (fabsf(cal.ax) < 0.6f || fabsf(cal.ax) > 1.7f || fabsf(cal.ay) < 0.6f || fabsf(cal.ay) > 1.7f)) cal = Calibration();
  if (touchAddr && !cal.set) startCalibration();
}

void loop() {
  unsigned long now = millis();
  if (toast.length() && now >= toastUntil) toast = "";
  if (WiFi.status() != WL_CONNECTED && now < 15000 && !view.reached) { delay(50); return; }  // the start picture stays while it joins
  if (now - lastFetch >= 2000 || lastFetch == 0) {
    lastFetch = now ? now : 1;
    fetch();
  }
  int x, y;
  if (fingerDown && millis() - lastDown > 1500) fingerDown = false;  // a lift that the chip did not report
  if (touched(x, y)) {
    // One tap is the moment a finger lands. A finger that rests, also across a slow network call, is no new tap.
    bool fresh = !fingerDown && now - lastTouch > 250;
    fingerDown = true;
    lastDown = millis();
    if (fresh) holdStart = now;
    lastTouch = now;
    if (calStep >= 0) {
      if (fresh) calibrationTap();
    } else if (x < 130 && y < 42 && now - holdStart > 3000) {
      startCalibration();  // a finger held on the name for 3 seconds
    } else if (fresh) {
      Serial.printf("touch %d %d (raw %d %d)\n", x, y, rawX, rawY);
      touchTaps++;
      tap(x, y);
    }
  }
  if (calStep < 0) draw();
  delay(20);
}

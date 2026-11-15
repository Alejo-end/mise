// mise: the touch screen of the box.
//
// Board: Guition JC4827W543C (ESP32-S3, 4.3 inch, 480 x 272, NV3041A over QSPI, GT911 touch).
// It joins the Wi-Fi, asks the backend for its view every 2 seconds (GET /box/screen) and shows it in three tabs:
// Door (the delivery to accept or reject), Cold (the air at the box over 2 hours) and Stock (the shelf slots and
// the shelf camera). A person accepts or rejects a delivery here, confirms the cold alarm, or reports a slot as low.
// The backend adds the temperature of the box to the record. The screen has no PIN, so it never approves an order.
//
// The look lives in src/ui/ as plain C++: a model (src/ui/model.*) and the drawing of a whole frame into a
// framebuffer (src/ui/draw.*, src/ui/canvas.*). The laptop preview (tools/preview.cpp) runs the same code.
// This file keeps what needs the board: the panel, the touch chip, the Wi-Fi, the flash and the network.
//
// The frame lives in PSRAM. The sketch draws a new frame only when the model changed or a motion runs, compares
// it with the frame on the panel, and sends only the rectangle that changed. An unchanged answer sends nothing.
// A task on the other core fetches the view and the camera pictures, so a slow network never stops a motion
// or a touch.
//
// The hardware facts come from a bring-up of this exact board (the owner's notes, 14 November 2026):
// the panel is right with the factory's own start-up table and colour inversion off. Both were verified on
// the board with other firmware. This sketch uses them through the Arduino display library.
//
// Build: arduino-cli compile --fqbn esp32:esp32:esp32s3:FlashSize=4M,PSRAM=opi,PartitionScheme=huge_app,CDCOnBoot=cdc screen

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Arduino_GFX_Library.h>
#include <Wire.h>
#include <Preferences.h>
#include <esp_heap_caps.h>
#include "src/factory_init_table.h"
#include "src/ui/canvas.h"
#include "src/ui/draw.h"
#include "src/ui/model.h"
#include "secrets.h"

using ui::W;
using ui::H;

// The types are declared before the first function, as the Arduino build needs.
// One answer of the backend, parsed by the fetch task and handed to the loop.
struct Answer { ui::View view; int asked = 0; };
// One reading of the distance sensor, parsed by the fetch task: 64 distances in cm, NAN where a cell has none.
struct DepthAnswer { float cm[ui::DEPTH_CELLS]; int age = 0; bool none = false; };
// Three buffers for the pictures of one size: the newest, the one before it (for the cross-fade), and the
// one the fetch task writes into. A buffer never changes while the model points at it.
struct PictureSlots {
  int w, h;
  uint16_t *buf[3] = {nullptr, nullptr, nullptr};
  int cur = -1, prev = -1, incoming = 0;
  bool ready = false, none = false;  // a picture, or the news "no picture", waits for the loop
  int readyAge = 0;
};

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
// Rotation 2 turns the picture 180 degrees, so it is upright with USB-C on the right, the way the screen sits in the box.
// The touch calibration stays in the panel's own frame (rotation 0), so a stored calibration survives the turn.
Arduino_GFX *gfx = new FactoryPanel(bus, GFX_NOT_DEFINED /* RST */, 2 /* rotation: USB-C on the right */, false /* IPS: inversion off */);

// The touch chip: a GT911 on SDA 8 and SCL 4. It answers at 0x5D on this board; 0x14 is its other address.
// Its reset and interrupt pins stay untouched.
uint8_t touchAddr = 0;   // 0: no chip found
int touchReads = 0, touchTaps = 0, touchErrors = 0;  // counters for the backend's log
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
unsigned long lastDown = 0, lastTouch = 0, holdStart = 0;

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
  // The calibration gives a place in the panel's own frame. The picture is turned 180 degrees, so both axes flip.
  x = 479 - constrain((int)lroundf(cal.ax * u + cal.bx), 0, 479);
  y = 271 - constrain((int)lroundf(cal.ay * v + cal.by), 0, 271);
  return true;
}

// The touch task reads the chip every 10 ms on its own, so a short tap is not lost while a frame draws.
// (The chip keeps only its newest report: when the loop read it only between slow frames, a tap that landed
// and lifted in between was gone, and on the Cold view, which animates all the time, taps went missing.)
// It hands each landing of a finger to the loop through a queue, and keeps where a resting finger is now.
struct TouchEvent { int x, y, rx, ry; uint32_t at; };
QueueHandle_t taps = nullptr;
volatile bool restDown = false;
volatile int restX = 0, restY = 0;
volatile uint32_t restSince = 0, restSeen = 0;

void touchTask(void *) {
  bool down = false;
  uint32_t lastSeen = 0, lastTap = 0;
  for (;;) {
    int x, y;
    uint32_t now = millis();
    if (touched(x, y)) {
      if (!down && now - lastTap > 120) {                 // a finger lands: one tap
        TouchEvent e{x, y, rawX, rawY, now};
        xQueueSend(taps, &e, 0);
        lastTap = now;
        restSince = now;
      }
      down = true;
      fingerDown = true;                                  // touched() clears it when the chip reports the lift
      lastSeen = now;
      restX = x; restY = y; restSeen = now; restDown = true;
    } else if (!fingerDown || now - lastSeen > 1500) {    // the chip reported the lift, or no report for long
      down = false;
      restDown = false;
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

// ---------------------------------------------------------------------------------------------------------
// The frame: drawn in PSRAM, compared with the frame on the panel, and sent as the rectangle that changed.

uint16_t *fb = nullptr;       // the frame being drawn
uint16_t *shown = nullptr;    // the frame on the panel
uint16_t *scratch = nullptr;  // a changed rectangle narrower than the screen, packed for one transfer
ui::Canvas *canvas = nullptr;
ui::Model model;
uint32_t statFrames = 0, statRender = 0, statFlush = 0, statPixels = 0, statAt = 0;

uint16_t *psram(size_t bytes) {
  uint16_t *p = (uint16_t *)heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!p) Serial.printf("no PSRAM for %u bytes\n", (unsigned)bytes);
  return p;
}

// Send what changed between fb and shown. Arduino_GFX takes the pixels as native RGB565 words and sends them
// most significant byte first, as the panel wants, so the frame needs no byte swap.
// Each frame is drawn whole, so after the transfer fb equals the panel everywhere: the two buffers swap
// places instead of copying, and the next frame is drawn into the old one.
void present() {
  int y0 = H, y1 = -1, x0 = W, x1 = -1;
  for (int y = 0; y < H; y++) {
    const uint16_t *a = fb + y * W, *b = shown + y * W;
    if (memcmp(a, b, W * 2) == 0) continue;
    if (y < y0) y0 = y;
    y1 = y;
    int l = 0, r = W - 1;
    while (l < W && a[l] == b[l]) l++;
    while (r > l && a[r] == b[r]) r--;
    if (l < x0) x0 = l;
    if (r > x1) x1 = r;
  }
  if (y1 < 0) return;  // nothing changed: no transfer at all
  int w = x1 - x0 + 1, h = y1 - y0 + 1;
  if (w >= W * 3 / 4) { x0 = 0; w = W; }  // whole rows go out straight from the frame, with no copy
  if (w == W) {
    gfx->draw16bitRGBBitmap(0, y0, fb + y0 * W, W, h);
  } else {
    for (int y = 0; y < h; y++) memcpy(scratch + y * w, fb + (y0 + y) * W + x0, w * 2);
    gfx->draw16bitRGBBitmap(x0, y0, scratch, w, h);
  }
  statPixels += w * h;
  uint16_t *t = fb;
  fb = shown;
  shown = t;
  canvas->px = fb;
}

bool moreFrames = false, looping = false;
uint32_t lastFrame = 0;

// Draw a frame when the model changed, while a motion runs, or about 12 times a second for a breathing dot.
// force draws one now, before the loop blocks on the network.
void frame(uint32_t now, bool force = false) {
  if (!force && !model.dirty && !moreFrames && !(looping && now - lastFrame >= 80)) return;
  model.dirty = false;
  uint32_t t0 = micros();
  ui::Frame f = ui::render(*canvas, model, now);
  uint32_t t1 = micros();
  present();
  uint32_t t2 = micros();
  moreFrames = f.animating;
  looping = f.looping;
  lastFrame = now;
  statFrames++;
  statRender += t1 - t0;
  statFlush += t2 - t1;
}

// ---------------------------------------------------------------------------------------------------------
// The fetch task: the view every 2 seconds, and the camera pictures and the distance sensor while they are
// on the screen.

SemaphoreHandle_t lock;
Answer *ready = nullptr;          // the newest answer, waiting for the loop
volatile bool fetchNow = false;   // ask the backend again at once
volatile bool wantThumb = false, wantFull = false, wantDepth = false;
DepthAnswer *depthReady = nullptr;  // the newest reading of the distance sensor, waiting for the loop
PictureSlots thumbs = {160, 90}, fulls = {480, 270};

// The query of the view carries the touch counters and the calibration, for the backend's log.
String screenUrl() {
  return String(BACKEND) + "/box/screen?touch=" + String(touchAddr, HEX) + "&reads=" + touchReads + "&errors=" + touchErrors +
         "&taps=" + touchTaps + "&cal=" +
         (cal.set ? String(cal.swap) + "," + String(cal.ax, 3) + "," + String(cal.bx, 1) + "," + String(cal.ay, 3) + "," + String(cal.by, 1)
                  : String("none"));
}

void fetchView(Answer &out) {
  ui::View &next = out.view;
  HTTPClient http;
  http.setConnectTimeout(2500);
  http.setTimeout(4000);
  if (WiFi.status() == WL_CONNECTED && http.begin(screenUrl()) && http.GET() == 200) {
    // ArduinoJson 7 sizes the document by itself. The answer is about 3 to 4 KB; a failed allocation is a failed parse.
    JsonDocument doc;
    if (!deserializeJson(doc, http.getString())) {
      next.reached = true;
      next.online = doc["online"] | false;
      next.alarm = doc["alarm"] | false;
      next.hasTemp = !doc["temperature_c"].isNull();
      next.temp = doc["temperature_c"] | 0.0f;
      next.approvals = doc["approvals"] | 0;
      JsonObject c = doc["cold"];
      next.room.hasLimit = !c["limit_c"].isNull();
      next.room.limit = c["limit_c"] | 0.0f;
      next.room.product = c["product"] | "";
      next.room.minutes = c["minutes_above"] | 0;
      next.room.allowed = c["allowed_minutes"] | 120;
      next.coldWarm = c["warm"] | 0;
      next.coldState = c["state"] | "unknown";
      next.hasPeak = !c["peak_c"].isNull();
      next.peak = c["peak_c"] | 0.0f;
      JsonArray points = c["points"];  // a named array, not a temporary, for the loop
      for (JsonVariant p : points) {   // a null point is a gap
        if (next.pointCount < ui::POINTS) next.points[next.pointCount++] = p.is<float>() ? p.as<float>() : NAN;
      }
      JsonArray stock = doc["stock"];
      for (JsonObject s : stock) {
        if (next.slotCount >= ui::SLOTS) break;
        ui::Slot &o = next.slots[next.slotCount++];
        o.slot = s["slot"] | 0;
        o.name = s["name"] | "";
        o.units = s["units"] | -1;
        o.target = s["target"] | -1;
        o.reorder = s["reorder"] | -1;
        o.onOrder = s["on_order"] | -1;
        o.low = s["low"] | false;
        o.stale = s["stale"] | false;
        o.emptyAt = s["empty_at"] | "";
      }
      next.approval = doc["approval"] | "";
      out.asked = doc["recalibrate"] | 0;  // the backend counts requests for a new calibration
      JsonObject d = doc["delivery"];
      if (!d.isNull()) {
        next.hasDelivery = true;
        next.orderId = d["order_id"] | "";
        next.total = d["total"] | "";
        next.more = d["more"] | 0;
        next.tooWarm = d["too_warm"] | false;
        next.door.hasLimit = !d["limit_c"].isNull();
        next.door.limit = d["limit_c"] | 0.0f;
        next.door.product = d["limit_of"] | "";
        next.door.minutes = d["minutes_above"] | 0;
        next.door.allowed = d["allowed_minutes"] | 120;
        JsonArray lines = d["lines"];
        for (JsonVariant line : lines) {
          if (next.lineCount < ui::LINES) next.lines[next.lineCount++] = line.as<const char *>() ? line.as<const char *>() : "";
        }
      }
      // The view to open on. A backend without the field gets the same rule here.
      const char *home = doc["view"] | (next.hasDelivery || next.alarm ? "door" : "cold");
      next.home = strcmp(home, "door") == 0 ? ui::TAB_DOOR : ui::TAB_COLD;
    }
  }
  http.end();
}

// One camera picture, streamed into the free buffer of its size. On any failure the old picture stays.
void fetchPicture(PictureSlots &ps) {
  xSemaphoreTake(lock, portMAX_DELAY);
  bool busy = ps.ready || ps.none;  // the loop has not taken the last one yet
  int into = ps.incoming;
  xSemaphoreGive(lock);
  if (busy || WiFi.status() != WL_CONNECTED || !ps.buf[into]) return;
  HTTPClient http;
  http.setConnectTimeout(2000);
  http.setTimeout(3000);
  const char *keys[] = {"X-Frame-Age", "X-Frame-Width", "X-Frame-Height"};
  if (!http.begin(String(BACKEND) + "/box/camera.rgb565?w=" + ps.w + "&h=" + ps.h)) return;
  http.collectHeaders(keys, 3);
  int code = http.GET();
  if (code == 204) {  // the backend has no picture at all
    xSemaphoreTake(lock, portMAX_DELAY);
    ps.none = true;
    xSemaphoreGive(lock);
  } else if (code == 200 && http.header("X-Frame-Width").toInt() == ps.w && http.header("X-Frame-Height").toInt() == ps.h) {
    // The body is RGB565 little-endian, the ESP32's own order and the framebuffer's, so it goes in as it comes.
    size_t need = (size_t)ps.w * ps.h * 2, got = 0;
    uint8_t *dst = (uint8_t *)ps.buf[into];
    WiFiClient *s = http.getStreamPtr();
    uint32_t start = millis();
    while (got < need && http.connected() && millis() - start < 4000) {
      size_t n = s->available();
      if (!n) { delay(2); continue; }
      got += s->readBytes(dst + got, min(n, need - got));
    }
    static uint32_t pictures = 0, spent = 0;
    spent += millis() - start;
    if (++pictures % 20 == 0) Serial.printf("camera: %u x %u, %u ms a picture on average\n", ps.w, ps.h, (unsigned)(spent / 20)), spent = 0;
    if (got == need) {
      xSemaphoreTake(lock, portMAX_DELAY);
      ps.ready = true;
      ps.readyAge = http.header("X-Frame-Age").toInt();
      xSemaphoreGive(lock);
    }
  }
  http.end();
}

// The distance sensor: GET /box/tof gives {"cm": [64 numbers or null, row 0 first], "age": s, "rows": 8, "cols": 8},
// or 204 when no reading ever arrived. About 400 bytes, so a short timeout.
void fetchDepth() {
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  http.setConnectTimeout(800);
  http.setTimeout(1200);
  if (!http.begin(String(BACKEND) + "/box/tof")) return;
  int code = http.GET();
  DepthAnswer *a = nullptr;
  if (code == 204) {
    a = new DepthAnswer();
    a->none = true;
  } else if (code == 200) {
    JsonDocument doc;
    if (!deserializeJson(doc, http.getString())) {
      JsonArray cm = doc["cm"];
      if (cm.size() == ui::DEPTH_CELLS && (doc["rows"] | 8) == 8 && (doc["cols"] | 8) == 8) {
        a = new DepthAnswer();
        int i = 0;
        for (JsonVariant v : cm) a->cm[i++] = v.is<float>() ? v.as<float>() : NAN;  // null: no reading in that cell
        a->age = doc["age"] | 0;
      }
    }
  }
  http.end();
  if (!a) return;  // a failed fetch keeps the last reading, which grows old and then shows as offline
  xSemaphoreTake(lock, portMAX_DELAY);
  delete depthReady;
  depthReady = a;
  xSemaphoreGive(lock);
}

void fetchTask(void *) {
  uint32_t lastView = 0, lastThumb = 0, lastFull = 0, lastDepth = 0;
  for (;;) {
    uint32_t now = millis();
    if (fetchNow || lastView == 0 || now - lastView >= 2000) {
      fetchNow = false;
      lastView = now ? now : 1;
      Answer *a = new Answer();
      fetchView(*a);
      xSemaphoreTake(lock, portMAX_DELAY);
      delete ready;  // an answer the loop did not take is old now
      ready = a;
      xSemaphoreGive(lock);
    }
    now = millis();
    // Pictures as fast as the Wi-Fi brings them, with a short pause so the view fetch and touch keep their turn.
    if (wantFull && (lastFull == 0 || now - lastFull >= 250)) { lastFull = now ? now : 1; fetchPicture(fulls); }
    else if (!wantFull) lastFull = 0;  // a view that opens fetches at once
    if (wantThumb && (lastThumb == 0 || now - lastThumb >= 500)) { lastThumb = now ? now : 1; fetchPicture(thumbs); }
    else if (!wantThumb) lastThumb = 0;
    if (wantDepth && (lastDepth == 0 || now - lastDepth >= 300)) { lastDepth = now ? now : 1; fetchDepth(); }
    else if (!wantDepth) lastDepth = 0;
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

void startCalibration();

// Take the newest answer, the newest pictures and the newest depth reading from the fetch task.
void takeFetched(uint32_t now) {
  xSemaphoreTake(lock, portMAX_DELAY);
  Answer *a = ready;
  ready = nullptr;
  xSemaphoreGive(lock);
  if (a) {
    if (a->view.reached) {
      if (calAsked >= 0 && a->asked != calAsked && calStep < 0) startCalibration();
      calAsked = a->asked;
    }
    model.apply(a->view, now);
    delete a;
  }
  xSemaphoreTake(lock, portMAX_DELAY);
  DepthAnswer *d = depthReady;
  depthReady = nullptr;
  xSemaphoreGive(lock);
  if (d) {
    if (d->none) model.noDepth();
    else model.setDepth(d->cm, d->age, now);
    delete d;
  }
  for (PictureSlots *ps : {&thumbs, &fulls}) {
    bool large = ps == &fulls;
    xSemaphoreTake(lock, portMAX_DELAY);
    if (ps->ready) {
      // The new picture becomes the newest, the newest the one before, and the oldest buffer is free again.
      int freed = ps->prev;
      ps->prev = ps->cur;
      ps->cur = ps->incoming;
      ps->incoming = freed >= 0 ? freed : (ps->cur + 1) % 3 == ps->prev ? (ps->cur + 2) % 3 : (ps->cur + 1) % 3;
      ps->ready = false;
      uint16_t *px = ps->buf[ps->cur];
      int age = ps->readyAge;
      xSemaphoreGive(lock);
      if (large) model.setFull(px, ps->w, ps->h, age, now);
      else model.setThumb(px, ps->w, ps->h, age, now);
    } else if (ps->none) {
      ps->none = false;
      ps->cur = ps->prev = -1;
      ps->incoming = 0;
      xSemaphoreGive(lock);
      model.noPicture(large, now);
    } else {
      xSemaphoreGive(lock);
    }
  }
}

// ---------------------------------------------------------------------------------------------------------
// What a touch sends to the backend.

// One request to the backend's log of events. It gives back the HTTP code, and the answer when the code is 200.
int post(const String &body, uint32_t timeout, String *answer = nullptr) {
  HTTPClient http;
  http.setTimeout(timeout);
  int code = -1;
  if (WiFi.status() == WL_CONNECTED && http.begin(String(BACKEND) + "/events")) {
    http.addHeader("Content-Type", "application/json");
    code = http.POST(body);
    if (code == 200 && answer) *answer = http.getString();
  }
  http.end();
  return code;
}

void toast(const char *title, const String &sub, bool good) {
  model.showToast(title, sub.c_str(), good, millis());
  fetchNow = true;  // ask the backend again at once
}

// Send one decision. The answer of the backend becomes the toast.
void send(const String &body, const char *done, const String &sub, bool good) {
  bool ok = post(body, 4000) == 200;
  toast(ok ? done : "Not sent", ok ? sub : String("The backend did not answer. Try again."), ok && good);
}

// Report a slot as low, the way the knob of the box does. The backend runs the purchase before it answers,
// so this waits longer than a decision, and the sheet says so while it waits.
void reportLow(int number) {
  int i = model.slotIndex(number);
  if (i < 0) return;
  String name = model.view.slots[i].name.c_str();
  model.beginWaiting();
  frame(millis(), true);
  String answer;
  int code = post("{\"type\":\"manual_low\",\"slot\":" + String(number) + ",\"source\":\"screen\"}", 15000, &answer);
  if (code == HTTPC_ERROR_READ_TIMEOUT) { toast("No answer yet", "The backend may still act. Look in the log.", false); return; }
  if (code != 200) { toast("Not sent", "The backend did not answer. Try again.", false); return; }
  JsonDocument reply;
  String status = deserializeJson(reply, answer) ? String("") : String(reply["status"] | "");
  Serial.printf("report low slot %d: %s\n", number, answer.c_str());
  if (status == "ignored") {
    String reason = reply["reason"] | "";
    if (reason.indexOf("already") >= 0) toast("Already reported", "An order or a request for it is in progress.", true);
    else toast("Not reported", "The backend did not act on it.", false);
  } else if (status == "error") {
    toast("Report failed", "The error is in the log.", false);
  } else {
    toast("Reported low", name + " is in the log.", true);
  }
}

void run(const ui::Action &a) {
  if (a.kind == ui::Action::DECIDE) {
    String id = model.view.orderId.c_str();
    send("{\"type\":\"delivery_decision\",\"accepted\":" + String(a.accepted ? "true" : "false") + ",\"order_id\":\"" + id +
         "\",\"source\":\"screen\"}", a.accepted ? "Accepted" : "Rejected", id + " is in the log, with the temperature.", a.accepted);
  } else if (a.kind == ui::Action::ALARM_SEEN) {
    send("{\"type\":\"alarm_seen\",\"source\":\"screen\"}", "Alarm confirmed", "It is in the log.", true);
  } else if (a.kind == ui::Action::REPORT_LOW) {
    reportLow(a.slot);
  }
  model.done(millis());
}

// ---------------------------------------------------------------------------------------------------------
// The touch calibration.

// The four targets of the calibration, 40 pixels in from each corner: top left, top right, bottom right, bottom left.
const int TARGET[4][2] = {{40, 40}, {440, 40}, {440, 232}, {40, 232}};
int calRaw[4][2];

void drawTarget() {
  // The targets are places in the panel's own frame. On the turned picture they appear at the opposite corner.
  ui::renderCalibration(*canvas, calStep, W - 1 - TARGET[calStep][0], H - 1 - TARGET[calStep][1]);
  present();
}

void startCalibration() {
  calStep = 0;
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
  model.dirty = true;  // the views come back
}

// ---------------------------------------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  fb = psram(W * H * 2);
  shown = psram(W * H * 2);
  scratch = psram(W * H * 2);
  for (PictureSlots *ps : {&thumbs, &fulls})
    for (uint16_t *&b : ps->buf) b = psram((size_t)ps->w * ps->h * 2);
  gfx->begin(20000000);  // 20 MHz is the speed that was verified on this panel
  if (!fb || !shown || !scratch) {  // without PSRAM there is no frame: say so in the library's own font
    gfx->fillScreen(0);
    gfx->setTextColor(0xFFFF);
    gfx->setCursor(20, 120);
    gfx->print("No PSRAM. Build with PSRAM=opi.");
    pinMode(GFX_BL, OUTPUT);
    digitalWrite(GFX_BL, HIGH);
    for (;;) delay(1000);
  }
  canvas = new ui::Canvas(fb, W, H);
  memset(shown, 0xA5, W * H * 2);  // a pattern no frame has, so the first frame goes out whole
  ui::renderSplash(*canvas, "Joining the network");
  present();  // a frame first, then the light
  pinMode(GFX_BL, OUTPUT);
  digitalWrite(GFX_BL, HIGH);
  Wire.begin(8, 4, 100000);
  touchFind();
  Serial.printf("touch chip at 0x%02X\n", touchAddr);
  model.setTouch(touchAddr != 0);
  store.begin("mise", false);
  if (store.getBytesLength("cal") == sizeof(cal)) store.getBytes("cal", &cal, sizeof(cal));
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  // A stored calibration with a scale that cannot be right (an older build accepted those) is dropped.
  if (cal.set && (fabsf(cal.ax) < 0.6f || fabsf(cal.ax) > 1.7f || fabsf(cal.ay) < 0.6f || fabsf(cal.ay) > 1.7f)) cal = Calibration();
  lock = xSemaphoreCreateMutex();
  // The fetch task runs on core 0 beside the Wi-Fi; the loop, the drawing and the touch stay on core 1.
  xTaskCreatePinnedToCore(fetchTask, "fetch", 12288, nullptr, 1, nullptr, 0);
  taps = xQueueCreate(8, sizeof(TouchEvent));
  xTaskCreatePinnedToCore(touchTask, "touch", 4096, nullptr, 2, nullptr, 1);   // above the loop, so it never waits
  if (touchAddr && !cal.set) startCalibration();
}

void loop() {
  uint32_t now = millis();
  model.setWifi(WiFi.status() == WL_CONNECTED);
  // The start picture stays while the screen joins the network, for up to 15 seconds.
  if (!model.wifi && now < 15000 && !model.view.reached && calStep < 0) { delay(50); return; }
  takeFetched(now);
  wantThumb = model.wantsThumb();
  wantFull = model.wantsFull();
  wantDepth = model.wantsDepth();

  TouchEvent e;
  while (taps && xQueueReceive(taps, &e, 0) == pdTRUE) {   // each landing of a finger, in order
    rawX = e.rx;
    rawY = e.ry;
    if (calStep >= 0) {
      calibrationTap();
    } else {
      Serial.printf("touch %d %d (raw %d %d)\n", e.x, e.y, e.rx, e.ry);
      touchTaps++;
      model.tap(e.x, e.y, now);
    }
  }
  if (restDown && calStep < 0) {
    model.touchAt = now;                                   // a resting finger keeps the screen awake
    if (restX < 130 && restY < 42 && now - restSince > 3000) {
      restSince = now;                                     // once, not again for the same hold
      startCalibration();                                  // a finger held on the name for 3 seconds
    }
  }
  if (calStep >= 0) { delay(10); return; }

  ui::Action a = model.poll(now);
  if (a.kind != ui::Action::NONE) {
    frame(now, true);  // the pressed button shows while the request runs
    run(a);
  }
  frame(millis());

  // Once in 10 seconds, how long the frames took, for the serial monitor.
  if (now - statAt >= 10000) {
    if (statFrames) {
      Serial.printf("frames %u, render %u ms, flush %u ms on average, %u pixels sent\n", (unsigned)statFrames,
                    (unsigned)(statRender / statFrames / 1000), (unsigned)(statFlush / statFrames / 1000), (unsigned)statPixels);
    }
    statFrames = statRender = statFlush = statPixels = 0;
    statAt = now;
  }
  delay(moreFrames ? 1 : 10);
}

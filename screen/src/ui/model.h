// What the screen knows and how a touch changes it. Plain C++ with no Arduino calls: the sketch fills the
// model from the backend and the touch chip, and the laptop preview fills it with sample data.
//
// The model holds the backend's last answer (View), the state of the person's session (the open tab, a
// question that waits for "Yes", a selected slot, a toast, the camera view) and the clocks of the animations.
// It never draws. Each change that the screen must show sets `dirty`; an unchanged answer leaves it clear.
#pragma once
#include <cstdint>
#include <string>

namespace ui {

const int W = 480, H = 272;
const int POINTS = 48, SLOTS = 3, LINES = 3, COLD_ROWS = 3;
const uint32_t IDLE_MS = 60000;      // a minute without a touch: back to the backend's view
const uint32_t CAMERA_IDLE_MS = 30000;
const uint32_t TOAST_MS = 2600;
const uint32_t PRESS_MS = 90;        // a pressed button shows for this long before it acts
const int CAMERA_LIVE_S = 10;        // a picture older than this counts as offline

enum Tab { TAB_DOOR, TAB_COLD, TAB_STOCK };

// A storage limit, whose limit it is, and how long the air at the box has been above it.
struct Cold {
  bool hasLimit = false;
  float limit = 0;
  std::string product;
  int minutes = 0, allowed = 120;
  bool operator==(const Cold &o) const;
};

// One shelf product in the cold room: its own limit and its state against it ("ok", "warm", "breach", "unknown").
struct ColdRow {
  std::string name, state = "unknown";
  bool hasLimit = false;
  float limit = 0;
  int minutes = 0;
  bool operator==(const ColdRow &o) const;
};

// One shelf slot. A count, target, reorder point or order that the backend does not know is -1.
struct Slot {
  int slot = 0, units = -1, target = -1, reorder = -1, onOrder = -1;
  bool low = false, stale = false;
  std::string name, emptyAt;
  bool operator==(const Slot &o) const;
};

// What the backend said last (GET /box/screen).
struct View {
  bool reached = false, online = false, alarm = false, hasTemp = false, hasDelivery = false, tooWarm = false, hasPeak = false;
  float temp = 0, peak = 0;
  int approvals = 0, more = 0, coldWarm = 0;
  Cold room, door;  // the shelf product that needs attention first; the coldest product of the order at the door
  std::string approval, orderId, total, lines[LINES], coldState = "unknown";
  int lineCount = 0;
  float points[POINTS] = {};  // the air over the last 2 hours, oldest first; NAN where no reading fell
  int pointCount = 0;
  Slot slots[SLOTS];
  int slotCount = 0;
  ColdRow coldRows[COLD_ROWS];  // every shelf product with its own limit, the ones that need a person first
  int coldRowCount = 0;
  int quietS = 0;               // the buzzer is silenced for this many more seconds
  Tab home = TAB_COLD;  // the view the backend opens on
  bool operator==(const View &o) const;
};

// A picture of the shelf camera as RGB565, in the byte order of the framebuffer.
struct Picture {
  const uint16_t *px = nullptr;
  int w = 0, h = 0;
};

// The camera: the newest picture, the one before it (for the cross-fade), and its age.
struct Camera {
  Picture cur, prev;
  uint32_t arrivedAt = 0;  // when cur arrived: the cross-fade runs from here
  uint32_t ageAt = 0;      // the clock time at which the age was ageS
  int ageS = -1;           // the age of cur in seconds when it arrived; -1: no picture
  bool live(uint32_t now) const { return cur.px && ageS >= 0 && age(now) <= CAMERA_LIVE_S; }
  int age(uint32_t now) const { return ageS + (int)((now - ageAt) / 1000); }
};

// The distance sensor: an 8 by 8 grid of distances in cm, row 0 first, as the sensor sees it.
// Each cell eases from what it showed to its new value, so a new reading never flashes.
const int DEPTH_CELLS = 64;
const float DEPTH_FAR_CM = 150;      // the far end of the colour scale: a shelf
const int DEPTH_LIVE_S = 5;          // a reading older than this counts as offline
const uint32_t DEPTH_EASE_MS = 150;
struct Depth {
  float cm[DEPTH_CELLS] = {};        // the newest reading; NAN where a cell has none
  float fromFar[DEPTH_CELLS] = {}, toFar[DEPTH_CELLS] = {};  // 0 near, 1 far: where the colour starts and ends
  float fromOn[DEPTH_CELLS] = {}, toOn[DEPTH_CELLS] = {};    // 1 for a cell with a reading, 0 without
  uint32_t arrivedAt = 0, ageAt = 0;
  int ageS = -1;                     // -1: no reading
  bool live(uint32_t now) const { return ageS >= 0 && age(now) <= DEPTH_LIVE_S; }
  int age(uint32_t now) const { return ageS + (int)((now - ageAt) / 1000); }
  // What cell i shows at the time now: how far (0 to 1) and how much it is a reading (0 to 1).
  void shown(int i, uint32_t now, float &far, float &on) const;
};

enum Ask { ASK_NONE, ASK_ACCEPT, ASK_REJECT };
enum Button { BTN_NONE, BTN_ACCEPT, BTN_REJECT, BTN_YES, BTN_BACK, BTN_REPORT, BTN_SEEN };
enum Link { LINK_OK, LINK_NO_WIFI, LINK_NO_BACKEND, LINK_BOX_OFFLINE };

// What the sketch must do after a touch: the actions that go to the backend.
struct Action {
  enum Kind { NONE, DECIDE, ALARM_SEEN, REPORT_LOW } kind = NONE;
  bool accepted = false;
  int slot = -1;  // REPORT_LOW: the slot number
};

// A number that rolls from its old text to its new one.
struct Roll {
  std::string from, to;
  uint32_t at = 0;
  bool up = true;  // the value grew: the new digits come from below
  void set(const std::string &text, float value, float old, uint32_t now, bool animate);
};

// The rectangles that drawing and touch share.
struct Rect {
  int x, y, w, h;
  bool hit(int px, int py, int mx = 0, int my = 0) const { return px >= x - mx && px < x + w + mx && py >= y - my && py < y + h + my; }
};
namespace layout {
const int SIDE = 20;
const Rect NAV = {20, 224, 440, 40};
const int NAV_TOUCH_Y = 216;  // a touch below this line belongs to the tabs
const int BODY_Y = 46, BODY_B = 216;
const Rect DOOR_CARD = {20, 48, 440, 108};
const Rect ACCEPT = {20, 166, 214, 46}, REJECT = {246, 166, 214, 46};
const Rect SHEET = {20, 56, 440, 160};
const Rect YES = {36, 154, 196, 46}, BACK = {248, 154, 196, 46};
const int ROW_X = 20, ROW_Y = 48, ROW_W = 268, ROW_H = 52, ROW_STEP = 58;
const Rect THUMB = {300, 48, 160, 90};
const Rect CAMERA_MODES = {268, 9, 150, 28};  // "Camera | Depth" in the strip of the large view
const int SEEN_X = 236;  // the alarm capsule reaches from here to the right edge of the bar
inline Rect row(int i) { return {ROW_X, ROW_Y + i * ROW_STEP, ROW_W, ROW_H}; }
inline Rect reportButton(int i) { Rect r = row(i); return {r.x + r.w - 10 - 100, r.y + 10, 100, 32}; }
}  // namespace layout

struct Model {
  View view;
  bool wifi = false;   // the sketch's Wi-Fi state
  bool touch = true;   // a touch chip answered
  bool dirty = true;   // something on the screen changed since the last frame

  Tab tab = TAB_COLD;
  Ask asking = ASK_NONE;  // the decision that waits for "Yes"
  int picked = -1;        // the stock slot number that a tap selected; -1: none
  bool reporting = false; // "Report <name> low?" waits for "Yes, report"
  bool waiting = false;   // the backend runs the purchase of a "Report low"; the sheet says so
  bool opened = false;    // the first answer of the backend has chosen the view

  std::string toast, toastSub;  // one line after a decision, and the smaller line under it
  bool toastGood = true, toastOn = false;
  uint32_t toastAt = 0;

  Button pressed = BTN_NONE;  // a button that shows its pressed state, and acts when PRESS_MS has passed
  uint32_t pressedAt = 0;
  bool busy = false;          // the pressed button's action runs in the sketch

  Camera thumb, full;         // the small picture in the Stock view, and the large one
  bool cameraOpen = false;
  uint32_t cameraAt = 0;      // when the large view opened or closed
  Depth depth;
  bool depthMode = false;     // the large view shows the distance sensor instead of the picture
  uint32_t modeAt = 0;        // when the large view changed its mode

  // The clocks of the animations. A clock of 0 means "never", so nothing animates at the start.
  Tab tabFrom = TAB_COLD;
  uint32_t tabAt = 0;          // the tab switch
  uint32_t coldAt = 0;         // the chart draws itself from here
  uint32_t sheetAt = 0;        // the sheet opened or closed
  bool sheetOpen = false;
  Ask sheetAsk = ASK_NONE;     // what the last sheet asked, kept for its closing fade
  int sheetSlot = -1;
  uint32_t pickAt = 0;         // a stock row was selected or released
  int pickedBefore = -1;
  uint32_t bodyAt = 0;         // the body fades in after a toast
  Roll temp, units[SLOTS];
  uint32_t touchAt = 0;        // the last touch

  // The backend's answer. It chooses the view on the first answer and turns to the door when a delivery
  // arrives or an alarm starts. It sets dirty only when something changed.
  void apply(const View &next, uint32_t now);
  void setWifi(bool on);
  void setTouch(bool ok);
  Link link() const;

  // A tap at a place on the display.
  void tap(int x, int y, uint32_t now);
  // The timers: toasts, pressed buttons, the idle return, the camera's idle close. It gives back an action
  // that the sketch must send to the backend; the button stays pressed until done() is called.
  Action poll(uint32_t now);
  void done(uint32_t now);
  void showToast(const std::string &title, const std::string &sub, bool good, uint32_t now);
  void beginWaiting();  // the backend works on a "Report low"

  // A new picture of the camera. ageS is the age the backend gave; px stays valid until the next call.
  void setThumb(const uint16_t *px, int w, int h, int ageS, uint32_t now);
  void setFull(const uint16_t *px, int w, int h, int ageS, uint32_t now);
  void noPicture(bool large, uint32_t now);  // the backend has no picture at all
  bool wantsThumb() const { return tab == TAB_STOCK && !cameraOpen && !toastOn; }
  bool wantsFull() const { return cameraOpen && !depthMode; }
  bool wantsDepth() const { return cameraOpen && depthMode; }
  // A new reading of the distance sensor: 64 distances in cm, NAN for a cell with none.
  void setDepth(const float *cm, int ageS, uint32_t now);
  void noDepth();  // the backend has no reading at all

  void setTab(Tab t, uint32_t now);
  int slotIndex(int number) const;
  bool coldIsWarm() const { return view.coldState == "warm" || view.coldState == "breach"; }
  bool anyLow() const;

 private:
  void openSheet(Ask ask, int slot, uint32_t now);
  void closeSheet(uint32_t now);
  void pick(int number, uint32_t now);
  void act(Button b, uint32_t now, Action &out);
  std::string lastOrder;
  bool lastAlarm = false, lastHome = false;
};

// Text for a temperature with one decimal, for example "4.2" or "-18.0".
std::string formatTemp(float value, int digits = 1);

}  // namespace ui

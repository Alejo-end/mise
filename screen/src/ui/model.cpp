#include "model.h"

#include <cmath>
#include <cstdio>

namespace ui {

bool Cold::operator==(const Cold &o) const {
  return hasLimit == o.hasLimit && limit == o.limit && product == o.product && minutes == o.minutes && allowed == o.allowed;
}

bool Slot::operator==(const Slot &o) const {
  return slot == o.slot && units == o.units && target == o.target && reorder == o.reorder && onOrder == o.onOrder &&
         low == o.low && stale == o.stale && name == o.name && emptyAt == o.emptyAt;
}

static bool samePoint(float a, float b) { return (std::isnan(a) && std::isnan(b)) || a == b; }

bool View::operator==(const View &o) const {
  if (reached != o.reached || online != o.online || alarm != o.alarm || hasTemp != o.hasTemp || hasDelivery != o.hasDelivery ||
      tooWarm != o.tooWarm || hasPeak != o.hasPeak || temp != o.temp || peak != o.peak || approvals != o.approvals ||
      more != o.more || coldWarm != o.coldWarm || !(room == o.room) || !(door == o.door) || approval != o.approval ||
      orderId != o.orderId || total != o.total || coldState != o.coldState || lineCount != o.lineCount ||
      pointCount != o.pointCount || slotCount != o.slotCount || home != o.home)
    return false;
  for (int i = 0; i < lineCount; i++) if (lines[i] != o.lines[i]) return false;
  for (int i = 0; i < pointCount; i++) if (!samePoint(points[i], o.points[i])) return false;
  for (int i = 0; i < slotCount; i++) if (!(slots[i] == o.slots[i])) return false;
  return true;
}

std::string formatTemp(float value, int digits) {
  char s[16];
  snprintf(s, sizeof s, "%.*f", digits, (double)value);
  std::string out = s;
  if (out == "-0.0" || out == "-0") out = out.substr(1);
  return out;
}

void Roll::set(const std::string &text, float value, float old, uint32_t now, bool animate) {
  if (text == to) return;
  from = to;
  to = text;
  up = value >= old;
  at = animate && !from.empty() && !text.empty() ? now : 0;
}

int Model::slotIndex(int number) const {
  for (int i = 0; i < view.slotCount; i++) if (view.slots[i].slot == number) return i;
  return -1;
}

bool Model::anyLow() const {
  for (int i = 0; i < view.slotCount; i++) if (view.slots[i].low) return true;
  return false;
}

Link Model::link() const {
  if (!wifi) return LINK_NO_WIFI;
  if (!view.reached) return LINK_NO_BACKEND;
  if (!view.online) return LINK_BOX_OFFLINE;
  return LINK_OK;
}

void Model::setWifi(bool on) {
  if (on != wifi) dirty = true;
  wifi = on;
}

void Model::setTouch(bool ok) {
  if (ok != touch) dirty = true;
  touch = ok;
}

void Model::openSheet(Ask ask, int slot, uint32_t now) {
  asking = ask;
  sheetOpen = true;
  sheetAt = now;
  sheetAsk = ask;
  sheetSlot = slot;
  dirty = true;
}

void Model::closeSheet(uint32_t now) {
  if (sheetOpen) {
    sheetOpen = false;
    sheetAt = now;
    dirty = true;
  }
}

void Model::pick(int number, uint32_t now) {
  pickedBefore = picked;
  picked = number;
  pickAt = now;
  dirty = true;
}

// Change the view. A question that waits for "Yes", a selected slot and the camera belong to the view that is left.
void Model::setTab(Tab t, uint32_t now) {
  if (t == tab) return;
  tabFrom = tab;
  tab = t;
  tabAt = now;
  asking = ASK_NONE;
  reporting = false;
  picked = -1;
  pickAt = 0;
  sheetOpen = false;
  sheetAt = 0;
  cameraOpen = false;
  cameraAt = 0;
  if (t == TAB_COLD) coldAt = now + 100;  // the chart draws itself once the new view fades in
  dirty = true;
}

void Model::apply(const View &incoming, uint32_t now) {
  View next = incoming;
  if (!next.reached) next.home = view.home;  // without an answer the screen keeps the view it had
  if (next.orderId != view.orderId && asking != ASK_NONE) {  // the question was about another order
    asking = ASK_NONE;
    closeSheet(now);
  }
  if (next.reached) {
    // The door comes first at once when a delivery arrives or an alarm starts: the backend's view turns to
    // the door, a new order waits, or the alarm begins. The last answer that arrived is the one compared.
    bool doorNow = next.home == TAB_DOOR;
    if (!opened) {
      setTab(next.home, now);
      tabAt = 0;  // the first view appears without a switch
      coldAt = now;
    } else if ((doorNow && !lastHome) || (next.alarm && !lastAlarm) || (next.hasDelivery && next.orderId != lastOrder)) {
      setTab(TAB_DOOR, now);
    }
    opened = true;
    lastOrder = next.orderId;
    lastAlarm = next.alarm;
    lastHome = doorNow;
  }
  // The numbers that roll when they change: the air now, and the count of each row.
  temp.set(next.hasTemp ? formatTemp(next.temp) : std::string(), next.temp, view.temp, now, view.hasTemp);
  for (int i = 0; i < SLOTS; i++) {
    const Slot *s = i < next.slotCount ? &next.slots[i] : nullptr;
    const Slot *o = i < view.slotCount ? &view.slots[i] : nullptr;
    std::string text = s && s->units >= 0 ? std::to_string(s->units) : std::string();
    units[i].set(text, s ? (float)s->units : 0, o ? (float)o->units : 0, now, o && s && o->slot == s->slot);
  }
  if (!(next == view)) dirty = true;
  view = next;
  if (picked >= 0 && slotIndex(picked) < 0) {  // the slot is gone from the list
    picked = -1;
    reporting = false;
    closeSheet(now);
  }
}

void Model::tap(int x, int y, uint32_t now) {
  using namespace layout;
  touchAt = now;
  if (toastOn || busy || waiting || pressed != BTN_NONE) return;
  if (cameraOpen) {
    // "Camera | Depth" switches the mode and keeps the view open. A tap anywhere else closes it.
    if (CAMERA_MODES.hit(x, y, 4, 8)) {
      bool depthNow = x >= CAMERA_MODES.x + CAMERA_MODES.w / 2;
      if (depthNow != depthMode) {
        depthMode = depthNow;
        modeAt = now;
        dirty = true;
      }
      return;
    }
    cameraOpen = false;
    cameraAt = now;
    dirty = true;
    return;
  }
  // The alarm capsule is in the bar. A tap under the bar belongs to the view, so it never confirms the alarm.
  auto press = [&](Button b) { pressed = b; pressedAt = now; dirty = true; };
  if (view.alarm && x >= SEEN_X - 8 && y < BODY_Y + 2) { press(BTN_SEEN); return; }
  if (y >= NAV_TOUCH_Y) {  // the tabs come before the margin of the buttons above them
    int i = (x - NAV.x) * 3 / NAV.w;
    setTab((Tab)(i < 0 ? 0 : i > 2 ? 2 : i), now);
    return;
  }
  if (tab == TAB_STOCK) {
    int i = slotIndex(picked);
    if (reporting && i >= 0) {
      if (BACK.hit(x, y, 6, 18)) press(BTN_BACK);
      else if (YES.hit(x, y, 6, 18)) press(BTN_YES);
      return;
    }
    if (THUMB.hit(x, y, 6, 6)) {
      cameraOpen = true;
      depthMode = false;  // the large view opens on the picture
      modeAt = 0;
      cameraAt = now;
      dirty = true;
      return;
    }
    for (int r = 0; r < view.slotCount; r++) {
      Rect rr = row(r);
      if (y < rr.y - 3 || y >= rr.y + rr.h + 3 || x > rr.x + rr.w + 4) continue;
      int number = view.slots[r].slot;
      if (picked == number && x >= reportButton(r).x - 8) press(BTN_REPORT);
      else pick(picked == number ? -1 : number, now);
      return;
    }
    return;
  }
  if (tab != TAB_DOOR || !view.hasDelivery) return;
  if (asking == ASK_NONE) {
    if (ACCEPT.hit(x, y, 6, 18)) press(BTN_ACCEPT);
    else if (REJECT.hit(x, y, 6, 18)) press(BTN_REJECT);
  } else if (BACK.hit(x, y, 6, 18)) {
    press(BTN_BACK);
  } else if (YES.hit(x, y, 6, 18)) {
    press(BTN_YES);
  }
}

// What a pressed button does once its pressed state has shown.
void Model::act(Button b, uint32_t now, Action &out) {
  switch (b) {
    case BTN_ACCEPT: openSheet(ASK_ACCEPT, -1, now); break;
    case BTN_REJECT: openSheet(ASK_REJECT, -1, now); break;
    case BTN_REPORT: reporting = true; openSheet(ASK_NONE, picked, now); break;
    case BTN_BACK:
      asking = ASK_NONE;
      reporting = false;
      closeSheet(now);
      break;
    case BTN_YES:
      if (reporting) { out.kind = Action::REPORT_LOW; out.slot = picked; }
      else if (asking != ASK_NONE) { out.kind = Action::DECIDE; out.accepted = asking == ASK_ACCEPT; }
      break;
    case BTN_SEEN: out.kind = Action::ALARM_SEEN; break;
    default: break;
  }
  if (out.kind != Action::NONE) busy = true;  // the button stays pressed while the sketch sends
  else pressed = BTN_NONE;
  dirty = true;
}

Action Model::poll(uint32_t now) {
  Action out;
  if (toastOn && now - toastAt >= TOAST_MS) {
    toastOn = false;
    bodyAt = now;
    if (tab == TAB_COLD) coldAt = now;
    dirty = true;
  }
  if (pressed != BTN_NONE && !busy && now - pressedAt >= PRESS_MS) act(pressed, now, out);
  if (out.kind != Action::NONE) return out;
  // A minute without a touch: back to the backend's view, with no question left open.
  if (opened && !toastOn && !busy && now - touchAt > IDLE_MS) {
    setTab(view.home, now);
    if (asking != ASK_NONE || picked >= 0 || reporting) dirty = true;
    asking = ASK_NONE;
    picked = -1;
    reporting = false;
    closeSheet(now);
  }
  if (cameraOpen && now - touchAt > CAMERA_IDLE_MS) {
    cameraOpen = false;
    cameraAt = now;
    dirty = true;
  }
  return out;
}

void Model::done(uint32_t now) {
  pressed = BTN_NONE;
  busy = false;
  asking = ASK_NONE;
  if (waiting) {
    picked = -1;
    reporting = false;
    waiting = false;
  }
  closeSheet(now);
  dirty = true;
}

void Model::showToast(const std::string &title, const std::string &sub, bool good, uint32_t now) {
  toast = title;
  toastSub = sub;
  toastGood = good;
  toastOn = true;
  toastAt = now;
  dirty = true;
}

void Model::beginWaiting() {
  waiting = true;
  dirty = true;
}

static void newPicture(Camera &cam, const uint16_t *px, int w, int h, int ageS, uint32_t now) {
  cam.prev = cam.live(now) ? cam.cur : Picture();  // a cross-fade from the old picture, or from the placeholder
  cam.cur = {px, w, h};
  cam.arrivedAt = now;
  cam.ageS = ageS;
  cam.ageAt = now;
}

void Model::setThumb(const uint16_t *px, int w, int h, int ageS, uint32_t now) {
  newPicture(thumb, px, w, h, ageS, now);
  if (wantsThumb()) dirty = true;
}

void Model::setFull(const uint16_t *px, int w, int h, int ageS, uint32_t now) {
  newPicture(full, px, w, h, ageS, now);
  if (cameraOpen) dirty = true;
}

void Model::noPicture(bool large, uint32_t now) {
  Camera &cam = large ? full : thumb;
  if (cam.ageS >= 0) dirty = true;
  cam = Camera();
  (void)now;
}

}  // namespace ui

namespace ui {

void Depth::shown(int i, uint32_t now, float &far, float &on) const {
  float t = arrivedAt ? (float)(int32_t)(now - arrivedAt) / DEPTH_EASE_MS : 1;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  t = 1 - (1 - t) * (1 - t) * (1 - t);  // ease out
  far = fromFar[i] + (toFar[i] - fromFar[i]) * t;
  on = fromOn[i] + (toOn[i] - fromOn[i]) * t;
}

void Model::setDepth(const float *cm, int ageS, uint32_t now) {
  for (int i = 0; i < DEPTH_CELLS; i++) {
    float far, on;
    depth.shown(i, now, far, on);  // the ease starts from what the cell shows now
    depth.fromFar[i] = on > 0 ? far : (std::isnan(cm[i]) ? 1 : cm[i] / DEPTH_FAR_CM);
    depth.fromOn[i] = depth.ageS >= 0 ? on : 0;
    depth.cm[i] = cm[i];
    float f = std::isnan(cm[i]) ? depth.fromFar[i] : cm[i] / DEPTH_FAR_CM;
    depth.toFar[i] = f < 0 ? 0 : f > 1 ? 1 : f;
    depth.toOn[i] = std::isnan(cm[i]) ? 0 : 1;
  }
  depth.arrivedAt = now;
  depth.ageS = ageS;
  depth.ageAt = now;
  if (wantsDepth()) dirty = true;
}

void Model::noDepth() {
  if (depth.ageS >= 0) dirty = true;
  depth = Depth();
}

}  // namespace ui

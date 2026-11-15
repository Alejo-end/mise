#include "draw.h"

#include <cmath>
#include <cstdio>
#include <cstring>

namespace ui {

using namespace layout;

// The type scale: captions, body, labels, titles, numbers, and the hero numerals.
static const Font &CAPTION = kInter13M;
static const Font &BODY = kInter15R;
static const Font &LABEL = kInter15M;
static const Font &TITLE = kInter20S;
static const Font &NUMBER = kInter28S;
static const Font &HERO = kInter60M;

static const float PI = 3.14159265f;

// The progress of a motion that started at `at` and lasts `ms`, from 0 to 1. A clock of 0 is a motion that
// never ran, so it counts as done. A running motion asks for another frame.
static float progress(uint32_t now, uint32_t at, float ms, Frame &f) {
  if (!at) return 1;
  float t = (float)(int32_t)(now - at) / ms;
  if (t < 1) f.animating = true;
  return clamp01(t);
}

// A slow wave from 0 to 1 and back, for the breathing dots. It asks for the slow loop.
static float breathe(uint32_t now, float periodMs, Frame &f) {
  f.looping = true;
  return 0.5f - 0.5f * cosf(2 * PI * (float)(now % (uint32_t)periodMs) / periodMs);
}

// ---------------------------------------------------------------------------------------------------------
// Small parts

// A check mark in a box of size s around (cx, cy), drawn as a stroke up to the fraction k of its length.
static void checkMark(Canvas &c, float cx, float cy, float s, float width, uint16_t colour, float k = 1, int alpha = 255) {
  float ax = cx - 0.5f * s, ay = cy + 0.02f * s, bx = cx - 0.14f * s, by = cy + 0.36f * s, ex = cx + 0.5f * s, ey = cy - 0.34f * s;
  float l1 = hypotf(bx - ax, by - ay), l2 = hypotf(ex - bx, ey - by), d = k * (l1 + l2);
  if (d <= 0) return;
  if (d <= l1) { line(c, ax, ay, ax + (bx - ax) * d / l1, ay + (by - ay) * d / l1, width, colour, alpha); return; }
  line(c, ax, ay, bx, by, width, colour, alpha);
  float e = (d - l1) / l2;
  line(c, bx, by, bx + (ex - bx) * e, by + (ey - by) * e, width, colour, alpha);
}

// A cross in a box of size s, the two strokes one after the other up to the fraction k.
static void crossMark(Canvas &c, float cx, float cy, float s, float width, uint16_t colour, float k = 1, int alpha = 255) {
  float h = s / 2, a = clamp01(k * 2), b = clamp01(k * 2 - 1);
  if (a > 0) line(c, cx - h, cy - h, cx - h + s * a, cy - h + s * a, width, colour, alpha);
  if (b > 0) line(c, cx + h, cy - h, cx + h - s * b, cy - h + s * b, width, colour, alpha);
}

// A drawn degree sign: a small ring at the height of the top of the digits.
static void degree(Canvas &c, float x, float capTop, float r, float t, uint16_t colour, int alpha = 255) {
  ring(c, x + r, capTop + r, r, t, colour, alpha);
}

// A small text with a dot before it, on a tinted capsule. It gives back the width.
static int chip(Canvas &c, float x, float y, const char *text, uint16_t colour, uint16_t fill, int fillAlpha, bool measure = false) {
  int w = 10 + 6 + 7 + textWidth(CAPTION, text) + 11, h = 24;
  if (measure) return w;
  fillRound(c, x, y, w, h, h / 2, fill, fillAlpha);
  fillCircle(c, x + 13, y + h / 2.0f, 3, colour);
  drawText(c, CAPTION, x + 23, y + 16.5f, text, colour);
  return w;
}

// A large button. A pressed button is a little darker and 2 pixels smaller on each side.
enum Style { FILLED, OUTLINED };
static void button(Canvas &c, const Rect &r, const char *label, Style style, uint16_t colour, uint16_t down, bool pressed,
                   char sign = 0) {
  float in = pressed ? 2 : 0;
  float x = r.x + in, y = r.y + in, w = r.w - 2 * in, h = r.h - 2 * in, rad = 14 - in;
  uint16_t ink = style == FILLED ? col::WHITE : colour;
  if (style == FILLED) {
    fillRound(c, x, y, w, h, rad, pressed ? down : colour);
  } else {
    if (pressed) fillRound(c, x, y, w, h, rad, colour, 40);
    strokeRound(c, x, y, w, h, rad, 1.5f, colour, colour == col::TEXT ? 70 : 255);
  }
  int tw = textWidth(TITLE, label), sw = sign ? 22 : 0;
  float lx = r.x + (r.w - tw - sw) / 2.0f, cy = r.y + r.h / 2.0f;
  if (sign == 'v') checkMark(c, lx + 7, cy, 13, 2.2f, ink);
  if (sign == 'x') crossMark(c, lx + 7, cy, 10, 2.2f, ink);
  drawText(c, TITLE, lx + sw, cy + 7, label, ink);
}

// A picture of the camera with rounded corners, blended at the given opacity.
static void picture(Canvas &c, const Picture &p, float fx, float fy, float r, int alpha) {
  if (!p.px) return;
  int x = (int)lroundf(fx + c.ox), y = (int)lroundf(fy + c.oy);
  int a = (alpha * c.opacity + 127) >> 8;
  if (a <= 0) return;
  float cx = x + p.w / 2.0f, cy = y + p.h / 2.0f, hx = p.w / 2.0f - r, hy = p.h / 2.0f - r;
  for (int j = 0; j < p.h; j++) {
    int py = y + j;
    if (py < c.cy0 || py >= c.cy1) continue;
    bool cornerRow = fabsf(py + 0.5f - cy) > hy;
    const uint16_t *src = p.px + j * p.w;
    uint16_t *dst = c.px + py * c.w;
    for (int i = 0; i < p.w; i++) {
      int px = x + i;
      if (px < c.cx0 || px >= c.cx1) continue;
      int cov = a;
      if (cornerRow && fabsf(px + 0.5f - cx) > hx) {
        float qx = fabsf(px + 0.5f - cx) - hx, qy = fabsf(py + 0.5f - cy) - hy;
        float d = sqrtf(qx * qx + qy * qy) - r;
        float k = clamp01(0.5f - d);
        cov = (int)(k * a);
        if (cov <= 0) continue;
      }
      dst[px] = cov >= 255 ? src[i] : mix(dst[px], src[i], cov);
    }
  }
}

// A camera drawn from shapes: a body, a lens and the bump of the viewfinder.
static void cameraIcon(Canvas &c, float cx, float cy, float s, uint16_t colour) {
  float w = s, h = s * 0.68f;
  strokeRound(c, cx - w / 2, cy - h / 2 + 2, w, h, s * 0.16f, 1.6f, colour);
  fillRound(c, cx - w * 0.2f, cy - h / 2 - 2, w * 0.4f, 6, 2.5f, colour);
  ring(c, cx, cy + 2, s * 0.2f, 1.6f, colour);
}

static void ageText(int s, char *out, int size, const char *prefix) {
  if (s < 60) snprintf(out, size, "%s%d s ago", prefix, s);
  else if (s < 3600) snprintf(out, size, "%s%d min ago", prefix, s / 60);
  else snprintf(out, size, "%s%d h ago", prefix, s / 3600);
}

// ---------------------------------------------------------------------------------------------------------
// The verdict of a storage limit: the words and the colour.

struct Verdict {
  const char *text;
  uint16_t colour;
  bool known, warm, breach;
};

static Verdict verdict(const View &v, const Cold &cold) {
  Verdict out{"Cold enough", col::GREEN, false, false, false};
  out.known = v.hasTemp && cold.hasLimit;
  out.warm = out.known && v.temp > cold.limit;
  out.breach = out.warm && cold.minutes >= cold.allowed;
  if (!v.hasTemp) out = {"No reading", col::TEXT2, false, false, false};
  else if (!cold.hasLimit) out = {"No limit known", col::TEXT2, false, false, false};
  else if (out.breach) { out.text = "Check before use"; out.colour = col::PINK; }
  else if (out.warm) { out.text = "Too warm"; out.colour = col::PINK; }
  return out;
}

// The verdict of the cold room as a whole, from the backend's state.
static Verdict roomVerdict(const Model &m) {
  const View &v = m.view;
  if (v.coldState == "ok") return {"Cold enough", col::GREEN, true, false, false};
  if (v.coldState == "breach") return {"Check before use", col::PINK, true, true, true};
  if (v.coldState == "warm") return {"Too warm", col::PINK, true, true, false};
  return {v.hasTemp ? "No limit known" : "No reading", col::TEXT2, false, false, false};
}

// ---------------------------------------------------------------------------------------------------------
// A number that rolls: each changed digit slides out and the new one slides in, inside the line's box.

static int rolling(Canvas &c, const Font &f, float x, float y, const Roll &roll, uint32_t now, uint16_t colour, Frame &fr) {
  float t = progress(now, roll.at, 320, fr);
  const std::string &to = roll.to, &from = roll.from;
  if (t >= 1 || from.empty()) return drawText(c, f, x, y, to.c_str(), colour);
  float e = easeOut(t), rise = f.cap + 6.0f, dir = roll.up ? 1 : -1;
  int sx0 = c.cx0, sy0 = c.cy0, sx1 = c.cx1, sy1 = c.cy1;
  int top = (int)(y + c.oy - f.cap - 3), bottom = (int)(y + c.oy + 4);
  c.clip(sx0, top > sy0 ? top : sy0, sx1, bottom < sy1 ? bottom : sy1);
  // The two texts are aligned on their right end, where the decimals are.
  int wTo = textWidth(f, to.c_str()), wFrom = textWidth(f, from.c_str());
  float pen = x;
  int n = (int)to.size(), shift = (int)to.size() - (int)from.size();
  char one[2] = {0, 0};
  for (int i = 0; i < n; i++) {
    one[0] = to[i];
    int j = i - shift;
    bool same = j >= 0 && j < (int)from.size() && from[j] == to[i];
    if (same) {
      drawText(c, f, pen, y, one, colour);
    } else {
      drawText(c, f, pen, y + dir * rise * (1 - e), one, colour, (int)(255 * e));
      if (j >= 0 && j < (int)from.size()) {
        one[0] = from[j];
        drawText(c, f, pen, y - dir * rise * e, one, colour, (int)(255 * (1 - e)));
      }
    }
    one[0] = to[i];
    pen += textWidth(f, one);
  }
  // Old characters to the left of the new text fade out in place.
  if (shift < 0) {
    float opx = x + wTo - wFrom;
    for (int j = 0; j < -shift; j++) {
      one[0] = from[j];
      drawText(c, f, opx, y - dir * rise * e, one, colour, (int)(255 * (1 - e)));
      opx += textWidth(f, one);
    }
  }
  c.clip(sx0, sy0, sx1, sy1);
  return (int)x + wTo;
}

// ---------------------------------------------------------------------------------------------------------
// The top bar

static void drawBar(Canvas &c, const Model &m, uint32_t now, Frame &f) {
  const View &v = m.view;
  drawAlpha(c, kWordmarkSmall, SIDE, 24 - kWordmarkSmall.baseline + 4, col::TEXT, 235);
  float after = SIDE + kWordmarkSmall.w + 14;
  if (!m.touch) drawText(c, CAPTION, after, 28, "No touch", col::PINK);
  else if (v.hasDelivery && v.more > 0 && !v.alarm) {
    char s[32];
    snprintf(s, sizeof s, "%d at the door", v.more + 1);
    drawText(c, CAPTION, after, 28, s, col::TEXT2);
  }

  if (v.alarm) {  // the capsule is the control: a tap says "I have seen it"
    bool down = m.pressed == BTN_SEEN;
    float x = SEEN_X, y = 8, w = W - SIDE - SEEN_X, h = 28, in = down ? 2 : 0;
    float b = breathe(now, 1600, f);
    strokeRound(c, x - 2 - 3 * b, y - 2 - 3 * b, w + 4 + 6 * b, h + 4 + 6 * b, (h + 4 + 6 * b) / 2, 1.5f, col::PINK,
                (int)(150 * (1 - b)));
    fillRound(c, x + in, y + in, w - 2 * in, h - 2 * in, (h - 2 * in) / 2, down ? col::PINK_DOWN : col::PINK_FILL);
    drawTextCentre(c, CAPTION, x + w / 2, y + 18.5f, "Cold alarm. Tap when seen", col::WHITE);
    return;
  }
  Link link = m.link();
  static const char *const WORDS[] = {"Online", "No Wi-Fi", "No backend", "Box offline"};
  const char *word = WORDS[link];
  bool ok = link == LINK_OK;
  int w = chip(c, 0, 0, word, 0, 0, 0, true);
  float x = W - SIDE - w, y = 10;
  if (ok) {
    fillRound(c, x, y, w, 24, 12, col::SURFACE);
    strokeRound(c, x, y, w, 24, 12, 1, col::HAIRLINE);
    fillCircle(c, x + 13, y + 12, 3, col::GREEN);
    drawText(c, CAPTION, x + 23, y + 16.5f, word, col::TEXT2);
  } else {
    chip(c, x, y, word, col::PINK, col::PINK, 36);
  }
}

// ---------------------------------------------------------------------------------------------------------
// The tabs: a segmented control with a highlight that slides between the segments.

static void drawNav(Canvas &c, const Model &m, uint32_t now, Frame &f) {
  const View &v = m.view;
  const Rect &n = NAV;
  fillRound(c, n.x, n.y, n.w, n.h, n.h / 2.0f, col::SURFACE);
  strokeRound(c, n.x, n.y, n.w, n.h, n.h / 2.0f, 1, col::HAIRLINE);
  float seg = (n.w - 8) / 3.0f;
  float t = easeOut(progress(now, m.tabAt, 280, f));
  float hx = n.x + 4 + seg * lerp((float)m.tabFrom, (float)m.tab, m.tabAt ? t : 1);
  fillRound(c, hx, n.y + 4, seg, n.h - 8, (n.h - 8) / 2.0f, col::RAISED);
  strokeRound(c, hx, n.y + 4, seg, n.h - 8, (n.h - 8) / 2.0f, 1, col::EDGE);
  static const char *const LABELS[3] = {"Door", "Cold", "Stock"};
  bool dot[3] = {v.hasDelivery, m.coldIsWarm(), m.anyLow()};
  uint16_t dotColour[3] = {col::BLUE, col::PINK, col::AMBER};
  for (int i = 0; i < 3; i++) {
    float sx = n.x + 4 + seg * i;
    float near = clamp01(1 - fabsf(hx - sx) / seg);  // how much of the highlight lies on this segment
    int lw = textWidth(LABEL, LABELS[i]);
    float lx = sx + (seg - lw) / 2.0f, base = n.y + n.h / 2.0f + 5.5f;
    drawText(c, LABEL, lx, base, LABELS[i], mix(col::TEXT2, col::TEXT, (int)(255 * near)));
    if (dot[i]) fillCircle(c, lx + lw + 7, base - LABEL.cap + 1, 3.5f, dotColour[i]);
  }
}

// ---------------------------------------------------------------------------------------------------------
// Door

// The gauge of the air against a limit: a track, a mark at the limit, and the value as a dot.
static void gauge(Canvas &c, float x, float y, float w, float temp, float limit, uint16_t colour) {
  float lo = fminf(limit - 6, temp - 1), hi = fmaxf(limit + 4, temp + 1);
  auto at = [&](float v) { return x + w * clamp01((v - lo) / (hi - lo)); };
  float vx = at(temp), lx = at(limit);
  fillRound(c, x, y - 2.5f, w, 5, 2.5f, col::RAISED);
  fillRound(c, x, y - 2.5f, vx - x, 5, 2.5f, colour, 90);
  fillRound(c, lx - 1, y - 7, 2, 14, 1, col::TEXT2);
  fillCircle(c, vx, y, 7.5f, col::SURFACE);
  fillCircle(c, vx, y, 5.5f, colour);
}

// The line under the verdict: how far the air is under the limit, or how long it was above it.
static void limitCaption(const View &v, const Cold &cold, const Verdict &vd, char *out, int size) {
  if (!vd.known) { snprintf(out, size, "%s", v.hasTemp ? "No limit in the data" : "The box sends none"); return; }
  if (vd.warm) { snprintf(out, size, "Above for %d min", cold.minutes); return; }
  snprintf(out, size, "%s" DEG " under %s" DEG, formatTemp(cold.limit - v.temp).c_str(), formatTemp(cold.limit, cold.limit == roundf(cold.limit) ? 0 : 1).c_str());
}

static void drawDoorDelivery(Canvas &c, const Model &m, Frame &f) {
  const View &v = m.view;
  const Rect &k = DOOR_CARD;
  fillRound(c, k.x, k.y, k.w, k.h, 16, col::SURFACE);
  strokeRound(c, k.x, k.y, k.w, k.h, 16, 1, col::HAIRLINE);
  const int split = 316;
  fillRect(c, split, k.y + 16, 1, k.h - 32, col::HAIRLINE);

  // The order: its number and total, and its lines.
  int left = k.x + 16, right = split - 16;
  char buf[96], fit[100];
  snprintf(buf, sizeof buf, "Order %s", v.orderId.c_str());
  int tw = textWidth(LABEL, v.total.c_str());
  drawText(c, CAPTION, left, k.y + 24, fitText(CAPTION, buf, right - left - tw - 12, fit, sizeof fit), col::TEXT2);
  drawTextRight(c, LABEL, right, k.y + 24, v.total.c_str(), col::TEXT);
  for (int i = 0; i < v.lineCount && i < 3; i++)
    drawText(c, BODY, left, k.y + 52 + i * 22, fitText(BODY, v.lines[i].c_str(), right - left, fit, sizeof fit), col::TEXT);

  // The verdict: the words, the air now, the gauge against the limit, and how far it is from the limit.
  Verdict vd = verdict(v, v.door);
  int vx = split + 16, vw = k.x + k.w - 16 - vx;
  fillCircle(c, vx + 3.5f, k.y + 19.5f, 3.5f, vd.colour);
  drawText(c, LABEL, vx + 13, k.y + 24, vd.text, vd.colour);
  if (v.hasTemp) {
    int x = rolling(c, NUMBER, vx, k.y + 62, m.temp, 0, col::TEXT, f);
    degree(c, x + 2, k.y + 62 - NUMBER.cap, 3.5f, 1.8f, col::TEXT);
    drawText(c, LABEL, x + 12, k.y + 62 - NUMBER.cap + LABEL.cap, "C", col::TEXT2);
  } else {
    drawText(c, NUMBER, vx, k.y + 62, "--", col::TEXT3);
  }
  if (vd.known) gauge(c, vx + 6, k.y + 76, vw - 12, v.temp, v.door.limit, vd.colour);
  limitCaption(v, v.door, vd, buf, sizeof buf);
  drawText(c, CAPTION, vx, k.y + 98, fitText(CAPTION, buf, vw, fit, sizeof fit), col::TEXT2);
}

static void drawDoorButtons(Canvas &c, const Model &m) {
  button(c, ACCEPT, "Accept", FILLED, col::BLUE_FILL, col::BLUE_DOWN, m.pressed == BTN_ACCEPT, 'v');
  button(c, REJECT, "Reject", OUTLINED, col::PINK, col::PINK, m.pressed == BTN_REJECT, 'x');
}

static void drawDoorEmpty(Canvas &c, const Model &m, Frame &f) {
  const View &v = m.view;
  // A calm sign that all is done: a soft disc, a thin ring and a check.
  float cx = 62, cy = 104;
  fillCircle(c, cx, cy, 34, col::GREEN, 20);
  ring(c, cx, cy, 34, 1.5f, col::GREEN, 90);
  checkMark(c, cx + 1, cy - 1, 24, 3, col::GREEN);

  float x = 120;
  drawText(c, TITLE, x, 88, "Nothing at the door", col::TEXT);
  char buf[96], fit[100];
  if (v.approvals > 0) {
    snprintf(buf, sizeof buf, "%d %s for the head chef, on the phone:", v.approvals, v.approvals == 1 ? "order waits" : "orders wait");
    drawText(c, BODY, x, 114, fitText(BODY, buf, W - SIDE - x, fit, sizeof fit), col::TEXT2);
    drawText(c, BODY, x, 136, fitText(BODY, v.approval.c_str(), W - SIDE - x, fit, sizeof fit), col::TEXT);
  } else {
    drawText(c, BODY, x, 114, "Every delivery is checked.", col::TEXT2);
  }

  // The cold room in one line: the state against the limit of the product that needs attention first.
  Rect r = {SIDE, 164, W - 2 * SIDE, 46};
  fillRound(c, r.x, r.y, r.w, r.h, 14, col::SURFACE);
  strokeRound(c, r.x, r.y, r.w, r.h, 14, 1, col::HAIRLINE);
  Verdict vd = verdict(v, v.room);
  float base = r.y + 28;
  drawText(c, LABEL, r.x + 16, base, "Cold room", col::TEXT);
  int cx2 = r.x + 16 + textWidth(LABEL, "Cold room") + 16;
  fillCircle(c, cx2 + 3.5f, base - 4.5f, 3.5f, vd.colour);
  if (v.coldWarm > 1) snprintf(buf, sizeof buf, "%d products too warm", v.coldWarm);
  else snprintf(buf, sizeof buf, "%s", vd.text);
  drawText(c, CAPTION, cx2 + 13, base - 0.5f, buf, vd.colour);
  if (v.hasTemp) {
    snprintf(buf, sizeof buf, "%s" DEG "C", formatTemp(v.temp).c_str());
    drawTextRight(c, LABEL, r.x + r.w - 16, base, buf, col::TEXT);
  }
  (void)f;
}

// ---------------------------------------------------------------------------------------------------------
// Cold

struct Plot {
  float x0, x1, y0, y1;  // the plot area
  float lo, hi;          // the scale
  float y(float v) const { return y1 - (v - lo) * (y1 - y0) / (hi - lo); }
};

// The air over the last 2 hours: a smooth line, a soft fill under it, the limit as a dashed line.
// reveal (0 to 1) draws the line from the left; the last point breathes once the line is complete.
static void chart(Canvas &c, const Model &m, const Rect &box, float reveal, uint32_t now, Frame &f) {
  const View &v = m.view;
  fillRound(c, box.x, box.y, box.w, box.h, 16, col::SURFACE);
  strokeRound(c, box.x, box.y, box.w, box.h, 16, 1, col::HAIRLINE);
  Plot p{(float)box.x + 14, (float)box.x + box.w - 44, (float)box.y + 22, (float)box.y + box.h - 34, 0, 6};

  // The scale fits the points and the limit, with at least 6 degrees.
  float lo = INFINITY, hi = -INFINITY;
  int known = 0, lastKnown = -1;
  for (int i = 0; i < v.pointCount; i++) {
    if (std::isnan(v.points[i])) continue;
    lo = fminf(lo, v.points[i]);
    hi = fmaxf(hi, v.points[i]);
    known++;
    lastKnown = i;
  }
  if (v.room.hasLimit) { lo = fminf(lo, v.room.limit); hi = fmaxf(hi, v.room.limit); }
  if (lo > hi) { lo = 0; hi = 6; }
  lo = floorf(lo - 0.5f);
  hi = ceilf(hi + 0.5f);
  if (hi - lo < 6) { float mid = (hi + lo) / 2; lo = floorf(mid - 3); hi = lo + 6; }
  p.lo = lo;
  p.hi = hi;

  // The guides: a base line, and a dotted line at one hour ago.
  fillRect(c, p.x0, p.y1 + 6, p.x1 - p.x0, 1, col::HAIRLINE);
  float mid = (p.x0 + p.x1) / 2;
  for (float y = p.y0; y < p.y1 + 4; y += 6) fillRect(c, mid, y, 1, 2, col::HAIRLINE);
  int axisY = box.y + box.h - 12;
  drawText(c, CAPTION, p.x0, axisY, "2 h ago", col::TEXT2);
  drawTextCentre(c, CAPTION, mid, axisY, "1 h ago", col::TEXT2);
  drawTextRight(c, CAPTION, p.x1, axisY, "Now", col::TEXT2);

  float limitY = v.room.hasLimit ? p.y(v.room.limit) : -1000;
  if (v.room.hasLimit) {
    for (float x = p.x0; x < p.x1; x += 7) fillRect(c, x, limitY - 0.5f, fminf(3.5f, p.x1 - x), 1, col::TEXT3);
    char s[16];
    snprintf(s, sizeof s, "%s" DEG, formatTemp(v.room.limit, v.room.limit == roundf(v.room.limit) ? 0 : 1).c_str());
    int w = textWidth(CAPTION, s) + 12;
    float lx = p.x1 + 6, ly = limitY - 10;
    fillRound(c, lx, ly, w, 20, 10, col::RAISED);
    drawText(c, CAPTION, lx + 6, ly + 14.5f, s, col::TEXT2);
  }

  if (!known) {  // a skeleton: a quiet wave where the line would be, and what is missing
    for (float x = p.x0; x < p.x1 - 2; x += 2) {
      float a = (x - p.x0) / (p.x1 - p.x0);
      float y = (p.y0 + p.y1) / 2 + 10 * sinf(a * 2 * PI * 1.3f + 0.6f);
      fillCircle(c, x, y, 1.4f, col::EDGE);
    }
    float cy = (p.y0 + p.y1) / 2;
    int tw = textWidth(CAPTION, "No readings in the last 2 hours") + 24;
    fillRound(c, (p.x0 + p.x1 - tw) / 2, cy - 13, tw, 26, 13, col::RAISED);
    drawTextCentre(c, CAPTION, (p.x0 + p.x1) / 2, cy + 4.5f, "No readings in the last 2 hours", col::TEXT2);
    return;
  }

  // The points through a monotone cubic curve, so the line never swings past a reading.
  int n = v.pointCount;
  float step = n > 1 ? (p.x1 - p.x0) / (n - 1) : 0;
  float tan[POINTS];
  for (int i = 0; i < n; i++) {
    float a = v.points[i];
    if (std::isnan(a)) { tan[i] = 0; continue; }
    bool hasL = i > 0 && !std::isnan(v.points[i - 1]), hasR = i + 1 < n && !std::isnan(v.points[i + 1]);
    float dl = hasL ? a - v.points[i - 1] : 0, dr = hasR ? v.points[i + 1] - a : 0;
    if (hasL && hasR) tan[i] = dl * dr <= 0 ? 0 : (dl + dr) / 2;
    else tan[i] = hasL ? dl : hasR ? dr : 0;
  }
  for (int i = 0; i + 1 < n; i++) {  // the limit of Fritsch and Carlson keeps each piece monotone
    float a = v.points[i], b = v.points[i + 1];
    if (std::isnan(a) || std::isnan(b)) continue;
    float d = b - a;
    if (d == 0) { tan[i] = tan[i + 1] = 0; continue; }
    float al = tan[i] / d, be = tan[i + 1] / d, s2 = al * al + be * be;
    if (s2 > 9) { float k = 3 / sqrtf(s2); tan[i] = k * al * d; tan[i + 1] = k * be * d; }
  }
  // The curve at each pixel column, NAN where no reading is near.
  const int X0 = (int)p.x0, X1 = (int)p.x1, COLS = X1 - X0 + 1;
  float ys[480];
  float revealX = p.x0 + (p.x1 - p.x0) * reveal;
  for (int k = 0; k < COLS; k++) {
    ys[k] = NAN;
    float x = X0 + k + 0.5f;
    if (n < 2 || x > revealX) continue;
    float u = (x - p.x0) / step;
    int i = (int)floorf(u);
    if (i < 0 || i >= n - 1) continue;
    float a = v.points[i], b = v.points[i + 1];
    if (std::isnan(a) || std::isnan(b)) continue;
    float t = u - i, t2 = t * t, t3 = t2 * t;
    float val = (2 * t3 - 3 * t2 + 1) * a + (t3 - 2 * t2 + t) * tan[i] + (-2 * t3 + 3 * t2) * b + (t3 - t2) * tan[i + 1];
    ys[k] = p.y(val);
  }
  // The fill: a soft gradient from the line down to the base, pink above the limit and blue under it.
  for (int k = 0; k < COLS; k++) {
    if (std::isnan(ys[k])) continue;
    int px = X0 + k;
    for (int py = (int)ceilf(ys[k]); py <= (int)p.y1 + 6; py++) {
      float a = 96 * (1 - (py - p.y0) / (p.y1 + 6 - p.y0));
      if (a <= 0) continue;
      // Dithering in the coverage hides the steps of the gradient.
      static const uint8_t B4[4][4] = {{0, 8, 2, 10}, {12, 4, 14, 6}, {3, 11, 1, 9}, {15, 7, 13, 5}};
      int ai = (int)(a + B4[py & 3][px & 3] / 2.0f - 3.5f);
      c.blend(px, py, py + 0.5f < limitY ? col::PINK : col::BLUE, ai < 0 ? 0 : ai);
    }
  }
  // The line: each pixel near the curve takes its coverage from its distance to the nearest piece.
  const float half = 1.25f;
  for (int k = 0; k < COLS; k++) {
    float top = INFINITY, bottom = -INFINITY;
    for (int j = k - 2; j <= k + 2; j++) {
      if (j < 0 || j >= COLS || std::isnan(ys[j])) continue;
      top = fminf(top, ys[j]);
      bottom = fmaxf(bottom, ys[j]);
    }
    if (top > bottom) continue;
    int px = X0 + k;
    for (int py = (int)floorf(top - half - 1); py <= (int)ceilf(bottom + half + 1); py++) {
      float qx = px + 0.5f, qy = py + 0.5f, best = 1e9f;
      for (int j = k - 2; j <= k + 1; j++) {
        if (j < 0 || j >= COLS || std::isnan(ys[j])) continue;
        float ax = X0 + j + 0.5f, ay = ys[j];
        float bx = ax + 1, by = j + 1 < COLS && !std::isnan(ys[j + 1]) ? ys[j + 1] : NAN;
        float d;
        if (std::isnan(by)) {
          d = hypotf(qx - ax, qy - ay);
        } else {
          float dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
          float t = clamp01(((qx - ax) * dx + (qy - ay) * dy) / l2);
          d = hypotf(qx - ax - t * dx, qy - ay - t * dy);
        }
        best = fminf(best, d);
      }
      float cov = half + 0.5f - best;
      if (cov > 0) c.blend(px, py, qy < limitY ? col::PINK : col::BLUE, (int)(255 * clamp01(cov)));
    }
  }
  // Lone readings with no neighbour: a small dot each.
  for (int i = 0; i < n; i++) {
    bool lone = !std::isnan(v.points[i]) && (i == 0 || std::isnan(v.points[i - 1])) && (i == n - 1 || std::isnan(v.points[i + 1]));
    float x = p.x0 + i * step;
    if (lone && x <= revealX) fillCircle(c, x, p.y(v.points[i]), 2, p.y(v.points[i]) < limitY ? col::PINK : col::BLUE);
  }
  // The newest reading: a dot with a slow breathing halo, or the pen of the line while it draws.
  float lx, ly;
  if (reveal < 1) {
    int k = (int)(revealX - X0);
    while (k > 0 && (k >= COLS || std::isnan(ys[k]))) k--;
    if (k < 0 || std::isnan(ys[k])) return;
    lx = X0 + k + 0.5f;
    ly = ys[k];
  } else {
    lx = p.x0 + lastKnown * step;
    ly = p.y(v.points[lastKnown]);
    float b = breathe(now, 2400, f);
    uint16_t hc = ly < limitY ? col::PINK : col::BLUE;
    fillCircle(c, lx, ly, 7 + 5 * b, hc, (int)(70 * (1 - 0.7f * b)));
  }
  uint16_t dc = ly < limitY ? col::PINK : col::BLUE;
  fillCircle(c, lx, ly, 5.5f, col::SURFACE);
  fillCircle(c, lx, ly, 4, dc);
}

// Two short lines under the state chip: the product above its limit, or the peak of the 2 hours.
static void coldLines(const Model &m, char *a, char *b, int size) {
  const View &v = m.view;
  a[0] = b[0] = 0;
  if (m.coldIsWarm()) {
    snprintf(a, size, "%s above its limit", v.room.product.empty() ? "A product" : v.room.product.c_str());
    snprintf(b, size, "for %d of %d min", v.room.minutes, v.room.allowed);
  } else if (v.coldState == "ok") {
    snprintf(a, size, "Every product cold enough.");
    if (v.hasPeak) snprintf(b, size, "Peak %s" DEG " in 2 hours", formatTemp(v.peak).c_str());
  } else if (v.hasTemp) {
    snprintf(a, size, "No storage limit");
    snprintf(b, size, "in the product data.");
  } else {
    snprintf(a, size, "The box sends");
    snprintf(b, size, "no temperature.");
  }
}

static void drawCold(Canvas &c, const Model &m, uint32_t now, Frame &f) {
  const View &v = m.view;
  Verdict vd = roomVerdict(m);
  int x = SIDE;
  int after = drawText(c, CAPTION, x, 66, "Cold room", col::TEXT2);
  if (v.coldWarm > 1) {
    char s[24];
    snprintf(s, sizeof s, "%d too warm", v.coldWarm);
    drawText(c, CAPTION, after + 8, 66, s, col::PINK);
  }
  // The hero: the air now in large numerals, a drawn degree sign and a small C.
  float base = 128;
  if (v.hasTemp) {
    int end = rolling(c, HERO, x - 3, base, m.temp, now, col::TEXT, f);
    float top = base - HERO.cap;
    degree(c, end + 3, top + 1, 5.5f, 2.6f, col::TEXT);
    drawText(c, TITLE, end + 18, top + TITLE.cap + 1, "C", col::TEXT2);
  } else {
    drawText(c, HERO, x - 3, base, "--", col::TEXT3);  // a placeholder; the chip under it says why
  }
  // The state as a chip, and two quiet lines under it.
  chip(c, x, 140, vd.text, vd.colour, vd.colour, vd.known ? 34 : 0);
  if (!vd.known) strokeRound(c, x, 140, chip(c, 0, 0, vd.text, 0, 0, 0, true), 24, 12, 1, col::HAIRLINE);
  char a[64], b[64], fit[70];
  coldLines(m, a, b, sizeof a);
  drawText(c, CAPTION, x, 186, fitText(CAPTION, a, 172, fit, sizeof fit), col::TEXT2);
  drawText(c, CAPTION, x, 204, fitText(CAPTION, b, 172, fit, sizeof fit), col::TEXT2);

  float reveal = easeOut(progress(now, m.coldAt, 520, f));
  chart(c, m, Rect{204, 48, W - SIDE - 204, BODY_B - 48}, reveal, now, f);
}

// ---------------------------------------------------------------------------------------------------------
// Stock

static void slotNote(const Slot &s, char *out, int size) {
  out[0] = 0;
  if (s.units < 0) { snprintf(out, size, "Not counted yet"); return; }
  int n = 0;
  if (s.stale) n += snprintf(out + n, size - n, "Count is old");
  if (!s.emptyAt.empty()) n += snprintf(out + n, size - n, "%sempty %s", n ? ", " : "", s.emptyAt.c_str());
  if (s.onOrder > 0) n += snprintf(out + n, size - n, "%s%d on order", n ? ", " : "", s.onOrder);
  if (n == 0 && s.target > 0) snprintf(out, size, "Reorder below %d", s.reorder >= 0 ? s.reorder : 0);
  if (out[0] >= 'a' && out[0] <= 'z') out[0] = (char)(out[0] - 32);
}

static void drawRow(Canvas &c, const Model &m, int i, uint32_t now, Frame &f) {
  const Slot &s = m.view.slots[i];
  Rect r = row(i);
  // How far this row is selected, from 0 to 1, with the motion of the last change.
  float t = easeOut(progress(now, m.pickAt, 220, f));
  float sel = lerp(m.pickAt && m.pickedBefore == s.slot ? 1.0f : 0.0f, m.picked == s.slot ? 1.0f : 0.0f, t);

  fillRound(c, r.x, r.y, r.w, r.h, 14, mix(col::SURFACE, col::RAISED, (int)(255 * sel)));
  strokeRound(c, r.x, r.y, r.w, r.h, 14, 1, mix(col::HAIRLINE, col::BLUE, (int)(255 * sel)));
  int left = r.x + 14, right = r.x + r.w - 14;

  // At the right: the count against the target, and under it a bar with a mark at the reorder point.
  // When the row is selected, they give way to "Report low", which slides in from the right.
  char target[16] = "";
  if (s.target > 0) snprintf(target, sizeof target, "/ %d", s.target);
  int tw = textWidth(CAPTION, target), uw = s.units >= 0 ? textWidth(TITLE, m.units[i].to.c_str()) : 0;
  int countW = tw + (uw ? uw + 5 : 0);
  const int barW = 84;
  float bx = right - barW, by = r.y + 36;
  int countA = (int)(255 * (1 - clamp01(sel * 1.8f)));  // it leaves before the button arrives
  if (countA > 0) {
    int saved = c.opacity;
    c.opacity = saved * countA / 255;
    drawText(c, CAPTION, right - tw, r.y + 22, target, col::TEXT2);
    if (s.units >= 0) rolling(c, TITLE, right - tw - 5 - uw, r.y + 22, m.units[i], now, col::TEXT, f);
    if (s.target > 0) {
      fillRound(c, bx, by, barW, 4, 2, col::RAISED);
      if (s.units > 0) fillRound(c, bx, by, fmaxf(4, barW * fminf(1, (float)s.units / s.target)), 4, 2, s.low ? col::AMBER : col::BLUE);
      if (s.reorder >= 0) fillRound(c, bx + barW * fminf(1, (float)s.reorder / s.target) - 1, by - 3, 2, 10, 1, col::TEXT2);
    }
    c.opacity = saved;
  }
  Rect b = reportButton(i);
  if (sel > 0) {
    int saved = c.opacity;
    c.opacity = (int)(saved * clamp01((sel - 0.3f) / 0.7f));
    float ox = c.ox;
    c.ox += (1 - sel) * 12;
    bool down = m.pressed == BTN_REPORT;
    float in = down ? 2 : 0;
    fillRound(c, b.x + in, b.y + in, b.w - 2 * in, b.h - 2 * in, (b.h - 2 * in) / 2, down ? col::BLUE_DOWN : col::BLUE_FILL);
    drawTextCentre(c, LABEL, b.x + b.w / 2.0f, b.y + 21, "Report low", col::WHITE);
    c.ox = ox;
    c.opacity = saved;
  }
  // At the left: the name, and a note under it.
  int nameRight = (int)lerp((float)(right - countW - 10), (float)(b.x - 10), sel);
  char fit[64];
  drawText(c, LABEL, left, r.y + 22, fitText(LABEL, s.name.c_str(), nameRight - left, fit, sizeof fit), col::TEXT);
  char note[64];
  slotNote(s, note, sizeof note);
  int x = left, noteRight = (int)lerp(bx - 12, (float)(b.x - 10), sel);
  if (s.low) x = drawText(c, CAPTION, x, r.y + 41, "Low", col::AMBER) + 8;
  drawText(c, CAPTION, x, r.y + 41, fitText(CAPTION, note, noteRight - x, fit, sizeof fit), col::TEXT2);
}

static void drawThumb(Canvas &c, const Model &m, uint32_t now, Frame &f) {
  const Rect &t = THUMB;
  const Camera &cam = m.thumb;
  bool live = cam.live(now);
  float k = easeOut(progress(now, cam.arrivedAt, 220, f));
  // The placeholder: under a picture that fades in, or alone when the camera is offline.
  if (!live || k < 1) {
    fillRound(c, t.x, t.y, t.w, t.h, 12, col::SURFACE);
    strokeRound(c, t.x, t.y, t.w, t.h, 12, 1, col::HAIRLINE);
    if (!live || !cam.prev.px) {
      cameraIcon(c, t.x + t.w / 2.0f, t.y + 36, 26, col::TEXT3);
      drawTextCentre(c, CAPTION, t.x + t.w / 2.0f, t.y + 72, "Camera offline", col::TEXT2);
    }
  }
  if (live) {
    if (k < 1 && cam.prev.px) picture(c, cam.prev, t.x, t.y, 12, 255);
    picture(c, cam.cur, t.x, t.y, 12, (int)(255 * k));
    // The "Live" chip, with a pink dot that breathes.
    float b = breathe(now, 2000, f);
    int w = 10 + 6 + 6 + textWidth(CAPTION, "Live") + 9;
    fillRound(c, t.x + 8, t.y + 8, w, 20, 10, col::BLACK, 120);
    fillCircle(c, t.x + 18, t.y + 18, 3, col::PINK, (int)(255 * (0.55f + 0.45f * b)));
    drawText(c, CAPTION, t.x + 26, t.y + 22.5f, "Live", col::WHITE);
    // A quiet sign that the picture opens large.
    float ex = t.x + t.w - 20, ey = t.y + t.h - 20;
    fillCircle(c, ex, ey, 11, col::BLACK, 110);
    line(c, ex - 4.5f, ey + 4.5f, ex + 4.5f, ey - 4.5f, 1.4f, col::WHITE, 220);
    line(c, ex + 1, ey - 4.5f, ex + 4.5f, ey - 4.5f, 1.4f, col::WHITE, 220);
    line(c, ex + 4.5f, ey - 4.5f, ex + 4.5f, ey - 1, 1.4f, col::WHITE, 220);
    line(c, ex - 4.5f, ey + 1, ex - 4.5f, ey + 4.5f, 1.4f, col::WHITE, 220);
    line(c, ex - 4.5f, ey + 4.5f, ex - 1, ey + 4.5f, 1.4f, col::WHITE, 220);
  }
  drawText(c, LABEL, t.x + 2, t.y + t.h + 24, "Shelf camera", col::TEXT);
  char s[32];
  if (live) ageText(cam.age(now), s, sizeof s, "");
  else if (cam.ageS >= 0 && cam.cur.px) ageText(cam.age(now), s, sizeof s, "Last picture ");
  else snprintf(s, sizeof s, "No picture yet");
  drawText(c, CAPTION, t.x + 2, t.y + t.h + 44, s, col::TEXT2);
  if (cam.ageS >= 0) f.looping = true;  // the age counts up each second
}

static void drawStock(Canvas &c, const Model &m, uint32_t now, Frame &f) {
  const View &v = m.view;
  if (!v.slotCount) {
    float cx = ROW_X + ROW_W / 2.0f;
    drawTextCentre(c, LABEL, cx, 124, v.reached ? "No shelf slots" : "No stock data yet", col::TEXT);
    drawTextCentre(c, CAPTION, cx, 146, v.reached ? "The rule set names none." : "The backend has not answered.", col::TEXT2);
  }
  for (int i = 0; i < v.slotCount && i < SLOTS; i++) drawRow(c, m, i, now, f);
  drawThumb(c, m, now, f);
}

// ---------------------------------------------------------------------------------------------------------
// The sheet: a question over the view, with the main action and "Back".

static void drawSheet(Canvas &c, const Model &m, uint32_t now, Frame &f) {
  // Opening, the sheet becomes opaque in 110 ms and rises 12 pixels in 260 ms, so the view under it never
  // shows through for long. Closing, it fades in 150 ms.
  float t = m.sheetAt ? (float)(int32_t)(now - m.sheetAt) : 1e9f;
  float shown, rise;
  if (m.sheetOpen) {
    if (t < 260) f.animating = true;
    shown = easeOut(clamp01(t / 110));
    rise = easeOut(clamp01(t / 260));
  } else {
    if (t < 150) f.animating = true;
    shown = 1 - easeOut(clamp01(t / 150));
    rise = 1;
  }
  if (shown <= 0) return;
  // The view under the sheet dims, with soft top and bottom edges so no hard line meets the bar or the tabs.
  for (int y = BODY_Y - 6; y < BODY_B + 6; y++) {
    float edge = fminf(clamp01((y - (BODY_Y - 6)) / 12.0f), clamp01((BODY_B + 6 - y) / 12.0f));
    dim(c, 0, y, W, 1, (int)(110 * shown * edge));
  }

  const View &v = m.view;
  char title[64], line1[96], line2[96], fit[100], yes[24];
  bool report = m.sheetAsk == ASK_NONE;
  uint16_t action = col::BLUE_FILL, down = col::BLUE_DOWN;
  if (report) {
    int i = m.slotIndex(m.sheetSlot);
    const Slot *s = i >= 0 ? &v.slots[i] : nullptr;
    const char *name = s ? s->name.c_str() : "this slot";
    if (m.waiting) {
      snprintf(title, sizeof title, "Reporting %s low", name);
      snprintf(line1, sizeof line1, "The agent decides within its rules.");
      snprintf(line2, sizeof line2, "This can take up to 15 seconds.");
    } else {
      snprintf(title, sizeof title, "Report %s low?", name);
      if (s && s->units >= 0 && s->target > 0) snprintf(line1, sizeof line1, "%d of %d counted on the shelf.", s->units, s->target);
      else snprintf(line1, sizeof line1, "The shelf count is not known.");
      snprintf(line2, sizeof line2, "The agent may order more, within its rules.");
    }
    snprintf(yes, sizeof yes, "Yes, report");
  } else {
    bool accept = m.sheetAsk == ASK_ACCEPT;
    snprintf(title, sizeof title, accept ? "Accept this delivery?" : "Reject this delivery?");
    snprintf(line1, sizeof line1, "Order %s, %s.", v.orderId.c_str(), v.total.c_str());
    snprintf(line2, sizeof line2, "The log keeps the decision with the temperature.");
    snprintf(yes, sizeof yes, accept ? "Yes, accept" : "Yes, reject");
    if (!accept) { action = col::PINK_FILL; down = col::PINK_DOWN; }
  }

  int saved = c.opacity;
  float oy = c.oy;
  c.opacity = (int)(saved * shown);
  c.oy += (1 - rise) * 12;
  const Rect &s = SHEET;
  shadow(c, s.x, s.y + 6, s.w, s.h, 20, 18, 120);
  fillRound(c, s.x, s.y, s.w, s.h, 20, col::RAISED);
  strokeRound(c, s.x, s.y, s.w, s.h, 20, 1, col::EDGE);
  int left = s.x + 16, width = s.w - 32;
  drawText(c, TITLE, left, s.y + 38, fitText(TITLE, title, width, fit, sizeof fit), col::TEXT);
  drawText(c, BODY, left, s.y + 64, fitText(BODY, line1, width, fit, sizeof fit), col::TEXT2);
  drawText(c, BODY, left, s.y + 84, fitText(BODY, line2, width, fit, sizeof fit), col::TEXT2);
  if (m.waiting) {
    // Three quiet dots where the buttons were: the screen waits for the backend.
    for (int i = 0; i < 3; i++) fillCircle(c, s.x + s.w / 2.0f - 16 + 16 * i, YES.y + YES.h / 2.0f, 3.5f, i == 0 ? col::BLUE : col::TEXT3);
  } else {
    button(c, YES, yes, FILLED, action, down, m.pressed == BTN_YES, report || m.sheetAsk == ASK_ACCEPT ? 'v' : 'x');
    button(c, BACK, "Back", OUTLINED, col::TEXT, col::TEXT, m.pressed == BTN_BACK);
  }
  c.opacity = saved;
  c.oy = oy;
}

// ---------------------------------------------------------------------------------------------------------
// The toast after a decision: a circle that grows in, a sign that draws itself, a title and one line.

static void drawToast(Canvas &c, const Model &m, uint32_t now, Frame &f) {
  float t = (float)(int32_t)(now - m.toastAt);
  f.animating = f.animating || t < 460 || t > TOAST_MS - 300;
  float out = 1 - easeOut(clamp01((t - (TOAST_MS - 260)) / 240.0f));
  int saved = c.opacity;
  c.opacity = (int)(saved * out);
  uint16_t ink = m.toastGood ? col::BLUE : col::PINK;
  float cx = W / 2.0f, cy = 100;
  float grow = easeOut(clamp01(t / 260)), r = 30 * (0.72f + 0.28f * grow);
  fillCircle(c, cx, cy, r, ink, (int)(46 * grow));
  ring(c, cx, cy, r, 2, ink, (int)(255 * grow));
  float draw = easeOut(clamp01((t - 140) / 320));
  if (m.toastGood) checkMark(c, cx + 1, cy, 24, 3, ink, draw);
  else crossMark(c, cx, cy, 16, 3, ink, draw);
  float k = easeOut(clamp01((t - 120) / 280));
  float oy = c.oy;
  c.oy += (1 - k) * 8;
  drawTextCentre(c, TITLE, cx, 164, m.toast.c_str(), col::TEXT, (int)(255 * k));
  char fit[100];
  drawTextCentre(c, BODY, cx, 188, fitText(BODY, m.toastSub.c_str(), W - 2 * SIDE, fit, sizeof fit), col::TEXT2, (int)(255 * k));
  c.oy = oy;
  c.opacity = saved;
}

// ---------------------------------------------------------------------------------------------------------
// The large camera view.

// The picture layer of the large view: the picture, or the calm offline state.
static void drawPictureLayer(Canvas &c, const Model &m, const Camera &cam, bool live, uint32_t now, Frame &f) {
  fillRect(c, 0, 0, W, H, col::BLACK);
  if (live) {
    const Picture &p = cam.cur;
    float k = easeOut(progress(now, cam.arrivedAt, 220, f));
    if (&cam == &m.full) {
      if (k < 1 && cam.prev.px) picture(c, cam.prev, (W - p.w) / 2.0f, (H - p.h) / 2.0f, 0, 255);
      picture(c, p, (W - p.w) / 2.0f, (H - p.h) / 2.0f, 0, (int)(255 * k));
    } else {
      // The thumbnail scaled up, so the slide shows a picture at once. A nearest-pixel scale, only for a moment.
      int x0 = c.cx0 > 0 ? c.cx0 : 0, y0 = c.cy0, oy = (int)lroundf(c.oy);
      for (int y = 0; y < H; y++) {
        int py = y + oy;
        if (py < y0 || py >= c.cy1) continue;
        const uint16_t *src = p.px + (y * p.h / H) * p.w;
        for (int x = x0; x < W && x < c.cx1; x++) c.px[py * c.w + x] = src[x * p.w / W];
      }
    }
  } else {
    fillRect(c, 0, 0, W, H, col::BG);
    cameraIcon(c, W / 2.0f, 112, 44, col::TEXT3);
    drawTextCentre(c, TITLE, W / 2.0f, 172, "Camera offline", col::TEXT);
    char s[40];
    if (cam.ageS >= 0 && cam.cur.px) ageText(cam.age(now), s, sizeof s, "Last picture ");
    else snprintf(s, sizeof s, "No picture from the shelf yet");
    drawTextCentre(c, BODY, W / 2.0f, 196, s, col::TEXT2);
  }
}

// The colour of a distance: bright accent blue near, a deep navy close to the page far away.
static uint16_t depthColour(float far) {
  // A straight scale, so 30, 45 and 60 cm on a shelf still look different.
  static const uint16_t NEAR = rgb(0x6A95FF), FAR = rgb(0x121B30);
  return mix(NEAR, FAR, (int)(255 * clamp01(far)));
}

// The depth layer of the large view: the 8 by 8 grid, the nearest distance and its age, and the legend.
static void drawDepthLayer(Canvas &c, const Model &m, uint32_t now, Frame &f) {
  const Depth &d = m.depth;
  bool live = d.live(now);
  fillRect(c, 0, 0, W, H, col::BG);
  const float cell = 23, gap = 3, size = 8 * cell + 7 * gap;
  const float gx = 152, gy = 58;  // the left column keeps 132 pixels for its words
  if (d.arrivedAt && (float)(int32_t)(now - d.arrivedAt) < DEPTH_EASE_MS) f.animating = true;
  // The cells carry no numbers: 64 numbers made the grid read like a table. The nearest cell has a thin
  // white ring instead, and the number of the nearest distance stands at the left.
  float nearest = INFINITY;
  int nearestCell = -1;
  for (int i = 0; i < DEPTH_CELLS; i++)
    if (live && !std::isnan(d.cm[i]) && d.cm[i] < nearest) { nearest = d.cm[i]; nearestCell = i; }
  for (int i = 0; i < DEPTH_CELLS; i++) {
    float x = gx + (i % 8) * (cell + gap), y = gy + (i / 8) * (cell + gap);
    float far, on;
    d.shown(i, now, far, on);
    if (!live) on = 0;  // the skeleton: outlines only
    if (on < 1) strokeRound(c, x, y, cell, cell, 6, 1, col::HAIRLINE, (int)(255 * (1 - on)));
    if (on > 0) fillRound(c, x, y, cell, cell, 6, depthColour(far), (int)(255 * on));
    if (i == nearestCell) strokeRound(c, x - 2, y - 2, cell + 4, cell + 4, 8, 1.5f, col::WHITE, 220);
  }

  // At the left: the nearest distance and the age, or that the sensor is offline.
  char s[40];
  if (live) {
    drawText(c, CAPTION, SIDE, 84, "Nearest", col::TEXT2);
    if (std::isfinite(nearest)) {
      snprintf(s, sizeof s, "%d", (int)lroundf(nearest));
      int x = drawText(c, NUMBER, SIDE, 116, s, col::TEXT);
      drawText(c, LABEL, x + 4, 116, "cm", col::TEXT2);
    } else {
      drawText(c, NUMBER, SIDE, 116, "--", col::TEXT3);
    }
    int a = d.age(now);
    if (a <= 0) snprintf(s, sizeof s, "now");
    else ageText(a, s, sizeof s, "");
    drawText(c, CAPTION, SIDE, 138, s, col::TEXT2);
    f.looping = true;  // the age counts up
  } else {
    drawText(c, LABEL, SIDE, 104, "Distance sensor", col::TEXT);
    drawText(c, LABEL, SIDE, 124, "offline", col::TEXT);
    if (d.ageS >= 0) {
      drawText(c, CAPTION, SIDE, 146, "Last reading", col::TEXT2);
      ageText(d.age(now), s, sizeof s, "");
      drawText(c, CAPTION, SIDE, 164, s, col::TEXT2);
    } else {
      drawText(c, CAPTION, SIDE, 146, "No reading yet", col::TEXT2);
    }
  }

  // At the right: the legend, near at the top and far at the bottom, with the ends of the scale.
  float lx = gx + size + 24, top = gy + 4, bottom = gy + size - 4;
  for (float y = top + 4; y < bottom - 4; y++) fillRect(c, lx, y, 8, 1, depthColour((y - top - 4) / (bottom - top - 8)), live ? 255 : 90);
  fillCircle(c, lx + 4, top + 4, 4, depthColour(0), live ? 255 : 90);
  fillCircle(c, lx + 4, bottom - 4, 4, depthColour(1), live ? 255 : 90);
  drawText(c, CAPTION, lx + 18, top + 10, "Near", col::TEXT);
  drawText(c, CAPTION, lx + 18, top + 28, "0 cm", col::TEXT2);
  drawText(c, CAPTION, lx + 18, bottom - 18, "Far", col::TEXT);
  snprintf(s, sizeof s, "%d cm", (int)DEPTH_FAR_CM);
  drawText(c, CAPTION, lx + 18, bottom, s, col::TEXT2);
}

// "Camera | Depth": a small segmented control on the shade of the strip, like the tabs.
static void drawModes(Canvas &c, float depthAmt) {
  const Rect &r = CAMERA_MODES;
  fillRound(c, r.x, r.y, r.w, r.h, r.h / 2.0f, col::BLACK, 130);
  strokeRound(c, r.x, r.y, r.w, r.h, r.h / 2.0f, 1, col::WHITE, 40);
  float seg = (r.w - 6) / 2.0f, hx = r.x + 3 + seg * depthAmt;
  fillRound(c, hx, r.y + 3, seg, r.h - 6, (r.h - 6) / 2.0f, col::WHITE, 44);
  static const char *const LABELS[2] = {"Camera", "Depth"};
  for (int i = 0; i < 2; i++) {
    float on = i ? depthAmt : 1 - depthAmt;
    drawTextCentre(c, CAPTION, r.x + 3 + seg * i + seg / 2, r.y + r.h / 2.0f + 4.5f, LABELS[i], col::WHITE, (int)(150 + 105 * on));
  }
}

static void drawCameraFull(Canvas &c, const Model &m, uint32_t now, Frame &f) {
  // The small picture stands in until a fresh large one arrives.
  const Camera &cam = m.full.live(now) || !m.thumb.live(now) ? m.full : m.thumb;
  bool live = cam.live(now);
  // The two modes cross-fade: the depth layer fades in over the picture, and out again.
  float k = easeOut(progress(now, m.modeAt, 200, f));
  float depthAmt = m.depthMode ? k : 1 - k;
  if (depthAmt < 1) drawPictureLayer(c, m, cam, live, now, f);
  if (depthAmt > 0) {
    int saved = c.opacity;
    c.opacity = (int)(saved * depthAmt);
    drawDepthLayer(c, m, now, f);
    c.opacity = saved;
  }
  // The strip over the top: a soft shade, the title, the age of the picture, the modes and the close control.
  vGradient(c, 0, 0, W, 56, col::BLACK, (int)(170 * (1 - depthAmt)), col::BLACK, 0);
  int x = drawText(c, LABEL, SIDE, 28, depthAmt < 0.5f ? "Shelf camera" : "Shelf depth", col::WHITE);
  if (live && depthAmt < 1) {
    char s[24];
    ageText(cam.age(now), s, sizeof s, "");
    float b = breathe(now, 2000, f);
    int a = (int)(255 * (1 - depthAmt));
    fillCircle(c, x + 14, 23, 3, col::PINK, (int)(a * (0.55f + 0.45f * b)));
    drawText(c, CAPTION, x + 23, 27.5f, s, col::WHITE, a * 200 / 255);
  }
  drawModes(c, depthAmt);
  fillCircle(c, W - SIDE - 14, 23, 15, col::BLACK, 130);
  ring(c, W - SIDE - 14, 23, 15, 1, col::WHITE, 60);
  crossMark(c, W - SIDE - 14, 23, 9, 1.6f, col::WHITE);
  if (cam.ageS >= 0) f.looping = true;
}

// ---------------------------------------------------------------------------------------------------------

static void drawTabBody(Canvas &c, const Model &m, Tab tab, uint32_t now, Frame &f) {
  if (tab == TAB_DOOR) {
    if (m.view.hasDelivery) {
      drawDoorDelivery(c, m, f);
      drawDoorButtons(c, m);
    } else {
      drawDoorEmpty(c, m, f);
    }
  } else if (tab == TAB_COLD) {
    drawCold(c, m, now, f);
  } else {
    drawStock(c, m, now, f);
  }
}

static void drawBody(Canvas &c, const Model &m, uint32_t now, Frame &f) {
  c.clip(0, BODY_Y - 4, W, BODY_B + 4);
  if (m.toastOn) {
    drawToast(c, m, now, f);
    c.noClip();
    return;
  }
  // A tab switch fades the old view out with a small shift, then the new one in from the other side.
  float e = m.tabAt ? (float)(int32_t)(now - m.tabAt) : 1e9f;
  float dir = m.tab > m.tabFrom ? 1 : -1;
  float after = easeOut(progress(now, m.bodyAt, 240, f));
  if (e < 110 && m.tabFrom != m.tab) {
    f.animating = true;
    float k = e / 110;
    c.opacity = (int)(255 * (1 - k) * after);
    c.ox = -dir * 8 * k;
    drawTabBody(c, m, m.tabFrom, now, f);
  } else {
    float k = easeOut(clamp01((e - 110) / 210));
    if (k < 1) f.animating = true;
    c.opacity = (int)(255 * k * after);
    c.ox = dir * 10 * (1 - k);
    drawTabBody(c, m, m.tab, now, f);
  }
  c.opacity = 255;
  c.ox = 0;
  drawSheet(c, m, now, f);
  c.noClip();
}

Frame render(Canvas &c, const Model &m, uint32_t now) {
  Frame f;
  c.noClip();
  c.opacity = 255;
  c.ox = c.oy = 0;
  // The large camera view slides up over everything, and down again when it closes.
  float cam = 0;
  if (m.cameraAt) {
    float t = (float)(int32_t)(now - m.cameraAt);
    float k = clamp01(t / (m.cameraOpen ? 280 : 240));
    if (k < 1) f.animating = true;
    cam = m.cameraOpen ? easeOut(k) : 1 - easeOut(k);
  } else if (m.cameraOpen) {
    cam = 1;
  }
  if (cam >= 1) {
    drawCameraFull(c, m, now, f);
    return f;
  }
  clear(c, col::BG);
  drawBar(c, m, now, f);
  drawNav(c, m, now, f);
  drawBody(c, m, now, f);
  if (cam > 0) {
    dim(c, 0, 0, W, H, (int)(160 * cam));
    c.oy = (1 - cam) * H;
    c.clip(0, (int)c.oy, W, H);
    drawCameraFull(c, m, now, f);
    c.oy = 0;
    c.noClip();
  }
  return f;
}

void renderSplash(Canvas &c, const char *line) {
  c.noClip();
  c.opacity = 255;
  c.ox = c.oy = 0;
  clear(c, col::BG);
  drawAlpha(c, kWordmarkLarge, (W - kWordmarkLarge.w) / 2.0f, 92, col::TEXT);
  drawTextCentre(c, BODY, W / 2.0f, 186, line, col::TEXT2);
}

void renderCalibration(Canvas &c, int step, int x, int y) {
  c.noClip();
  c.opacity = 255;
  c.ox = c.oy = 0;
  clear(c, col::BG);
  drawTextCentre(c, TITLE, W / 2.0f, 128, "Tap the centre of the cross", col::TEXT);
  char s[48];
  snprintf(s, sizeof s, "Target %d of 4. Use a fingertip.", step + 1);
  drawTextCentre(c, BODY, W / 2.0f, 154, s, col::TEXT2);
  fillCircle(c, x + 0.5f, y + 0.5f, 22, col::BLUE, 30);
  ring(c, x + 0.5f, y + 0.5f, 12, 2, col::BLUE);
  line(c, x + 0.5f - 24, y + 0.5f, x + 0.5f + 24, y + 0.5f, 1.5f, col::BLUE);
  line(c, x + 0.5f, y + 0.5f - 24, x + 0.5f, y + 0.5f + 24, 1.5f, col::BLUE);
}

}  // namespace ui

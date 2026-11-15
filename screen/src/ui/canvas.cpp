#include "canvas.h"

#include <cmath>
#include <cstring>

namespace ui {

uint16_t mix(uint16_t a, uint16_t b, int t) {
  // The three channels move in one 32-bit word, spread so they cannot carry into each other.
  uint32_t k = (uint32_t)(t + 4) >> 3;  // 0 to 32
  uint32_t x = (a | ((uint32_t)a << 16)) & 0x07E0F81F;
  uint32_t y = (b | ((uint32_t)b << 16)) & 0x07E0F81F;
  uint32_t r = (x + (((y - x) * k) >> 5)) & 0x07E0F81F;
  return (uint16_t)(r | (r >> 16));
}

void Canvas::clip(int x0, int y0, int x1, int y1) {
  cx0 = x0 < 0 ? 0 : x0;
  cy0 = y0 < 0 ? 0 : y0;
  cx1 = x1 > w ? w : x1;
  cy1 = y1 > h ? h : y1;
}

void clear(Canvas &c, uint16_t colour) {
  for (int i = 0, n = c.w * c.h; i < n; i++) c.px[i] = colour;
}

// One span of a row at full coverage.
static void span(Canvas &c, int y, int x0, int x1, uint16_t colour, int alpha) {
  if (y < c.cy0 || y >= c.cy1) return;
  if (x0 < c.cx0) x0 = c.cx0;
  if (x1 > c.cx1) x1 = c.cx1;
  int a = (alpha * c.opacity + 127) >> 8;
  if (a <= 0 || x0 >= x1) return;
  uint16_t *p = c.px + y * c.w;
  if (a >= 255) {
    for (int x = x0; x < x1; x++) p[x] = colour;
  } else {
    for (int x = x0; x < x1; x++) p[x] = mix(p[x], colour, a);
  }
}

void fillRect(Canvas &c, float x, float y, float w, float h, uint16_t colour, int alpha) {
  fillRound(c, x, y, w, h, 0, colour, alpha);
}

// The signed distance from a point to a rounded rectangle with centre (cx, cy), half size minus radius (hx, hy).
static inline float roundDist(float px, float py, float cx, float cy, float hx, float hy, float r) {
  float qx = fabsf(px - cx) - hx, qy = fabsf(py - cy) - hy;
  float ox = qx > 0 ? qx : 0, oy = qy > 0 ? qy : 0;
  float inside = qx > qy ? qx : qy;
  return sqrtf(ox * ox + oy * oy) + (inside < 0 ? inside : 0) - r;
}

// A rounded rectangle, filled (t <= 0) or as a band of width t inside its edge. Pixels that lie fully inside
// the shape (or fully in the hole of a band) take a fast path; only the pixels near an edge compute a distance.
static void roundShape(Canvas &c, float x, float y, float w, float h, float r, float t, uint16_t colour, int alpha) {
  if (w <= 0 || h <= 0) return;
  x += c.ox;
  y += c.oy;
  float m = (w < h ? w : h) / 2;
  if (r > m) r = m;
  if (r < 0) r = 0;
  float cx = x + w / 2, cy = y + h / 2, hx = w / 2 - r, hy = h / 2 - r;
  int y0 = (int)floorf(y), y1 = (int)ceilf(y + h), x0 = (int)floorf(x), x1 = (int)ceilf(x + w);
  if (y0 < c.cy0) y0 = c.cy0;
  if (y1 > c.cy1) y1 = c.cy1;
  if (x0 < c.cx0) x0 = c.cx0;
  if (x1 > c.cx1) x1 = c.cx1;
  bool band = t > 0;
  for (int py = y0; py < y1; py++) {
    float yc = py + 0.5f;
    float vd = h / 2 - fabsf(yc - cy);       // the distance to the top or bottom edge
    bool corner = fabsf(yc - cy) > hy;       // a row that crosses a rounded corner
    // The run of pixels that the fast path covers: fully inside, or fully in the hole of a band.
    float inset = band ? t : 0;
    int fl = 1, fr = 0;
    if (vd >= inset + 0.5f) {
      if (corner) { fl = (int)ceilf(cx - hx - 0.5f); fr = (int)floorf(cx + hx - 0.5f); }
      else { fl = (int)ceilf(x + inset); fr = (int)floorf(x + w - inset) - 1; }
      if (band && corner) { fl = 1; fr = 0; }  // a corner row of a band: compute every pixel
    }
    for (int px = x0; px < x1; px++) {
      if (px >= fl && px <= fr) {
        if (!band) span(c, py, px, fr + 1, colour, alpha);
        px = fr;
        continue;
      }
      float d = roundDist(px + 0.5f, yc, cx, cy, hx, hy, r);
      float cov = 0.5f - d;
      cov = cov < 0 ? 0 : cov > 1 ? 1 : cov;
      if (band) {
        float in = 0.5f - (d + t);
        cov -= in < 0 ? 0 : in > 1 ? 1 : in;
      }
      if (cov > 0.002f) c.blend(px, py, colour, (int)(cov * alpha + 0.5f));
    }
  }
}

void fillRound(Canvas &c, float x, float y, float w, float h, float r, uint16_t colour, int alpha) {
  roundShape(c, x, y, w, h, r, 0, colour, alpha);
}
void strokeRound(Canvas &c, float x, float y, float w, float h, float r, float t, uint16_t colour, int alpha) {
  roundShape(c, x, y, w, h, r, t, colour, alpha);
}
void fillCircle(Canvas &c, float cx, float cy, float r, uint16_t colour, int alpha) {
  roundShape(c, cx - r, cy - r, 2 * r, 2 * r, r, 0, colour, alpha);
}
void ring(Canvas &c, float cx, float cy, float r, float t, uint16_t colour, int alpha) {
  roundShape(c, cx - r, cy - r, 2 * r, 2 * r, r, t, colour, alpha);
}

void line(Canvas &c, float x0, float y0, float x1, float y1, float w, uint16_t colour, int alpha) {
  x0 += c.ox; x1 += c.ox; y0 += c.oy; y1 += c.oy;
  float r = w / 2, dx = x1 - x0, dy = y1 - y0, len2 = dx * dx + dy * dy;
  int bx0 = (int)floorf(fminf(x0, x1) - r - 1), bx1 = (int)ceilf(fmaxf(x0, x1) + r + 1);
  int by0 = (int)floorf(fminf(y0, y1) - r - 1), by1 = (int)ceilf(fmaxf(y0, y1) + r + 1);
  if (bx0 < c.cx0) bx0 = c.cx0;
  if (by0 < c.cy0) by0 = c.cy0;
  if (bx1 > c.cx1) bx1 = c.cx1;
  if (by1 > c.cy1) by1 = c.cy1;
  for (int py = by0; py < by1; py++) {
    for (int px = bx0; px < bx1; px++) {
      float qx = px + 0.5f - x0, qy = py + 0.5f - y0;
      float k = len2 > 0 ? (qx * dx + qy * dy) / len2 : 0;
      k = k < 0 ? 0 : k > 1 ? 1 : k;
      float ex = qx - k * dx, ey = qy - k * dy;
      float cov = r + 0.5f - sqrtf(ex * ex + ey * ey);
      if (cov <= 0) continue;
      c.blend(px, py, colour, (int)((cov > 1 ? 1 : cov) * alpha + 0.5f));
    }
  }
}

void shadow(Canvas &c, float x, float y, float w, float h, float r, float blur, int alpha) {
  x += c.ox;
  y += c.oy;
  float cx = x + w / 2, cy = y + h / 2, hx = w / 2 - r, hy = h / 2 - r;
  int x0 = (int)floorf(x - blur), x1 = (int)ceilf(x + w + blur), y0 = (int)floorf(y - blur), y1 = (int)ceilf(y + h + blur);
  if (x0 < c.cx0) x0 = c.cx0;
  if (y0 < c.cy0) y0 = c.cy0;
  if (x1 > c.cx1) x1 = c.cx1;
  if (y1 > c.cy1) y1 = c.cy1;
  const uint16_t black = 0;
  for (int py = y0; py < y1; py++) {
    bool mid = py + 0.5f > y + 1 && py + 0.5f < y + h - 1;
    for (int px = x0; px < x1; px++) {
      if (mid && px > x + 1 && px < x + w - 2) { px = (int)(x + w - 2); continue; }  // under the shape: hidden
      float d = roundDist(px + 0.5f, py + 0.5f, cx, cy, hx, hy, r);
      if (d <= 0 || d >= blur) continue;
      float k = 1 - d / blur;
      c.blend(px, py, black, (int)(k * k * alpha));
    }
  }
}

// A 4 by 4 Bayer matrix: the order in which pixels round up, to hide the steps of RGB565.
static const uint8_t BAYER[4][4] = {{0, 8, 2, 10}, {12, 4, 14, 6}, {3, 11, 1, 9}, {15, 7, 13, 5}};

static inline uint16_t dither(int r, int g, int b, int x, int y) {
  int k = BAYER[y & 3][x & 3];
  // Each channel scaled to its bit depth, with the threshold of this pixel added before the cut.
  int r5 = (r * 31 * 32 + (2 * k + 1) * 255) / (255 * 32);
  int g6 = (g * 63 * 32 + (2 * k + 1) * 255) / (255 * 32);
  int b5 = (b * 31 * 32 + (2 * k + 1) * 255) / (255 * 32);
  if (r5 > 31) r5 = 31;
  if (g6 > 63) g6 = 63;
  if (b5 > 31) b5 = 31;
  return (uint16_t)(r5 << 11 | g6 << 5 | b5);
}

void vGradient(Canvas &c, int x, int y, int w, int h, uint16_t a, int alphaA, uint16_t b, int alphaB) {
  x += (int)lroundf(c.ox);
  y += (int)lroundf(c.oy);
  int ar, ag, ab, br, bg, bb;
  unpack(a, ar, ag, ab);
  unpack(b, br, bg, bb);
  int x0 = x < c.cx0 ? c.cx0 : x, x1 = x + w > c.cx1 ? c.cx1 : x + w;
  for (int py = y < c.cy0 ? c.cy0 : y; py < y + h && py < c.cy1; py++) {
    float t = h > 1 ? (float)(py - y) / (h - 1) : 0;
    int r = (int)lerp(ar, br, t), g = (int)lerp(ag, bg, t), bl = (int)lerp(ab, bb, t);
    int al = (int)(lerp(alphaA, alphaB, t) * c.opacity / 255);
    if (al <= 0) continue;
    uint16_t *row = c.px + py * c.w;
    for (int px = x0; px < x1; px++) {
      int dr, dg, db;
      unpack(row[px], dr, dg, db);
      row[px] = dither(dr + ((r - dr) * al) / 255, dg + ((g - dg) * al) / 255, db + ((bl - db) * al) / 255, px, py);
    }
  }
}

void dim(Canvas &c, int x, int y, int w, int h, int amount) {
  const uint16_t night = rgb(0x03050B);
  for (int py = y < c.cy0 ? c.cy0 : y; py < y + h && py < c.cy1; py++)
    for (int px = x < c.cx0 ? c.cx0 : x; px < x + w && px < c.cx1; px++) c.px[py * c.w + px] = mix(c.px[py * c.w + px], night, amount);
}

static inline const Glyph *glyphOf(const Font &f, char ch) {
  uint8_t u = (uint8_t)ch;
  if (u < f.first || u > f.last) return nullptr;
  return &f.glyphs[u - f.first];
}

int textWidth(const Font &f, const char *s) {
  int w = 0;
  for (; *s; s++)
    if (const Glyph *g = glyphOf(f, *s)) w += g->adv;
  return w;
}

int drawText(Canvas &c, const Font &f, float x, float y, const char *s, uint16_t colour, int alpha) {
  int pen = (int)lroundf(x + c.ox), base = (int)lroundf(y + c.oy);
  int start = pen;
  for (; *s; s++) {
    const Glyph *g = glyphOf(f, *s);
    if (!g) continue;
    if (g->w && g->h) {
      int gx = pen + g->dx, gy = base + g->dy;
      // Whole glyphs outside the clip are skipped before any pixel is read.
      if (gx < c.cx1 && gx + g->w > c.cx0 && gy < c.cy1 && gy + g->h > c.cy0) {
        const uint8_t *bits = f.bits + g->offset;
        int n = g->w * g->h;
        for (int i = 0; i < n; i++) {
          int v = (bits[i >> 1] >> ((i & 1) ? 0 : 4)) & 15;
          if (!v) continue;
          c.blend(gx + i % g->w, gy + i / g->w, colour, v * 17 * alpha / 255);
        }
      }
    }
    pen += g->adv;
  }
  return (int)x + (pen - start);
}

int drawTextRight(Canvas &c, const Font &f, float right, float y, const char *s, uint16_t colour, int alpha) {
  return drawText(c, f, right - textWidth(f, s), y, s, colour, alpha);
}

int drawTextCentre(Canvas &c, const Font &f, float centre, float y, const char *s, uint16_t colour, int alpha) {
  return drawText(c, f, centre - textWidth(f, s) / 2, y, s, colour, alpha);
}

const char *fitText(const Font &f, const char *s, int width, char *out, int size) {
  int n = (int)strlen(s);
  if (textWidth(f, s) <= width || size < 4) return s;
  if (n > size - 2) n = size - 2;
  while (n > 0) {
    memcpy(out, s, n);
    while (n > 0 && out[n - 1] == ' ') n--;  // no space before the ellipsis
    out[n] = 0x7F;                           // the ellipsis glyph of the atlases
    out[n + 1] = 0;
    if (textWidth(f, out) <= width) return out;
    n--;
  }
  out[0] = 0x7F;
  out[1] = 0;
  return out;
}

void drawAlpha(Canvas &c, const Alpha4 &a, float x, float y, uint16_t colour, int alpha) {
  int ox = (int)lroundf(x + c.ox), oy = (int)lroundf(y + c.oy), n = a.w * a.h;
  for (int i = 0; i < n; i++) {
    int v = (a.bits[i >> 1] >> ((i & 1) ? 0 : 4)) & 15;
    if (v) c.blend(ox + i % a.w, oy + i / a.w, colour, v * 17 * alpha / 255);
  }
}

}  // namespace ui

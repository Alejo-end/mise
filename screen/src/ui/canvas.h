// A 16-bit RGB565 framebuffer and the shapes the screen is made of. Plain C++ with no Arduino calls,
// so the laptop preview (tools/preview.cpp) draws exactly the pixels that the board draws.
//
// Every shape is anti-aliased: its coverage of each pixel comes from a signed distance, and the colour
// is blended into the pixel by that coverage. Coordinates are floats in pixels; the pixel (x, y) covers
// the square from x to x + 1. A canvas has a clip rectangle and an opacity that every draw call respects,
// so a whole view can fade or slide by drawing it with another opacity or offset.
#pragma once
#include <cstdint>
#include "font.h"

namespace ui {

// A colour as 0xRRGGBB to RGB565, at compile time.
constexpr uint16_t rgb(uint32_t hex) {
  return (uint16_t)(((hex >> 8) & 0xF800) | ((hex >> 5) & 0x07E0) | ((hex >> 3) & 0x001F));
}
// RGB565 back to 8-bit channels.
inline void unpack(uint16_t c, int &r, int &g, int &b) {
  r = (((c >> 11) & 31) * 527 + 23) >> 6;
  g = (((c >> 5) & 63) * 259 + 33) >> 6;
  b = ((c & 31) * 527 + 23) >> 6;
}
// The mix of two colours: a = 0 gives a, a = 255 gives b.
uint16_t mix(uint16_t a, uint16_t b, int t);

struct Canvas {
  uint16_t *px = nullptr;
  int w = 0, h = 0;
  int cx0 = 0, cy0 = 0, cx1 = 0, cy1 = 0;  // the clip rectangle, the right and bottom edges excluded
  int opacity = 255;                       // multiplies the coverage of every draw call
  float ox = 0, oy = 0;                    // an offset added to every coordinate

  Canvas(uint16_t *pixels, int width, int height) : px(pixels), w(width), h(height) { noClip(); }
  void clip(int x0, int y0, int x1, int y1);  // intersects with the canvas, not with the clip before
  void noClip() { cx0 = 0; cy0 = 0; cx1 = w; cy1 = h; }
  // Blend one pixel by a coverage of 0 to 255 (opacity applied here).
  inline void blend(int x, int y, uint16_t c, int a) {
    if (x < cx0 || y < cy0 || x >= cx1 || y >= cy1) return;
    a = (a * opacity + 127) >> 8;
    if (a <= 0) return;
    uint16_t &d = px[y * w + x];
    d = a >= 255 ? c : mix(d, c, a);
  }
};

// Solid shapes. alpha is the colour's own opacity (0 to 255).
void clear(Canvas &c, uint16_t colour);
void fillRect(Canvas &c, float x, float y, float w, float h, uint16_t colour, int alpha = 255);
void fillRound(Canvas &c, float x, float y, float w, float h, float r, uint16_t colour, int alpha = 255);
void strokeRound(Canvas &c, float x, float y, float w, float h, float r, float t, uint16_t colour, int alpha = 255);
void fillCircle(Canvas &c, float cx, float cy, float r, uint16_t colour, int alpha = 255);
void ring(Canvas &c, float cx, float cy, float r, float t, uint16_t colour, int alpha = 255);
// A line with round ends, width w.
void line(Canvas &c, float x0, float y0, float x1, float y1, float w, uint16_t colour, int alpha = 255);
// A soft shadow under a rounded rectangle: a blur of the given radius, dark at the edge of the shape.
void shadow(Canvas &c, float x, float y, float w, float h, float r, float blur, int alpha);
// A vertical gradient in a rectangle, from colour a at the top to colour b at the bottom, each with its own
// opacity. Ordered dithering hides the steps of RGB565.
void vGradient(Canvas &c, int x, int y, int w, int h, uint16_t a, int alphaA, uint16_t b, int alphaB);
// Darken a rectangle towards black by amount (0 to 255), for the dim layer under a sheet.
void dim(Canvas &c, int x, int y, int w, int h, int amount);

// Text. The pen sits on the baseline at x. The functions give back the x after the text.
int textWidth(const Font &f, const char *s);
int drawText(Canvas &c, const Font &f, float x, float y, const char *s, uint16_t colour, int alpha = 255);
int drawTextRight(Canvas &c, const Font &f, float right, float y, const char *s, uint16_t colour, int alpha = 255);
int drawTextCentre(Canvas &c, const Font &f, float centre, float y, const char *s, uint16_t colour, int alpha = 255);
// A text cut to a width, with ".." at its end when it is cut. out must hold at least strlen(s) + 3 bytes.
const char *fitText(const Font &f, const char *s, int width, char *out, int size);
void drawAlpha(Canvas &c, const Alpha4 &a, float x, float y, uint16_t colour, int alpha = 255);

// Easing and small helpers for animation.
inline float clamp01(float t) { return t < 0 ? 0 : t > 1 ? 1 : t; }
inline float easeOut(float t) { t = 1 - clamp01(t); return 1 - t * t * t; }
inline float easeInOut(float t) { t = clamp01(t); return t < 0.5f ? 4 * t * t * t : 1 - (-2 * t + 2) * (-2 * t + 2) * (-2 * t + 2) / 2; }
inline float lerp(float a, float b, float t) { return a + (b - a) * t; }

}  // namespace ui

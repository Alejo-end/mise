// The format of the glyph atlases that tools/make_fonts.py writes.
#pragma once
#include <cstdint>

namespace ui {

// One glyph: where its 4-bit alpha starts in the atlas, its ink box, and the advance of the pen.
// dx and dy place the top left of the ink box relative to the pen on the baseline.
struct Glyph { uint32_t offset; uint8_t w, h; int8_t dx, dy; uint8_t adv; };

struct Font {
  const uint8_t *bits;
  const Glyph *glyphs;
  uint8_t first, last;   // the characters in the atlas
  uint8_t size;          // the pixel size it was rendered at
  int8_t ascent, descent;
  uint8_t cap, xHeight;  // the height of "H" and of "x" above the baseline
};

// A picture as 4-bit alpha, for the wordmark. baseline is the row of the text's baseline.
struct Alpha4 { const uint8_t *bits; uint16_t w, h; int16_t baseline; };

extern const Font kInter13M, kInter15R, kInter15M, kInter20S, kInter28S, kInter60M;
extern const Alpha4 kWordmarkSmall, kWordmarkLarge;

}  // namespace ui

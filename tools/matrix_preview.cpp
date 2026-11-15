// Draws the screens of the box's LED matrix (device/sketch/matrix_ui.h) as one picture, for review.
//   c++ -std=c++17 -O2 -o /tmp/matrix_preview tools/matrix_preview.cpp && /tmp/matrix_preview > matrix.ppm
// Each LED is a round dot. Brightness 0 to 7 maps to the blue of the real matrix.
#include "../device/sketch/matrix_ui.h"
#include <cstdio>
#include <vector>
#include <string>

struct Shot { std::string name; ui::State s; unsigned long t; };

int main() {
  std::vector<Shot> shots;
  auto base = [] { ui::State s; s.bootAt = 0; s.limitAt = 0; s.touchAt = 90000; s.tempKnown = true; s.tempC = 4.6f; return s; };
  const unsigned long T = 100000;  // a moment long after boot and the last turn
  { auto s = base(); s.limitEur = 40; shots.push_back({"limit 40", s, T}); }
  { auto s = base(); s.limitEur = 150; shots.push_back({"limit 150", s, T}); }
  { auto s = base(); s.limitEur = 300; shots.push_back({"limit 300", s, T}); }
  { auto s = base(); s.limitEur = 50; s.prevLimitEur = 40; s.limitAt = T - 60; s.touchAt = T - 60; shots.push_back({"limit rolls", s, T}); }
  { auto s = base(); s.alarm = true; s.tempC = 9.2f; shots.push_back({"cold alarm", s, T}); }
  { auto s = base(); s.delivery = true; shots.push_back({"delivery at the door", s, T}); }
  { auto s = base(); s.approval = true; shots.push_back({"order to approve", s, T + 400}); }
  { auto s = base(); s.delivery = true; s.touchAt = T - 1000; shots.push_back({"turned: limit over a status", s, T}); }
  { auto s = base(); s.holding = true; s.holdAt = T - 900; s.touchAt = T - 900; shots.push_back({"hold, half", s, T}); }
  { auto s = base(); s.done = true; s.doneAt = T - 400; shots.push_back({"hold done", s, T}); }
  { auto s = base(); s.checking = true; s.checkAt = T - 20000; shots.push_back({"delivery check", s, T}); }
  { auto s = base(); s.bootAt = T - 300; shots.push_back({"boot wave", s, T}); }

  const int D = 14, PAD = 10, GAP = 16, PER = 4;
  int fw = ui::COLS * D, fh = ui::ROWS * D;
  int rows = (shots.size() + PER - 1) / PER;
  int W = PAD * 2 + PER * fw + (PER - 1) * GAP, H = PAD * 2 + rows * fh + (rows - 1) * GAP;
  std::vector<unsigned char> img(W * H * 3, 18);
  for (size_t i = 0; i < shots.size(); i++) {
    ui::Frame f;
    ui::render(shots[i].s, shots[i].t, f);
    int ox = PAD + (i % PER) * (fw + GAP), oy = PAD + (i / PER) * (fh + GAP);
    for (int y = 0; y < ui::ROWS; y++)
      for (int x = 0; x < ui::COLS; x++) {
        float k = f[y][x] / 7.0f;
        int cr = 30 + (int)(150 * k), cg = 34 + (int)(190 * k), cb = 46 + (int)(209 * k);
        for (int py = 0; py < D; py++)
          for (int px = 0; px < D; px++) {
            float dx = px - D / 2 + 0.5f, dy = py - D / 2 + 0.5f;
            if (dx * dx + dy * dy > (D * 0.38f) * (D * 0.38f)) continue;
            unsigned char *p = &img[((oy + y * D + py) * W + ox + x * D + px) * 3];
            p[0] = cr; p[1] = cg; p[2] = cb;
          }
      }
    fprintf(stderr, "%zu %s\n", i + 1, shots[i].name.c_str());
  }
  printf("P6\n%d %d\n255\n", W, H);
  fwrite(img.data(), 1, img.size(), stdout);
}

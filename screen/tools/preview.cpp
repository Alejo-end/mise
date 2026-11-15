// The laptop preview of the screen. It draws every important state and some animation frames with the same
// UI code that runs on the board, and writes them as PNG files.
//
// Build and run from the folder screen/:
//   c++ -std=c++17 -O2 -Wall -Wextra -Isrc/ui tools/preview.cpp src/ui/*.cpp -o /tmp/mise-preview
//   /tmp/mise-preview preview            (the PNGs go to screen/preview/)
//   /tmp/mise-preview preview zoom       (also writes each state at twice the size into zoom/)
//
// It writes PPM files and converts them with sips, which every Mac has.

#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

#include "canvas.h"
#include "draw.h"
#include "model.h"

using namespace ui;
using ui::layout::CAMERA_MODES;

static std::string outDir = "preview", zoomDir;

static void writePng(const std::string &name, const uint16_t *px, int w, int h, int scale = 1) {
  std::string ppm = outDir + "/" + name + ".ppm", png = outDir + "/" + name + ".png";
  if (scale > 1) { ppm = zoomDir + "/" + name + ".ppm"; png = zoomDir + "/" + name + ".png"; }
  FILE *f = fopen(ppm.c_str(), "wb");
  if (!f) { perror(ppm.c_str()); exit(1); }
  fprintf(f, "P6\n%d %d\n255\n", w * scale, h * scale);
  std::vector<uint8_t> rowBuf(w * scale * 3);
  for (int y = 0; y < h; y++) {
    for (int x = 0; x < w; x++) {
      int r, g, b;
      unpack(px[y * w + x], r, g, b);
      for (int s = 0; s < scale; s++) {
        rowBuf[(x * scale + s) * 3] = (uint8_t)r;
        rowBuf[(x * scale + s) * 3 + 1] = (uint8_t)g;
        rowBuf[(x * scale + s) * 3 + 2] = (uint8_t)b;
      }
    }
    for (int s = 0; s < scale; s++) fwrite(rowBuf.data(), 1, rowBuf.size(), f);
  }
  fclose(f);
  std::string cmd = "sips -s format png '" + ppm + "' --out '" + png + "' >/dev/null 2>&1 && rm '" + ppm + "'";
  if (system(cmd.c_str()) != 0) fprintf(stderr, "sips failed for %s\n", ppm.c_str());
}

// ---------------------------------------------------------------------------------------------------------
// Sample data

// A test picture of the shelf: a wall, a shelf board, and three products in their colours.
static std::vector<uint16_t> shelfPicture(int w, int h, float shift) {
  std::vector<uint16_t> px(w * h);
  auto set = [&](int x, int y, int r, int g, int b) {
    if (x < 0 || y < 0 || x >= w || y >= h) return;
    static const int B[4][4] = {{0, 8, 2, 10}, {12, 4, 14, 6}, {3, 11, 1, 9}, {15, 7, 13, 5}};
    int d = B[y & 3][x & 3] / 2;  // ordered dithering, as a camera picture has no steps
    auto c8 = [](int v) { return v < 0 ? 0 : v > 255 ? 255 : v; };
    r = c8(r + d); g = c8(g + d / 2); b = c8(b + d);
    px[y * w + x] = (uint16_t)(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3));
  };
  for (int y = 0; y < h; y++)
    for (int x = 0; x < w; x++) {
      float u = (float)x / w, v = (float)y / h;
      float light = 0.95f - 0.3f * v - 0.25f * (u - 0.45f) * (u - 0.45f);
      set(x, y, (int)(150 * light), (int)(145 * light), (int)(135 * light));
    }
  // The shelf board and its shadow.
  int board = (int)(h * 0.74f);
  for (int y = board; y < h; y++)
    for (int x = 0; x < w; x++) {
      float k = y < board + h * 0.05f ? 1.0f : 0.55f;
      set(x, y, (int)(120 * k), (int)(96 * k), (int)(70 * k));
    }
  struct Box { float x0, x1, top; int r, g, b; } boxes[] = {
      {0.08f, 0.30f, 0.20f, 40, 90, 200},    // a milk carton
      {0.38f, 0.60f, 0.48f, 235, 200, 60},   // butter
      {0.68f, 0.90f, 0.34f, 236, 236, 230},  // yoghurt
  };
  for (const Box &b : boxes) {
    int x0 = (int)((b.x0 + shift) * w), x1 = (int)((b.x1 + shift) * w), y0 = (int)(b.top * h);
    for (int y = y0; y < board; y++)
      for (int x = x0; x < x1; x++) {
        float side = (float)(x - x0) / (x1 - x0);
        float k = side > 0.82f ? 0.8f : 1.0f - 0.08f * side;
        bool label = y > y0 + (board - y0) * 0.35f && y < y0 + (board - y0) * 0.6f;
        if (label) set(x, y, (int)(250 * k), (int)(250 * k), (int)(250 * k));
        else set(x, y, (int)(b.r * k), (int)(b.g * k), (int)(b.b * k));
      }
    for (int y = board; y < board + h / 30 + 1; y++)
      for (int x = x0; x < x1 + w / 40; x++) set(x, y, 70, 55, 40);
  }
  return px;
}

static std::vector<uint16_t> thumbA, thumbB, fullA, fullB;

// A test reading of the distance sensor: a wall at 120 cm, three products at 30, 45 and 60 cm on a shelf
// board, and two cells with no reading. shift moves the near product a little, for the ease.
static void depthGrid(float *cm, float shift) {
  for (int r = 0; r < 8; r++)
    for (int c = 0; c < 8; c++) {
      float v = 120 + 3 * sinf(r * 1.3f + c * 0.7f);   // the wall
      if (r >= 2 && r <= 6 && c <= 1) v = 30 + shift + 0.8f * r;   // the milk carton
      if (r >= 4 && r <= 6 && c >= 3 && c <= 4) v = 45 + 0.6f * c;  // the butter
      if (r >= 3 && r <= 6 && c >= 6) v = 60 + 0.7f * r;           // the yoghurt
      if (r == 7) v = 24 + 2 * c * 0.3f;                           // the front of the shelf board
      cm[r * 8 + c] = v;
    }
  cm[2] = NAN;
  cm[13] = NAN;
}

static View baseView() {
  View v;
  v.reached = v.online = v.hasTemp = true;
  v.temp = 3.4f;
  v.room.hasLimit = true;
  v.room.limit = 6;
  v.room.product = "Butter";
  v.coldState = "ok";
  v.hasPeak = true;
  v.peak = 4.1f;
  v.pointCount = POINTS;
  for (int i = 0; i < POINTS; i++) v.points[i] = 3.4f + 0.5f * sinf(i * 0.21f) + 0.15f * sinf(i * 0.9f);
  v.points[47] = 3.4f;
  v.slotCount = 3;
  v.slots[0] = Slot();
  v.slots[0].slot = 1; v.slots[0].name = "Valio milk 1 l"; v.slots[0].units = 14; v.slots[0].target = 30; v.slots[0].reorder = 10;
  v.slots[0].emptyAt = "Sun 21:00";
  v.slots[1].slot = 2; v.slots[1].name = "Butter 500 g"; v.slots[1].units = 4; v.slots[1].target = 12; v.slots[1].reorder = 5;
  v.slots[1].low = true; v.slots[1].onOrder = 24;
  v.slots[2].slot = 3; v.slots[2].name = "Greek yoghurt 1 kg"; v.slots[2].units = -1; v.slots[2].target = 20; v.slots[2].reorder = 6;
  v.home = TAB_COLD;
  return v;
}

static View deliveryView(bool warm) {
  View v = baseView();
  v.hasDelivery = true;
  v.home = TAB_DOOR;
  v.orderId = "VO-1042";
  v.total = "EUR 84.20";
  v.lines[0] = "12 x Valio milk 1 l";
  v.lines[1] = "6 x Butter 500 g";
  v.lines[2] = "4 x Oltermanni cheese slices, extra long name 1 kg";
  v.lineCount = 3;
  v.door.hasLimit = true;
  v.door.limit = 6;
  v.door.product = "Butter";
  if (warm) {
    v.temp = 8.3f;
    v.tooWarm = true;
    v.door.minutes = 14;
  }
  return v;
}

// A model that has seen one answer at the clock time 1000, with no motion left.
static Model modelFor(const View &v, Tab tab) {
  Model m;
  m.wifi = true;
  m.apply(v, 1000);
  m.setTab(tab, 1000);
  m.tabAt = 0;
  m.coldAt = 0;
  m.temp.at = 0;
  for (Roll &r : m.units) r.at = 0;
  m.touchAt = 1000;
  m.thumb = Camera();
  return m;
}

static void withCamera(Model &m, uint32_t now) {
  m.setThumb(thumbA.data(), 160, 90, 1, now - 1500);
  m.thumb.arrivedAt = 0;
  m.setFull(fullA.data(), 480, 270, 1, now - 1500);
  m.full.arrivedAt = 0;
}

// ---------------------------------------------------------------------------------------------------------

static std::vector<uint16_t> fb(W *H);

static Frame shot(const std::string &name, const Model &m, uint32_t now) {
  Canvas c(fb.data(), W, H);
  Frame f = render(c, m, now);
  writePng(name, fb.data(), W, H);
  if (!zoomDir.empty()) writePng(name, fb.data(), W, H, 2);
  return f;
}

// A contact sheet: frames of one motion side by side at half size, each with its time above it.
struct Sheet {
  int cols, rows, cw = W / 2, ch = H / 2 + 20;
  std::vector<uint16_t> px;
  int n = 0;
  Sheet(int c, int r) : cols(c), rows(r), px((size_t)c * (W / 2 + 8) * r * (H / 2 + 20), col::BG) {}
  int sw() const { return cols * (cw + 8); }
  void add(const Model &m, uint32_t now, const char *label) {
    Canvas c(fb.data(), W, H);
    render(c, m, now);
    int ox = (n % cols) * (cw + 8) + 4, oy = (n / cols) * ch + 20;
    for (int y = 0; y < H / 2; y++)
      for (int x = 0; x < W / 2; x++) {
        int r = 0, g = 0, b = 0;
        for (int k = 0; k < 4; k++) {
          int rr, gg, bb;
          unpack(fb[(y * 2 + k / 2) * W + x * 2 + k % 2], rr, gg, bb);
          r += rr; g += gg; b += bb;
        }
        px[(oy + y) * sw() + ox + x] = rgb(((uint32_t)(r / 4) << 16) | ((uint32_t)(g / 4) << 8) | (uint32_t)(b / 4));
      }
    Canvas s(px.data(), sw(), rows * ch);
    drawText(s, kInter13M, ox, oy - 6, label, col::TEXT2);
    n++;
  }
  void write(const std::string &name) { writePng(name, px.data(), sw(), rows * ch); }
};

int main(int argc, char **argv) {
  if (argc > 1) outDir = argv[1];
  if (argc > 2) zoomDir = argv[2];
  if (!zoomDir.empty()) system(("mkdir -p '" + zoomDir + "'").c_str());
  system(("mkdir -p '" + outDir + "'").c_str());
  thumbA = shelfPicture(160, 90, 0);
  thumbB = shelfPicture(160, 90, 0.03f);
  fullA = shelfPicture(480, 270, 0);
  fullB = shelfPicture(480, 270, 0.03f);
  const uint32_t T = 100000;  // a clock time well after every motion

  // Door
  {
    Model m = modelFor(deliveryView(false), TAB_DOOR);
    shot("door-cold-enough", m, T);
    m = modelFor(deliveryView(true), TAB_DOOR);
    shot("door-too-warm", m, T);
    View v = baseView();
    v.approvals = 2;
    v.approval = "Valio Oy: weekly dairy order, EUR 312.40";
    m = modelFor(v, TAB_DOOR);
    shot("door-empty", m, T);
    m = modelFor(deliveryView(false), TAB_DOOR);
    m.tap(ui::layout::REJECT.x + 20, ui::layout::REJECT.y + 20, T);
    shot("door-pressed", m, T + 40);
    m.poll(T + 100);
    shot("door-confirm-reject", m, T + 1000);
    m = modelFor(deliveryView(true), TAB_DOOR);
    m.tap(ui::layout::ACCEPT.x + 20, ui::layout::ACCEPT.y + 20, T);
    m.poll(T + 100);
    shot("door-confirm-accept", m, T + 1000);
  }
  // Cold
  {
    View v = baseView();
    for (int i = 0; i < POINTS; i++) v.points[i] = 3.2f + (i > 20 ? (i - 20) * 0.2f : 0) + 0.15f * sinf(i * 0.7f);
    v.temp = v.points[47];
    v.coldState = "warm";
    v.coldWarm = 2;
    v.room.minutes = 34;
    v.points[8] = NAN; v.points[9] = NAN;
    Model m = modelFor(v, TAB_COLD);
    shot("cold-rising", m, T);
    m = modelFor(baseView(), TAB_COLD);
    shot("cold-steady", m, T);
    View e = baseView();
    for (int i = 0; i < POINTS; i++) e.points[i] = NAN;
    e.hasPeak = false;
    m = modelFor(e, TAB_COLD);
    shot("cold-no-readings", m, T);
    View n = baseView();
    n.hasTemp = false;
    n.coldState = "unknown";
    m = modelFor(n, TAB_COLD);
    shot("cold-no-reading", m, T);
  }
  // Stock and the camera
  {
    Model m = modelFor(baseView(), TAB_STOCK);
    withCamera(m, T);
    shot("stock", m, T);
    m.tap(ui::layout::row(1).x + 40, ui::layout::row(1).y + 20, T);
    shot("stock-selected", m, T + 1000);
    Rect b = ui::layout::reportButton(1);
    m.tap(b.x + 10, b.y + 10, T + 1000);
    m.poll(T + 1100);
    shot("stock-confirm", m, T + 2000);
    m.tap(ui::layout::YES.x + 10, ui::layout::YES.y + 10, T + 2000);
    m.poll(T + 2100);
    m.beginWaiting();
    shot("stock-waiting", m, T + 2200);
    Model off = modelFor(baseView(), TAB_STOCK);
    off.setThumb(thumbA.data(), 160, 90, 40, T - 1000);
    shot("stock-camera-offline", off, T);
    Model cam = modelFor(baseView(), TAB_STOCK);
    withCamera(cam, T);
    cam.tap(ui::layout::THUMB.x + 20, ui::layout::THUMB.y + 20, T);
    shot("camera-full", cam, T + 1000);
    shot("camera-slide-half", cam, T + 80);
    // The distance sensor in the large view.
    float grid[DEPTH_CELLS], grid2[DEPTH_CELLS];
    depthGrid(grid, 0);
    depthGrid(grid2, 40);
    cam.tap(CAMERA_MODES.x + CAMERA_MODES.w - 20, CAMERA_MODES.y + 10, T + 1000);
    cam.setDepth(grid, 0, T + 1000);
    shot("depth", cam, T + 1300);
    shot("depth-switch-half", cam, T + 1000 + 60);
    cam.setDepth(grid2, 0, T + 2000);
    shot("depth-ease-half", cam, T + 2000 + 50);
    Model dOff = cam;
    dOff.noDepth();
    shot("depth-offline", dOff, T + 3000);
    dOff.setDepth(grid, 0, T - 20000);
    shot("depth-stale", dOff, T + 3000);
    Model camOff = modelFor(baseView(), TAB_STOCK);
    camOff.tap(ui::layout::THUMB.x + 20, ui::layout::THUMB.y + 20, T);
    shot("camera-full-offline", camOff, T + 1000);
  }
  // Toasts, the alarm, the link states, the start picture, the calibration
  {
    Model m = modelFor(deliveryView(false), TAB_DOOR);
    m.showToast("Accepted", "VO-1042 is in the log, with the temperature.", true, T);
    shot("toast-accepted", m, T + 1000);
    m.showToast("Not sent", "The backend did not answer. Try again.", false, T);
    shot("toast-not-sent", m, T + 1000);
    View a = deliveryView(true);
    a.alarm = true;
    m = modelFor(a, TAB_DOOR);
    shot("alarm", m, T);
    m = modelFor(baseView(), TAB_COLD);
    m.setWifi(false);
    shot("no-wifi", m, T);
    View off = baseView();
    off.online = false;
    off.hasTemp = false;
    off.coldState = "unknown";
    m = modelFor(off, TAB_COLD);
    shot("box-offline", m, T);
    View three = deliveryView(false);
    three.more = 2;
    m = modelFor(three, TAB_DOOR);
    m.setTouch(false);
    shot("no-touch-three-waiting", m, T);
    Canvas c(fb.data(), W, H);
    renderSplash(c, "Joining the network");
    writePng("splash", fb.data(), W, H);
    renderCalibration(c, 0, W - 1 - 40, H - 1 - 40);
    writePng("calibration", fb.data(), W, H);
  }

  // Motion: contact sheets.
  {
    Sheet s(3, 2);
    Model m = modelFor(deliveryView(false), TAB_DOOR);
    m.setTab(TAB_COLD, T);
    for (int t : {0, 60, 120, 180, 240, 320}) s.add(m, T + t, (std::to_string(t) + " ms").c_str());
    s.write("motion-tab-switch");
  }
  {
    Sheet s(3, 2);
    Model m = modelFor(baseView(), TAB_COLD);
    m.coldAt = T;
    for (int t : {0, 100, 200, 300, 400, 520}) s.add(m, T + t, (std::to_string(t) + " ms").c_str());
    s.write("motion-chart-draw");
  }
  {
    Sheet s(3, 2);
    Model m = modelFor(deliveryView(false), TAB_DOOR);
    m.showToast("Accepted", "VO-1042 is in the log, with the temperature.", true, T);
    for (int t : {0, 80, 160, 260, 400, 2500}) s.add(m, T + t, (std::to_string(t) + " ms").c_str());
    s.write("motion-toast");
  }
  {
    Sheet s(3, 2);
    Model m = modelFor(deliveryView(false), TAB_DOOR);
    m.tap(ui::layout::REJECT.x + 20, ui::layout::REJECT.y + 20, T);
    s.add(m, T + 40, "pressed, 40 ms");
    m.poll(T + 90);
    for (int t : {0, 60, 120, 240}) s.add(m, T + 90 + t, ("sheet " + std::to_string(t) + " ms").c_str());
    m.tap(ui::layout::BACK.x + 20, ui::layout::BACK.y + 20, T + 1000);
    m.poll(T + 1090);
    s.add(m, T + 1090 + 80, "closing, 80 ms");
    s.write("motion-sheet");
  }
  {
    Sheet s(3, 2);
    Model m = modelFor(baseView(), TAB_STOCK);
    withCamera(m, T);
    m.tap(ui::layout::row(0).x + 40, ui::layout::row(0).y + 20, T);
    for (int t : {0, 60, 120, 220}) s.add(m, T + t, ("select " + std::to_string(t) + " ms").c_str());
    m.setThumb(thumbB.data(), 160, 90, 0, T + 1000);
    s.add(m, T + 1100, "new picture, 100 ms");
    m.tap(ui::layout::THUMB.x + 20, ui::layout::THUMB.y + 20, T + 2000);
    s.add(m, T + 2000 + 140, "camera opens, 140 ms");
    s.write("motion-stock-camera");
  }
  {
    Sheet s(3, 2);
    Model m = modelFor(baseView(), TAB_STOCK);
    withCamera(m, T);
    m.tap(ui::layout::THUMB.x + 20, ui::layout::THUMB.y + 20, T - 2000);
    float grid[DEPTH_CELLS], grid2[DEPTH_CELLS];
    depthGrid(grid, 0);
    depthGrid(grid2, 40);
    m.setDepth(grid, 0, T);
    m.tap(CAMERA_MODES.x + CAMERA_MODES.w - 20, CAMERA_MODES.y + 10, T);
    for (int t : {0, 50, 100, 200}) s.add(m, T + t, ("to Depth, " + std::to_string(t) + " ms").c_str());
    m.setDepth(grid2, 0, T + 1000);
    s.add(m, T + 1000 + 50, "new reading, 50 ms");
    s.add(m, T + 1000 + 150, "new reading, 150 ms");
    s.write("motion-depth");
  }
  {
    Sheet s(3, 2);
    Model m = modelFor(baseView(), TAB_COLD);
    View v = baseView();
    v.temp = 4.7f;
    m.apply(v, T);
    for (int t : {0, 60, 120, 180, 240, 320}) s.add(m, T + t, ("3.4 to 4.7, " + std::to_string(t) + " ms").c_str());
    s.write("motion-roll");
  }

  // The time of one full render on this machine, for the estimate on the board.
  {
    Model m = modelFor(baseView(), TAB_COLD);
    Canvas c(fb.data(), W, H);
    auto t0 = std::chrono::steady_clock::now();
    const int N = 200;
    for (int i = 0; i < N; i++) render(c, m, T + i * 16);
    auto t1 = std::chrono::steady_clock::now();
    double ms = std::chrono::duration<double, std::milli>(t1 - t0).count() / N;
    Model d = modelFor(deliveryView(false), TAB_DOOR);
    t0 = std::chrono::steady_clock::now();
    for (int i = 0; i < N; i++) render(c, d, T);
    t1 = std::chrono::steady_clock::now();
    double ms2 = std::chrono::duration<double, std::milli>(t1 - t0).count() / N;
    Model st = modelFor(baseView(), TAB_STOCK);
    withCamera(st, T);
    t0 = std::chrono::steady_clock::now();
    for (int i = 0; i < N; i++) render(c, st, T);
    t1 = std::chrono::steady_clock::now();
    double ms3 = std::chrono::duration<double, std::milli>(t1 - t0).count() / N;
    printf("render on this machine: cold %.3f ms, door %.3f ms, stock %.3f ms\n", ms, ms2, ms3);
  }
  return 0;
}

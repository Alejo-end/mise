// Visible items, drawn the same way on the box's camera setup page and on the kitchen agent's demo page. The box
// serves this file as assets/visible-view.js, the backend as /visible-view.js (the same file).
//
// The recognised picture gets plain box outlines and small numbered badges (1, 2, 3 ...). Badges stay inside the
// picture and never overlap each other; they are laid out again whenever the picture's size changes (narrow
// screens). The list uses the same numbers; names, quantities and uncertainty are only in the list. Picture,
// boxes and list come from one recognition result: each mark's number is its object's place in that result
// (visible_items.marks). Display only: nothing here changes what was recognised or counted.
//
// Colour = the object's own status, the same in its outline, its badge and its list line: green recognised (an
// active-inventory product), amber unresolved (unknown, ambiguous, flavour unreadable), grey recognised but not in
// the inventory (Salted Caramel). It never follows the aggregate: a green Aito stays green beside "at least 2".
(function (g) {
  "use strict";
  const CSS = `
  .vv-pic { position:relative; line-height:0; margin:0 0 10px; max-width:100% }
  .vv-pic img { display:block; width:100%; height:auto; border-radius:6px }
  .vv-layer { position:absolute; inset:0; overflow:hidden; pointer-events:none }
  .vv-box { position:absolute; box-sizing:border-box; border:2px solid #2fae4f; border-radius:3px; background:transparent }
  .vv-box.vv-s-unresolved { border-color:#f2a900; border-style:dashed } .vv-box.vv-s-not-inventory { border-color:#a3a3a3 }
  .vv-badge { position:absolute; box-sizing:border-box; display:flex; align-items:center; justify-content:center;
    border-radius:50%; border:2px solid #fff; color:#fff; font:700 12px/1 system-ui, -apple-system, Arial, sans-serif;
    box-shadow:0 0 0 1px rgba(0,0,0,.45) }
  .vv-badge.vv-s-recognised, .vv-chip.vv-s-recognised { background:#1f7a33 }
  .vv-badge.vv-s-unresolved, .vv-chip.vv-s-unresolved { background:#9a6400 }
  .vv-badge.vv-s-not-inventory, .vv-chip.vv-s-not-inventory { background:#5c5c5c }
  .vv-list { list-style:none; margin:0 0 8px; padding:0 }
  .vv-list li { display:flex; gap:8px; align-items:flex-start; padding:6px 0; border-bottom:1px solid rgba(0,0,0,.15) }
  .vv-nums { display:flex; flex-wrap:wrap; gap:3px; flex:none; max-width:42% }
  .vv-chip { box-sizing:border-box; display:inline-flex; align-items:center; justify-content:center; min-width:22px; height:22px;
    padding:0 5px; border-radius:11px; color:#fff; font:700 12px/1 system-ui, -apple-system, Arial, sans-serif }

  .vv-chip.vv-nobox { background:transparent; color:inherit; border:1.5px dashed currentColor }
  .vv-box { background:transparent }
  .vv-text { flex:1; min-width:0; overflow-wrap:anywhere }
  .vv-list li.vv-s-recognised .vv-text { color:#1d6b2e } .vv-list li.vv-s-unresolved .vv-text { color:#8a5800 }
  .vv-list li.vv-s-not-inventory .vv-text { color:#5c5c5c }
  .vv-list li.vv-none { font-style:italic; opacity:.75 }`;

  function css() {
    if (document.getElementById("vv-css")) return;
    const s = document.createElement("style"); s.id = "vv-css"; s.textContent = CSS; document.head.appendChild(s);
  }

  // Badge places (top-left, in pixels) inside a W x H picture; badges are size x size and never overlap (gap px apart).
  // Tried in order: the box's inside corners, its top and middle, just outside it, then a widening ring of places
  // around its first corner. Pure: no DOM.
  function placeBadges(marks, W, H, size, gap = 2) {
    const placed = [], out = {};
    const cx = (x) => Math.min(Math.max(0, x), W - size), cy = (y) => Math.min(Math.max(0, y), H - size);
    const free = (x, y) => placed.every((p) => x + size + gap <= p.x || p.x + size + gap <= x || y + size + gap <= p.y || p.y + size + gap <= y);
    for (const m of marks) {
      if (!m.box) continue;
      const bx = m.box[0] * W, by = m.box[1] * H, bw = m.box[2] * W, bh = m.box[3] * H, i = 3;
      const tries = [[bx + i, by + i], [bx + bw - size - i, by + i], [bx + i, by + bh - size - i], [bx + bw - size - i, by + bh - size - i],
                     [bx + (bw - size) / 2, by + i], [bx + (bw - size) / 2, by + (bh - size) / 2],
                     [bx, by - size - gap], [bx - size - gap, by], [bx + bw + gap, by], [bx, by + bh + gap]].map(([x, y]) => [cx(x), cy(y)]);
      let spot = tries.find(([x, y]) => free(x, y));
      for (let r = 1, step = size + gap; !spot && r < 60; r++) {
        for (let dy = -r; dy <= r && !spot; dy++) for (let dx = -r; dx <= r && !spot; dx++) {
          if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
          const x = cx(tries[0][0] + dx * step), y = cy(tries[0][1] + dy * step);
          if (free(x, y)) spot = [x, y];
        }
      }
      if (!spot) continue;  // the picture is full of badges: this one goes without (the list still has its number)
      placed.push({x: spot[0], y: spot[1]}); out[m.n] = {x: spot[0], y: spot[1]};
    }
    return out;
  }

  const badgeSize = (w) => (w < 360 ? 18 : w < 560 ? 20 : 24);
  // the object's own status; results from before 15 Nov 05:20 carry only a kind
  const KIND_STATUS = {"product": "recognised", "not-inventory": "not-inventory"};
  const statusOf = (x) => (x && (x.status || KIND_STATUS[x.kind])) || "unresolved";
  const pct = (v) => `${(v * 100).toFixed(3)}%`;

  function layout(layer, img, marks) {
    const W = img.clientWidth, H = img.clientHeight;
    if (!W || !H) return;
    const size = badgeSize(W);
    layer.innerHTML = "";
    marks.filter((m) => m.box).forEach((m) => {
      const b = document.createElement("div"); b.className = `vv-box vv-s-${statusOf(m)}`;
      Object.assign(b.style, {left: pct(m.box[0]), top: pct(m.box[1]), width: pct(m.box[2]), height: pct(m.box[3])});
      layer.appendChild(b);
    });
    const at = placeBadges(marks, W, H, size);
    marks.forEach((m) => {
      if (!at[m.n]) return;
      const d = document.createElement("span"); d.className = `vv-badge vv-s-${statusOf(m)}`; d.textContent = m.n;
      Object.assign(d.style, {left: `${at[m.n].x}px`, top: `${at[m.n].y}px`, width: `${size}px`, height: `${size}px`,
                              fontSize: `${Math.round(size * 0.52)}px`});
      d.setAttribute("aria-label", `object ${m.n}, ${statusOf(m)}`);
      layer.appendChild(d);
    });
    layer.dataset.width = W;
  }

  // The picture of one result with its numbered boxes. src: an image URL for exactly this result, or null.
  function drawImage(wrap, src, vi) {
    css();
    const marks = (vi && vi.marks) || [];
    const key = `${src}|${JSON.stringify(marks)}`;
    if (wrap._vvKey === key) return;
    wrap._vvKey = key;
    if (wrap._vvRO) { wrap._vvRO.disconnect(); wrap._vvRO = null; }
    wrap.innerHTML = "";
    wrap.classList.add("vv-pic");
    if (!src) { wrap.hidden = true; return; }
    wrap.hidden = false;
    const img = document.createElement("img"); img.alt = "The recognised picture, with numbered boxes";
    const layer = document.createElement("div"); layer.className = "vv-layer";
    wrap.append(img, layer);
    const redo = () => layout(layer, img, marks);
    img.onload = redo;
    img.src = src;
    if (g.ResizeObserver) { wrap._vvRO = new ResizeObserver(redo); wrap._vvRO.observe(wrap); }
    else g.addEventListener("resize", redo);
  }

  // The list of one result: each line starts with the numbers of its boxes (a dashed number: no place in the picture).
  function drawList(ul, vi, emptyText) {
    css();
    ul.classList.add("vv-list");
    ul.innerHTML = "";
    const marks = new Map(((vi && vi.marks) || []).map((m) => [m.n, m]));
    const items = (vi && vi.items) || [];
    if (!items.length) {
      const li = document.createElement("li"); li.className = "vv-none"; li.textContent = emptyText; ul.appendChild(li); return;
    }
    items.forEach((it) => {
      const li = document.createElement("li"); li.className = `vv-${it.kind} vv-s-${statusOf(it)}`;
      const nums = document.createElement("span"); nums.className = "vv-nums";
      (it.marks || []).forEach((n) => {
        const m = marks.get(n), c = document.createElement("span");
        c.className = `vv-chip vv-s-${statusOf(m || it)}${m && m.box ? "" : " vv-nobox"}`;
        c.textContent = n; c.title = m && m.box ? `box ${n} in the picture` : `object ${n}: no place in the picture`;
        nums.appendChild(c);
      });
      const text = document.createElement("span"); text.className = "vv-text"; text.textContent = it.text;
      li.append(nums, text); ul.appendChild(li);
    });
  }

  g.VisibleView = {drawImage, drawList, placeBadges};
})(window);

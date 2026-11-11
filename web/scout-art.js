"use strict";

// Observation-station props use only integer rectangles on a 2 / 4 px grid.
// The public functions return complete, self-contained SVG strings. Counted
// objects are drawn only from real input counts; the empty scene has empty racks.
(() => {
  const C = {
    wood: "#a48d6a", woodLight: "#c6b284", woodPale: "#decea7", woodDark: "#7d7155",
    bark: "#a8845d", barkLight: "#c19c6f", barkDark: "#866b4c", cut: "#e4d2a5", ring: "#bea171",
    green: "#8fa66a", greenLight: "#b7c98d", greenDark: "#708952",
    sage: "#9cac91", sageLight: "#c2cfac", sageDark: "#7d8c74",
    blue: "#8ba7a0", blueLight: "#c5d9cb", blueDark: "#627e79",
    cream: "#f0e7cc", paper: "#e9dfbb", paperShade: "#cfc5a0", wall: "#e2d9ba",
    gold: "#d1b279", rose: "#c49a84", muted: "#acb194", ink: "#5e6b59",
    stone: "#a8b29d", stoneLight: "#d0d1b5", shadow: "#6f835735",
  };
  const phaseMap = Object.freeze({
    idle: "idle", gathering: "gathering", searching: "gathering",
    carrying: "carrying", collecting: "carrying", returning: "carrying",
    sorting: "sorting", storing: "sorting", completed: "idle", partial: "idle",
    failed: "idle", interrupted: "idle",
  });
  const count = value => {
    const number = typeof value === "number" ? value : typeof value === "string" ? Number(value) : 0;
    return Number.isFinite(number) ? Math.max(0, Math.floor(number)) : 0;
  };
  const phaseOf = value => typeof value === "string" && Object.prototype.hasOwnProperty.call(phaseMap, value) ? phaseMap[value] : "idle";
  const stateOf = input => ({ phase: phaseOf(input?.phase), materialCount: count(input?.materialCount), rawCount: count(input?.rawCount), bookCount: count(input?.bookCount) });
  function canvas(width, height, name, phase = "idle") {
    const shapes = [];
    const r = (x, y, w, h, color, extra = "") => {
      if (w > 0 && h > 0) shapes.push(`<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${color}"${extra}/>`);
    };
    return {
      r,
      group(className, draw) { shapes.push(`<g class="${className}">`); draw(); shapes.push("</g>"); },
      finish(title) { return `<svg xmlns="http://www.w3.org/2000/svg" class="scout-art scout-art-${name} phase-${phase}" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="${title}" shape-rendering="crispEdges" fill="none" stroke="none" style="display:block;width:${width}px;max-width:100%;height:auto;stroke:none;stroke-width:0;image-rendering:pixelated;shape-rendering:crispEdges">${shapes.join("")}</svg>`; },
    };
  }
  function log(r, x, y, length = 36) {
    r(x + 2, y + 2, length - 10, 16, C.bark);
    r(x, y + 6, 4, 8, C.barkDark);
    r(x + 4, y + 2, length - 18, 4, C.barkLight);
    r(x + 4, y + 14, length - 18, 4, C.barkDark);
    r(x + 10, y + 8, 8, 2, C.barkDark);
    r(x + 20, y + 6, 4, 2, C.barkLight);
    // Octagonal cut end with an offset growth ring and a small heartwood center.
    r(x + length - 16, y + 4, 16, 12, C.barkDark);
    r(x + length - 12, y, 8, 20, C.barkDark);
    r(x + length - 14, y + 4, 12, 12, C.cut);
    r(x + length - 12, y + 2, 8, 16, C.cut);
    r(x + length - 12, y + 6, 8, 8, C.ring);
    r(x + length - 10, y + 4, 4, 12, C.ring);
    r(x + length - 10, y + 8, 4, 4, C.cut);
    r(x + length - 8, y + 8, 2, 4, C.barkLight);
  }
  function book(r, x, bottom, width = 10, height = 28, color = C.blue) {
    r(x, bottom - height, width, height, color);
    r(x, bottom - height, 2, height, C.woodDark);
    r(x + 2, bottom - height + 2, width - 2, 2, C.cream);
    r(x + 2, bottom - 6, width - 2, 2, C.paperShade);
    if (height >= 24) r(x + 4, bottom - height + 10, 2, 6, C.cream);
  }
  function axe(r, x, y) {
    r(x + 12, y + 4, 4, 38, C.woodDark);
    r(x + 12, y + 6, 2, 34, C.woodLight);
    r(x + 6, y + 2, 18, 12, C.stone);
    r(x + 2, y + 4, 8, 16, C.stone);
    r(x, y + 8, 4, 12, C.stoneLight);
    r(x + 2, y + 4, 4, 8, C.stoneLight);
    r(x + 8, y + 2, 12, 2, C.stoneLight);
    r(x + 6, y + 14, 6, 4, C.sageDark);
    r(x + 12, y + 2, 4, 12, C.woodDark);
    r(x + 16, y + 4, 4, 6, C.sageDark);
  }
  function wheel(r, x, y) {
    r(x + 4, y, 8, 16, C.woodDark); r(x, y + 4, 16, 8, C.woodDark);
    r(x + 4, y + 4, 8, 8, C.stone); r(x + 6, y + 6, 4, 4, C.woodPale);
  }
  function cart(r, x, y, rawCount = 0) {
    wheel(r, x + 8, y + 30); wheel(r, x + 48, y + 30);
    r(x + 4, y + 28, 60, 4, C.woodDark);
    r(x + 6, y + 18, 56, 12, C.wood);
    r(x + 6, y + 18, 56, 4, C.woodLight);
    r(x + 6, y + 26, 56, 4, C.woodDark);
    r(x + 10, y + 12, 4, 18, C.woodDark); r(x + 52, y + 12, 4, 18, C.woodDark);
    r(x + 62, y + 20, 16, 4, C.woodDark); r(x + 76, y + 16, 16, 4, C.woodDark);
    r(x + 84, y + 14, 8, 4, C.woodLight);
    const visible = Math.min(3, rawCount);
    if (visible > 0) log(r, x + 16, y, 38);
    if (visible > 1) log(r, x + 24, y - 12, 36);
    if (visible > 2) log(r, x + 12, y - 24, 36);
  }
  function compass(r, x, y, unit = 2) {
    const p = (a, b, w, h, color) => r(x + a * unit, y + b * unit, w * unit, h * unit, color);
    p(4, 0, 8, 2, C.wood); p(2, 2, 12, 2, C.wood); p(0, 4, 16, 8, C.wood); p(2, 12, 12, 2, C.wood); p(4, 14, 8, 2, C.wood);
    p(4, 2, 8, 12, C.cream); p(2, 4, 12, 8, C.cream);
    p(7, 3, 2, 5, C.rose); p(6, 5, 4, 3, C.rose); p(7, 8, 2, 5, C.blue); p(6, 8, 4, 3, C.blue);
    p(7, 7, 2, 2, C.gold); p(1, 7, 2, 2, C.woodDark); p(13, 7, 2, 2, C.woodDark);
  }
  function shelf(r, x, y, width, height, books, capacity = 24) {
    r(x + 8, y + 8, width - 16, height - 12, C.woodDark);
    r(x + 12, y + 12, width - 24, height - 20, "#b4a989");
    r(x, y, width, 8, C.wood); r(x + 4, y, width - 8, 4, C.woodPale);
    r(x + 4, y + 8, 8, height - 8, C.wood); r(x + width - 12, y + 8, 8, height - 8, C.woodDark);
    const bays = Math.floor((width - 28) / 14);
    const colors = [C.blue, C.sage, C.rose, C.gold, C.woodPale];
    let remaining = Math.min(capacity, books);
    for (let row = 0; row < 3; row++) {
      const bottom = y + 40 + row * 40;
      for (let col = 0; col < bays && remaining > 0; col++, remaining--) {
        const height = 22 + ((col + row) % 3) * 4;
        book(r, x + 18 + col * 14, bottom, 10, height, colors[(col + row * 2) % colors.length]);
      }
      r(x + 8, bottom, width - 16, 8, C.wood); r(x + 8, bottom, width - 16, 4, C.woodPale);
      r(x + 14, bottom + 8, width - 26, 4, "#8f8060");
    }
    r(x, y + height - 8, width, 8, C.woodDark);
    r(x + 12, y + height, 12, 8, C.woodDark); r(x + width - 24, y + height, 12, 8, C.woodDark);
    // Fixed color tabs classify sections; they do not invent research titles.
    for (let i = 0; i < 3; i++) { r(x + 28 + i * 40, y + 42, 16, 6, [C.blue, C.sage, C.gold][i]); r(x + 32 + i * 40, y + 44, 8, 2, C.cream); }
  }

  function yard(input = {}) {
    const s = stateOf(input), c = canvas(240, 160, "yard", s.phase), r = c.r;
    r(16, 124, 148, 8, C.shadow); r(140, 148, 88, 8, C.shadow);
    c.group("scout-material-rack", () => {
      r(28, 28, 124, 88, "#b0ad88"); r(32, 32, 116, 80, "#c7c7a1");
      r(20, 24, 8, 104, C.woodDark); r(152, 24, 8, 104, C.woodDark);
      r(20, 24, 4, 100, C.woodLight); r(152, 24, 4, 100, C.wood);
      r(16, 20, 148, 8, C.sageDark); r(20, 16, 140, 4, C.sage); r(28, 12, 124, 4, C.sageLight);
      for (const y of [66, 112]) { r(24, y, 132, 8, C.wood); r(24, y, 132, 4, C.woodLight); r(28, y + 8, 124, 4, C.woodDark); }
      for (const x of [68, 112]) r(x, 30, 4, 82, C.wood);
      [C.sage, C.blue, C.gold].forEach((color, i) => { r(36 + i * 44, 30, 20, 10, C.woodDark); r(38 + i * 44, 32, 16, 6, color); });
      const visible = Math.min(6, s.materialCount);
      for (let i = 0; i < visible; i++) log(r, 30 + (i % 3) * 44, i < 3 ? 90 : 44, 34);
      if (s.phase === "sorting") { r(14, 36, 2, 18, C.gold); r(16, 34, 8, 2, C.gold); r(16, 54, 8, 2, C.gold); }
    });
    c.group("scout-axe", () => { r(174, 42, 18, 4, C.woodDark); axe(r, 170, 44); });
    c.group("scout-cart", () => cart(r, 142, 108, s.rawCount));
    // Small square trail marks indicate the current motion, not a completion result.
    if (s.phase === "gathering") { r(196, 94, 4, 4, C.gold); r(206, 86, 4, 4, C.gold); r(214, 78, 4, 4, C.gold); }
    if (s.phase === "carrying") { r(126, 128, 8, 2, C.woodLight); r(120, 134, 10, 2, C.woodLight); }
    return c.finish(s.materialCount || s.rawCount ? "星探院落，材料架、原木和手推车" : "星探院落，空材料架和空手推车");
  }

  function room(input = {}) {
    const s = stateOf(input), c = canvas(640, 360, "room", s.phase), r = c.r;
    r(0, 0, 640, 360, C.wall); r(0, 184, 640, 176, "#d0bd94");
    // Quiet timber joinery and floorboards keep the books and work table prominent.
    r(0, 0, 640, 12, C.wood); r(8, 0, 624, 4, C.woodLight);
    r(0, 12, 12, 340, C.woodDark); r(4, 12, 8, 340, C.wood);
    r(628, 12, 12, 340, C.woodDark); r(628, 12, 4, 332, C.woodLight);
    for (let y = 24; y < 176; y += 24) { r(16, y, 608, 2, "#d7cbaa"); for (let x = 24 + (y % 48 ? 0 : 48); x < 620; x += 96) r(x, y + 2, 2, 22, "#d4c7a5"); }
    r(12, 176, 616, 12, C.woodDark); r(12, 176, 616, 4, C.woodLight);
    for (let y = 188; y < 344; y += 16) {
      r(12, y, 616, 2, "#b6a27e"); r(12, y + 2, 616, 2, "#ddcca6");
      for (let x = 36 + (y % 32 ? 0 : 40); x < 620; x += 80) r(x, y + 4, 2, 12, "#bda985");
    }
    r(12, 344, 616, 8, C.wood); r(0, 352, 640, 8, C.woodDark);
    c.group("scout-window", () => {
      r(264, 28, 116, 108, C.woodDark); r(272, 36, 100, 92, C.blueLight);
      r(272, 36, 100, 44, "#dce5ce"); r(272, 80, 100, 48, "#b8cba1");
      r(280, 84, 24, 44, C.green); r(284, 76, 16, 12, C.green); r(276, 92, 32, 24, C.green);
      r(344, 96, 28, 32, C.sage); r(352, 88, 20, 16, C.sage);
      r(316, 36, 8, 92, C.wood); r(272, 76, 100, 8, C.wood);
      r(268, 32, 108, 4, C.woodLight); r(260, 132, 124, 8, C.wood); r(256, 132, 132, 4, C.woodLight);
      for (const x of [248, 384]) { r(x, 28, 12, 96, C.sage); r(x, 28, 4, 88, C.sageLight); r(x, 112, 12, 4, C.sageDark); }
    });
    const leftCapacity = 30;
    c.group("scout-bookcase-left", () => shelf(r, 32, 36, 192, 140, Math.min(leftCapacity, s.bookCount), leftCapacity));
    c.group("scout-bookcase-right", () => shelf(r, 436, 36, 172, 140, Math.max(0, s.bookCount - leftCapacity), 27));
    // Compass is a permanent observation instrument, independent of progress.
    compass(r, 300, 148, 2);
    c.group("scout-material-box", () => {
      r(36, 316, 144, 12, C.shadow);
      r(40, 244, 132, 72, C.woodDark); r(44, 248, 124, 60, C.wood);
      r(36, 240, 140, 12, C.wood); r(40, 240, 132, 4, C.woodPale);
      for (let y = 260; y <= 292; y += 16) { r(48, y, 116, 12, C.woodLight); r(48, y + 10, 116, 2, C.woodDark); r(96, y + 4, 16, 4, C.woodDark); }
      r(48, 312, 12, 12, C.woodDark); r(152, 312, 12, 12, C.woodDark);
      for (let i = 0; i < Math.min(3, s.materialCount); i++) log(r, 44 + i * 40, 220, 36);
      [C.sage, C.blue, C.gold].forEach((color, i) => r(52, 264 + i * 16, 12, 4, color));
    });
    c.group("scout-map-table", () => {
      r(228, 308, 192, 12, C.shadow);
      // Chair behind the map desk.
      r(292, 180, 64, 36, C.woodDark); r(296, 184, 56, 28, C.sage); r(304, 188, 40, 4, C.sageLight);
      r(224, 260, 8, 52, C.woodDark); r(404, 260, 8, 52, C.woodDark);
      r(228, 262, 4, 42, C.woodLight); r(400, 262, 4, 42, C.woodLight);
      r(216, 202, 208, 64, C.woodDark); r(220, 198, 200, 60, C.wood); r(224, 198, 192, 4, C.woodPale);
      for (let yy = 214; yy <= 246; yy += 16) r(224, yy, 192, 2, C.woodLight);
      // A blank-base geography map is part of the room, not a fictional report.
      r(240, 210, 136, 40, C.paperShade); r(244, 208, 128, 38, C.cream);
      for (let x = 260; x < 372; x += 24) r(x, 210, 2, 34, "#ded6b5");
      for (let y = 220; y < 244; y += 12) r(246, y, 124, 2, "#ded6b5");
      r(260, 216, 36, 8, C.sageLight); r(268, 224, 32, 12, C.sageLight); r(280, 236, 12, 6, C.sageLight);
      r(316, 222, 32, 8, C.blueLight); r(332, 216, 20, 8, C.blueLight); r(308, 230, 32, 6, C.blueLight);
      r(244, 208, 8, 38, C.paper); r(366, 208, 8, 38, C.paper);
      r(388, 216, 8, 20, C.woodDark); r(390, 212, 4, 20, C.gold);
      if (s.phase === "sorting" && s.materialCount > 0) { r(380, 236, 24, 16, C.paperShade); r(380, 234, 22, 14, C.cream); r(384, 238, 12, 2, C.blue); r(384, 242, 8, 2, C.sage); }
    });
    c.group("scout-storage", () => {
      r(468, 290, 120, 12, C.shadow); r(472, 240, 108, 56, C.woodDark);
      r(476, 244, 100, 44, C.wood); r(472, 236, 108, 12, C.woodLight);
      r(484, 236, 4, 56, C.woodDark); r(564, 236, 4, 56, C.woodDark); r(516, 256, 20, 8, C.woodPale);
      // Trays remain visibly empty until actual materials are present.
      r(484, 210, 84, 22, C.wood); r(488, 214, 76, 14, "#b8ad89"); r(484, 228, 84, 6, C.woodLight);
      if (s.materialCount > 0) { r(492, 214, 24, 14, C.paperShade); r(494, 214, 20, 10, C.cream); }
      if (s.materialCount > 1) { r(524, 214, 28, 14, C.paperShade); r(526, 214, 24, 10, C.cream); }
    });
    return c.finish(s.bookCount ? "星探资料室，资料书架、地图桌和材料箱" : "星探资料室，空资料书架、地图桌和材料箱");
  }

  function icon(kind) {
    const allowed = ["logs", "rack", "books", "compass", "axe", "cart"];
    const key = allowed.includes(kind) ? kind : "compass", c = canvas(32, 32, `icon-${key}`), r = c.r;
    if (key === "logs") {
      r(4, 8, 18, 8, C.bark); r(4, 8, 18, 2, C.barkLight); r(18, 6, 10, 12, C.barkDark); r(20, 8, 6, 8, C.cut); r(22, 10, 2, 4, C.ring);
      r(2, 20, 18, 8, C.bark); r(2, 20, 18, 2, C.barkLight); r(16, 18, 10, 12, C.barkDark); r(18, 20, 6, 8, C.cut); r(20, 22, 2, 4, C.ring);
    } else if (key === "rack") {
      r(4, 6, 4, 24, C.woodDark); r(24, 6, 4, 24, C.woodDark); r(2, 4, 28, 4, C.sage);
      r(6, 16, 20, 4, C.woodLight); r(6, 26, 20, 4, C.woodLight);
      r(10, 10, 4, 2, C.blue); r(18, 10, 4, 2, C.gold);
    } else if (key === "books") {
      book(r, 4, 28, 8, 22, C.blue); book(r, 12, 28, 8, 26, C.sage); book(r, 20, 28, 8, 20, C.rose); r(2, 28, 28, 4, C.wood);
    } else if (key === "axe") {
      r(16, 6, 4, 24, C.wood); r(16, 8, 2, 20, C.woodLight); r(6, 4, 22, 10, C.stone); r(4, 6, 8, 12, C.stoneLight); r(16, 4, 4, 10, C.woodDark); r(10, 14, 6, 4, C.sageDark);
    } else if (key === "cart") {
      r(4, 14, 18, 10, C.wood); r(4, 14, 18, 4, C.woodLight); r(8, 10, 2, 14, C.woodDark); r(18, 10, 2, 14, C.woodDark);
      r(22, 16, 6, 2, C.woodDark); r(28, 12, 4, 4, C.woodDark); r(6, 24, 6, 6, C.woodDark); r(18, 24, 6, 6, C.woodDark); r(8, 26, 2, 2, C.stoneLight); r(20, 26, 2, 2, C.stoneLight);
    } else compass(r, 0, 0, 2);
    const names = { logs: "原木材料", rack: "材料架", books: "资料书籍", compass: "探索罗盘", axe: "采集工具", cart: "搬运手推车" };
    return c.finish(names[key]);
  }

  function sprite(phase = "idle") {
    const state = phaseOf(typeof phase === "object" ? phase?.phase : phase), c = canvas(48, 48, "state", state), r = c.r;
    // These are activity badges, not an alternate resident or an invented result.
    if (state === "gathering") { compass(r, 8, 8, 2); r(2, 6, 4, 4, C.gold); r(40, 36, 4, 4, C.gold); }
    else if (state === "carrying") {
      log(r, 4, 14, 36); r(10, 38, 8, 2, C.woodLight); r(2, 42, 12, 2, C.woodLight);
    } else if (state === "sorting") {
      r(6, 6, 4, 36, C.woodDark); r(38, 6, 4, 36, C.woodDark); r(6, 6, 36, 4, C.sage); r(8, 36, 32, 4, C.woodLight);
      book(r, 14, 36, 8, 20, C.blue); book(r, 24, 36, 8, 24, C.sage); r(2, 20, 2, 8, C.gold);
    }
    return c.finish({ idle: "星探当前没有进行中的采集动作", gathering: "星探正在探索采集", carrying: "星探正在搬回原始材料", sorting: "星探正在整理资料" }[state]);
  }

  const hotspots = Object.freeze({
    yard: Object.freeze({ rack: Object.freeze([16, 12, 148, 116]), cart: Object.freeze([140, 82, 96, 74]) }),
    room: Object.freeze({ books: Object.freeze([32, 36, 192, 148]), booksRight: Object.freeze([436, 36, 172, 148]), materials: Object.freeze([36, 216, 144, 112]), desk: Object.freeze([216, 198, 208, 114]), storage: Object.freeze([468, 208, 120, 96]) }),
  });
  window.StarhavenScoutArt = Object.freeze({ yard, room, icon, sprite, hotspots });
})();

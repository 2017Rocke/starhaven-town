"use strict";

// Shared pixel houses. Every shape lives on a four-unit grid; there are no
// diagonal paths, curves, gradients, fonts, filters, or remote assets.
(() => {
  const NS = "http://www.w3.org/2000/svg";
  const STYLES = {
    command: { roof: ["#a48d6a", "#c0a780", "#827252"], wall: ["#c6a580", "#dfc29c", "#ad8e6e"], wood: ["#947c59", "#bba27a", "#75674d"] },
    observatory: { roof: ["#8fa89a", "#b1c1ab", "#6e8c80"], wall: ["#d0c9a6", "#e6ddbd", "#b2b397"], wood: ["#879780", "#b0b99b", "#657d70"] },
    architecture: { roof: ["#82967a", "#a4b08d", "#657b65"], wall: ["#c1c7a6", "#d9dfbd", "#a3af93"], wood: ["#899780", "#b1b99b", "#667c6b"] },
    workshop: { roof: ["#ad956e", "#c4ad83", "#89795b"], wall: ["#ceaa80", "#e2c49b", "#b38e6a"], wood: ["#9b805c", "#c1a479", "#79674f"] },
    farm: { roof: ["#b78967", "#cfa47e", "#936e53"], wall: ["#d8c69a", "#eee0b7", "#bba87e"], wood: ["#a28a60", "#c7ae7d", "#806e4e"] },
    music: { roof: ["#9b8e9e", "#b7a8b1", "#7d7587"], wall: ["#d2c4bc", "#e9dbcf", "#b6aaa6"], wood: ["#968578", "#bdab94", "#776f67"] },
    finance: { roof: ["#91a277", "#b1bc8d", "#74875f"], wall: ["#d0c49b", "#e7dcba", "#b3a984"], wood: ["#988b65", "#bfb085", "#787351"] },
    school: { roof: ["#b58e6f", "#cfa98b", "#93765d"], wall: ["#ded3ae", "#eee4c7", "#c3b690"], wood: ["#a28d68", "#c8b48a", "#817351"] },
    cottage: { roof: ["#b88b6c", "#d0a384", "#966f54"], wall: ["#dfd1ac", "#eee2c2", "#c4b58f"], wood: ["#a18a65", "#c7b087", "#81704f"] },
    angling: { roof: ["#c2ae7f", "#d8c493", "#9e8c63"], wall: ["#e2d9bd", "#efe7cd", "#c8bda0"], wood: ["#7f8f8a", "#a5b2ab", "#61716c"] },
  };
  const C = {
    stone: "#b4b59b", stoneLight: "#cfceb2", stoneDark: "#929b81",
    glass: "#a5beb3", glassLight: "#e9e5c8", glassShade: "#7d9a91",
    green: "#819b68", greenLight: "#adc18a", greenDark: "#657f59",
    pink: "#c99884", cream: "#efe5c7", gold: "#d3b579", shadow: "#8ca17138",
  };
  function group(name) {
    const node = document.createElementNS(NS, "g");
    node.setAttribute("shape-rendering", "crispEdges");
    if (name) node.setAttribute("data-pixel-art", name);
    return node;
  }
  function rect(parent, x, y, width, height, fill) {
    const node = document.createElementNS(NS, "rect");
    for (const [key, value] of Object.entries({ x, y, width, height, fill })) node.setAttribute(key, String(value));
    parent.append(node);
    return node;
  }
  // Narrow a finished group horizontally around its centre. Every edge is placed
  // once and snapped to the same 2px lattice, so two shapes that used to abut
  // still abut: snapping x and x+width (not the width itself) is what keeps the
  // courses, planks and window trim from drifting apart or overlapping.
  // One-pixel hairlines (fish columns, seams) keep their width and only move.
  function squeezeX(node, factor, center = 160) {
    const place = value => center + (value - center) * factor;
    const snap2 = value => Math.round(value / 2) * 2;
    const walk = parent => {
      for (const child of parent.children || []) {
        if (!child || typeof child !== "object") continue;
        if (child.name === "rect") {
          const x = Number(child.attrs.x), width = Number(child.attrs.width);
          if (!Number.isFinite(x) || !Number.isFinite(width)) continue;
          if (width <= 2) {
            child.attrs.x = String(Math.round(place(x)));
          } else {
            const left = snap2(place(x)), right = snap2(place(x + width));
            child.attrs.x = String(left);
            child.attrs.width = String(Math.max(2, right - left));
          }
        } else {
          walk(child);
        }
      }
    };
    walk(node);
    return node;
  }
  function resolve(agent) {
    const style = Object.prototype.hasOwnProperty.call(STYLES, agent?.house?.style) ? agent.house.style : "cottage";
    const defaults = { workshop: "tools", architecture: "blueprint", farm: "poultry", music: "music", finance: "finance", school: "books", observatory: "stars", command: "planning", angling: "fishing", cottage: "garden" };
    return { style, p: STYLES[style], motif: agent?.house?.motif || defaults[style], variant: Math.abs(Number(agent?.house?.variant) || 0) % 4 };
  }
  function stoneBase(g, x, y, w) {
    rect(g, x - 4, y, w + 8, 8, C.stone);
    rect(g, x - 4, y, w + 4, 4, C.stoneLight);
    rect(g, x, y + 8, w + 8, 4, C.stoneDark);
    for (let px = x + 12; px < x + w; px += 24) rect(g, px, y + 4, 4, 4, C.stoneDark);
  }
  function wall(g, p, x, y, w, h, stone = false) {
    const base = stone ? [C.stone, C.stoneLight, C.stoneDark] : p.wall;
    rect(g, x, y, w, h, base[0]);
    rect(g, x + w - 12, y, 12, h, base[2]);
    rect(g, x + 4, y + 4, w - 20, 4, base[1]);
    for (let row = 12; row < h - 8; row += 12) {
      rect(g, x + 4, y + row, w - 16, 4, row % 24 ? base[1] : base[2]);
      for (let px = x + 12 + (row % 24 ? 0 : 16); px < x + w - 16; px += 32) rect(g, px, y + row - 8, 4, 8, base[2]);
    }
    if (!stone) {
      rect(g, x + 4, y + 4, 4, h - 4, p.wood[1]);
      rect(g, x + w - 16, y + 4, 4, h - 4, p.wood[0]);
    }
    stoneBase(g, x, y + h - 4, w);
    // Corner quoins and a plinth shadow lift the masonry without new shapes.
    for (let quoin = y + 8; quoin < y + h - 12; quoin += 16) {
      rect(g, x, quoin, 8, 4, base[1]);
      rect(g, x + w - 8, quoin + 8, 8, 4, base[1]);
    }
    rect(g, x, y + h - 8, w, 4, base[2]);
  }
  function roof(g, p, x, y, width, rows) {
    // A roof is built one stair and one staggered tile at a time.
    for (let row = 0; row < rows; row++) {
      const inset = (rows - row - 1) * 8;
      const rx = x + inset, rw = width - inset * 2, ry = y + row * 4;
      if (rw <= 0) continue;
      rect(g, rx, ry, rw, 4, row % 4 === 0 ? p.roof[1] : p.roof[0]);
      rect(g, rx, ry, 4, 4, p.roof[1]);
      rect(g, rx + rw - 4, ry, 4, 4, p.roof[2]);
      if (row % 2) for (let tile = rx + 12 + (row % 4 === 1 ? 0 : 8); tile < rx + rw - 8; tile += 24) rect(g, tile, ry, 4, 4, p.roof[2]);
    }
    rect(g, x - 4, y + rows * 4, width + 8, 4, p.roof[2]);
    rect(g, x, y + rows * 4 + 4, width, 4, p.wood[2]);
    rect(g, x + 4, y + rows * 4 + 8, width - 8, 4, p.wall[2]);
    // Ridge cap and fascia nails: the two details every roof was missing.
    const ridgeInset = (rows - 1) * 8;
    if (width - ridgeInset * 2 > 24) {
      rect(g, x + ridgeInset, y - 8, width - ridgeInset * 2, 4, p.roof[1]);
      rect(g, x + ridgeInset - 4, y - 4, width - ridgeInset * 2 + 8, 4, p.roof[2]);
      for (let cap = x + ridgeInset + 8; cap < x + width - ridgeInset - 8; cap += 24) rect(g, cap, y - 4, 4, 4, p.roof[1]);
    }
    for (let nail = x + 8; nail < x + width - 8; nail += 32) rect(g, nail, y + rows * 4 + 4, 4, 4, p.wood[0]);
  }
  function windowFrame(g, p, x, y, width = 32, height = 32, shutters = false) {
    rect(g, x, y, width, height, p.wood[2]);
    rect(g, x + 4, y + 4, width - 8, height - 8, C.glass);
    rect(g, x + 4, y + 4, width - 8, 8, C.glassLight);
    rect(g, x + 4, y + height - 12, width - 8, 4, C.glassShade);
    rect(g, x + Math.floor(width / 8) * 4, y + 4, 4, height - 8, p.wood[1]);
    rect(g, x + 4, y + Math.floor(height / 8) * 4, width - 8, 4, p.wood[1]);
    rect(g, x - 4, y + height, width + 8, 4, p.wood[1]);
    rect(g, x, y + height + 4, width + 4, 4, p.wood[0]);
    // Two glass reflections read as a window instead of a flat pane.
    rect(g, x + 8, y + 8, 8, 4, C.glassLight);
    rect(g, x + 4, y + height - 16, 4, 4, C.glassLight);
    if (shutters) {
      for (const sx of [x - 12, x + width + 4]) {
        rect(g, sx, y + 4, 8, height - 4, p.roof[2]);
        for (let yy = 8; yy < height; yy += 8) rect(g, sx, y + yy, 8, 4, p.roof[0]);
      }
    }
  }
  function door(g, p, x = 104, y = 124) {
    rect(g, x - 4, y - 4, 40, 48, p.wood[2]);
    rect(g, x, y, 32, 44, p.wood[0]);
    rect(g, x + 4, y + 4, 24, 4, p.wood[1]);
    rect(g, x + 8, y + 4, 4, 40, p.wood[2]);
    rect(g, x + 20, y + 4, 4, 40, p.wood[2]);
    rect(g, x + 4, y + 8, 24, 12, C.glassShade);
    rect(g, x + 8, y + 8, 16, 8, C.glassLight);
    rect(g, x + 24, y + 28, 4, 4, C.gold);
    // Iron hinges on the door leaf.
    rect(g, x + 4, y + 12, 4, 4, C.glassShade);
    rect(g, x + 4, y + 32, 4, 4, C.glassShade);
    // Three steps below the leaf, relative to y so taller walls stay correct.
    rect(g, x - 4, y + 44, 40, 4, C.stoneLight);
    rect(g, x - 8, y + 48, 48, 4, C.stone);
    rect(g, x - 12, y + 52, 56, 4, C.stoneDark);
  }
  function chimney(g, p, x, y, height) {
    rect(g, x, y, 20, height, p.wood[0]);
    rect(g, x + 12, y, 8, height, p.wood[2]);
    for (let yy = y + 8; yy < y + height; yy += 12) rect(g, x, yy, 16, 4, p.wood[1]);
    rect(g, x - 4, y, 28, 4, p.wood[2]);
    rect(g, x - 4, y - 4, 24, 4, p.wood[1]);
    rect(g, x + 4, y - 16, 8, 8, "#e7e8d2");
    rect(g, x + 12, y - 28, 12, 8, "#e1e4cc");
  }
  function lantern(g, p, x, y) {
    rect(g, x, y, 4, 16, p.wood[2]); rect(g, x, y, 12, 4, p.wood[2]);
    rect(g, x + 4, y + 8, 12, 16, p.wood[0]); rect(g, x + 8, y + 12, 4, 8, C.gold);
    rect(g, x + 4, y + 24, 12, 4, p.wood[2]);
  }
  function flowerBox(g, p, x, y, width = 32) {
    rect(g, x, y, width, 12, p.wood[0]); rect(g, x, y, width, 4, p.wood[1]);
    for (let xx = x + 4; xx < x + width - 4; xx += 12) {
      rect(g, xx, y - 8, 4, 8, C.green); rect(g, xx - 4, y - 12, 12, 8, xx % 24 ? C.pink : C.gold);
      rect(g, xx, y - 12, 4, 4, C.cream);
    }
  }
  function screen(g, p, x, y, width = 48, kind = "code") {
    rect(g, x, y, width, 36, p.wood[2]); rect(g, x + 4, y + 4, width - 8, 24, "#657d75");
    rect(g, x + 4, y + 28, width - 8, 4, p.wood[1]);
    if (kind === "finance") {
      for (let i = 0; i < Math.floor((width - 16) / 12); i++) {
        const xx = x + 8 + i * 12, yy = y + 8 + (i % 3 === 1 ? 4 : 0);
        rect(g, xx + 4, yy - 4, 4, 24 - (i % 2) * 4, i % 2 ? "#c7957e" : "#adc093");
        rect(g, xx, yy + 4, 12, 8, i % 2 ? "#c7957e" : "#adc093");
      }
    } else {
      rect(g, x + 8, y + 8, 8, 4, "#d7d5b1"); rect(g, x + 20, y + 8, 12, 4, "#adc093");
      rect(g, x + 12, y + 16, 8, 4, "#a9c2b0"); rect(g, x + 24, y + 16, width - 36, 4, "#d0ba91");
      rect(g, x + 8, y + 24, 12, 4, "#adc093");
    }
  }
  function toolRack(g, p, x, y, width = 56) {
    rect(g, x, y, width, 40, p.wood[0]); rect(g, x + 4, y + 4, width - 8, 32, p.wall[1]);
    for (let xx = x + 8, index = 0; xx < x + width - 8; xx += 16, index++) {
      if (index % 3 === 0) {
        rect(g, xx, y + 8, 12, 4, C.stoneDark); rect(g, xx + 4, y + 12, 4, 20, p.wood[0]);
      } else if (index % 3 === 1) {
        rect(g, xx, y + 8, 4, 8, C.stoneDark); rect(g, xx + 8, y + 8, 4, 8, C.stoneDark);
        rect(g, xx, y + 16, 12, 4, C.stoneDark); rect(g, xx + 4, y + 20, 4, 12, C.stoneDark);
      } else {
        rect(g, xx + 4, y + 8, 4, 12, p.roof[2]); rect(g, xx + 4, y + 20, 4, 12, C.stoneDark);
      }
    }
    rect(g, x, y + 36, width, 4, p.wood[1]);
  }
  function blueprint(g, p, x, y, width = 56, height = 32) {
    rect(g, x, y, width, height, p.wood[2]); rect(g, x + 4, y + 4, width - 8, height - 8, "#86a5a0");
    rect(g, x + 12, y + 8, 4, Math.max(4, height - 16), "#d8dfc7"); rect(g, x + 12, y + 8, width - 28, 4, "#d8dfc7");
    rect(g, x + width - 20, y + 8, 4, Math.max(4, height - 16), "#d8dfc7"); rect(g, x + 12, y + height - 12, width - 28, 4, "#d8dfc7");
    rect(g, x + 24, y + 16, 4, Math.max(4, height - 24), "#b7cdba"); rect(g, x + 28, y + 16, Math.max(4, width - 48), 4, "#b7cdba");
  }
  function guitar(g, p, x, y) {
    rect(g, x + 12, y, 8, 8, p.wood[2]); rect(g, x + 12, y + 8, 4, 24, p.wood[1]);
    rect(g, x + 4, y + 28, 20, 8, p.roof[0]); rect(g, x + 8, y + 36, 12, 4, p.roof[0]);
    rect(g, x, y + 40, 28, 16, p.roof[0]); rect(g, x + 4, y + 56, 20, 4, p.roof[2]);
    rect(g, x + 12, y + 28, 4, 20, p.wood[1]); rect(g, x + 8, y + 44, 12, 4, p.wood[2]);
    rect(g, x + 4, y + 40, 4, 12, p.roof[1]); rect(g, x + 20, y + 44, 4, 8, p.roof[2]);
  }
  function speaker(g, p, x, y, small = false) {
    const height = small ? 28 : 40;
    rect(g, x, y, 28, height, p.wood[2]); rect(g, x + 4, y + 4, 20, height - 8, "#898b7b");
    rect(g, x + 8, y + 8, 12, 8, "#626f64"); rect(g, x + 12, y + 8, 4, 4, "#abb19b");
    if (!small) { rect(g, x + 8, y + 24, 12, 8, "#626f64"); rect(g, x + 12, y + 24, 4, 4, "#abb19b"); }
    rect(g, x + 4, y + height - 4, 20, 4, p.wood[0]);
  }
  // Clay shingles: a staggered tile per roof row, drawn in the roof's own
  // palette so the slope reads as courses rather than one flat block.
  function shingles(g, p, x, y, width, rows) {
    for (let row = 0; row < rows; row++) {
      const inset = (rows - row - 1) * 8;
      const rx = x + inset, rw = width - inset * 2, ry = y + row * 4;
      if (rw <= 16) continue;
      const offset = row % 2 ? 8 : 0;
      for (let tile = rx + 4 + offset; tile < rx + rw - 8; tile += 16) {
        rect(g, tile, ry, 12, 2, p.roof[1]);
        rect(g, tile + 12, ry, 4, 2, p.roof[2]);
      }
      rect(g, rx + 4, ry + 2, rw - 8, 2, row % 3 === 0 ? p.roof[0] : p.roof[2]);
    }
  }
  // Brick courses for plastered walls, kept inside the wall rectangle.
  function brickwork(g, p, x, y, width, height) {
    for (let row = 0; row < height - 12; row += 8) {
      const ry = y + 8 + row;
      const offset = (row / 8) % 2 ? 8 : 0;
      for (let bx = x + 8 + offset; bx < x + width - 12; bx += 16) {
        rect(g, bx, ry, 12, 4, row % 16 === 0 ? p.wall[2] : p.wall[1]);
        rect(g, bx, ry + 4, 12, 2, p.wall[2]);
      }
    }
  }
  // A raised timber stage for the beat workshop: deck, two step feet, valance.
  function stage(g, p, x, y, width, dressed = false) {
    rect(g, x, y + 26, width, 12, p.wood[0]);
    rect(g, x, y + 26, width, 4, p.wood[1]);
    for (let plank = x + 8; plank < x + width - 4; plank += 16) rect(g, plank, y + 30, 4, 8, p.wood[2]);
    rect(g, x, y + 38, width, 4, p.wood[2]);
    rect(g, x + 4, y + 38, 8, 8, p.wood[0]);
    rect(g, x + width - 12, y + 38, 8, 8, p.wood[0]);
    // Valance and its scalloped edge.
    rect(g, x, y + 22, width, 6, dressed ? "#7c6f88" : p.roof[0]);
    rect(g, x, y + 22, width, 2, dressed ? "#948aa0" : p.roof[1]);
    for (let scallop = x + 4; scallop < x + width - 4; scallop += 12) rect(g, scallop, y + 28, 8, 4, dressed ? "#6b6076" : p.roof[2]);
  }
  function bird(g, x, y, adult = false, facingLeft = false) {
    // A readable silhouette: tail, broad body, separate head, beak and two feet.
    const body = adult ? "#eee5c9" : "#d8bf80", light = adult ? "#faf0d7" : "#eee0a9";
    const head = facingLeft ? x : x + 16, beak = facingLeft ? x - 4 : x + 28;
    rect(g, x + 4, y + 12, 20, 16, body); rect(g, x + 8, y + 12, 12, 8, light);
    rect(g, head, y + 4, 12, 16, body); rect(g, head, y + 4, 12, 4, light);
    rect(g, head + (facingLeft ? 0 : 8), y + 8, 4, 4, "#746852"); rect(g, beak, y + 16, 4, 4, "#c69d62");
    rect(g, facingLeft ? x + 24 : x, y + 8, 4, 12, light);
    if (adult) rect(g, head + 4, y, 8, 4, "#bd9277");
    rect(g, x + 8, y + 28, 4, 4, "#ad8e5e"); rect(g, x + 20, y + 28, 4, 4, "#ad8e5e");
  }
  function fence(g, p, x, y, width) {
    rect(g, x, y + 8, width, 4, p.wood[1]); rect(g, x, y + 20, width, 4, p.wood[0]);
    for (let xx = x; xx <= x + width - 4; xx += 24) {
      rect(g, xx, y + 4, 4, 24, p.wood[0]); rect(g, xx, y, 4, 8, p.wood[1]);
    }
  }
  function crate(g, p, x, y, width = 28) {
    rect(g, x, y, width, 24, p.wood[0]); rect(g, x + 4, y + 4, width - 8, 16, p.wall[0]);
    rect(g, x, y, width, 4, p.wood[1]); rect(g, x, y + 12, width, 4, p.wood[1]);
    rect(g, x + 4, y, 4, 24, p.wood[2]); rect(g, x + width - 8, y, 4, 24, p.wood[2]);
  }
  // Reed thatch: a shallower pitch than the clay roofs, with ragged eaves.
  function reedRoof(g, p, x, y, width, rows) {
    for (let row = 0; row < rows; row++) {
      const inset = (rows - row - 1) * 4;
      const rx = x + inset, rw = width - inset * 2, ry = y + row * 4;
      if (rw <= 0) continue;
      rect(g, rx, ry, rw, 4, row % 3 === 2 ? p.roof[1] : p.roof[0]);
      rect(g, rx, ry, 4, 4, p.roof[1]);
      rect(g, rx + rw - 4, ry, 4, 4, p.roof[2]);
      if (row % 2) for (let tile = rx + 12; tile < rx + rw - 12; tile += 20) rect(g, tile, ry, 8, 2, p.roof[2]);
    }
    for (let tooth = x - 4; tooth <= x + width - 8; tooth += 12) rect(g, tooth, y + rows * 4, 8, 4, p.roof[1]);
    for (let tooth = x + 2; tooth <= x + width - 8; tooth += 24) rect(g, tooth, y + rows * 4 + 4, 8, 4, p.roof[2]);
    rect(g, x + width / 2 - 40, y - 8, 80, 8, p.roof[2]);
    rect(g, x + width / 2 - 32, y - 4, 64, 4, p.roof[1]);
  }
  // Stepped village pond in the same water colours as the meadow decorations.
  function pond(g, x, y, width, height) {
    rect(g, x - 12, y - 8, width + 24, height + 16, "#c6d2a4");
    rect(g, x - 8, y - 4, width + 16, height + 8, "#d2d7b4");
    rect(g, x - 4, y - 4, width + 8, height + 8, "#dcdcb8");
    rect(g, x, y, width, height, "#8fb5ae");
    rect(g, x + 4, y + 4, width - 8, height - 8, "#a6cbca");
    rect(g, x + 12, y + 12, width - 24, height - 24, "#b6d6d1");
    // Cut the four corners so the water reads as a pond, not a pool.
    for (const [cx, cy] of [[x, y], [x + width - 8, y], [x, y + height - 8], [x + width - 8, y + height - 8]]) {
      rect(g, cx, cy, 8, 8, "#dcdcb8");
    }
    // Ripples stay inside the water for narrow ponds as well.
    rect(g, x + 16, y + 10, 20, 4, "#d4e7d9");
    rect(g, x + 52, y + 18, 16, 4, "#d4e7d9");
    rect(g, x + 28, y + 26, 12, 4, "#cfe2d2");
  }
  function jetty(g, p, x, y, width) {
    for (let plank = x; plank < x + width; plank += 16) {
      rect(g, plank, y, 12, 12, plank % 32 ? p.wood[0] : p.wood[1]);
      rect(g, plank + 12, y + 4, 4, 8, p.wood[2]);
    }
    rect(g, x, y, width, 4, p.wood[1]);
    rect(g, x, y + 8, width, 4, p.wood[2]);
    rect(g, x + 4, y + 12, 4, 16, p.wood[2]);
    rect(g, x + width - 8, y + 12, 4, 16, p.wood[2]);
    // A short ladder gives the deck a reason to sit above the water.
    rect(g, x + width - 20, y + 12, 4, 16, p.wood[0]);
    rect(g, x + width - 20, y + 16, 12, 4, p.wood[0]);
  }
  function boat(g, p, x, y) {
    rect(g, x, y, 48, 8, p.wood[1]);
    rect(g, x + 4, y + 4, 40, 4, p.wood[2]);
    rect(g, x + 4, y - 4, 40, 4, "#dfe3d2");
    rect(g, x, y - 4, 4, 12, p.wood[0]);
    rect(g, x + 44, y - 4, 4, 12, p.wood[0]);
    rect(g, x + 16, y - 8, 16, 4, p.wood[0]);
    rect(g, x + 20, y - 12, 8, 4, "#c98a5c");
    rect(g, x + 52, y - 4, 4, 20, p.wood[2]);
  }
  function netRack(g, p, x, y, width) {
    rect(g, x, y, 8, 56, p.wood[0]);
    rect(g, x + width - 8, y, 8, 56, p.wood[0]);
    rect(g, x, y, width, 8, p.wood[1]);
    for (let i = 0; i < 4; i++) rect(g, x + 12 + i * 12, y + 8, 4, 36, C.cream);
    for (let j = 0; j < 4; j++) rect(g, x + 8, y + 12 + j * 8, width - 16, 4, C.cream);
    rect(g, x + 8, y + 48, width - 16, 4, p.wood[2]);
    for (let i = 0; i < 4; i++) rect(g, x + 12 + i * 12, y + 2, 8, 8, i % 2 ? "#d8bf80" : "#c98a5c");
  }
  // Straw texture: short dashes staggered across every thatch row.
  function thatchStreaks(g, p, x, y, width, rows) {
    for (let row = 0; row < rows; row++) {
      const inset = (rows - row - 1) * 4;
      const rx = x + inset + 12, rw = width - inset * 2 - 24, ry = y + row * 4;
      if (rw <= 32) continue;
      for (let i = 0; i < 4; i++) {
        const sx = rx + Math.floor((rw - 8) * (i + 1) / 5);
        rect(g, sx, ry, 8, 4, row % 2 ? p.roof[2] : p.roof[1]);
      }
    }
  }
  // A row of stone footings under a wall.
  function footings(g, x, y, width) {
    for (let stone = x + 4; stone < x + width - 8; stone += 24) {
      rect(g, stone, y, 16, 4, C.stoneLight);
      rect(g, stone, y + 4, 16, 4, C.stoneDark);
    }
  }
  // Two posts and a rail; balusters keep the porch from reading as a fence.
  function porchRail(g, p, x, y, width) {
    rect(g, x, y, width, 4, p.wood[1]);
    rect(g, x, y + 12, width, 4, p.wood[0]);
    for (let post = x + 8; post < x + width - 8; post += 16) rect(g, post, y + 4, 4, 8, p.wood[2]);
  }
  // Fish species are data, not bespoke drawings: a body profile in 2px rows,
  // a tail span, a palette and a pattern. Adding a species means adding a row
  // here, and the same renderer can draw it elsewhere later.
  // Fish species are data: per-column top/bottom outlines, a palette, a tail
  // midpoint and a pattern. Adding a species means adding one row here.
  const FISH = {
    carp: {
      name: "鲤鱼", body: "#c8a86a", back: "#a8854e", belly: "#e6d6a8", fin: "#b3905a", mark: "#8f7342",
      tailMid: 10, pattern: "scales",
      top: [null, 8, 7, 6, 5, 5, 4, 4, 4, 3, 3, 3, 3, 4, 4, 4, 5, 5, 6, 7, 8, null, null, null],
      bottom: [null, 12, 13, 14, 15, 15, 16, 16, 16, 17, 17, 17, 17, 16, 16, 16, 15, 15, 14, 13, 12, null, null, null],
    },
    crucian: {
      name: "鲫鱼", body: "#c9d2cc", back: "#9fb0ad", belly: "#e6eeea", fin: "#8fa5a2", mark: "#7d9492",
      tailMid: 11, pattern: "none",
      top: [null, 7, 6, 5, 4, 4, 3, 3, 3, 3, 3, 3, 4, 4, 4, 5, 5, 6, 6, 7, null, null, null, null],
      bottom: [null, 13, 14, 15, 16, 16, 17, 17, 17, 18, 18, 18, 17, 17, 17, 16, 16, 15, 15, 14, null, null, null, null],
    },
    trout: {
      name: "鳟鱼", body: "#b9c6b2", back: "#8fa383", belly: "#dfe7d4", fin: "#7d9270", mark: "#6b8059",
      tailMid: 10, pattern: "spots",
      top: [null, 8, 7, 6, 6, 5, 5, 5, 4, 4, 4, 4, 4, 5, 5, 5, 6, 6, 6, 7, 7, null, null, null],
      bottom: [null, 12, 13, 13, 14, 14, 15, 15, 15, 16, 16, 16, 16, 15, 15, 15, 14, 14, 14, 13, 13, null, null, null],
    },
  };
  // A fish hangs by the tail: one 1px column per outline step, so the
  // silhouette follows the species table instead of a rectangle block.
  function driedFish(g, x, y, species = "crucian") {
    const f = FISH[species] || FISH.crucian;
    rect(g, x + 10, y - 10, 1, 10, "#8a7a5c");
    rect(g, x + 9, y - 12, 3, 2, "#b9a06d");
    f.top.forEach((top, i) => {
      const bottom = f.bottom[i];
      if (top == null || bottom == null) return;
      rect(g, x + i, y + top, 1, bottom - top + 1, f.body);
      rect(g, x + i, y + top, 1, 2, f.back);
      rect(g, x + i, y + bottom - 1, 1, 2, f.belly);
    });
    // Forked tail: joint plus two swept lobes.
    const mid = f.tailMid;
    rect(g, x + 20, y + mid - 1, 1, 3, f.fin);
    rect(g, x + 21, y + mid - 3, 2, 3, f.fin);
    rect(g, x + 21, y + mid + 1, 2, 3, f.fin);
    rect(g, x + 23, y + mid - 4, 1, 2, f.fin);
    rect(g, x + 23, y + mid + 3, 1, 2, f.fin);
    // Dorsal, pectoral and pelvic fins keep the species silhouette distinct.
    rect(g, x + 8, y + f.top[10] - 1, 4, 1, f.fin);
    rect(g, x + 9, y + f.top[10] - 2, 2, 1, f.fin);
    rect(g, x + 5, y + f.bottom[8] - 1, 3, 1, f.fin);
    rect(g, x + 10, y + f.bottom[12] - 1, 2, 1, f.fin);
    // Head: eye pupil, highlight, gill arc and mouth notch.
    rect(g, x + 3, y + 8, 2, 2, "#2f3f3c");
    rect(g, x + 3, y + 7, 2, 1, f.belly);
    rect(g, x + 5, y + 9, 1, 3, f.mark);
    rect(g, x + 2, y + 10, 1, 1, f.mark);
    // Species pattern.
    if (f.pattern === "scales") {
      for (let i = 0; i < 3; i++) {
        rect(g, x + 7 + i * 3, y + 9, 2, 1, f.mark);
        rect(g, x + 8 + i * 3, y + 12, 2, 1, f.mark);
      }
    } else if (f.pattern === "spots") {
      for (const [sx, sy] of [[7, 7], [10, 8], [8, 12], [13, 11]]) rect(g, x + sx, y + sy, 2, 2, f.mark);
    } else {
      rect(g, x + 7, y + 7, 4, 1, "#f2f4e6");
      rect(g, x + 8, y + 10, 3, 1, "#eef1e6");
    }
    // Fin rays along the tail joint.
    rect(g, x + 20, y + mid, 1, 1, f.mark);
  }
  function fishSign(g, p, x, y, width = 56) {
    // A bracketed board with a fish silhouette and two water dashes.
    rect(g, x, y, width, 16, p.wood[2]);
    rect(g, x + 4, y + 4, width - 8, 8, p.wall[1]);
    rect(g, x, y, width, 4, p.wood[1]);
    rect(g, x, y + 12, width, 4, p.wood[0]);
    const fx = x + 6;
    rect(g, fx + 4, y + 4, 12, 8, "#3f6e77");
    rect(g, fx + 16, y, 4, 4, "#3f6e77");
    rect(g, fx + 16, y + 12, 4, 4, "#3f6e77");
    rect(g, fx + 20, y + 4, 4, 8, "#3f6e77");
    rect(g, fx + 8, y + 4, 4, 4, C.cream);
    // A hook mark and two water dashes finish the sign.
    rect(g, x + width - 20, y - 4, 4, 12, p.wood[2]);
    rect(g, x + width - 16, y + 4, 8, 4, "#7fa9ab");
    // Iron brackets tie the board to the wall.
    rect(g, x - 4, y - 4, 4, 24, p.wood[2]);
    rect(g, x + width, y - 4, 4, 24, p.wood[2]);
  }
  function house(agent) {
    const { style, p, motif, variant } = resolve(agent), g = group(`house-${style}`);
    // The angling hut draws its own footing shadow: its base sits 16px lower
    // than the other styles, so the shared line would float above the plinth.
    if (style !== "angling") {
      rect(g, 28, 176, 184, 8, C.shadow); rect(g, 40, 184, 160, 4, C.shadow);
    }
    if (style === "architecture") {
      // A stone research tower and an offset timber wing, with a bridge between.
      wall(g, p, 144, 48, 68, 120, true); roof(g, p, 136, 16, 84, 7);
      wall(g, p, 28, 100, 124, 68); roof(g, p, 16, 56, 148, 10);
      rect(g, 144, 88, 68, 8, p.wood[0]); rect(g, 144, 88, 64, 4, p.wood[1]);
      windowFrame(g, p, 160, 56, 28, 28); windowFrame(g, p, 160, 108, 28, 32);
      blueprint(g, p, 44, 112, 48, 32); door(g, p);
      rect(g, 204, 72, 8, 52, p.wood[0]); rect(g, 204, 76, 4, 40, p.wood[1]);
      for (let yy = 80; yy < 120; yy += 12) rect(g, 196, yy, 16, 4, p.wood[2]);
    } else if (style === "workshop") {
      // 280-wide footprint matching the angling hut: x 20..300 on the 4px grid.
      // Roof y16..80, wall y80..176, yard y180..236. The shared plot transform
      // offsets houses by (40,18), so this branch cancels it.
      const local = group("workshop-house");
      local.setAttribute("transform", "translate(-40 -18)");
      g.append(local);

      chimney(local, p, 236, 40, 52);
      for (let joint = 48; joint < 84; joint += 8) rect(local, 240, joint, 12, 2, p.wood[1]);
      wall(local, p, 36, 80, 248, 96);
      footings(local, 36, 176, 248);
      roof(local, p, 24, 16, 272, 16);
      shingles(local, p, 24, 16, 272, 16);
      brickwork(local, p, 36, 80, 248, 80);

      // Ridge cap: sits on the narrow top course, never wider than that course
      // plus a small overhang, so nothing floats above the peak.
      // roof(local,p,24,16,272,16): top course is x144..176 (32 wide).
      rect(local, 140, 8, 40, 4, p.roof[2]);
      rect(local, 144, 4, 32, 4, p.roof[1]);
      for (let cap = 148; cap < 172; cap += 8) rect(local, cap, 4, 4, 4, p.roof[2]);
      for (let tile = 48; tile < 272; tile += 24) rect(local, tile, 72, 8, 4, p.roof[2]);
      for (let nail = 40; nail < 280; nail += 32) rect(local, nail, 80, 4, 4, p.wood[0]);
      // Gable vent centred on the wall, below the roof's top course.
      rect(local, 140, 56, 32, 20, p.wood[2]);
      rect(local, 144, 60, 24, 12, p.wall[2]);
      for (let slat = 64; slat < 72; slat += 4) rect(local, 148, slat, 16, 2, p.wood[1]);

      // Corner boards and a plank wainscot, same construction as the hut.
      rect(local, 36, 80, 4, 96, p.wood[1]);
      rect(local, 40, 80, 4, 96, p.wood[0]);
      rect(local, 280, 80, 4, 96, p.wood[1]);
      rect(local, 276, 80, 4, 96, p.wood[0]);
      rect(local, 36, 160, 248, 16, p.wood[0]);
      rect(local, 36, 160, 248, 4, p.wood[1]);
      for (let plank = 44; plank < 284; plank += 24) rect(local, plank, 160, 4, 16, p.wood[2]);
      for (let bolt = 52; bolt < 284; bolt += 24) rect(local, bolt, 164, 4, 4, p.wood[1]);

      // Four windows with shutters, plus glass highlights.
      windowFrame(local, p, 52, 88, 32, 28, true);
      windowFrame(local, p, 236, 88, 32, 28, true);
      windowFrame(local, p, 52, 124, 32, 32);
      windowFrame(local, p, 236, 124, 32, 32);
      for (const [wx, wy] of [[52, 88], [236, 88], [52, 124], [236, 124]]) {
        rect(local, wx + 8, wy + 8, 8, 4, C.glassLight);
        rect(local, wx + 8, wy + 16, 4, 4, C.glassLight);
      }
      for (const sx of [40, 88, 228, 272]) {
        for (let slat = 92; slat < 112; slat += 8) rect(local, sx, slat, 8, 4, p.roof[2]);
      }

      // Porch with a timber awning; the code display hangs beside the door.
      rect(local, 120, 108, 80, 8, p.wood[2]);
      rect(local, 120, 108, 80, 4, p.wood[1]);
      for (let rafter = 124; rafter < 196; rafter += 12) rect(local, rafter, 112, 4, 4, p.wood[2]);
      rect(local, 116, 112, 88, 4, p.wood[1]);
      rect(local, 120, 116, 8, 60, p.wood[0]);
      rect(local, 192, 116, 8, 60, p.wood[0]);
      rect(local, 120, 116, 4, 60, p.wood[1]);
      rect(local, 192, 116, 4, 60, p.wood[1]);
      rect(local, 120, 172, 16, 4, C.stone);
      rect(local, 188, 172, 16, 4, C.stone);
      door(local, p, 144, 124);

      // The workshop facade keeps its real code screen and tool rack.
      screen(local, p, 60, 108, 48, "code");
      toolRack(local, p, 208, 108, 44);

      // A signboard above the porch reads as the shop name.
      rect(local, 128, 96, 64, 12, p.wood[2]);
      rect(local, 132, 100, 56, 4, p.wall[1]);
      rect(local, 136, 100, 16, 4, p.roof[0]);
      rect(local, 156, 100, 24, 4, p.wall[2]);

      // Hanging lamp and a coiled cable by the door.
      lantern(local, p, 204, 116);
      rect(local, 208, 148, 16, 4, "#5c6b63");
      rect(local, 212, 152, 8, 8, "#5c6b63");
      rect(local, 214, 156, 4, 4, "#8a9a74");
      if (motif === "music") {
        // A small wall-mounted speaker grille for the beat workshop.
        rect(local, 244, 148, 20, 20, p.wood[2]);
        rect(local, 248, 152, 12, 12, "#626f64");
        rect(local, 250, 154, 8, 4, "#abb19b");
      }
    } else if (style === "farm") {
      wall(g, p, 28, 96, 140, 72); roof(g, p, 16, 48, 164, 11);
      windowFrame(g, p, 44, 112, 32, 28, true); door(g, p);
      // The adjoining nesting loft stays low and visibly belongs to the house.
      wall(g, p, 176, 128, 40, 40); roof(g, p, 168, 108, 56, 4);
      rect(g, 184, 140, 24, 20, p.wood[2]); rect(g, 188, 152, 16, 8, C.gold);
      rect(g, 192, 144, 8, 8, C.cream); rect(g, 176, 164, 44, 4, p.wood[1]);
      rect(g, 92, 84, 28, 8, p.wood[2]); rect(g, 96, 84, 20, 4, p.wall[1]);
      for (let x = 40; x < 84; x += 12) rect(g, x, 156, 8, 4, p.wall[1]);
    } else if (style === "observatory") {
      wall(g, p, 32, 112, 176, 56); roof(g, p, 20, 92, 200, 4);
      wall(g, p, 84, 64, 76, 48);
      // A stepped copper dome, deliberately built from rectangles.
      for (const [x, y, w] of [[108, 20, 28], [96, 24, 52], [88, 32, 68], [80, 40, 84], [76, 48, 92], [76, 56, 92]]) {
        rect(g, x, y, w, 8, p.roof[0]); rect(g, x, y, w - 8, 4, p.roof[1]); rect(g, x + w - 12, y + 4, 12, 4, p.roof[2]);
      }
      rect(g, 116, 24, 8, 40, p.roof[2]); rect(g, 116, 24, 4, 32, p.roof[1]);
      windowFrame(g, p, 108, 72, 28, 28); windowFrame(g, p, 44, 120, 32, 28); windowFrame(g, p, 168, 120, 28, 28); door(g, p);
      rect(g, 192, 52, 4, 36, p.wood[2]); rect(g, 184, 84, 20, 4, p.wood[2]);
      rect(g, 180, 40, 32, 12, p.roof[2]); rect(g, 180, 40, 28, 4, p.roof[1]); rect(g, 208, 36, 12, 20, p.roof[0]); rect(g, 216, 40, 4, 12, C.glassLight);
    } else if (style === "command") {
      wall(g, p, 32, 48, 52, 120, true); roof(g, p, 24, 24, 68, 5);
      windowFrame(g, p, 44, 60, 28, 32); rect(g, 48, 4, 4, 24, p.wood[2]);
      rect(g, 52, 4, 24, 12, p.roof[1]); rect(g, 52, 16, 16, 4, p.roof[0]);
      wall(g, p, 76, 108, 132, 60); roof(g, p, 64, 64, 156, 10);
      windowFrame(g, p, 164, 120, 28, 28); door(g, p);
      rect(g, 92, 100, 56, 12, p.wood[2]); rect(g, 96, 100, 48, 8, C.cream);
      rect(g, 104, 104, 8, 4, p.roof[0]); rect(g, 120, 104, 16, 4, C.green);
      lantern(g, p, 84, 120);
    } else if (style === "music") {
      wall(g, p, 28, 104, 184, 64); roof(g, p, 16, 48, 208, 13);
      windowFrame(g, p, 40, 116, 32, 32, true); windowFrame(g, p, 172, 116, 28, 32); door(g, p);
      rect(g, 84, 100, 72, 12, p.wood[2]); rect(g, 88, 100, 64, 8, C.cream);
      for (const xx of [92, 104, 116, 128, 140]) rect(g, xx, 100, 4, 4, p.wood[2]);
      wall(g, p, 100, 64, 40, 28); roof(g, p, 92, 40, 56, 5); windowFrame(g, p, 108, 68, 24, 20);
    } else if (style === "finance") {
      wall(g, p, 32, 104, 176, 64, true); roof(g, p, 20, 60, 200, 10);
      for (const x of [40, 184]) { rect(g, x, 116, 8, 52, p.wood[0]); rect(g, x, 116, 4, 48, p.wood[1]); }
      windowFrame(g, p, 56, 120, 28, 28); windowFrame(g, p, 156, 120, 28, 28); door(g, p);
      rect(g, 92, 92, 56, 16, p.wood[2]); rect(g, 96, 96, 48, 8, C.cream);
      rect(g, 108, 96, 8, 8, C.gold); rect(g, 120, 96, 12, 4, p.roof[0]);
    } else if (style === "school") {
      wall(g, p, 28, 104, 184, 64); roof(g, p, 16, 52, 208, 12);
      windowFrame(g, p, 40, 116, 36, 32); windowFrame(g, p, 164, 116, 36, 32); door(g, p);
      wall(g, p, 100, 32, 40, 36); roof(g, p, 92, 12, 56, 4);
      rect(g, 108, 40, 24, 20, p.wood[2]); rect(g, 112, 44, 16, 12, C.gold); rect(g, 108, 56, 24, 4, p.roof[1]);
      rect(g, 120, 60, 4, 8, p.wood[0]);
      rect(g, 88, 100, 64, 12, p.wood[2]); rect(g, 92, 100, 56, 8, C.cream);
      rect(g, 112, 100, 4, 8, p.roof[0]); rect(g, 124, 100, 4, 8, C.green);
    } else if (style === "angling") {
      // 280-wide footprint on the shared 4px grid: x 20..300, so every
      // neighbour keeps at least 20px of grass. The plot transform offsets
      // each house by (40,18); this branch cancels it and draws in absolute
      // plot coordinates.
      //
      // The plot is 280 tall, so the thatch gets 20 courses (80px) instead of
      // the old 16, and the wall moves down 16px to pay for it. Everything
      // below the roof is therefore expressed relative to WALL_TOP rather than
      // as fixed y values.
      const local = group("angling-house");
      local.setAttribute("transform", "translate(-40 -18)");
      g.append(local);

      const WALL_TOP = 96;          // was 80; roof now ends here instead of 80
      const WALL_H = 96;
      const WALL_BOTTOM = WALL_TOP + WALL_H;   // 192
      const ROOF_ROWS = 20;
      const RIDGE_Y = 16 + ROOF_ROWS * 4;      // 96, bottom course of the thatch
      // reedRoof paints 4px past each flank for its ragged eaves, so the roof
      // body starts at 24 to keep the whole silhouette inside x 20..300.
      reedRoof(local, p, 24, 16, 272, ROOF_ROWS);
      thatchStreaks(local, p, 24, 16, 272, ROOF_ROWS);

      wall(local, p, 36, WALL_TOP, 248, WALL_H);
      footings(local, 36, WALL_BOTTOM, 248);
      // The stack is drawn over the thatch so it reads as a chimney on the roof.
      chimney(local, p, 228, 52, 52);
      for (let joint = 60; joint < 96; joint += 8) rect(local, 232, joint, 12, 2, p.wood[1]);

      // Ridge stitching sits on the narrow top course. With 20 rows the top row of
      // reedRoof(24,16,272,20) is x100..220 (120 wide), so the cap stays inside it.
      rect(local, 96, 8, 128, 4, p.roof[2]);
      rect(local, 100, 4, 120, 4, p.roof[1]);
      for (let stitch = 108; stitch < 212; stitch += 8) rect(local, stitch, 12, 4, 4, p.roof[2]);
      // Eave teeth and loose straw sit on the bottom course, which moved down.
      for (let tooth = 28; tooth < 280; tooth += 28) rect(local, tooth, RIDGE_Y - 4, 8, 4, p.roof[1]);
      for (let tuft = 36; tuft < 280; tuft += 20) rect(local, tuft, RIDGE_Y, 8, 4, p.roof[1]);
      for (let dash = 48; dash < 276; dash += 32) rect(local, dash, RIDGE_Y + 4, 12, 4, p.wall[1]);

      // Corner boards frame the plaster between the roof and the wainscot.
      rect(local, 36, WALL_TOP, 4, WALL_H, p.wood[1]);
      rect(local, 40, WALL_TOP, 4, WALL_H, p.wood[0]);
      rect(local, 280, WALL_TOP, 4, WALL_H, p.wood[1]);
      rect(local, 276, WALL_TOP, 4, WALL_H, p.wood[0]);

      // Plank wainscot along the base keeps the plaster from reading generic.
      const WAINSCOT_Y = WALL_BOTTOM - 16;
      rect(local, 36, WAINSCOT_Y, 248, 16, p.wood[0]);
      rect(local, 36, WAINSCOT_Y, 248, 4, p.wood[1]);
      for (let plank = 44; plank < 284; plank += 24) rect(local, plank, WAINSCOT_Y, 4, 16, p.wood[2]);
      for (let bolt = 52; bolt < 284; bolt += 24) rect(local, bolt, WAINSCOT_Y + 4, 4, 4, p.wood[1]);

      // Windows follow the wall: the upper pair sits just under the eave, the
      // lower pair level with the wainscot.
      const W_UP = WALL_TOP + 8;            // 104
      const W_LOW = W_UP + 40;              // 144
      windowFrame(local, p, 52, W_UP, 32, 28, true);
      windowFrame(local, p, 236, W_UP, 32, 28, true);
      windowFrame(local, p, 52, W_LOW, 32, 28);
      windowFrame(local, p, 236, W_LOW, 32, 28);
      for (const [wx, wy] of [[52, W_UP], [236, W_UP], [52, W_LOW], [236, W_LOW]]) {
        rect(local, wx + 8, wy + 8, 8, 4, C.glassLight);
        rect(local, wx + 8, wy + 16, 4, 4, C.glassLight);
      }
      // Shutter slats on the two upper windows.
      for (const sx of [40, 88, 228, 272]) {
        for (let slat = W_UP + 4; slat < W_UP + 24; slat += 8) rect(local, sx, slat, 8, 4, p.roof[2]);
      }

      // Porch: a thatch awning on two posts, with the sign on its gable.
      const AWNING_Y = WALL_TOP + 28;       // 124
      rect(local, 120, AWNING_Y, 80, 8, p.roof[1]);
      rect(local, 120, AWNING_Y, 80, 4, p.roof[2]);
      for (let tile = 124; tile < 200; tile += 16) rect(local, tile, AWNING_Y + 4, 8, 2, p.roof[2]);
      rect(local, 116, AWNING_Y + 4, 88, 4, p.wood[1]);
      rect(local, 120, AWNING_Y + 8, 8, 60, p.wood[0]);
      rect(local, 192, AWNING_Y + 8, 8, 60, p.wood[0]);
      rect(local, 120, AWNING_Y + 8, 4, 60, p.wood[1]);
      rect(local, 192, AWNING_Y + 8, 4, 60, p.wood[1]);
      rect(local, 120, AWNING_Y + 16, 4, 4, p.wood[2]);
      rect(local, 192, AWNING_Y + 16, 4, 4, p.wood[2]);
      rect(local, 116, WALL_BOTTOM - 4, 16, 4, C.stone);
      rect(local, 188, WALL_BOTTOM - 4, 16, 4, C.stone);

      const DOOR_Y = WALL_TOP + 44;         // 140
      door(local, p, 144, DOOR_Y);
      fishSign(local, p, 128, WALL_TOP + 4, 64);
      lantern(local, p, 204, AWNING_Y + 8);
      // A bait barrel and a tackle crate flank the door in warm timber tones.
      const BARREL_Y = WALL_BOTTOM - 44;    // 148
      rect(local, 88, BARREL_Y, 24, 24, "#b99d6f");
      rect(local, 88, BARREL_Y, 24, 4, "#cfb587");
      rect(local, 88, BARREL_Y + 8, 24, 4, "#8f7a55");
      rect(local, 88, BARREL_Y + 16, 24, 4, "#8f7a55");
      rect(local, 92, BARREL_Y + 4, 16, 4, "#a98f63");
      rect(local, 92, BARREL_Y + 20, 16, 4, "#a98f63");
      const CRATE_Y = WALL_BOTTOM - 40;     // 152
      rect(local, 208, CRATE_Y, 24, 20, "#b99d6f");
      rect(local, 208, CRATE_Y, 24, 4, "#cfb587");
      rect(local, 208, CRATE_Y + 12, 24, 4, "#8f7a55");
      rect(local, 212, CRATE_Y, 4, 20, "#8f7a55");
      rect(local, 224, CRATE_Y, 4, 20, "#8f7a55");
      // Footing shadow, now that the plinth sits at WALL_BOTTOM.
      rect(local, 28, WALL_BOTTOM + 4, 264, 8, C.shadow);
      rect(local, 40, WALL_BOTTOM + 12, 240, 4, C.shadow);
    } else {
      chimney(g, p, 180, 40, 44);
      wall(g, p, 28, 104, 184, 64); roof(g, p, 16, 48, 208, 13);
      windowFrame(g, p, 44, 116, 32, 28, true); windowFrame(g, p, 168, 116, 28, 28, true); door(g, p);
      wall(g, p, 100, 68, 40, 28); roof(g, p, 92, 44, 56, 5); windowFrame(g, p, 108, 72, 24, 20);
      flowerBox(g, p, 44, 156); flowerBox(g, p, 168, 156, 28);
    }
    // Small structural variations keep neighbors distinct without changing style.
    if (variant % 2 === 1 && !["farm", "command", "workshop", "angling"].includes(style)) lantern(g, p, 140, 124);
    // The two rebuilt houses are drawn at 280 then trimmed to 0.81, so they read
    // slightly narrower than the original 208-wide homes instead of towering
    // over them. Keep this in sync with squeezeX's snap grid below.
    if (style === "angling" || style === "workshop") squeezeX(g, 0.81);
    return g;
  }

  function yard(agent) {
    const { style, p, motif } = resolve(agent), g = group(`yard-${style}-${motif}`);
    // Keep x128..196 clear in front of the central doorstep and resident sprite.
    if (style === "workshop") {
      // 280-wide yard. The stage fills x 20..120, the gear wall x 200..300, and
      // x 124..196 stays clear for the resident sprite (56x70, centred at 160).
      stage(g, p, 20, 182, 100, motif === "music");
      if (motif === "music") {
        // Backdrop cloth with folds, then the act on the deck.
        rect(g, 20, 182, 100, 24, "#7c6f88");
        rect(g, 20, 182, 100, 4, "#948aa0");
        for (let fold = 24; fold < 116; fold += 8) rect(g, fold, 186, 4, 16, "#6b6076");
        for (let tie = 28; tie < 116; tie += 16) rect(g, tie, 200, 4, 4, "#5a5164");
        speaker(g, p, 24, 196);
        speaker(g, p, 56, 196, true);
        guitar(g, p, 84, 164);
        rect(g, 48, 182, 4, 14, "#5c6b63"); rect(g, 44, 178, 12, 4, "#8a9a74");
        // Cable coil and a spare amp head beside the stage.
        rect(g, 20, 214, 12, 12, "#5c6b63");
        rect(g, 24, 218, 4, 4, "#8a9a74");
        rect(g, 36, 214, 20, 12, "#898b7b");
        rect(g, 36, 214, 20, 4, "#a3a596");
        for (let knob = 40; knob < 54; knob += 4) rect(g, knob, 220, 2, 2, "#626f64");
        // Poster board on the right: three pinned flyers.
        rect(g, 200, 184, 60, 44, p.wood[0]);
        rect(g, 204, 188, 52, 36, p.wall[1]);
        for (const [px, py, pw, ph] of [[208, 192, 20, 12], [232, 192, 20, 12], [208, 208, 44, 12]]) {
          rect(g, px, py, pw, ph, "#f2ecd6");
          rect(g, px, py, pw, 2, "#d9d2b4");
          rect(g, px + 2, py + 4, pw - 4, 2, "#8a9a74");
          rect(g, px + 2, py + 8, pw - 8, 2, "#b7bdd0");
          rect(g, px + pw / 2 - 1, py - 2, 2, 2, "#c96b5a");
        }
        // Record crate and a stacked pair of speakers under the board.
        crate(g, p, 204, 210, 32);
        for (let sleeve = 208; sleeve < 232; sleeve += 4) rect(g, sleeve, 206, 2, 4, sleeve % 8 ? "#c2a97a" : "#b0a0c0");
        crate(g, p, 244, 210, 28);
        crate(g, p, 278, 214, 20);
      } else {
        toolRack(g, p, 24, 196, 84);
        crate(g, p, 28, 210, 32); crate(g, p, 72, 214, 24);
        crate(g, p, 216, 210, 32); crate(g, p, 260, 214, 24);
        rect(g, 268, 202, 8, 8, C.stoneLight);
      }
    } else if (style === "architecture") {
      rect(g, 28, 196, 92, 8, p.wood[0]); rect(g, 32, 204, 4, 28, p.wood[2]); rect(g, 108, 204, 4, 28, p.wood[2]);
      blueprint(g, p, 32, 176, 56, 24);
      // A tiny architectural model rises beside the laid-out drawings.
      rect(g, 92, 184, 12, 12, C.stone); rect(g, 100, 172, 12, 24, C.stoneLight); rect(g, 96, 168, 20, 4, p.roof[0]);
      rect(g, 104, 176, 4, 8, C.glassShade);
      if (motif === "finance") {
        rect(g, 220, 208, 4, 24, p.wood[2]); rect(g, 284, 208, 4, 24, p.wood[2]); screen(g, p, 212, 176, 84, "finance");
      } else { blueprint(g, p, 220, 176, 72, 40); rect(g, 224, 216, 4, 16, p.wood[0]); rect(g, 280, 216, 4, 16, p.wood[0]); }
    } else if (style === "farm" || motif === "poultry") {
      // The roofed run, water and feed remain readable around three individual birds.
      rect(g, 208, 192, 100, 40, "#d9cc9e"); rect(g, 212, 192, 92, 4, "#e7d9ae");
      rect(g, 240, 168, 4, 60, p.wood[0]); rect(g, 300, 168, 4, 60, p.wood[0]);
      for (let row = 0; row < 4; row++) {
        rect(g, 236 - row * 4, 152 + row * 4, 72 + row * 4, 4, row % 2 ? p.roof[0] : p.roof[1]);
      }
      rect(g, 224, 168, 84, 4, p.roof[2]);
      rect(g, 36, 188, 24, 28, "#d2bf8e"); rect(g, 40, 184, 16, 4, "#c2aa77"); rect(g, 40, 196, 16, 4, "#b5a06f");
      rect(g, 76, 212, 40, 12, "#a1afa0"); rect(g, 80, 212, 32, 4, "#bdd4c5"); rect(g, 80, 224, 4, 4, p.wood[2]); rect(g, 108, 224, 4, 4, p.wood[2]);
      bird(g, 72, 176, true, true); bird(g, 212, 196, false, false); bird(g, 268, 192, false, true);
      fence(g, p, 208, 212, 100); fence(g, p, 24, 212, 36);
    } else if (style === "music" || motif === "music") {
      rect(g, 24, 224, 96, 8, p.wood[0]); rect(g, 24, 224, 96, 4, p.wood[1]);
      speaker(g, p, 28, 184); speaker(g, p, 64, 196, true); guitar(g, p, 92, 164);
      rect(g, 220, 196, 72, 8, p.wood[1]); rect(g, 228, 204, 4, 24, p.wood[2]); rect(g, 280, 204, 4, 24, p.wood[2]);
      flowerBox(g, p, 232, 188, 48); fence(g, p, 212, 212, 88);
    } else if (style === "finance" || motif === "finance") {
      screen(g, p, 24, 176, 88, "finance"); rect(g, 28, 212, 4, 20, p.wood[0]); rect(g, 100, 212, 4, 20, p.wood[0]);
      crate(g, p, 216, 208, 36); rect(g, 224, 200, 20, 8, C.gold); rect(g, 228, 196, 12, 4, C.cream);
      flowerBox(g, p, 264, 208, 32); fence(g, p, 208, 212, 92);
    } else if (style === "school" || motif === "books") {
      rect(g, 24, 180, 88, 48, p.wood[0]); rect(g, 28, 184, 80, 36, p.wood[2]);
      for (let i = 0; i < 8; i++) { rect(g, 32 + i * 8, 188 + i % 2 * 4, 4, 24 - i % 2 * 4, [p.roof[1], C.greenLight, C.pink, C.stoneLight][i % 4]); }
      rect(g, 24, 216, 88, 4, p.wood[1]); rect(g, 216, 204, 88, 8, p.wood[1]); rect(g, 224, 212, 4, 20, p.wood[2]); rect(g, 292, 212, 4, 20, p.wood[2]);
      rect(g, 236, 196, 40, 8, C.cream); rect(g, 252, 196, 4, 8, p.roof[0]);
    } else if (style === "observatory") {
      rect(g, 52, 180, 4, 40, p.wood[2]); rect(g, 40, 220, 28, 4, p.wood[2]);
      rect(g, 28, 168, 48, 16, p.roof[0]); rect(g, 28, 168, 44, 4, p.roof[1]); rect(g, 72, 164, 12, 24, p.roof[2]); rect(g, 80, 168, 4, 16, C.glassLight);
      blueprint(g, p, 220, 180, 72, 36); rect(g, 224, 216, 4, 16, p.wood[0]); rect(g, 280, 216, 4, 16, p.wood[0]);
      fence(g, p, 24, 212, 84);
    } else if (style === "command") {
      rect(g, 24, 176, 88, 40, p.wood[0]); rect(g, 28, 180, 80, 32, C.cream);
      for (let row = 0; row < 3; row++) { rect(g, 32, 184 + row * 8, 4, 4, C.green); rect(g, 44, 184 + row * 8, 52 - row * 8, 4, "#b9b794"); }
      rect(g, 32, 216, 4, 16, p.wood[2]); rect(g, 100, 216, 4, 16, p.wood[2]);
      crate(g, p, 212, 208, 32); flowerBox(g, p, 260, 208, 40); fence(g, p, 208, 212, 96);
    } else if (style === "angling") {
      // Yard spans x 20..300 and starts under the plinth at y200 (the house grew
      // 16px taller). The drying rack fills the left third, the pond and jetty
      // the right, and x 132..188 stays open for the resident sprite.
      const RACK_Y = 200;
      rect(g, 20, RACK_Y, 8, 52, p.wood[0]);
      rect(g, 112, RACK_Y, 8, 52, p.wood[0]);
      rect(g, 20, RACK_Y, 100, 8, p.wood[1]);
      rect(g, 20, RACK_Y + 4, 100, 4, p.wood[2]);
      for (const [fx, species] of [[26, "carp"], [52, "crucian"], [78, "trout"]]) driedFish(g, fx, RACK_Y + 14, species);
      // Cleats and a rope coil make the rack read as built, not drawn.
      for (const [cx, cy] of [[20, RACK_Y + 24], [112, RACK_Y + 24], [20, RACK_Y + 42], [112, RACK_Y + 42]]) rect(g, cx, cy, 8, 4, p.wood[2]);
      rect(g, 56, RACK_Y + 2, 12, 4, "#d8bf80");
      rect(g, 56, RACK_Y + 6, 4, 4, "#b9a06d");
      // A wooden pail waits under the rack, warm-toned so it reads as gear.
      rect(g, 86, RACK_Y + 36, 12, 12, "#b99d6f");
      rect(g, 86, RACK_Y + 36, 12, 4, "#cfb587");
      rect(g, 86, RACK_Y + 44, 12, 4, "#8f7a55");
      rect(g, 86, RACK_Y + 40, 12, 2, "#a98f63");
      rect(g, 88, RACK_Y + 32, 8, 4, "#8f7a55");
      // Pond and jetty sit right of the doorway lane. The lane is x132..188 before
      // the squeeze; after 0.81 trimming it ends near x183, so the bank starts at
      // 208 to stay clear while keeping the water inside the plot.
      const POND_Y = 208;
      pond(g, 208, POND_Y, 72, 36);
      for (const [sx, sy] of [[220, POND_Y - 8], [244, POND_Y - 8], [264, POND_Y - 8]]) {
        rect(g, sx, sy, 16, 4, "#c8cfa6");
        rect(g, sx, sy + 4, 16, 4, "#b3c397");
      }
      for (const [sx, sy] of [[212, POND_Y + 36], [256, POND_Y + 36]]) {
        rect(g, sx, sy, 16, 4, "#b3c397");
        rect(g, sx, sy - 4, 16, 4, "#c8cfa6");
      }
      for (const [px, py] of [[218, POND_Y + 16], [238, POND_Y + 32], [264, POND_Y + 8]]) {
        rect(g, px, py, 8, 4, "#8fb06a");
        rect(g, px, py, 4, 4, "#a6c47e");
      }
      jetty(g, p, 200, POND_Y + 8, 48);
      boat(g, p, 214, POND_Y + 20);
      rect(g, 244, POND_Y + 12, 12, 4, p.wood[0]);
      rect(g, 244, POND_Y + 16, 4, 4, p.wood[2]);
      // Reeds mark the bank, kept out of the lane the resident walks through.
      for (const [rx, ry] of [[204, POND_Y - 4], [278, POND_Y - 4], [278, POND_Y + 20]]) {
        rect(g, rx, ry, 4, 20, "#849d70"); rect(g, rx + 8, ry + 4, 4, 16, "#a1b480");
        rect(g, rx + 6, ry - 4, 8, 8, "#a58f64");
      }
    } else if (motif === "hiking") {
      // A trailhead below the windows: pack and boots on the left, signpost
      // and cairn on the right, keeping the front door path clear.
      rect(g, 32, 190, 20, 8, p.roof[1]);
      rect(g, 28, 198, 40, 32, p.roof[0]);
      rect(g, 28, 198, 40, 4, p.roof[1]);
      rect(g, 32, 206, 32, 14, p.wall[0]);
      rect(g, 32, 198, 4, 32, p.wood[1]);
      rect(g, 60, 198, 4, 32, p.wood[1]);
      rect(g, 34, 222, 8, 4, p.wood[2]);
      rect(g, 72, 210, 12, 20, p.wood[2]);
      rect(g, 72, 210, 12, 4, p.wood[1]);
      rect(g, 68, 226, 16, 6, C.stoneDark);
      rect(g, 88, 210, 12, 20, p.wood[2]);
      rect(g, 88, 210, 12, 4, p.wood[1]);
      rect(g, 84, 226, 16, 6, C.stoneDark);
      flowerBox(g, p, 104, 204, 28);
      rect(g, 244, 188, 8, 48, p.wood[0]);
      rect(g, 244, 188, 4, 48, p.wood[1]);
      rect(g, 212, 196, 32, 12, p.wood[0]);
      rect(g, 212, 196, 32, 4, p.wood[1]);
      rect(g, 216, 200, 24, 4, C.cream);
      rect(g, 208, 200, 4, 4, p.wood[0]);
      rect(g, 252, 212, 32, 12, p.wood[0]);
      rect(g, 252, 212, 32, 4, p.wood[1]);
      rect(g, 256, 216, 24, 4, C.cream);
      rect(g, 284, 216, 4, 4, p.wood[0]);
      rect(g, 292, 220, 20, 8, C.stone);
      rect(g, 296, 212, 16, 8, C.stoneLight);
      rect(g, 300, 204, 8, 8, C.stoneDark);
    } else {
      rect(g, 44, 220, 52, 4, p.wood[1]); rect(g, 48, 224, 4, 12, p.wood[2]); rect(g, 88, 224, 4, 12, p.wood[2]);
      fence(g, p, 208, 212, 96);
    }
    // Match the house trim so the yard lines up with its narrowed owner.
    if (style === "angling" || style === "workshop") squeezeX(g, 0.81);
    return g;
  }

  function labelY(agent) {
    // Four units above the main roof; detached smoke, flags and telescopes are ignored.
    const roofAnchors = { command: 20, observatory: 16, architecture: 16, workshop: 4, farm: 44, music: 40, finance: 56, school: 8, angling: 4, cottage: 44 };
    return roofAnchors[resolve(agent).style];
  }

  window.StarhavenHouseArt = Object.freeze({ house, yard, labelY });
})();

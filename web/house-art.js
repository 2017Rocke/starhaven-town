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
  function resolve(agent) {
    const style = Object.prototype.hasOwnProperty.call(STYLES, agent?.house?.style) ? agent.house.style : "cottage";
    const defaults = { workshop: "tools", architecture: "blueprint", farm: "poultry", music: "music", finance: "finance", school: "books", observatory: "stars", command: "planning", cottage: "garden" };
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
    rect(g, x - 4, 168, 40, 4, C.stoneLight);
    rect(g, x - 8, 172, 48, 4, C.stone);
    rect(g, x - 12, 176, 56, 4, C.stoneDark);
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
    rect(g, x + 12, y + 8, 4, height - 16, "#d8dfc7"); rect(g, x + 12, y + 8, width - 28, 4, "#d8dfc7");
    rect(g, x + width - 20, y + 8, 4, height - 16, "#d8dfc7"); rect(g, x + 12, y + height - 12, width - 28, 4, "#d8dfc7");
    rect(g, x + 24, y + 16, 4, height - 24, "#b7cdba"); rect(g, x + 28, y + 16, width - 48, 4, "#b7cdba");
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

  function house(agent) {
    const { style, p, motif, variant } = resolve(agent), g = group(`house-${style}`);
    rect(g, 28, 176, 184, 8, C.shadow); rect(g, 40, 184, 160, 4, C.shadow);
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
      chimney(g, p, 180, 36, 44);
      wall(g, p, 28, 104, 184, 64); roof(g, p, 16, 48, 208, 13);
      // Ordered tools and a real code display are built into the facade.
      screen(g, p, 40, 116, 48, "code"); toolRack(g, p, 156, 116, 44); door(g, p);
      rect(g, 76, 92, 88, 8, p.wood[2]); rect(g, 80, 92, 80, 4, p.wood[1]);
      for (let xx = 88; xx <= 148; xx += 20) { rect(g, xx, 76, 8, 12, C.glassShade); rect(g, xx, 76, 8, 4, C.glassLight); }
      if (motif === "music") {
        rect(g, 212, 116, 12, 4, p.wood[2]); rect(g, 220, 116, 4, 24, p.wood[2]);
        rect(g, 216, 136, 12, 8, p.roof[2]); rect(g, 216, 136, 4, 4, p.roof[1]);
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
    } else {
      chimney(g, p, 180, 40, 44);
      wall(g, p, 28, 104, 184, 64); roof(g, p, 16, 48, 208, 13);
      windowFrame(g, p, 44, 116, 32, 28, true); windowFrame(g, p, 168, 116, 28, 28, true); door(g, p);
      wall(g, p, 100, 68, 40, 28); roof(g, p, 92, 44, 56, 5); windowFrame(g, p, 108, 72, 24, 20);
      flowerBox(g, p, 44, 156); flowerBox(g, p, 168, 156, 28);
    }
    // Small structural variations keep neighbors distinct without changing style.
    if (variant % 2 === 1 && !["farm", "command"].includes(style)) lantern(g, p, 140, 124);
    return g;
  }

  function yard(agent) {
    const { style, p, motif } = resolve(agent), g = group(`yard-${style}-${motif}`);
    // Keep x128..196 clear in front of the central doorstep and resident sprite.
    if (style === "workshop") {
      toolRack(g, p, 24, 168, 84);
      rect(g, 24, 208, 88, 8, p.wood[0]); rect(g, 28, 216, 4, 16, p.wood[2]); rect(g, 100, 216, 4, 16, p.wood[2]);
      crate(g, p, 40, 212, 32); crate(g, p, 80, 212, 24);
      if (motif === "music") { speaker(g, p, 212, 192); guitar(g, p, 256, 172); }
      else { crate(g, p, 216, 204, 32); crate(g, p, 260, 196, 28); rect(g, 268, 188, 8, 8, C.stoneLight); }
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
    } else {
      flowerBox(g, p, 28, 204, 80); flowerBox(g, p, 216, 204, 76);
      rect(g, 44, 220, 52, 4, p.wood[1]); rect(g, 48, 224, 4, 12, p.wood[2]); rect(g, 88, 224, 4, 12, p.wood[2]);
      fence(g, p, 208, 212, 96);
    }
    return g;
  }

  function labelY(agent) {
    // Four units above the main roof; detached smoke, flags and telescopes are ignored.
    const roofAnchors = { command: 20, observatory: 16, architecture: 16, workshop: 44, farm: 44, music: 40, finance: 56, school: 8, cottage: 44 };
    return roofAnchors[resolve(agent).style];
  }

  window.StarhavenHouseArt = Object.freeze({ house, yard, labelY });
})();

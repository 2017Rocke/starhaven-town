"use strict";

// A shared 320×280 plot and a two-pixel lattice unite every profession.
// Structure uses 4px courses; small tools and glass details use 2px. No raster
// scaling, curves, gradients, fonts, filters or external assets are required.
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
    police: { roof: ["#718696", "#94a6ae", "#536b7b"], wall: ["#c4cecc", "#e0e2d5", "#a6b5b3"], wood: ["#71828b", "#a1b0ae", "#536770"] },
    inspection: { roof: ["#678780", "#8ba59a", "#496c67"], wall: ["#bccbbb", "#d9dfc9", "#9dac9f"], wood: ["#6b8075", "#9aaf96", "#4d655d"] },
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
    const snap = value => Math.round(value / 2) * 2;
    const left = snap(x), top = snap(y);
    const attributes = { x: left, y: top, width: Math.max(2, snap(x + width) - left), height: Math.max(2, snap(y + height) - top), fill };
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    parent.append(node);
    return node;
  }
  // Narrow a finished group horizontally around its centre. Every edge is placed
  // once and snapped to the same 2px lattice, so two shapes that used to abut
  // still abut: snapping x and x+width (not the width itself) is what keeps the
  // courses, planks and window trim from drifting apart or overlapping.
  // Two-pixel seams keep their width and only move.
  function squeezeX(node, factor, center = 160) {
    const place = value => center + (value - center) * factor;
    const snap2 = value => Math.round(value / 2) * 2;
    const walk = parent => {
      for (const child of parent.children || []) {
        if (!child || typeof child !== "object") continue;
        if (child.localName === "rect") {
          const x = Number(child.getAttribute("x")), width = Number(child.getAttribute("width"));
          if (!Number.isFinite(x) || !Number.isFinite(width)) continue;
          if (width <= 2) {
            child.setAttribute("x", String(snap2(place(x))));
          } else {
            const left = snap2(place(x)), right = snap2(place(x + width));
            child.setAttribute("x", String(left));
            child.setAttribute("width", String(Math.max(2, right - left)));
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
    const defaults = { workshop: "tools", architecture: "blueprint", farm: "poultry", music: "music", finance: "finance", school: "books", observatory: "stars", command: "planning", angling: "fishing", cottage: "garden", police: "security", inspection: "audit" };
    return { style, p: STYLES[style], motif: agent?.house?.motif && agent.house.motif !== "none" ? agent.house.motif : defaults[style], variant: Math.abs(Number(agent?.house?.variant) || 0) % 4 };
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
  function windowFrame(parent, p, x, y, width = 32, height = 32, shutters = false) {
    const g=group("divided-window");g.setAttribute("data-part","window");parent.append(g);
    rect(g,x,y,width,height,p.wood[2]);
    rect(g,x+2,y+2,width-4,2,p.wood[1]);
    const midX=Math.floor(width/8)*4,midY=Math.floor(height/8)*4;
    // Four independent glass panes let the map illuminate them at night without
    // covering the mullions. No glowing rectangle crosses a wooden divider.
    for(const [dx,pw] of [[4,midX-4],[midX+4,width-midX-8]]) {
      for(const [dy,ph] of [[4,midY-4],[midY+4,height-midY-8]]) {
        rect(g,x+dx,y+dy,pw,ph,"var(--house-window-fill, #a5beb3)").setAttribute("class","house-window-light");
        rect(g,x+dx+2,y+dy+2,Math.max(2,pw-4),2,"var(--house-window-glint, #e9e5c8)").setAttribute("class","house-window-glint");
      }
    }
    rect(g,x+midX,y+4,4,height-8,p.wood[1]);rect(g,x+4,y+midY,width-8,4,p.wood[1]);
    rect(g,x-4,y+height,width+8,4,p.wood[1]);rect(g,x,y+height+4,width+4,4,p.wood[0]);
    if(shutters) for(const sx of [x-12,x+width+4]) {
      rect(g,sx,y+4,8,height-4,p.roof[2]);
      for(let yy=8;yy<height;yy+=8)rect(g,sx,y+yy,8,4,p.roof[0]);
      rect(g,sx+2,y+8,2,height-12,p.roof[1]);
    }
  }

  function door(parent, p, x = 144, y = 132) {
    const g=group("front-door");g.setAttribute("data-part","door");parent.append(g);
    rect(g,x-4,y-4,40,48,p.wood[2]);rect(g,x,y,32,44,p.wood[0]);
    rect(g,x+4,y+4,24,4,p.wood[1]);rect(g,x+4,y+20,24,20,p.wood[2]);
    rect(g,x+6,y+22,20,16,p.wood[0]);rect(g,x+8,y+24,2,12,p.wood[1]);
    for(const [dx,w] of [[4,10],[16,12]]) {
      rect(g,x+dx,y+8,w,10,"var(--house-window-fill, #a5beb3)").setAttribute("class","house-window-light");
      rect(g,x+dx+2,y+10,6,2,"var(--house-window-glint, #e9e5c8)").setAttribute("class","house-window-glint");
    }
    rect(g,x+14,y+8,2,10,p.wood[1]);rect(g,x+24,y+28,4,4,C.gold);
    rect(g,x+2,y+24,2,4,p.wood[2]);rect(g,x+2,y+34,2,4,p.wood[2]);
    const steps=group("door-steps");steps.setAttribute("data-part","steps");parent.append(steps);
    rect(steps,x-4,y+44,40,4,C.stoneLight);rect(steps,x-8,y+48,48,4,C.stone);rect(steps,x-12,y+52,56,4,C.stoneDark);
    rect(steps,x-6,y+48,44,2,C.stoneLight);rect(steps,x-10,y+52,52,2,C.stone);
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
    rect(g, x + 4, y + 8, 12, 16, p.wood[0]); rect(g, x + 8, y + 12, 4, 8, "var(--house-lamp-fill, #e4ca88)").setAttribute("class", "house-lamp-light");
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
  // A common scale makes a door, a pane of glass and a stone block mean the
  // same thing in every house. Silhouettes and equipment carry the profession.
  const LAYOUTS = {
    command: { roof: 8, door: 132, left: 28, right: 292 },
    observatory: { roof: 12, door: 140, left: 32, right: 288 },
    architecture: { roof: 20, door: 132, left: 28, right: 292 },
    workshop: { roof: 28, door: 132, left: 28, right: 292 },
    farm: { roof: 36, door: 132, left: 28, right: 292 },
    music: { roof: 32, door: 132, left: 28, right: 292 },
    finance: { roof: 40, door: 132, left: 32, right: 288 },
    school: { roof: 16, door: 132, left: 28, right: 292 },
    cottage: { roof: 36, door: 132, left: 32, right: 288 },
    angling: { roof: 20, door: 140, left: 28, right: 292 },
    police: { roof: 24, door: 132, left: 28, right: 292 },
    inspection: { roof: 12, door: 132, left: 28, right: 292 },
  };
  function layout(agent) {
    const m = LAYOUTS[resolve(agent).style];
    return {
      // hit includes the building silhouette and steps, never yard furniture.
      hit: { x: m.left, y: m.roof, width: m.right - m.left, height: m.door + 64 - m.roof },
      // label is the bottom-centre anchor above the highest roof decoration.
      label: { x: 160, y: m.roof - 8 },
      // resident is the TOP centre of the 70px-tall map sprite; its feet sit
      // 44px beyond the last step, leaving the door itself easy to recognize.
      resident: { x: 160, y: m.door + 30 },
      // doorstep is ground at the bottom of the last stone step.
      doorstep: { x: 160, y: m.door + 56 },
    };
  }
  function foundation(g, x, y, width) {
    const base = group("foundation"); base.setAttribute("data-part", "foundation"); g.append(base);
    rect(base, x - 4, y + 8, width + 12, 8, "#73876226");
    rect(base, x, y, width, 8, C.stoneDark);
    rect(base, x, y, width, 2, C.stoneLight);
    for (let sx = x + 4; sx < x + width - 8; sx += 20) {
      rect(base, sx, y + 2, 16, 4, C.stone); rect(base, sx + 2, y + 2, 10, 2, C.stoneLight);
    }
  }
  function facade(g, p, x = 44, y = 104, width = 232, bottom = 180, material = "plaster") {
    const height = bottom - y;
    rect(g, x, y, width, height, p.wall[0]);
    rect(g, x + width - 12, y, 12, height, p.wall[2]);
    rect(g, x + 4, y + 4, width - 20, 4, p.wall[1]);
    if (material === "timber") {
      for (let row = y + 12; row < bottom - 20; row += 12) {
        rect(g, x + 4, row, width - 20, 2, p.wall[2]);
        rect(g, x + 4, row - 2, width - 20, 2, p.wall[1]);
      }
    } else {
      // Sparse repairs leave large plaster areas quiet and readable.
      for (const [dx,dy] of [[16,20],[84,12],[184,28],[204,46],[60,52]]) {
        if (dy < height - 20 && dx + 20 <= width - 12) { rect(g,x+dx,y+dy,16,4,p.wall[1]); rect(g,x+dx+12,y+dy+4,8,2,p.wall[2]); }
      }
    }
    rect(g, x, bottom - 20, width, 20, p.wood[0]);
    rect(g, x, bottom - 20, width, 4, p.wood[1]);
    for (let sx = x + 12; sx < x + width - 8; sx += 24) rect(g,sx,bottom-16,2,16,p.wood[2]);
    for (const sx of [x, x + width - 8]) {
      rect(g, sx, y, 8, height, p.wood[2]); rect(g,sx,y,2,height,p.wood[1]);
    }
    foundation(g, x - 4, bottom, width + 8);
  }
  function tiledRoof(g, p, x, y, width, rows, step = 8) {
    const art = group("tiled-roof"); art.setAttribute("data-part","roof"); g.append(art);
    for(let row=0;row<rows;row++) {
      const inset=(rows-row-1)*step, left=x+inset, w=width-inset*2, yy=y+row*4;
      if(w<8) continue;
      rect(art,left,yy,w,4,p.roof[0]);
      rect(art,left,yy,4,4,p.roof[1]); rect(art,left+w-8,yy,8,4,p.roof[2]);
      if(row%2===1) for(let tx=left+10+(row%4===1?8:0);tx<left+w-12;tx+=24) {
        rect(art,tx,yy,14,2,p.roof[1]); rect(art,tx+14,yy,2,4,p.roof[2]);
      }
    }
    const ridge=(rows-1)*step, ridgeWidth=width-ridge*2;
    rect(art,x+ridge-4,y-8,ridgeWidth+8,4,p.roof[2]);
    rect(art,x+ridge,y-8,ridgeWidth,2,p.roof[1]);
    rect(art,x+ridge-4,y-4,ridgeWidth+8,4,p.roof[0]);
    rect(art,x-4,y+rows*4,width+8,4,p.roof[2]);
    rect(art,x,y+rows*4+4,width,4,p.wood[2]);
    rect(art,x+4,y+rows*4+8,width-8,4,p.wall[2]);
    for(let nx=x+8;nx<x+width-8;nx+=32) rect(art,nx,y+rows*4+4,2,2,p.wood[1]);
  }
  function awning(g,p,y=112) {
    rect(g,124,y,72,4,p.roof[1]); rect(g,120,y+4,80,6,p.roof[0]);
    rect(g,120,y+10,80,4,p.roof[2]);
    for(const x of [124,192]) { rect(g,x,y+14,4,52,p.wood[2]);rect(g,x,y+14,2,48,p.wood[1]); }
  }
  function plaque(g,p,x,y,width,kind) {
    rect(g,x-2,y+2,width+4,20,p.wood[2]);rect(g,x,y,width,18,p.wood[0]);rect(g,x+4,y+4,width-8,10,C.cream);
    rect(g,x+4,y+2,width-8,2,p.wood[1]);
    const cx=x+width/2;
    if(kind==="mountain") {
      [[-14,10,8,4],[-10,6,8,4],[-6,2,8,4],[2,6,8,4],[6,10,8,4]].forEach(([dx,dy,w,h])=>rect(g,cx+dx,y+dy,w,h,C.greenDark));
      rect(g,cx-4,y+2,4,4,C.cream);
    } else if(kind==="book") {
      rect(g,cx-14,y+4,12,8,"#8ca697");rect(g,cx+2,y+4,12,8,"#8ca697");rect(g,cx-2,y+4,4,10,p.wood[2]);
    } else if(kind==="music") {
      rect(g,cx-2,y+4,4,8,p.roof[2]);rect(g,cx-8,y+10,8,4,p.roof[2]);rect(g,cx+2,y+4,8,4,p.roof[2]);
    } else if(kind==="chicken") {
      rect(g,cx-10,y+8,16,6,"#b98c62");rect(g,cx+2,y+4,8,6,"#b98c62");rect(g,cx+6,y+2,4,2,"#bd806a");rect(g,cx+10,y+8,4,2,C.gold);
    } else {
      rect(g,x+8,y+6,10,2,p.roof[2]);rect(g,x+24,y+6,width-32,2,p.wood[0]);rect(g,x+10,y+10,width-20,2,p.wood[1]);
    }
  }
  function star(g,x,y,color=C.gold) {
    rect(g,x+6,y,4,4,color);rect(g,x+2,y+4,12,4,color);rect(g,x,y+8,16,4,color);rect(g,x+4,y+12,8,4,color);rect(g,x+2,y+16,4,2,color);rect(g,x+10,y+16,4,2,color);
  }
  function telescope(g,p,x,y,small=false) {
    const scope=group("telescope");g.append(scope);
    rect(scope,x+24,y+24,4,24,p.wood[2]);rect(scope,x+14,y+46,24,4,p.wood[2]);
    rect(scope,x+18,y+34,4,12,p.wood[1]);rect(scope,x+32,y+36,4,12,p.wood[1]);
    rect(scope,x,y+12,24,12,p.roof[2]);rect(scope,x+8,y+8,24,12,p.roof[0]);
    rect(scope,x+20,y+4,24,12,p.roof[1]);rect(scope,x+40,y,8,20,p.wood[2]);
    rect(scope,x+44,y+4,4,12,C.glassLight);rect(scope,x+8,y+12,4,8,C.gold);
    if(small) squeezeX(scope,.75,x+24);
  }
  function bookshelf(g,p,x,y,width=80,height=48) {
    rect(g,x,y,width,height,p.wood[2]);rect(g,x+4,y+4,width-8,height-8,p.wall[2]);
    for(let row=0;row<2;row++) {
      for(let i=0;i<Math.floor((width-12)/10);i++) {
        const hh=14+(i%2)*4, yy=y+20+row*22;
        rect(g,x+8+i*10,yy-hh,6,hh,["#8ba6a0","#b89c7a","#adba8c","#b39eac"][i%4]);
        rect(g,x+8+i*10,yy-hh+4,6,2,C.cream);
      }
      rect(g,x+4,y+22+row*22,width-8,4,p.wood[1]);
    }
  }
  function bulletin(g,p,x,y,width=88) {
    rect(g,x+6,y+42,4,20,p.wood[2]);rect(g,x+width-10,y+42,4,20,p.wood[2]);
    rect(g,x,y,width,44,p.wood[0]);rect(g,x+4,y+4,width-8,36,p.wall[1]);
    for(const [dx,dy,w,h] of [[8,8,28,24],[42,8,32,12],[42,24,32,12]]) {
      rect(g,x+dx,y+dy,w,h,C.cream);rect(g,x+dx+4,y+dy+6,w-8,2,"#a0ad90");
      rect(g,x+dx+4,y+dy+12,w-12,2,"#b6b299");rect(g,x+dx+w/2,y+dy-2,2,4,C.pink);
    }
  }
  function shield(parent, p, x, y) {
    const g = group("police-badge"); parent.append(g);
    rect(g,x+4,y,24,4,p.wood[2]); rect(g,x,y+4,32,16,p.wood[2]);
    rect(g,x+4,y+20,24,8,p.wood[2]); rect(g,x+8,y+28,16,4,p.wood[2]);
    rect(g,x+4,y+4,24,14,C.gold); rect(g,x+8,y+18,16,8,C.gold); rect(g,x+12,y+26,8,2,C.gold);
    rect(g,x+8,y+4,16,2,C.cream); star(g,x+8,y+6,p.roof[2]);
  }
  function auditMark(parent, p, x, y) {
    const g = group("audit-ledger"); parent.append(g);
    rect(g,x,y,32,30,p.wood[2]); rect(g,x+4,y+2,24,26,C.cream); rect(g,x+4,y+2,4,26,p.roof[1]);
    for(let row=0;row<3;row++)rect(g,x+12,y+6+row*6,12,2,p.wall[2]);
    rect(g,x+12,y+18,4,4,p.roof[2]); rect(g,x+16,y+22,4,4,p.roof[2]);
    rect(g,x+20,y+18,4,4,p.roof[2]); rect(g,x+24,y+14,4,4,p.roof[2]);
  }
  function evidenceCabinet(parent, p, x, y) {
    const g = group("evidence-cabinet"); parent.append(g);
    rect(g,x,y,88,64,p.wood[2]); rect(g,x+4,y+4,80,56,p.wall[1]);
    rect(g,x+6,y+6,76,20,p.wall[2]);
    for(let i=0;i<6;i++) {
      rect(g,x+10+i*12,y+10,8,14,i%2?p.roof[0]:p.wood[1]);
      rect(g,x+12+i*12,y+14,4,2,C.cream);
    }
    rect(g,x+4,y+26,80,4,p.wood[0]);
    for(const dx of [10,48]) {
      rect(g,x+dx,y+34,30,20,"#bba584");rect(g,x+dx,y+34,30,4,"#d6c39e");
      rect(g,x+dx+12,y+34,6,20,C.cream); rect(g,x+dx+14,y+42,2,4,p.roof[2]);
    }
    rect(g,x+4,y+58,80,4,p.wood[1]); rect(g,x+8,y+64,4,6,p.wood[2]); rect(g,x+76,y+64,4,6,p.wood[2]);
  }
  function house(agent) {
    const {style,p,motif,variant}=resolve(agent),g=group(`house-${style}`),m=LAYOUTS[style];
    g.setAttribute("data-part","building");
    // Every structure is drawn directly in 320×280 plot coordinates.
    if(style==="command") {
      facade(g,p,44,108,232,180);
      // A central flag tower rises behind the broad civic roof.
      facade(g,p,132,40,56,104,"timber");
      tiledRoof(g,p,124,24,72,3,8);
      rect(g,156,8,4,20,p.wood[2]);rect(g,160,8,28,12,"#bd9274");rect(g,180,12,8,12,"#a47d62");
      tiledRoof(g,p,32,64,256,10,8);
      windowFrame(g,p,64,124,32,32,true);windowFrame(g,p,224,124,32,32,true);
      rect(g,112,104,96,24,p.wood[1]);rect(g,116,108,88,16,C.cream);star(g,152,106,p.roof[2]);
      // Pinned agenda beside the entry, with warm masonry and brass lamps.
      rect(g,104,136,20,28,C.cream);for(let n=0;n<3;n++)rect(g,108,142+n*6,12,2,"#9da98b");
      lantern(g,p,204,132);
    } else if(style==="observatory") {
      facade(g,p,agent?.house?.painted ? 68 : 48,108,agent?.house?.painted ? 204 : 224,188);
      // Oxidised copper dome built from horizontal steps, with a visible seam.
      const crown=[48,88,120,152,176,200,216,224,232];
      crown.forEach((width,i)=>{
        const x=160-width/2, yy=40+i*6;
        rect(g,x,yy,width,6,p.roof[0]);rect(g,x,yy,8,6,p.roof[1]);rect(g,x+width-12,yy,12,6,p.roof[2]);
        if(i%2===0) for(let px=x+24;px<x+width-16;px+=32)rect(g,px,yy,2,6,p.roof[2]);
      });
      rect(g,40,94,240,8,p.roof[2]);rect(g,44,102,232,6,p.wood[1]);
      rect(g,154,32,12,8,C.gold);rect(g,158,22,4,10,p.wood[2]);star(g,152,12,C.gold);
      telescope(g,p,208,14,true);
      windowFrame(g,p,agent?.house?.painted ? 84 : 68,124,32,32);windowFrame(g,p,220,124,32,32);
      rect(g,112,116,24,40,p.wood[2]);rect(g,116,120,16,32,"#819c96");star(g,116,124,C.cream);
      rect(g,200,124,12,36,p.wood[2]);for(let i=0;i<3;i++)rect(g,202,128+i*10,8,6,[C.cream,C.gold,p.roof[1]][i]);
      lantern(g,p,184,140);
    } else if(style==="architecture") {
      facade(g,p,44,108,232,180,"plaster");
      facade(g,p,52,60,72,112,"timber");
      tiledRoof(g,p,44,28,88,6,6);
      tiledRoof(g,p,128,64,156,10,6);
      windowFrame(g,p,68,72,32,24);
      rect(g,64,104,48,4,p.wood[2]);rect(g,72,108,4,12,p.wood[1]);rect(g,100,108,4,12,p.wood[1]);
      blueprint(g,p,64,132,52,32);windowFrame(g,p,224,128,32,32,true);
      rect(g,200,116,4,56,p.wood[2]);for(let y=124;y<168;y+=8)rect(g,200,y,8,2,C.cream);
      plaque(g,p,128,108,64,"book");
      // The model rests beside the window, clear of the night-lit glass.
      rect(g,106,88,4,12,C.cream);rect(g,110,92,6,8,C.gold);rect(g,104,100,16,4,p.wood[1]);
    } else if(style==="workshop") {
      facade(g,p,44,108,232,180,"timber");
      chimney(g,p,240,56,44);tiledRoof(g,p,32,36,256,17,6);
      windowFrame(g,p,64,124,32,32,true);windowFrame(g,p,224,124,32,32,true);
      screen(g,p,96,140,36,"code");toolRack(g,p,196,136,24);
      awning(g,p,112);plaque(g,p,132,108,56,"tools");
      rect(g,84,84,36,20,p.wood[2]);rect(g,88,88,28,12,p.wall[2]);for(let x=92;x<112;x+=6)rect(g,x,88,2,12,p.wood[1]);
      if(motif==="music") {rect(g,248,160,16,12,p.wood[2]);rect(g,252,162,8,6,p.wall[1]);}
    } else if(style==="farm") {
      facade(g,p,44,108,232,180,"timber");
      tiledRoof(g,p,32,44,256,15,6);
      // Split barn doors remain centred; the small chicken hatch is separate.
      windowFrame(g,p,64,124,32,28,true);windowFrame(g,p,224,124,32,28,true);
      rect(g,104,144,24,28,p.wood[2]);rect(g,108,148,16,20,"#73634c");rect(g,104,172,24,4,p.wood[1]);
      rect(g,196,148,16,24,"#d5bc87");rect(g,192,164,24,4,p.roof[1]);
      plaque(g,p,128,108,64,"chicken");
      rect(g,150,72,20,20,p.wood[2]);rect(g,154,76,12,12,p.wall[1]);rect(g,158,76,4,12,p.wood[0]);
    } else if(style==="music") {
      facade(g,p,44,108,232,180);
      tiledRoof(g,p,32,40,256,16,6);windowFrame(g,p,64,124,32,32,true);windowFrame(g,p,224,124,32,32,true);
      plaque(g,p,128,108,64,"music");
      for(const [x,h] of [[100,20],[108,28],[116,16],[200,16],[208,28],[216,20]])rect(g,x,172-h,4,h,p.roof[2]);
      rect(g,144,72,32,20,p.wood[2]);rect(g,148,76,24,12,C.glass);
      rect(g,154,78,4,8,C.gold);rect(g,150,84,8,4,C.gold);rect(g,158,78,10,4,C.gold);
    } else if(style==="finance") {
      facade(g,p,48,112,224,180);
      tiledRoof(g,p,36,48,248,15,6);
      windowFrame(g,p,64,128,32,28);windowFrame(g,p,224,128,32,28);
      for(const x of [108,204]){rect(g,x,128,8,44,p.wood[2]);rect(g,x-2,128,12,4,C.stoneLight);rect(g,x-2,168,12,4,C.stoneLight);}
      plaque(g,p,126,108,68,"ledger");
      rect(g,142,78,36,20,p.wood[2]);rect(g,146,82,28,12,C.cream);
      [[148,88,4,4],[154,84,4,8],[160,86,4,6],[166,82,4,10]].forEach(v=>rect(g,...v,C.greenDark));
    } else if(style==="school") {
      facade(g,p,44,108,232,180);
      // Bell cupola sits behind the reading room's roof.
      rect(g,140,36,40,36,p.wall[0]);tiledRoof(g,p,132,24,56,3,8);
      rect(g,150,44,20,20,p.wood[2]);rect(g,156,48,8,12,C.gold);rect(g,152,60,16,4,C.gold);
      tiledRoof(g,p,32,64,256,10,8);
      windowFrame(g,p,60,124,36,32,true);windowFrame(g,p,224,124,36,32,true);
      plaque(g,p,126,108,68,"book");
      rect(g,104,140,20,28,p.wood[2]);rect(g,108,144,12,20,"#7b9583");rect(g,110,148,8,2,C.cream);rect(g,110,154,6,2,C.cream);
      lantern(g,p,200,132);
    } else if(style==="police") {
      facade(g,p,44,108,232,180);
      // A broad blue-grey station roof and masonry pilasters give the civic
      // office its own silhouette without borrowing another resident's house.
      tiledRoof(g,p,32,48,256,14,6);
      rect(g,68,28,20,36,p.wood[2]);rect(g,64,24,28,6,p.wood[1]);
      for(const x of [48,264]) {
        rect(g,x,112,8,60,p.wall[1]);rect(g,x-4,112,16,4,p.wood[0]);rect(g,x-4,168,16,8,p.wood[0]);
      }
      windowFrame(g,p,72,126,32,30);windowFrame(g,p,216,126,32,30);
      rect(g,124,104,72,4,p.wood[2]);rect(g,120,108,80,8,p.roof[1]);rect(g,124,116,72,4,p.roof[2]);
      for(const x of [128,188]) {rect(g,x,120,4,52,p.wall[1]);rect(g,x-2,168,8,8,p.wood[0]);}
      shield(g,p,144,72);
      rect(g,108,136,12,28,p.wood[2]);rect(g,110,138,8,22,C.cream);
      rect(g,112,142,4,2,p.roof[0]);rect(g,112,148,4,2,p.roof[0]);
      lantern(g,p,196,128);
    } else if(style==="inspection") {
      // A stepped audit office, antenna tower and separate roll-up garage.
      facade(g,p,44,96,160,180,"timber");facade(g,p,204,116,72,180,"timber");
      const roofArt=group("inspection-roof");roofArt.setAttribute("data-part","roof");g.append(roofArt);
      rect(roofArt,40,68,168,12,p.roof[2]);rect(roofArt,44,64,160,8,p.roof[1]);
      rect(roofArt,36,80,176,12,p.roof[0]);rect(roofArt,32,92,184,6,p.roof[2]);rect(roofArt,36,98,176,4,p.wood[1]);
      for(let x=52;x<200;x+=24) {rect(roofArt,x,70,16,2,p.roof[0]);rect(roofArt,x+8,84,12,2,p.roof[1]);}
      rect(roofArt,212,56,52,60,p.wall[0]);rect(roofArt,252,60,12,56,p.wall[2]);
      rect(roofArt,208,48,60,8,p.roof[0]);rect(roofArt,204,44,68,4,p.roof[1]);
      rect(roofArt,200,112,80,8,p.roof[2]);rect(roofArt,204,120,72,4,p.wood[1]);
      rect(roofArt,236,12,4,32,p.wood[2]);rect(roofArt,224,20,28,4,p.wood[1]);
      rect(roofArt,220,16,4,12,p.roof[2]);rect(roofArt,252,16,4,12,p.roof[2]);
      rect(roofArt,228,30,20,4,p.roof[1]);rect(roofArt,236,12,4,4,C.gold);
      auditMark(g,p,96,56);windowFrame(g,p,68,124,32,32,true);windowFrame(g,p,222,72,24,24);
      awning(g,p,112);
      const garage=group("inspection-garage");g.append(garage);
      rect(garage,212,130,56,48,p.wood[2]);rect(garage,216,134,48,42,p.wall[2]);
      for(let y=136;y<172;y+=6) {rect(garage,216,y,48,2,p.wall[1]);rect(garage,218,y+2,44,2,p.wood[1]);}
      rect(garage,236,166,8,4,p.wood[2]);rect(garage,210,176,60,4,C.stoneDark);
      rect(g,106,132,12,28,p.wood[2]);rect(g,108,134,8,8,p.roof[1]);rect(g,110,136,4,4,C.cream);
      rect(g,108,146,8,2,p.wall[1]);rect(g,108,152,8,2,p.wall[1]);lantern(g,p,184,132);
    } else if(style==="angling") {
      facade(g,p,44,112,232,188,"timber");
      reedRoof(g,p,32,28,256,20);thatchStreaks(g,p,32,28,256,20);
      chimney(g,p,240,60,48);
      windowFrame(g,p,64,132,32,28,true);windowFrame(g,p,224,132,32,28,true);
      awning(g,p,120);fishSign(g,p,128,112,64);
      rect(g,104,156,20,24,"#b99d6f");rect(g,104,160,20,4,"#d8be88");rect(g,104,172,20,4,"#8f7a55");
      crate(g,p,200,156,20);lantern(g,p,184,148);
    } else {
      facade(g,p,48,108,224,180,"timber");
      chimney(g,p,240,64,36);tiledRoof(g,p,36,44,248,15,6);
      windowFrame(g,p,68,124,32,32,true);windowFrame(g,p,220,124,32,32,true);
      awning(g,p,112);plaque(g,p,128,108,64,motif==="hiking"?"mountain":"garden");
      flowerBox(g,p,68,168,32);flowerBox(g,p,220,168,32);
      if(motif==="hiking") {rect(g,112,136,4,40,p.wood[2]);rect(g,108,136,12,4,p.wood[1]);rect(g,204,136,4,40,p.wood[2]);}
    }
    door(g,p,144,m.door);
    // Individual homes retain one small, reproducible change in furnishing.
    if(!["observatory","angling","command","school","police","inspection"].includes(style))lantern(g,p,variant%2?192:120,m.door+4);
    return g;
  }
  function plantPot(g,p,x,y) {
    rect(g,x+4,y+14,20,16,p.wood[0]);rect(g,x,y+12,28,6,p.wood[1]);
    rect(g,x+10,y-2,4,16,C.greenDark);rect(g,x+2,y,12,8,C.green);rect(g,x+14,y-6,12,12,C.greenLight);
    rect(g,x+4,y+18,4,8,p.wood[1]);
  }
  function discussion(g,p,x,y) {
    rect(g,x+8,y+12,56,16,p.wood[0]);rect(g,x+8,y+12,56,4,p.wood[1]);
    rect(g,x+16,y+28,4,16,p.wood[2]);rect(g,x+52,y+28,4,16,p.wood[2]);
    rect(g,x,y,24,8,p.wood[0]);rect(g,x,y+8,4,16,p.wood[2]);rect(g,x+20,y+8,4,16,p.wood[2]);
    rect(g,x+48,y+40,24,8,p.wood[1]);rect(g,x+48,y+48,4,6,p.wood[2]);rect(g,x+68,y+48,4,6,p.wood[2]);
    rect(g,x+24,y+16,24,8,C.cream);rect(g,x+34,y+16,2,8,p.roof[2]);
  }
  function yard(agent) {
    const {style,p,motif}=resolve(agent),g=group(`yard-${style}-${motif}`);
    if(agent?.house?.painted && (style==="command"||style==="observatory")) {
      // Civic forecourts keep their map-owned paths. Light equipment sits on
      // either side of the entrance; counted materials stay in the real rack.
      const equipment = (kind, draw) => {
        const item = group(`civic-${kind}`);
        item.setAttribute("data-part", "equipment");
        item.setAttribute("data-equipment", kind);
        draw(item); g.append(item);
      };
      if(style==="command") {
        equipment("planning-board", item => bulletin(item,p,24,204,96));
        equipment("meeting-table", item => discussion(item,p,188,200));
      } else {
        equipment("telescope", item => telescope(item,p,84,212));
        equipment("research-table", item => {
          rect(item,196,236,64,8,p.wood[0]);rect(item,196,236,64,3,p.wood[1]);
          rect(item,200,244,4,16,p.wood[2]);rect(item,252,244,4,16,p.wood[2]);
          rect(item,204,216,40,22,C.cream);rect(item,208,220,32,14,"#a4bdb2");
          for(const x of [214,226,234])rect(item,x,224+(x%4),2,2,C.cream);
          rect(item,218,224,10,2,p.roof[2]);rect(item,226,226,2,6,p.roof[2]);
          rect(item,248,226,8,10,p.wood[1]);rect(item,250,222,2,8,C.gold);
        });
      }
      return g;
    }
    const path=group("front-path");path.setAttribute("data-part","path");g.append(path);
    const step=layout(agent).doorstep.y;
    for(let y=step+8;y<272;y+=18) {
      rect(path,146,y,28,8,"#c7c7a7");rect(path,148,y,24,2,"#dfddbd");rect(path,150,y+6,24,2,"#aeb898");
    }
    const furniture=group("yard-furniture");furniture.setAttribute("data-part","furniture");g.append(furniture);
    const f=furniture;
    if(style==="workshop"||style==="music") {
      const live=motif==="music"||style==="music";
      stage(f,p,24,198,96,live);
      if(live) {
        rect(f,24,194,96,24,"#85778c");rect(f,24,194,96,4,"#a499ad");
        for(let x=32;x<116;x+=16)rect(f,x,198,4,16,"#6e647d");
        speaker(f,p,28,202);speaker(f,p,60,214,true);guitar(f,p,88,184);
        rect(f,52,192,4,24,p.wood[2]);rect(f,46,190,16,4,C.stoneDark);
        bulletin(f,p,200,188,88);crate(f,p,208,236,28);crate(f,p,260,232,28);
      } else {
        toolRack(f,p,28,200,84);crate(f,p,40,236,28);crate(f,p,80,236,28);
        rect(f,204,228,92,8,p.wood[1]);rect(f,208,236,4,20,p.wood[2]);rect(f,284,236,4,20,p.wood[2]);
        rect(f,220,212,32,16,C.stoneDark);rect(f,216,208,40,8,C.stone);rect(f,228,204,16,4,C.stoneLight);
        toolRack(f,p,264,192,28);
      }
    } else if(style==="architecture") {
      rect(f,24,224,96,8,p.wood[1]);rect(f,28,232,4,24,p.wood[2]);rect(f,108,232,4,24,p.wood[2]);
      blueprint(f,p,28,196,56,28);
      // Architectural model: three clearly separate blocks and small glazing.
      rect(f,88,208,12,16,C.stone);rect(f,100,196,16,28,C.stoneLight);rect(f,96,192,24,4,p.roof[0]);rect(f,104,200,4,8,C.glassShade);
      rect(f,208,216,4,36,p.wood[2]);rect(f,284,216,4,36,p.wood[2]);
      if(motif==="finance")screen(f,p,200,184,96,"finance");else blueprint(f,p,200,180,96,40);
      rect(f,228,228,48,12,C.cream);rect(f,232,230,36,2,"#85a39d");rect(f,240,226,4,16,p.wood[0]);
      crate(f,p,204,236,24);plantPot(f,p,272,224);
    } else if(style==="police") {
      const desk=group("checking-desk");f.append(desk);
      rect(desk,24,226,96,8,p.wood[0]);rect(desk,24,226,96,2,p.wood[1]);
      rect(desk,28,234,4,22,p.wood[2]);rect(desk,108,234,4,22,p.wood[2]);
      rect(desk,34,210,32,16,C.cream);rect(desk,40,212,2,12,p.roof[1]);
      for(let y=214;y<224;y+=4)rect(desk,46,y,14,2,p.wall[2]);
      rect(desk,80,220,28,6,p.wood[2]);rect(desk,92,196,4,24,p.wood[0]);
      rect(desk,76,194,20,4,p.wood[1]);rect(desk,72,198,12,6,p.roof[1]);rect(desk,74,204,8,4,C.gold);
      rect(desk,94,208,14,10,"#bba584");rect(desk,100,208,4,10,C.cream);
      evidenceCabinet(f,p,204,188);
    } else if(style==="inspection") {
      const desk=group("audit-desk");f.append(desk);
      rect(desk,24,226,96,8,p.wood[0]);rect(desk,24,226,96,2,p.wood[1]);
      rect(desk,28,234,4,22,p.wood[2]);rect(desk,108,234,4,22,p.wood[2]);
      auditMark(desk,p,32,196);
      for(const [x,y,w,color] of [[74,218,36,p.roof[2]],[78,210,32,p.wood[1]],[72,202,36,p.roof[0]]]) {
        rect(desk,x,y,w,6,color);rect(desk,x+4,y+2,w-8,2,C.cream);
      }
      const monitors=group("monitoring-console");f.append(monitors);
      rect(monitors,204,226,92,10,p.wood[0]);rect(monitors,204,226,92,2,p.wood[1]);
      rect(monitors,208,236,4,20,p.wood[2]);rect(monitors,284,236,4,20,p.wood[2]);
      for(const x of [206,254]) {
        rect(monitors,x,190,40,28,p.wood[2]);rect(monitors,x+4,194,32,20,"#789c92");
        rect(monitors,x+16,218,8,8,p.wood[2]);rect(monitors,x+10,224,20,2,p.wood[1]);
        for(let row=0;row<3;row++) {rect(monitors,x+8,198+row*4,4,2,C.cream);rect(monitors,x+16,198+row*4,16-row*4,2,"#bfd0af");}
      }
      rect(monitors,224,230,50,4,p.wall[1]);for(let x=228;x<272;x+=8)rect(monitors,x,230,4,2,p.roof[2]);
      rect(monitors,232,242,36,14,p.wall[2]);rect(monitors,236,246,28,2,p.wood[2]);rect(monitors,236,252,8,2,C.gold);
    } else if(style==="farm"||motif==="poultry") {
      // A distinct roofed run, fenced ground and separate feed/water equipment.
      rect(f,200,212,100,44,"#dacda4");rect(f,204,214,92,4,"#e9ddb5");
      rect(f,204,190,4,64,p.wood[2]);rect(f,292,190,4,64,p.wood[2]);
      tiledRoof(f,p,204,178,92,4,6);
      bird(f,210,218,false,true);bird(f,258,212,true,false);
      fence(f,p,200,232,100);
      rect(f,28,212,28,32,"#cdb886");rect(f,32,208,20,4,"#e5d3a2");rect(f,32,220,20,4,"#ac9567");
      rect(f,80,238,40,8,"#89a99e");rect(f,84,238,32,4,"#c1d9cc");
      bird(f,72,202,true,true);rect(f,48,246,24,8,C.gold);rect(f,52,244,4,4,C.cream);rect(f,62,244,4,4,C.cream);
    } else if(style==="angling") {
      const yy=206;
      rect(f,24,yy,8,50,p.wood[0]);rect(f,112,yy,8,50,p.wood[0]);rect(f,24,yy,96,6,p.wood[1]);
      for(const [x,fish] of [[30,"carp"],[56,"crucian"],[82,"trout"]])driedFish(f,x,yy+18,fish);
      rect(f,32,yy+6,80,2,p.wood[2]);rect(f,30,250,80,4,p.wood[2]);
      rect(f,88,242,16,14,"#b99d6f");rect(f,88,242,16,4,"#d8be88");
      pond(f,212,214,68,36);jetty(f,p,200,218,48);boat(f,p,220,238);
      for(const [x,y] of [[202,206],[284,208],[288,234]]) {
        rect(f,x,y,2,20,C.greenDark);rect(f,x+6,y+4,2,16,C.green);rect(f,x-2,y-4,6,8,"#ad9567");
      }
    } else if(style==="observatory") {
      telescope(f,p,32,198);
      bookshelf(f,p,204,192,88,52);rect(f,208,244,4,12,p.wood[2]);rect(f,284,244,4,12,p.wood[2]);
      rect(f,90,238,28,16,C.cream);rect(f,94,240,16,2,p.roof[2]);rect(f,96,234,4,4,C.gold);
      rect(f,220,242,24,10,"#86a5a0");rect(f,224,244,16,2,C.cream);
    } else if(style==="command") {
      bulletin(f,p,24,188,96);discussion(f,p,208,198);
    } else if(style==="finance") {
      screen(f,p,24,192,96,"finance");rect(f,28,228,4,24,p.wood[2]);rect(f,108,228,4,24,p.wood[2]);
      discussion(f,p,204,198);rect(f,220,206,12,6,C.gold);rect(f,222,202,8,4,C.cream);
    } else if(style==="school") {
      bookshelf(f,p,24,192,92,52);rect(f,28,244,4,12,p.wood[2]);rect(f,108,244,4,12,p.wood[2]);
      discussion(f,p,208,198);rect(f,284,226,12,20,"#9cb38a");rect(f,286,226,8,2,C.cream);
    } else if(motif==="hiking") {
      // Climbing pack, rolled mat, boots and a route board make the cottage a
      // trailhead. Everything is outside the clear central walking corridor.
      rect(f,32,210,36,40,p.roof[2]);rect(f,36,214,28,32,p.roof[0]);rect(f,40,206,20,4,p.wood[1]);
      rect(f,36,228,28,14,p.roof[1]);rect(f,40,234,20,2,p.wood[2]);rect(f,38,216,4,30,p.wood[1]);rect(f,58,216,4,30,p.wood[1]);
      rect(f,28,204,44,8,C.green);rect(f,30,204,4,8,C.greenLight);rect(f,64,204,4,8,C.greenDark);
      for(const x of [80,100]){rect(f,x,226,10,22,p.wood[2]);rect(f,x,226,10,4,p.wood[1]);rect(f,x-4,244,16,8,C.stoneDark);}
      bulletin(f,p,204,188,92);blueprint(f,p,212,198,72,24);
      [[284,244,16,8],[288,236,12,8],[290,230,8,6]].forEach(v=>rect(f,...v,C.stone));
    } else {
      discussion(f,p,28,198);plantPot(f,p,88,218);
      rect(f,208,228,84,8,p.wood[1]);rect(f,212,236,4,18,p.wood[2]);rect(f,284,236,4,18,p.wood[2]);
      plantPot(f,p,216,202);plantPot(f,p,264,214);
      fence(f,p,208,232,92);
    }
    return g;
  }
  function labelY(agent) { return layout(agent).label.y; }
  window.StarhavenHouseArt=Object.freeze({house,yard,layout,labelY});
})();

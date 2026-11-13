"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { execFileSync } = require("node:child_process");
const journey = require("../web/scout-journey.js");
const { ROUTE, sample, along, prefix, length } = journey;
const start = Date.parse("2026-11-11T09:00:00+08:00");
const at = seconds => new Date(start + seconds * 1000).toISOString();
const active = { started_at: at(0), forest_arrival_at: at(12), return_started_at: null, arrival_at: null, home_at: null, return_from_progress: 1, cargo_count: 0, can_start: false };
const done = { ...active, return_started_at: at(14), arrival_at: at(26), home_at: at(28), cargo_count: 3 };
const near = (a,b) => assert.ok(Math.hypot(a[0]-b[0],a[1]-b[1]) < 0.05, `${a} != ${b}`);
let checks = 0;
function check(name, run) { run(); checks++; console.log(`PASS ${name}`); }

// Mount the real workspace renderer and inspect its SVG, including the actual
// CSS placement. Exported route metadata alone cannot catch shifted paving.
function renderedYard() {
  const node = (tag = "div", cls = "") => ({
    tag, className: cls, style: {}, dataset: {}, children: [], innerHTML: "", listeners: {},
    append(...children) { this.children.push(...children); },
    setAttribute() {}, addEventListener(event, callback) { this.listeners[event] = callback; },
    querySelector(selector) { return this.children.find(child => `.${child.className}` === selector) || null; },
  });
  const controls = new Map(), world = node(), camera = [];
  const context = {
    window: { StarhavenScoutJourney: journey, StarhavenForestArt: { cargo: () => "" }, addEventListener() {} },
    document: { hidden: true, querySelectorAll: () => [], addEventListener() {},
      querySelector(selector) { if (!controls.has(selector)) controls.set(selector, node()); return controls.get(selector); } },
    matchMedia: () => ({ matches: false, addEventListener() {} }), clearTimeout() {},
  };
  for (const file of ["scout-art.js", "forest-art.js", "scout-workspace.js"]) {
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web", file), "utf8"), context, { filename: file });
  }
  context.window.StarhavenScoutWorkspace.init({ el: node, api: () => new Promise(() => {}), getSelected: () => null, centerMap: point => camera.push(point) });
  context.window.StarhavenScoutWorkspace.renderMap(world, { core: [960, 330, 960, 660] });
  return { apron: world.querySelector(".scout-yard-path"), rack: world.querySelector(".scout-map-yard"),
    sign: world.querySelector(".forest-map-sign"), forest: context.window.StarhavenForestArt, art: context.window.StarhavenScoutArt, controls, camera };
}
const fixture = renderedYard();
const attributes = tag => Object.fromEntries([...tag.matchAll(/([\w-]+)="([^"]*)"/g)].map(match => [match[1], match[2]]));
const svg = attributes(fixture.apron.innerHTML.match(/<svg\b[^>]*>/)[0]);
const paving = attributes(fixture.apron.innerHTML.match(/<polyline\b[^>]*>/)[0]);
const view = svg.viewBox.split(/\s+/).map(Number);
const scale = [parseFloat(fixture.apron.style.width) / view[2], parseFloat(fixture.apron.style.height) / view[3]];
const worldPoint = ([x, y]) => [parseFloat(fixture.apron.style.left) + (x - view[0]) * scale[0], parseFloat(fixture.apron.style.top) + (y - view[1]) * scale[1]];
const paintedPoints = paving.points.trim().split(/\s+/).map(point => worldPoint(point.split(",").map(Number)));
const halfWidth = Number(paving["stroke-width"]) * scale[0] / 2;
const stone = paintedPoints.slice(1).map((point, i) => [Math.min(point[0], paintedPoints[i][0]) - halfWidth,
  Math.min(point[1], paintedPoints[i][1]) - halfWidth, Math.max(point[0], paintedPoints[i][0]) + halfWidth,
  Math.max(point[1], paintedPoints[i][1]) + halfWidth]);
const contains = (rect, [x,y]) => x >= rect[0] && x <= rect[2] && y >= rect[1] && y <= rect[3];
const overlap = (a,b) => a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1];
const townMap = JSON.parse(execFileSync(process.env.PYTHON || "python3", ["-c",
  "import json,server,xml.etree.ElementTree as ET; ET.register_namespace('', 'http://www.w3.org/2000/svg'); town=ET.parse('web/assets/town.svg').getroot(); statue=next(node for node in town if node.get('id')=='village-landmark'); print(json.dumps(dict(origin=[server.WORLD_X,server.WORLD_Y],world=[server.WORLD_W,server.WORLD_H],core=[server.CORE_X,server.CORE_Y],plot=[server.PLOT_W,server.PLOT_H],slots=server.PLOT_SLOTS,network=server.ROAD_NETWORK,statue=ET.tostring(statue,encoding='unicode'))))"],
  { cwd: path.join(__dirname, ".."), encoding: "utf8" }));
const plots = townMap.slots.map(([x,y]) => [x-townMap.plot[0]/2,y-townMap.plot[1]/2,x+townMap.plot[0]/2,y+townMap.plot[1]/2]);

function forestShapes(markup) {
  const stack = [{ sx:1, sy:1, tx:0, ty:0, prop:null }], shapes = [];
  for (const match of markup.matchAll(/<(\/)?(g|rect|path)\b([^>]*)>/g)) {
    const [, closing, tag, raw] = match;
    if (closing) { if (tag === "g") stack.pop(); continue; }
    const attr = attributes(raw), parent = stack.at(-1);
    if (tag === "g") {
      const group = { ...parent, prop: attr["data-forest-prop"] || (attr["data-forest-tree"] !== undefined ? "tree" : parent.prop) };
      const rest = (attr.transform || "").replace(/(translate|scale)\(([^)]+)\)/g, (_, name, values) => {
        const [x,y] = values.split(/[\s,]+/).map(Number);
        if (name === "translate") { group.tx += group.sx*x; group.ty += group.sy*(y || 0); }
        else { group.sx *= x; group.sy *= y ?? x; }
        return "";
      });
      assert.match(rest, /^\s*$/); stack.push(group); continue;
    }
    let bounds;
    if (tag === "rect") {
      const [x,y,w,h] = ["x","y","width","height"].map(key => Number(attr[key]));
      bounds = [x,y,x+w,y+h];
    } else if (parent.prop) {
      // Crown paths consist of straight integer steps. Include every vertex,
      // rather than approximating the trees by their trunk/anchor coordinates.
      const tokens = attr.d.match(/[a-z]|-?\d+(?:\.\d+)?/ig), points = [];
      let x=0,y=0,first=[0,0],command="",i=0;
      while (i<tokens.length) {
        if (/^[a-z]$/i.test(tokens[i])) command=tokens[i++];
        const lower=command.toLowerCase(), relative=lower===command;
        if (lower==="m" || lower==="l") {
          x=(relative?x:0)+Number(tokens[i++]); y=(relative?y:0)+Number(tokens[i++]);
          if(lower==="m") first=[x,y];
        } else if(lower==="h") x=(relative?x:0)+Number(tokens[i++]);
        else if(lower==="v") y=(relative?y:0)+Number(tokens[i++]);
        else if(lower==="z") { [x,y]=first; command=""; }
        else assert.fail(`unsupported forest path command ${command}`);
        points.push([x,y]);
      }
      bounds=[Math.min(...points.map(p=>p[0])),Math.min(...points.map(p=>p[1])),Math.max(...points.map(p=>p[0])),Math.max(...points.map(p=>p[1]))];
    } else continue;
    const [x1,y1,x2,y2]=bounds;
    shapes.push({ bounds:[x1*parent.sx+parent.tx,y1*parent.sy+parent.ty,x2*parent.sx+parent.tx,y2*parent.sy+parent.ty], prop:parent.prop, fill:attr.fill });
  }
  assert.equal(stack.length,1);
  return shapes;
}
const forest = forestShapes(fixture.forest.scene({ roadNetwork:true }));
const forestPaving = forest.filter(item=>!item.prop).map(item=>item.bounds);
const statueParts = forestShapes(`<g data-forest-prop="statue">${townMap.statue}</g>`).map(({bounds})=>
  [bounds[0]+townMap.core[0],bounds[1]+townMap.core[1],bounds[2]+townMap.core[0],bounds[3]+townMap.core[1]]);

check("all forest crowns and props clear the relocated homes and stay inside the world", () => {
  const world = [townMap.origin[0],townMap.origin[1],townMap.origin[0]+townMap.world[0],townMap.origin[1]+townMap.world[1]];
  assert.equal(fixture.forest.trees.length,15);
  const props=forest.filter(item=>item.prop);
  for(const kind of ["tree","bench","lamp","sign","flowers"]) assert.ok(props.some(item=>item.prop===kind));
  for(const {bounds,prop} of props) {
    assert.ok(bounds[0]>=world[0] && bounds[1]>=world[1] && bounds[2]<=world[2] && bounds[3]<=world[3], `${prop} outside world: ${bounds}`);
    assert.ok(!plots.some(plot=>overlap(bounds,plot)), `${prop} overlaps a home: ${bounds}`);
  }
  for(const bounds of forestPaving) assert.ok(!plots.some(plot=>overlap(bounds,plot)), `forest paving crosses a home: ${bounds}`);
});
check("the relocated forest path and patrol remain continuously paved across the northern street", () => {
  const paved=[...forestPaving,...townMap.network.edges.map(edge=>edge.bounds),...townMap.network.pedestrian_accesses.map(entry=>entry.bounds)];
  for(let step=0;step<=12000;step+=10) {
    const feet=sample(active,start+step).point;
    if(feet[1]<=330) assert.ok(paved.some(rect=>contains(rect,feet)), `forest route leaves paving at ${feet}`);
    assert.ok(!plots.some(plot=>contains(plot,feet)), `traveller enters a home at ${feet}`);
  }
  for(let step=0;step<=28000;step+=50) {
    const pose=sample(active,start+12000+step);
    assert.equal(pose.phase,"working"); assert.equal(pose.point[1],-280);
    assert.ok(forestPaving.some(rect=>contains(rect,pose.point)), `patrol leaves clearing at ${pose.point}`);
  }
});
check("forest navigation controls and cargo placement follow the relocated clearing", () => {
  fixture.sign.listeners.click(); fixture.controls.get("#map-forest").listeners.click();
  assert.deepEqual(JSON.parse(JSON.stringify(fixture.camera)),[[1380,-300],[1380,-300]]);
  const css=fs.readFileSync(path.join(__dirname,"../web/scout-workspace.css"),"utf8");
  const top=cls=>Number(css.match(new RegExp(`\\.${cls}\\{[^}]*?top:(-?\\d+)px`))[1]);
  assert.equal(top("forest-map-sign"),-125); assert.equal(top("forest-cargo"),-318);
});
check("saved normalized progress restores old journeys onto the current path without changing work", () => {
  for(const progress of [.1,.42,.7,.95]) {
    const seconds=12*progress;
    const saved={...active,return_started_at:at(seconds),arrival_at:at(seconds*2),home_at:at(seconds*2),return_from_progress:progress,cargo_count:0};
    const serialized=JSON.stringify(saved), resumed=JSON.parse(serialized);
    near(sample(resumed,start+seconds*1000).point,along(ROUTE,progress).point);
    near(sample(resumed,start+seconds*1000).point,sample(active,start+seconds*1000-.01).point);
    near(sample(resumed,start+seconds*1500).point,along(ROUTE,progress/2).point);
    near(sample(resumed,start+seconds*2000).point,ROUTE[0]);
    assert.equal(JSON.stringify(resumed),serialized);
  }
});

check("yard paving stops at the eastern ring while the traveller follows existing village roads", () => {
  assert.deepEqual(paintedPoints, [[1590,930],[1510,930],[1510,796]]);
  assert.deepEqual(ROUTE.slice(3), [[1510,674],[1468,674],[1468,634],[1420,634],[1420,280],[1380,280],[1380,-144],[1320,-144],[1320,-280]]);
  assert.equal(parseFloat(fixture.rack.style.left),1530); assert.equal(parseFloat(fixture.rack.style.top),780);
  assert.ok(stone.every(rect=>rect[1]>=782), "yard paving must not cover the ring or main street");
  assert.equal(scale[0], 1); assert.equal(scale[1], 1);
  assert.equal(halfWidth * 2, 28);
  assert.equal(paving["stroke-linecap"], "square"); assert.equal(paving["stroke-linejoin"], "miter");
  assert.equal((fixture.apron.innerHTML.match(/<polyline\b/g) || []).length, 1);
});
check("the short yard approach supports outbound and returning feet through its corner", () => {
  for (let seconds = 0; seconds <= 28; seconds += .01) {
    const pose = sample(done, start + seconds * 1000);
    if (pose.point[1] >= 782) assert.ok(stone.some(rect => contains(rect, pose.point)), `feet leave yard paving at ${pose.point}`);
  }
  // Full-width corner squares catch seams that centre-line checks can miss.
  for (const [cx,cy] of [[1510,930]]) {
    for (let x = cx - 13; x <= cx + 13; x++) for (let y = cy - 13; y <= cy + 13; y++) {
      assert.ok(stone.some(rect => contains(rect, [x,y])), `open paving corner at ${x},${y}`);
    }
  }
});
check("paving and travelling feet avoid the actual material rack and yard props", () => {
  const css = fs.readFileSync(path.join(__dirname, "../web/scout-workspace.css"), "utf8");
  const yardCSS = css.match(/\.scout-map-yard\{([^}]+)\}/)[1];
  const yardWidth = Number(yardCSS.match(/(?:^|;)width:(\d+)px/)[1]);
  const yardHeight = Number(yardCSS.match(/(?:^|;)height:(\d+)px/)[1]);
  const yardSVG = attributes(fixture.rack.innerHTML.match(/<svg\b[^>]*>/)[0]).viewBox.split(/\s+/).map(Number);
  const props = [...fixture.rack.innerHTML.matchAll(/<rect\b[^>]*>/g)].map(match => {
    const attr = attributes(match[0]), x = Number(attr.x), y = Number(attr.y);
    const origin = [parseFloat(fixture.rack.style.left), parseFloat(fixture.rack.style.top)];
    return [origin[0] + x * yardWidth / yardSVG[2], origin[1] + y * yardHeight / yardSVG[3],
      origin[0] + (x + Number(attr.width)) * yardWidth / yardSVG[2], origin[1] + (y + Number(attr.height)) * yardHeight / yardSVG[3]];
  });
  assert.ok(props.length > 20, "the real yard artwork must be inspected");
  for (const rect of stone) assert.ok(!props.some(prop => overlap(rect, prop)), `paving overlaps a yard prop at ${rect}`);
  for (let progress = 0; progress <= 1; progress += .001) {
    const feet = along(ROUTE, progress).point;
    assert.ok(!props.some(prop => contains(prop, feet)), `route crosses a yard prop at ${feet}`);
  }
});
check("the cart has a separate parking bay in every load and activity state", () => {
  const group = (svg, name) => {
    const content=svg.match(new RegExp(`<g class="${name}">([\\s\\S]*?)</g>`))?.[1];
    assert.ok(content,`missing actual ${name} drawing`);
    return [...content.matchAll(/<rect\b[^>]*>/g)].map(match=>{
      const attr=attributes(match[0]), x=Number(attr.x), y=Number(attr.y);
      return { bounds:[x,y,x+Number(attr.width),y+Number(attr.height)],fill:attr.fill };
    });
  };
  for(const phase of ["idle","gathering","carrying","sorting"]) for(const rawCount of [0,1,2,3,9]) {
    const input={phase,materialCount:6,rawCount}, saved=JSON.stringify(input), svg=fixture.art.yard(input);
    const rack=group(svg,"scout-material-rack"), axe=group(svg,"scout-axe"), cart=group(svg,"scout-cart");
    for(const {bounds} of cart) {
      assert.ok(bounds.every(Number.isInteger), `cart must retain crisp pixel geometry: ${bounds}`);
      assert.ok(bounds[0]>=166 && bounds[1]>=94 && bounds[2]<=238 && bounds[3]<=154, `cart leaves its parking bay: ${bounds}`);
      assert.ok(![...rack,...axe].some(prop=>overlap(bounds,prop.bounds)), `loaded cart overlaps another tool: ${bounds}`);
    }
    assert.equal(cart.filter(shape=>shape.fill==="#a8845d").length,Math.min(3,rawCount),"cart logs must follow actual cargo, including an empty cart");
    assert.equal(rack.filter(shape=>shape.fill==="#a8845d").length,6,"parking must not change saved rack materials");
    assert.equal(JSON.stringify(input),saved);
    const [x,y,w,h]=fixture.art.hotspots.yard.cart;
    assert.ok(cart.every(({bounds})=>bounds[0]>=x && bounds[1]>=y && bounds[2]<=x+w && bounds[3]<=y+h),"cart hotspot must cover the actual loaded cart");
  }
  const empty=fixture.art.yard({materialCount:0,rawCount:0});
  assert.ok(![...group(empty,"scout-material-rack"),...group(empty,"scout-cart")].some(shape=>shape.fill==="#a8845d"),"empty storage must not invent logs");
});
check("legacy materials stay home without a journey", () => {
  assert.equal(sample({ started_at: at(0), forest_arrival_at: null, can_start: true }, start + 50000).phase, "home");
  assert.equal(sample(null).phase, "home");
});
check("road segments remain orthogonal and avoid actual statue parts and northern homes", () => {
  ROUTE.slice(1).forEach((p,i) => assert.ok(p[0] === ROUTE[i][0] || p[1] === ROUTE[i][1]));
  assert.ok(statueParts.length>10, "actual statue artwork must be inspected");
  for (let t=0;t<=1;t+=.001) {
    const [x,y]=along(ROUTE,t).point;
    if (y>=-50 && y<=230) assert.ok(x>1357 && x<1403);
    assert.ok(!statueParts.some(part=>contains(part,[x,y])), `route crosses actual statue at ${x},${y}`);
  }
});
check("outbound runs exactly once then continues at the forest", () => {
  near(sample(active,start).point,ROUTE[0]);
  assert.equal(sample(active,start+6000).phase,"outbound");
  near(sample(active,start+12000).point,ROUTE.at(-1));
  assert.equal(sample(active,start+12000).phase,"working");
  assert.equal(sample(active,start+90000).phase,"working");
});
check("fast success reaches forest before taking saved cargo home", () => {
  assert.equal(sample(done,start+5000).phase,"outbound");
  assert.equal(sample(done,start+13000).phase,"working");
  assert.equal(sample(done,start+18000).phase,"returning");
  assert.equal(sample(done,start+26000).phase,"unloading");
  assert.equal(sample(done,start+28000).phase,"home");
  assert.equal(sample(done,start+18000).cargo,3);
});
check("early failure turns around at the actual point without teleporting", () => {
  const failed = {...active,return_started_at:at(3),arrival_at:at(6),home_at:at(6),return_from_progress:.25,cargo_count:0};
  near(sample(failed,start+3000).point,along(ROUTE,.25).point);
  near(sample(failed,start+3000).point,sample(failed,start+2999.9).point);
  assert.equal(sample(failed,start+4500).phase,"returning");
  assert.equal(sample(failed,start+6000).phase,"home");
  assert.equal(sample(failed,start+4500).cargo,0);
});
check("return starts from the active forest clearing", () => {
  const slow = {...done,return_started_at:at(30),arrival_at:at(42),home_at:at(44)};
  near(sample(slow,start+30000).point,sample(slow,start+29999.9).point);
  near(sample(slow,start+41999.9).point,sample(slow,start+42000).point);
});
check("refreshing and reopening recover the same position without cargo mutations", () => {
  const serialized = JSON.stringify(done);
  const first = sample(done,start+19000);
  assert.deepEqual(sample(JSON.parse(serialized),start+19000),first);
  assert.equal(JSON.stringify(done),serialized);
  assert.equal(sample(done,start+999000).phase,"home");
});
check("reduced motion preserves work phase while keeping the sprite still", () => {
  near(sample(active,start+1000,true).point,sample(active,start+11000,true).point);
  assert.equal(sample(done,start+19000,true).phase,"returning");
  assert.equal(sample(done,start+19000,true).moving,false);
  near(sample(done,start+30000,true).point,ROUTE[0]);
});
check("persisted delivered state wins when the system clock moves backwards", () => {
  const delivered = {...done,phase:"home",can_start:true};
  assert.equal(sample(delivered,start+19000).phase,"home");
  near(sample(delivered,start+19000).point,ROUTE[0]);
});
check("route prefixes clamp invalid progress at town and forest", () => {
  assert.equal(length(prefix(-1)),0);
  near(prefix(2).at(-1),ROUTE.at(-1));
});
console.log(`${checks} journey checks passed.`);

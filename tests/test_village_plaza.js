"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { execFileSync } = require("node:child_process");
const project = path.join(__dirname, "..");
const python = process.env.PYTHON || "python3";
const NS = "http://www.w3.org/2000/svg";
const read = file => fs.readFileSync(path.join(project, file), "utf8");

// ElementTree reads the actual XML, including nested groups. No road outline or
// server landmark coordinates are reproduced as a second renderer in this test.
const xmlReader = `import json,sys,xml.etree.ElementTree as ET

def tree(n):
 return dict(tag=n.tag.split('}')[-1], attrs=n.attrib, children=[tree(c) for c in n])
`;
const fixture = JSON.parse(execFileSync(python, ["-c", xmlReader + `
import server
references=[dict(id='row-'+style,pair=pair,plot=dict(slot=slot,pos=server.TownApp._slot_pos(slot)),house=server.design_house('row-'+style,style,role,'','','#879780')) for pair,style,role,slot in [('linghang','architecture','架构师',1),('scout','angling','钓鱼',4)]]
print(json.dumps(dict(map=dict(core=[server.CORE_X,server.CORE_Y,server.CORE_W,server.CORE_H],world=[server.WORLD_W,server.WORLD_H],origin=[server.WORLD_X,server.WORLD_Y],plot=[server.PLOT_W,server.PLOT_H],slots=server.PLOT_SLOTS,road_network=server.ROAD_NETWORK),homes=[dict(id=k,house=server.painted_house(k)) for k in server.PAINTED_HOMES],references=references,town=tree(ET.parse('web/assets/town.svg').getroot()))))
`], { cwd: project, encoding: "utf8" }));
function parseXML(markup) {
  return JSON.parse(execFileSync(python, ["-c", xmlReader + "print(json.dumps(tree(ET.fromstring(sys.stdin.read()))))"], { input: markup, encoding: "utf8" }));
}
function flatten(tree) { return [tree, ...tree.children.flatMap(flatten)]; }
function find(tree, key, value) {
  const matches = flatten(tree).filter(node => node.attrs[key] === value);
  assert.equal(matches.length, 1, `expected one ${key}=${value}`);
  return matches[0];
}

const state = new WeakMap();
function element(namespaceURI, localName) {
  const node = {
    namespaceURI, localName, style: {}, dataset: {},
    getAttribute(key) { return state.get(this).attrs[key] ?? null; },
    setAttribute(key, value) { state.get(this).attrs[key] = String(value); },
    removeAttribute(key) { delete state.get(this).attrs[key]; },
    get children() { return state.get(this).children; },
    append(...children) { for (const child of children) { state.get(child).parent = this; this.children.push(child); } },
    appendChild(child) { this.append(child); return child; },
    remove() { const parent = state.get(this).parent; if (parent) parent.children.splice(parent.children.indexOf(this), 1); },
    cloneNode(deep) { const copy = element(namespaceURI, localName); for (const [key, value] of Object.entries(state.get(this).attrs)) copy.setAttribute(key, value); if (deep) copy.append(...this.children.map(child => child.cloneNode(true))); return copy; },
    matches(selector) { return selector.split(",").some(part => part[0] === "." && (this.getAttribute("class") || "").split(/\s+/).includes(part.slice(1))); },
    querySelector(selector) { return this.children.find(child => child.matches(selector)) || null; },
    addEventListener() {},
  };
  state.set(node, { attrs: {}, children: [] });
  return node;
}
function snapshot(node) { return { tag: node.localName, attrs: { ...state.get(node).attrs }, children: node.children.map(snapshot) }; }
function svgNode(tag, attrs = {}) { const node = element(NS, tag); for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value); return node; }
const document = { createElementNS: element, createDocumentFragment: () => element(null, "fragment") };
const browser = { window: {}, document };
vm.runInNewContext(read("web/house-art.js"), browser);
vm.runInNewContext(read("web/scout-art.js"), browser);
vm.runInNewContext(read("web/road-art.js"), browser);
const houseArt = browser.window.StarhavenHouseArt;
const journey = require("../web/scout-journey.js");
const app = read("web/app.js");
const appStart = app.indexOf("  function plotOrigin(agent, map) {");
const appEnd = app.indexOf("\n  function renderPlot(agent, map)", appStart);
assert.ok(appStart >= 0 && appEnd > appStart, "missing actual plot entry points");
const integration = {
  ...browser, svgNode,
  pixelRect: (x, y, width, height, fill) => svgNode("rect", { x, y, width, height, fill }),
  pixelPath: (d, fill) => svgNode("path", { d, fill }),
  mulberry32: () => () => 0.5,
};
vm.runInNewContext(app.slice(appStart, appEnd) + "\nthis.plotOrigin=plotOrigin;this.plotSvg=plotSvg;", integration);

function orthogonalPath(d) {
  const tokens = d.match(/[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?/g) || [];
  assert.match(d.replace(/[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?/g, ""), /^[\s,]*$/);
  let i = 0, command, x = 0, y = 0, points = [], polygons = [];
  while (i < tokens.length) {
    if (/^[A-Za-z]$/.test(tokens[i])) command = tokens[i++];
    assert.ok(/^[MLHVZmlhvz]$/.test(command), `non-pixel path command ${command}`);
    const relative = command === command.toLowerCase(), op = command.toUpperCase();
    if (op === "Z") { assert.ok(points.length >= 3); polygons.push(points); [x, y] = points[0]; points = []; command = undefined; continue; }
    const number = () => { const value = Number(tokens[i++]); assert.ok(Number.isFinite(value), "incomplete path command"); return value; };
    if (op === "M" || op === "L") { const nx = number(), ny = number(); x = nx + (relative ? x : 0); y = ny + (relative ? y : 0); }
    else if (op === "H") x = number() + (relative ? x : 0);
    else y = number() + (relative ? y : 0);
    points.push([x, y]);
    if (op === "M") command = relative ? "l" : "L";
  }
  assert.equal(points.length, 0, "pixel path must close every contour");
  return polygons;
}
function bounds(points) { return [Math.min(...points.map(p => p[0])), Math.min(...points.map(p => p[1])), Math.max(...points.map(p => p[0])), Math.max(...points.map(p => p[1]))]; }
function geometry(tree, origin = [0, 0], scale = [1, 1]) {
  const result = [];
  function visit(node, parts, ids, parent, inherited) {
    const local = [1, 1, 0, 0];
    const remainder = (node.attrs.transform || "").replace(/(translate|scale)\(([^)]*)\)/g, (_, name, raw) => {
      const values = raw.trim().split(/[\s,]+/).map(Number);
      assert.ok(values.length >= 1 && values.length <= 2 && values.every(Number.isFinite));
      if (name === "translate") { local[2] += local[0] * values[0]; local[3] += local[1] * (values[1] || 0); }
      else { local[0] *= values[0]; local[1] *= values[1] ?? values[0]; }
      return "";
    });
    assert.match(remainder, /^\s*$/, "geometry parser must not silently ignore a transform");
    const matrix = [parent[0] * local[0], parent[1] * local[1], parent[0] * local[2] + parent[2], parent[1] * local[3] + parent[3]];
    const fillRule = node.attrs["fill-rule"] || inherited.fillRule || "nonzero";
    const fill = node.attrs.fill || inherited.fill;
    const equipment = node.attrs["data-equipment"] || inherited.equipment;
    assert.ok(["nonzero", "evenodd"].includes(fillRule));
    const nextParts = [...parts, node.attrs["data-part"]].filter(Boolean), nextIds = [...ids, node.attrs.id].filter(Boolean);
    let polygons = [];
    if (node.tag === "rect") {
      const [x, y, w, h] = ["x", "y", "width", "height"].map(key => Number(node.attrs[key]));
      assert.ok([x, y, w, h].every(Number.isFinite) && w > 0 && h > 0);
      polygons = [[[x, y], [x + w, y], [x + w, y + h], [x, y + h]]];
    } else if (node.tag === "path") polygons = orthogonalPath(node.attrs.d);
    else if (node.tag === "polygon") {
      const values = node.attrs.points.trim().split(/[\s,]+/).map(Number);
      assert.ok(values.length >= 6 && values.length % 2 === 0 && values.every(Number.isFinite));
      polygons = [Array.from({ length: values.length / 2 }, (_, i) => values.slice(i * 2, i * 2 + 2))];
    } else assert.ok(["g", "svg", "fragment"].includes(node.tag), `unsupported geometry ${node.tag}`);
    if (polygons.length) {
      polygons = polygons.map(points => points.map(([x, y]) => [matrix[2] + x * matrix[0], matrix[3] + y * matrix[1]]));
      result.push({ node, parts: nextParts, ids: nextIds, fillRule, fill, equipment, polygons, bounds: bounds(polygons.flat()) });
    }
    node.children.forEach(child => visit(child, nextParts, nextIds, matrix, { fillRule, fill, equipment }));
  }
  visit(tree, [], [], [scale[0], scale[1], origin[0], origin[1]], {});
  return result;
}
function contains(shape, x, y) {
  let crossings = 0, winding = 0;
  for (const points of shape.polygons) {
    for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
      const a = points[i], b = points[j];
      if ((a[1] > y) !== (b[1] > y) && x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]) {
        crossings++;
        winding += b[1] > a[1] ? 1 : -1;
      }
    }
  }
  return shape.fillRule === "evenodd" ? crossings % 2 === 1 : winding !== 0;
}
const overlaps = (a, b) => a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1];
function intersects(shape, rect) {
  if (!overlaps(shape.bounds, rect)) return false;
  // For orthogonal polygons, occupancy is constant in each cell between vertex
  // coordinates. This checks the complete area without relying on a coarse grid.
  const axis = dim => [...new Set([rect[dim], rect[dim + 2], ...shape.polygons.flat().map(p => p[dim]).filter(v => v > rect[dim] && v < rect[dim + 2])])].sort((a, b) => a - b);
  const xs = axis(0), ys = axis(1);
  for (let i = 1; i < xs.length; i++) for (let j = 1; j < ys.length; j++) if (contains(shape, (xs[i - 1] + xs[i]) / 2, (ys[j - 1] + ys[j]) / 2)) return true;
  return false;
}
function shapesIntersect(a, b) {
  if (!overlaps(a.bounds, b.bounds)) return false;
  const rect = [Math.max(a.bounds[0], b.bounds[0]), Math.max(a.bounds[1], b.bounds[1]), Math.min(a.bounds[2], b.bounds[2]), Math.min(a.bounds[3], b.bounds[3])];
  const points = [...a.polygons.flat(), ...b.polygons.flat()];
  const axis = dim => [...new Set([rect[dim], rect[dim + 2], ...points.map(p => p[dim]).filter(v => v > rect[dim] && v < rect[dim + 2])])].sort((x, y) => x - y);
  const xs = axis(0), ys = axis(1);
  for (let i = 1; i < xs.length; i++) for (let j = 1; j < ys.length; j++) {
    const x = (xs[i - 1] + xs[i]) / 2, y = (ys[j - 1] + ys[j]) / 2;
    if (contains(a, x, y) && contains(b, x, y)) return true;
  }
  return false;
}
const solidShape = shape => !/^#[\da-f]{8}$/i.test(shape.fill || "") && Number(shape.node.attrs.opacity ?? 1) > 0.5;
const origin = fixture.map.core.slice(0, 2);
const worldRect = local => [local[0] + origin[0], local[1] + origin[1], local[2] + origin[0], local[3] + origin[1]];
const pavingTree = find(fixture.town, "id", "village-roads");
const paving = geometry(pavingTree, origin);
const part = name => paving.filter(shape => shape.parts.includes(name));
const surface = part("road-surface"), edges = part("road-curb"), markings = part("road-marking");
const sidewalks = part("sidewalk"), doorsteps = part("doorstep"), island = part("roundabout-island");
const paved = (x, y) => surface.some(shape => contains(shape, x, y));
const onIsland = (x, y) => island.some(shape => contains(shape, x, y));
const islandBounds = bounds(island.flatMap(shape => shape.polygons.flat()));
const ringCenter = [(islandBounds[0] + islandBounds[2]) / 2, (islandBounds[1] + islandBounds[3]) / 2];
const sections = new Map();
function roadSection(axis, position) {
  const key = `${axis}:${position}`;
  if (sections.has(key)) return sections.get(key);
  const across = axis === "horizontal" ? 1 : 0, pixels = [];
  for (let point = origin[across] + 0.5; point < origin[across] + fixture.map.core[across + 2]; point++) {
    if (axis === "horizontal" ? paved(position, point) : paved(point, position)) pixels.push(point);
  }
  assert.ok(pixels.length, `missing ${axis} street at ${position}`);
  const start = pixels[0] - 0.5, end = pixels.at(-1) + 0.5;
  assert.equal(pixels.length, end - start, `${axis} street has a gap at ${position}`);
  const result = { start, end, width: end - start, center: (start + end) / 2 };
  sections.set(key, result);
  return result;
}
const external = geometry(snapshot(browser.window.StarhavenRoadArt.render(fixture.map, { occupiedSlots: fixture.map.slots.map((_, index) => index) })));
const externalPaving = external.filter(shape => shape.parts.includes("road-surface") || shape.parts.includes("access-path") || shape.parts.includes("sidewalk"));
const externalCurbs = external.filter(shape => shape.parts.includes("road-curb"));
const statueTree = find(fixture.town, "id", "village-landmark");
const statue = geometry(statueTree, origin);
const statueBounds = bounds(statue.flatMap(shape => shape.polygons.flat()));
const landmarks = fixture.homes.map(agent => {
  const position = [...integration.plotOrigin(agent, fixture.map)];
  const layout = houseArt.layout(agent);
  const shapes = geometry(snapshot(houseArt.house(agent)), position);
  const yardTree = snapshot(houseArt.yard(agent)), furniture = geometry(yardTree, position);
  return { agent, position, layout, shapes, yardTree, furniture, door: [position[0] + layout.doorstep.x, position[1] + layout.doorstep.y] };
});
const rowReferences = fixture.references.map(agent => {
  const position = [...integration.plotOrigin(agent, fixture.map)];
  return { agent, position, shapes: geometry(snapshot(houseArt.house(agent)), position) };
});
let checks = 0;
function check(name, fn) { fn(); checks++; console.log(`PASS ${name}`); }

check("the civic streets use actual pixel surfaces, curbs, markings and separate pedestrian paving", () => {
  assert.equal(pavingTree.attrs["data-terrain"], "civic-streets");
  for (const name of ["road-surface", "road-curb", "road-marking", "road-texture", "sidewalk", "doorstep", "roundabout-island"]) assert.ok(part(name).length, `missing actual ${name} geometry`);
  for (const node of flatten(pavingTree)) {
    assert.equal(node.attrs.filter, undefined, "nested filters blur the native night renderer");
    assert.ok(!/filter\s*:/.test(node.attrs.style || ""));
    assert.ok(["g", "rect", "polygon", "path"].includes(node.tag));
    if (node.tag === "rect") assert.ok(!node.attrs.rx && !node.attrs.ry, "paving corners must stay square");
  }
  for (const shape of paving) {
    assert.ok(shape.polygons.flat().flat().every(Number.isInteger), "subpixel paving");
    for (const polygon of shape.polygons) polygon.forEach((p, i) => {
      const q = polygon[(i + 1) % polygon.length];
      assert.ok(p[0] === q[0] || p[1] === q[1], "paving must have stepped, orthogonal edges");
    });
  }
});

check("all three village road entrances meet the real outer network at a continuous 64px width", () => {
  const centreY = origin[1] + 390, top = centreY - 32, bottom = centreY + 32;
  const roads = external.filter(shape => shape.parts.includes("road-surface"));
  for (const boundary of [origin[0], origin[0] + fixture.map.core[2]]) {
    const inside = boundary === origin[0] ? 0.5 : -0.5;
    for (let y = top + 0.5; y < bottom; y++) {
      assert.ok(paved(boundary + inside, y), `village road narrows at ${boundary},${y}`);
      assert.ok(roads.some(shape => contains(shape, boundary - inside, y)), `outer street is misaligned at ${boundary},${y}`);
      if (y > top + 2 && y < bottom - 2) assert.ok(!edges.some(shape => contains(shape, boundary + inside, y)), "a curb seals the village road mouth");
    }
  }
  const north = fixture.map.road_network.pedestrian_accesses.find(access => access.id === "village-path");
  assert.ok(north);assert.equal(north.width, 64);
  assert.equal(north.doorstep[0], origin[0] + 460);assert.equal(north.doorstep[1], origin[1]);
  for (let x = north.doorstep[0] - 31.5; x < north.doorstep[0] + 32; x++) {
    for (let y = north.point[1] + 0.5; y < north.doorstep[1]; y++) {
      assert.ok(externalPaving.some(shape => contains(shape, x, y)), `northern connection narrows at ${x},${y}`);
      assert.ok(!externalCurbs.some(shape => contains(shape, x, y)), `outer curb seals the northern connection at ${x},${y}`);
    }
    assert.ok(paved(x, north.doorstep[1] + 0.5), "northern street stops at the village boundary");
  }
  // Measure both painted edges at every pixel column/row. A centre line alone
  // cannot prove the street width; the ring itself intentionally widens out.
  for (const [left, right] of [[0, 344], [576, 780], [894, 960]]) for (let x = origin[0] + left + 0.5; x < origin[0] + right; x++) {
    assert.equal(roadSection("horizontal", x).width, 64, `main street changes width at ${x}`);
  }
  for (let y = origin[1] + 0.5; y < origin[1] + 290; y++) assert.equal(roadSection("vertical", y).width, 64, `north street changes width at ${y}`);
  assert.equal(roadSection("horizontal", origin[0] + 0.5).center, centreY);
  assert.equal(roadSection("horizontal", origin[0] + fixture.map.core[2] - 0.5).center, centreY);
  assert.equal(roadSection("vertical", origin[1] + 0.5).center, north.doorstep[0]);
});

check("every main street stays completely straight and its actual axis meets the actual island centre", () => {
  const north = fixture.map.road_network.pedestrian_accesses.find(access => access.id === "village-path");
  assert.equal(ringCenter[0], north.doorstep[0], "the island is off the real northern entrance axis");
  assert.equal(ringCenter[1], roadSection("horizontal", origin[0] + 0.5).center, "the island is off the real west entrance axis");
  for (const [left, right] of [[0, 344], [576, 780], [894, 960]]) for (let x = origin[0] + left + 0.5; x < origin[0] + right; x++) {
    const section = roadSection("horizontal", x);
    assert.equal(section.start, ringCenter[1] - 32, `horizontal street bends at its north edge x=${x}`);
    assert.equal(section.end, ringCenter[1] + 32, `horizontal street bends at its south edge x=${x}`);
    assert.equal(section.center, ringCenter[1], `horizontal street axis misses the actual island centre at x=${x}`);
  }
  for (let y = origin[1] + 0.5; y < origin[1] + 290; y++) {
    const section = roadSection("vertical", y);
    assert.equal(section.start, ringCenter[0] - 32, `north street bends at its west edge y=${y}`);
    assert.equal(section.end, ringCenter[0] + 32, `north street bends at its east edge y=${y}`);
    assert.equal(section.center, ringCenter[0], `north street axis misses the actual island centre at y=${y}`);
  }
});

check("the actual landmark foundations and steps align with the architecture and angling houses in the same row", () => {
  assert.equal(rowReferences.length, 2);
  for (const reference of rowReferences) {
    const landmark = landmarks.find(home => home.agent.id === reference.agent.pair);
    assert.ok(landmark, "missing paired landmark");
    assert.equal(reference.agent.house.style, reference.agent.pair === "linghang" ? "architecture" : "angling");
    assert.equal(landmark.position[1], reference.position[1], `${landmark.agent.id}: plot origin is not on the adjoining residential row`);
    for (const name of ["foundation", "steps"]) {
      const baseline = home => {
        const shapes = home.shapes.filter(shape => shape.parts.includes(name) && solidShape(shape));
        assert.ok(shapes.length, `${home.agent.id}: no actual ${name} artwork`);
        return Math.max(...shapes.map(shape => shape.bounds[3]));
      };
      assert.equal(baseline(landmark), baseline(reference), `${landmark.agent.id}: actual ${name} baseline is not aligned with ${reference.agent.house.style}`);
    }
  }
});

check("real landmark steps meet complete 24px paths and sidewalks without a road entering either house", () => {
  assert.equal(landmarks.length, 2);
  const pedestrianPaving = [...sidewalks, ...doorsteps, ...surface];
  for (const { agent, door, shapes } of landmarks) {
    const [x, y] = door;
    const streetTop = roadSection("horizontal", x + 0.5).start;
    const steps = shapes.filter(shape => shape.parts.includes("steps"));
    assert.ok(steps.length > 0);
    assert.equal(Math.max(...steps.map(shape => shape.bounds[3])), y, `${agent.id}: layout no longer meets rendered steps`);
    const entrance = doorsteps.filter(shape => contains(shape, x, y + 0.5));
    assert.ok(entrance.length, `${agent.id}: no actual doorstep path at the stairs`);
    assert.ok(entrance.every(shape => shape.bounds[0] >= x - 12 && shape.bounds[2] <= x + 12 && shape.bounds[1] >= y && shape.bounds[3] <= streetTop), `${agent.id}: doorstep was widened into a road or crosses the carriageway`);
    for (let py = y + 0.5; py < streetTop; py++) for (let px = x - 11.5; px < x + 12; px++) {
      assert.ok(pedestrianPaving.some(shape => contains(shape, px, py)), `${agent.id}: doorstep paving gap ${px},${py}`);
      assert.ok(!edges.some(shape => contains(shape, px, py)), `${agent.id}: curb blocks the doorway at ${px},${py}`);
    }
    // A translucent cast shadow can fall on the path; solid masonry and the
    // actual stair rectangles must meet the paving without overlapping it.
    const solid = shapes.filter(shape => !/^#[\da-f]{8}$/i.test(shape.node.attrs.fill || ""));
    for (const shape of solid) assert.ok(!surface.some(road => intersects(road, shape.bounds)), `${agent.id}: paving intrudes on actual house or steps ${shape.bounds}`);
  }
  for (const x of [160, 224, 304, 616, 648, 672]) {
    const px = origin[0] + x + 0.5, streetTop = roadSection("horizontal", px).start;
    for (let py = streetTop - 13.5; py < streetTop; py++) {
      assert.ok([...sidewalks, ...doorsteps].some(shape => contains(shape, px, py)), `north sidewalk loses its 14px width at ${px},${py}`);
    }
  }
});

check("the painted plot entry point adds real landmark furniture while all entrance paving stays in the map", () => {
  for (const { agent } of landmarks) {
    const layers = integration.plotSvg(agent, fixture.map);
    assert.equal(layers.children.length, 2);
    const ground = layers.children[0];
    assert.equal(ground.children.length, 2, `${agent.id}: extra doorstep or lawn layer was added`);
    assert.deepEqual(snapshot(ground.children[0]), snapshot(houseArt.house(agent)));
    assert.deepEqual(snapshot(ground.children[1]), snapshot(houseArt.yard(agent)));
    const yardTree = snapshot(ground.children[1]), furniture = geometry(yardTree);
    assert.ok(furniture.length, `${agent.id}: the landmark yard omitted its equipment`);
    assert.ok(!flatten(yardTree).some(node => node.attrs["data-part"] === "path"), `${agent.id}: yard reintroduced a patch path`);
    assert.ok(furniture.every(shape => shape.parts.includes("equipment") && shape.equipment), `${agent.id}: the yard contains extra ground or ungrouped furniture`);
  }
});

check("the actual landmark equipment sits beside clear doorways without entering buildings, streets or the scout corridor", () => {
  const expected = { linghang: ["planning-board", "meeting-table"], scout: ["telescope", "research-table"] };
  const pedestrianPaving = [...surface, ...sidewalks, ...doorsteps];
  const allBuildings = landmarks.flatMap(home => home.shapes).filter(solidShape);
  for (const { agent, position, door, furniture } of landmarks) {
    const names = [...new Set(furniture.map(shape => shape.equipment))].sort();
    assert.deepEqual(names, [...expected[agent.id]].sort(), `${agent.id}: missing or unexpected equipment`);
    const streetTop = roadSection("horizontal", door[0] + 0.5).start;
    const doorway = [door[0] - 12, door[1], door[0] + 12, streetTop];
    for (const [index, name] of expected[agent.id].entries()) {
      const shapes = furniture.filter(shape => shape.equipment === name), area = bounds(shapes.flatMap(shape => shape.polygons.flat()));
      assert.ok(shapes.length > 1, `${name}: equipment label has no usable artwork`);
      assert.ok(area[1] >= door[1] && area[3] <= position[1] + 270, `${name}: equipment is not contained in the front yard`);
      assert.ok(index === 0 ? area[2] <= doorway[0] : area[0] >= doorway[2], `${name}: equipment is not arranged beside its doorway`);
      for (const shape of shapes.filter(solidShape)) {
        assert.ok(!intersects(shape, doorway), `${name}: equipment blocks the complete 24px door path`);
        assert.ok(!pedestrianPaving.some(paving => shapesIntersect(shape, paving)), `${name}: equipment occupies an actual street, sidewalk or doorway at ${shape.bounds}`);
        assert.ok(!allBuildings.some(building => shapesIntersect(shape, building)), `${name}: equipment overlaps an actual building at ${shape.bounds}`);
        assert.ok(!furniture.some(other => other.equipment !== name && solidShape(other) && shapesIntersect(shape, other)), `${name}: equipment overlaps another item`);
        for (let i = 1; i < journey.ROUTE.length; i++) {
          const segment = bounds([journey.ROUTE[i - 1], journey.ROUTE[i]]);
          assert.ok(!intersects(shape, [segment[0] - 12, segment[1] - 12, segment[2] + 12, segment[3] + 12]), `${name}: equipment intrudes into the scout's 24px corridor`);
        }
      }
    }
  }
});

const ring = surface.filter(shape => shape.fillRule === "evenodd" && shape.polygons.length > 1);
check("the pixel roundabout has a real island hole and a continuous 64px circulating road", () => {
  assert.equal(ring.length, 1, "expected one actual ring road with an island hole");
  assert.ok(onIsland(...ringCenter));assert.ok(!paved(...ringCenter), "the island was counted as a road surface");
  for (const shape of island) for (let x = shape.bounds[0] + 1; x < shape.bounds[2]; x += 2) for (let y = shape.bounds[1] + 1; y < shape.bounds[3]; y += 2) {
    if (contains(shape, x, y)) assert.ok(!paved(x, y), `another road paints across the island at ${x},${y}`);
  }
  // Measure normal cross-sections against the continuous design ellipse. The
  // expected widths do not reproduce the artwork's four-pixel stepping code.
  for (let degrees = 0; degrees < 360; degrees += 2) {
    const angle = degrees * Math.PI / 180, c = Math.cos(angle), s = Math.sin(angle);
    const start = [ringCenter[0] + 52 * c, ringCenter[1] + 36 * s];
    const norm = Math.hypot(c / 52, s / 36), direction = [c / 52 / norm, s / 36 / norm];
    const painted = [];
    for (let distance = -8; distance <= 76; distance += 0.5) {
      if (contains(ring[0], start[0] + direction[0] * distance, start[1] + direction[1] * distance)) painted.push(distance);
    }
    assert.ok(painted.length, `missing circulating road at ${degrees} degrees`);
    const first = painted[0], last = painted.at(-1), width = last - first + 0.5;
    assert.ok(Math.abs(first) <= 4, `island boundary leaves its pixel tolerance at ${degrees} degrees`);
    const outside = [start[0] + direction[0] * 68, start[1] + direction[1] * 68];
    const throughMouth = (outside[1] >= ringCenter[1] - 32 && outside[1] < ringCenter[1] + 32)
      || (outside[0] >= ringCenter[0] - 32 && outside[0] < ringCenter[0] + 32 && outside[1] < ringCenter[1]);
    assert.ok(width >= 60 && (throughMouth || width <= 68), `ring width ${width}px at ${degrees} degrees is outside the 64px pixel tolerance`);
    assert.equal(painted.length, Math.round(width / 0.5), `ring has a gap at ${degrees} degrees`);
    const midpoint = [start[0] + direction[0] * (first + last) / 2, start[1] + direction[1] * (first + last) / 2];
    assert.ok(!edges.some(shape => contains(shape, ...midpoint)), `curb blocks circulation at ${degrees} degrees`);
  }
  assert.ok(markings.some(shape => shape.bounds[0] < ringCenter[0] + 116 && shape.bounds[2] > ringCenter[0] - 116 && shape.bounds[1] < ringCenter[1] + 100 && shape.bounds[3] > ringCenter[1] - 100), "roundabout has no visible lane markings");
});

check("the star-hugging Po statue has actual pixel parts inside the reserved landmark area", () => {
  assert.equal(statueTree.attrs["data-landmark"], "po-star-statue");
  assert.ok(!flatten(fixture.town).some(node => node.attrs.id === "village-fountain"), "the old fountain remains behind the statue");
  const allowed = [ringCenter[0] - 47, ringCenter[1] - 75, ringCenter[0] + 47, ringCenter[1] + 8];
  assert.ok(statueBounds[0] >= allowed[0] && statueBounds[1] >= allowed[1] && statueBounds[2] <= allowed[2] && statueBounds[3] <= allowed[3], "statue extends beyond the reserved landmark area");
  for (const node of flatten(statueTree)) {
    assert.ok(["g", "rect", "polygon", "path"].includes(node.tag), `non-pixel statue element ${node.tag}`);
    assert.equal(node.attrs.filter, undefined, "statue must not use nested filters");
    assert.ok(!/filter\s*:/.test(node.attrs.style || ""));
    if (node.tag === "rect") assert.ok(!node.attrs.rx && !node.attrs.ry, "statue pixels must have square corners");
  }
  for (const shape of statue) {
    assert.ok(shape.polygons.flat().flat().every(Number.isInteger), "subpixel statue geometry");
    for (const polygon of shape.polygons) polygon.forEach((p, i) => {
      const q = polygon[(i + 1) % polygon.length];
      assert.ok(p[0] === q[0] || p[1] === q[1], "statue curves must be represented by stepped pixels");
    });
  }
  const parts = Object.fromEntries(["antenna", "head", "body", "star", "base"].map(name => {
    const shapes = geometry(find(statueTree, "data-part", name), origin);
    assert.ok(shapes.length > 0, `${name} labels an empty group`);
    return [name, { shapes, bounds: bounds(shapes.flatMap(shape => shape.polygons.flat())) }];
  }));
  const centerY = part => (part.bounds[1] + part.bounds[3]) / 2;
  assert.ok(centerY(parts.antenna) < centerY(parts.head) && centerY(parts.head) < centerY(parts.body) && centerY(parts.body) < centerY(parts.base), "antenna, head, body and base lost their recognizable arrangement");
  assert.ok(overlaps(parts.star.bounds, parts.body.bounds), "the held star is detached from the body");
  const hasColor = (part, predicate) => part.shapes.some(shape => {
    const fill = shape.node.attrs.fill || "";
    if (!/^#[\da-f]{6}$/i.test(fill)) return false;
    const [r, g, b] = [1, 3, 5].map(offset => parseInt(fill.slice(offset, offset + 2), 16));
    return predicate(r, g, b);
  });
  assert.ok(hasColor(parts.body, (r, g, b) => r > g + 25 && r > b + 25), "Po needs a visible red body");
  assert.ok(hasColor(parts.star, (r, g, b) => r > b + 45 && g > b + 25 && r >= g), "the held star needs a visible gold surface");
  // The ring opening may be transparent or painted in the actual paving color.
  // Look for an enclosed opening in the final visible pixels, not a fixed path.
  const backgroundColors = new Set(surface.map(shape => shape.node.attrs.fill));
  const antennaPaint = [...parts.antenna.shapes].reverse();
  const antennaSolid = (x, y) => {
    const shape = antennaPaint.find(pixel => contains(pixel, x, y));
    return shape && !backgroundColors.has(shape.node.attrs.fill);
  };
  const ab = parts.antenna.bounds;
  let hasRingOpening = false;
  for (let x = ab[0] + 0.5; x < ab[2] && !hasRingOpening; x++) for (let y = ab[1] + 0.5; y < ab[3] && !hasRingOpening; y++) {
    if (antennaSolid(x, y)) continue;
    hasRingOpening = [[1, 0], [-1, 0], [0, 1], [0, -1]].every(([dx, dy]) => {
      for (let px = x + dx, py = y + dy; px >= ab[0] && px < ab[2] && py >= ab[1] && py < ab[3]; px += dx, py += dy) if (antennaSolid(px, py)) return true;
      return false;
    });
  }
  assert.ok(hasRingOpening, "Po's antenna lost its visible ring opening");
  for (const shape of parts.base.shapes) for (let x = shape.bounds[0] + 0.5; x < shape.bounds[2]; x++) for (let y = shape.bounds[1] + 0.5; y < shape.bounds[3]; y++) {
    if (contains(shape, x, y)) {
      assert.ok(onIsland(x, y), `statue base leaves the central island at ${x},${y}`);
      assert.ok(!paved(x, y), `a carriageway runs beneath the statue base at ${x},${y}`);
    }
  }
});

const bridgeTree = find(fixture.town, "id", "village-bridge");
const bridgeDeck = geometry(find(bridgeTree, "data-part", "bridge-deck"), origin);
const bridgeRails = geometry(find(bridgeTree, "data-part", "bridge-rails"), origin);
check("the wooden bridge keeps a full 64px clear deck and uninterrupted approaches", () => {
  const ordered = flatten(fixture.town);
  assert.ok(ordered.indexOf(bridgeTree) > ordered.indexOf(pavingTree), "street paving covers the wooden bridge");
  assert.ok(bridgeDeck.length && bridgeRails.length);
  const clear = worldRect([780, 358, 894, 422]);
  for (let x = clear[0] - 1.5; x < clear[2] + 2; x++) for (let y = clear[1] + 0.5; y < clear[3]; y++) {
    assert.ok(paved(x, y) || bridgeDeck.some(shape => contains(shape, x, y)), `bridge narrows or disconnects at ${x},${y}`);
  }
  assert.ok(!bridgeRails.some(shape => intersects(shape, clear)), "a bridge rail intrudes into the clear deck");
  assert.ok(![...edges, ...markings].some(shape => intersects(shape, clear)), "a road curb or centre dash paints over the wooden deck");
  assert.ok(!surface.some(shape => intersects(shape, worldRect([809, 358, 894, 422]))), "stone road covers the bridge across the river");
  assert.ok(new Set(bridgeDeck.map(shape => shape.fill)).size >= 3, "the wooden deck lost its plank shading");
  assert.ok(bridgeDeck.some(shape => shape.node.tag === "rect" && shape.bounds[2] - shape.bounds[0] <= 12 && shape.bounds[3] - shape.bounds[1] >= 32), "the original narrow wooden planks were replaced by a flat road");
  const wood = bridgeDeck.filter(shape => /^#[\da-f]{6}$/i.test(shape.fill || "")).some(shape => {
    const [r, g, b] = [1, 3, 5].map(offset => parseInt(shape.fill.slice(offset, offset + 2), 16));
    return r > b + 25 && g > b + 10;
  });
  assert.ok(wood, "the bridge no longer uses its warm timber palette");
});

const garden = geometry(find(fixture.town, "id", "village-garden"), origin);
check("the actual garden stays beyond the southern edge of the roundabout", () => {
  assert.ok(garden.length > 0);
  const gardenBounds = bounds(garden.flatMap(shape => shape.polygons.flat()));
  assert.ok(gardenBounds[1] >= origin[1] + 513, "the garden was not moved clear of the island's southern approach");
  for (const shape of garden) assert.ok(!surface.some(road => intersects(road, shape.bounds)), `roundabout invades the garden at ${shape.bounds}`);
});

// Run the real map entry point to get the yard's placement, then apply the real
// CSS dimensions to the real rack SVG. The test does not assume a rack offset.
const workspace = read("web/scout-workspace.js");
const renderStart = workspace.indexOf("  function renderMap(world, map) {");
const renderEnd = workspace.indexOf("\n  function stopFollowing()", renderStart);
assert.ok(renderStart >= 0 && renderEnd > renderStart);
const world = element(null, "div");
const scoutContext = {
  window: { ...browser.window, StarhavenScoutJourney: journey },
  node: (tag, className) => { const n = element(null, tag); n.setAttribute("class", className); return n; },
  sceneState: {}, imageParams: () => ({ materialCount: 6, rawCount: 3, phase: "idle" }),
  paintJourney() {}, animate() {},
};
vm.runInNewContext(workspace.slice(renderStart, renderEnd) + "\nthis.renderMap=renderMap;", scoutContext);
scoutContext.renderMap(world, fixture.map);
const yard = world.querySelector(".scout-map-yard");
const yardXML = parseXML(yard.innerHTML);
const css = read("web/scout-workspace.css").match(/\.scout-map-yard\{([^}]+)\}/)?.[1];
assert.ok(css, "missing yard size rule");
const cssWidth = Number(css.match(/(?:^|;)width:(\d+)px/)?.[1]);
const cssHeight = Number(css.match(/(?:^|;)height:(\d+)px/)?.[1]);
const viewBox = yardXML.attrs.viewBox.split(/\s+/).map(Number);
const yardOrigin = [Number.parseFloat(yard.style.left), Number.parseFloat(yard.style.top)];
const rack = geometry(find(yardXML, "class", "scout-material-rack"), yardOrigin, [cssWidth / viewBox[2], cssHeight / viewBox[3]]);
const apron = world.querySelector(".scout-yard-path");
assert.ok(apron, "the real map omitted the scout's short yard path");
const apronXML = parseXML(apron.innerHTML);
const apronView = apronXML.attrs.viewBox.split(/\s+/).map(Number);
const apronScale = [Number.parseFloat(apron.style.width) / apronView[2], Number.parseFloat(apron.style.height) / apronView[3]];
const apronOrigin = [Number.parseFloat(apron.style.left) - apronView[0] * apronScale[0], Number.parseFloat(apron.style.top) - apronView[1] * apronScale[1]];
const apronShapes = [];
for (const line of flatten(apronXML).filter(node => node.tag === "polyline")) {
  assert.equal(line.attrs["stroke-linecap"], "square");assert.equal(line.attrs["stroke-linejoin"], "miter");
  const values = line.attrs.points.trim().split(/[\s,]+/).map(Number);
  const points = Array.from({ length: values.length / 2 }, (_, index) => values.slice(index * 2, index * 2 + 2));
  const half = Number(line.attrs["stroke-width"]) / 2;
  assert.ok(half > 0 && points.length >= 2 && values.every(Number.isFinite));
  // Square caps and right-angle miter joins occupy the union of these swept
  // rectangles. Read the actual SVG stroke and viewport, not YARD_ROUTE.
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i];
    assert.ok(a[0] === b[0] || a[1] === b[1]);
    apronShapes.push(...geometry({ tag: "rect", attrs: { x: Math.min(a[0], b[0]) - half, y: Math.min(a[1], b[1]) - half,
      width: Math.abs(a[0] - b[0]) + 2 * half, height: Math.abs(a[1] - b[1]) + 2 * half, fill: line.attrs.stroke }, children: [] }, apronOrigin, apronScale));
  }
}
assert.ok(apronShapes.length > 0, "the yard connector has no actual stroked surface");

check("the actual yard stays south of the road and the scout walks continuously around the island", () => {
  assert.ok(rack.length > 0 && Number.isFinite(cssWidth) && Number.isFinite(cssHeight));
  assert.deepEqual(yardOrigin, [origin[0] + 570, origin[1] + 450], "material yard did not move clear of the widened street");
  for (const shape of rack) assert.ok(!surface.some(road => intersects(road, shape.bounds)), `road enters the actual material rack at ${shape.bounds}`);
  const walkable = [...surface, ...sidewalks, ...doorsteps, ...bridgeDeck, ...apronShapes];
  const core = [origin[0], origin[1], origin[0] + fixture.map.core[2], origin[1] + fixture.map.core[3]];
  for (let i = 1; i < journey.ROUTE.length; i++) {
    const a = journey.ROUTE[i - 1], b = journey.ROUTE[i];
    assert.ok(a[0] === b[0] || a[1] === b[1], "travel route must remain orthogonal");
    const segment = bounds([a, b]);
    const clearance = [segment[0] - 12, segment[1] - 12, segment[2] + 12, segment[3] + 12];
    assert.ok(!statue.some(shape => intersects(shape, clearance)), `traveller loses twelve-pixel clearance from an actual statue pixel between ${a} and ${b}`);
    const length = Math.hypot(b[0] - a[0], b[1] - a[1]), samples = Math.max(1, Math.ceil(length / 2));
    for (let sample = 0; sample <= samples; sample++) {
      const x = a[0] + (b[0] - a[0]) * sample / samples, y = a[1] + (b[1] - a[1]) * sample / samples;
      if (x < core[0] || x >= core[2] || y < core[1] || y >= core[3]) continue;
      assert.ok(walkable.some(shape => contains(shape, x, y)), `scout leaves real village paving at ${x},${y}`);
      assert.ok(!onIsland(x, y), `scout crosses the central island at ${x},${y}`);
      assert.ok(!edges.some(shape => contains(shape, x, y)), `a curb seals the scout's route at ${x},${y}`);
      assert.ok(!rack.some(shape => contains(shape, x, y)), `scout walks through the material rack at ${x},${y}`);
      for (const { shapes, agent } of landmarks) assert.ok(!shapes.some(shape => !/^#[\da-f]{8}$/i.test(shape.fill || "") && contains(shape, x, y)), `scout walks through ${agent.id} at ${x},${y}`);
    }
  }
});

console.log(`${checks} village plaza checks passed.`);

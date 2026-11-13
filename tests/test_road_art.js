"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { execFileSync } = require("node:child_process");
const project = path.join(__dirname, "..");
const NS = "http://www.w3.org/2000/svg";
const data = new WeakMap(), legacyReads = new Set();
function createElementNS(namespaceURI, localName) {
  const node = {
    namespaceURI, localName, style: {},
    getAttribute(key) { return data.get(this).attrs.get(key) ?? null; },
    setAttribute(key, value) { data.get(this).attrs.set(key, String(value)); },
    get children() { return data.get(this).children; },
    append(...children) { data.get(this).children.push(...children); },
    appendChild(child) { this.append(child); return child; },
  };
  const proxy = new Proxy(node, { get(target, key, receiver) {
    if (key === "name" || key === "attrs") legacyReads.add(key);
    return Reflect.get(target, key, receiver);
  } });
  data.set(proxy, { attrs: new Map(), children: [] });
  return proxy;
}
const context = { window: {}, document: { createElementNS } };
const source = fs.readFileSync(path.join(project, "web/road-art.js"), "utf8");
vm.runInNewContext(source, context, { filename: "road-art.js" });
const art = context.window.StarhavenRoadArt;
assert.equal(typeof art?.render, "function");
const commonJS = { module: { exports: {} }, document: { createElementNS } };
vm.runInNewContext(source, commonJS, { filename: "road-art.js" });
assert.equal(typeof commonJS.module.exports.render, "function", "module must work in Node without window");

function elements(root) {
  const result = [];
  function visit(node, parts) {
    assert.equal(node.namespaceURI, NS);
    assert.equal(node.getAttribute("filter"), null, "nested SVG filters break the native Mac night renderer");
    assert.equal(node.getAttribute("transform"), null, "all road geometry must use explicit world coordinates");
    const next = new Set(parts);
    if (node.getAttribute("data-part")) next.add(node.getAttribute("data-part"));
    result.push({ node, parts: next });
    for (const child of node.children) visit(child, next);
  }
  visit(root, new Set());
  return result;
}
function rectangles(root) {
  return elements(root).filter(item => item.node.localName === "rect").map(({ node, parts }) => {
    const values = ["x", "y", "width", "height"].map(key => {
      assert.notEqual(node.getAttribute(key), null, `missing rectangle ${key}`);
      return Number(node.getAttribute(key));
    });
    assert.ok(values.every(Number.isFinite), `non-finite rectangle ${values}`);
    const [x, y, width, height] = values;
    assert.ok(width > 0 && height > 0, `empty rectangle ${values}`);
    return { bounds: [x, y, x + width, y + height], parts, node };
  });
}
function overlap(a, b) { return a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1]; }
function contains(bounds, x, y) { return x >= bounds[0] && x <= bounds[2] && y >= bounds[1] && y <= bounds[3]; }
function within(bounds, world) { return bounds[0] >= world[0] && bounds[1] >= world[1] && bounds[2] <= world[2] && bounds[3] <= world[3]; }
function part(items, name) { return items.filter(item => item.parts.has(name)); }
let checks = 0;
function check(name, run) { run(); checks++; console.log(`PASS ${name}`); }

// Read the actual map contract from Python, rather than maintaining a second
// copy of the 28 plots or relying on renderer-labelled metadata as evidence.
const fixtureScript = "import json,server; print(json.dumps(dict(core=[server.CORE_X,server.CORE_Y,server.CORE_W,server.CORE_H],world=[server.WORLD_W,server.WORLD_H],origin=[server.WORLD_X,server.WORLD_Y],plot=[server.PLOT_W,server.PLOT_H],slots=server.PLOT_SLOTS,road_network=server.ROAD_NETWORK)))";
const map = JSON.parse(execFileSync(process.env.PYTHON || "python3", ["-c", fixtureScript], { cwd: project, encoding: "utf8" }));
const occupied = map.slots.map((_, slot) => slot);
const tree = art.render(map, { occupiedSlots: occupied });
const rendered = rectangles(tree), roads = part(rendered, "road-surface"), curbs = part(rendered, "road-curb"), markings = part(rendered, "road-marking");
const network = map.road_network, nodes = new Map(network.nodes.map(node => [node.id, node]));
const plotBounds = map.slots.map(([x, y]) => [x - map.plot[0] / 2, y - map.plot[1] / 2, x + map.plot[0] / 2, y + map.plot[1] / 2]);
const core = [map.core[0], map.core[1], map.core[0] + map.core[2], map.core[1] + map.core[3]];
const northernStreet = network.streets.reduce((north, street) => street.centre < north.centre ? street : north);
const northernRoads = roads.filter(road => road.node.getAttribute("data-direction") === "horizontal" && road.bounds[1] === northernStreet.top);

check("all actual streets are 64px wide and remain outside every home and the village", () => {
  assert.equal(map.slots.length, 28);
  assert.ok(network.edges.every(edge => edge.width === 64 && edge.surface === "street"), "a narrow shared lane remains in the actual map");
  assert.ok(roads.length > 0 && curbs.length > 0 && markings.length > 0);
  assert.ok(northernRoads.length > 0, "the northern street was not painted");
  for (const road of roads) {
    const vertical = road.node.getAttribute("data-direction") === "vertical";
    assert.equal(road.bounds[vertical ? 2 : 3] - road.bounds[vertical ? 0 : 1], 64, "painted street is narrower than the main road");
    assert.equal(road.node.getAttribute("data-surface"), "street");
  }
  for (const item of rendered) {
    assert.ok(within(item.bounds, network.bounds), `outside world: ${item.bounds}`);
    assert.ok(!overlap(item.bounds, core), `artwork intrudes on village: ${item.bounds}`);
    assert.ok(!plotBounds.some(plot => overlap(item.bounds, plot)), `artwork intrudes on a plot: ${item.bounds}`);
    assert.ok(item.bounds.every(Number.isInteger), `subpixel art: ${item.bounds}`);
  }
  assert.equal(legacyReads.size, 0, "renderer must use real SVG DOM attributes");
});
check("every actual navigation edge is continuously painted including its full square cap", () => {
  for (const edge of network.edges) {
    const a = nodes.get(edge.from), b = nodes.get(edge.to);
    // A centre-only check would miss narrowed roads or disconnected corners.
    // Sample the complete two-dimensional corridor at two-pixel resolution.
    for (let x = edge.bounds[0] + 1; x < edge.bounds[2]; x += 2) {
      for (let y = edge.bounds[1] + 1; y < edge.bounds[3]; y += 2) {
        assert.ok(roads.some(road => contains(road.bounds, x, y)), `unpainted ${edge.id} at ${x},${y}`);
      }
    }
    for (let ratio = 0; ratio <= 1; ratio += 0.02) {
      const x = a.x + (b.x - a.x) * ratio, y = a.y + (b.y - a.y) * ratio;
      assert.ok(!curbs.some(curb => contains(curb.bounds, x, y)), `curb blocks route ${edge.id} at ${x},${y}`);
    }
  }
});
check("junction centre lines are clear and connecting arms have no curb barriers", () => {
  for (const junction of network.junctions) {
    assert.ok(!markings.some(line => overlap(line.bounds, junction.bounds)), `centre dash crosses ${junction.node}`);
    const node = nodes.get(junction.node);
    const connected = network.edges.filter(edge => edge.from === node.id || edge.to === node.id);
    for (const edge of connected) {
      const other = nodes.get(edge.from === node.id ? edge.to : edge.from);
      const dx = Math.sign(other.x - node.x), dy = Math.sign(other.y - node.y);
      for (let distance = 0; distance <= junction.width / 2 + 4; distance += 2) {
        if (distance > edge.length) break;
        const x = node.x + dx * distance, y = node.y + dy * distance;
        assert.ok(!curbs.some(curb => contains(curb.bounds, x, y)), `closed road arm ${edge.id}`);
      }
    }
  }
});
check("all 28 occupied home entrances provide a real 32px path and an open curb", () => {
  const entrances = elements(tree).filter(item => item.node.getAttribute("data-slot") !== null && item.node.getAttribute("data-slot") !== "pedestrian");
  assert.equal(entrances.length, 28);
  for (const access of network.accesses) {
    const [x, y] = access.doorstep, endY = access.point[1];
    const path = rectangles(entrances.find(item => Number(item.node.getAttribute("data-slot")) === access.slot).node);
    const northRoadEdge = Math.min(...roads.filter(road => contains(road.bounds, x, endY)).map(road => road.bounds[1]));
    for (let px = x - 15; px <= x + 15; px += 2) for (let py = y; py <= northRoadEdge + 1; py++) {
      assert.ok(path.some(item => contains(item.bounds, px, py)), `entrance ${access.slot} has a gap at ${px},${py}`);
      assert.ok(!curbs.some(item => contains(item.bounds, px, py)), `curb blocks entrance ${access.slot}`);
    }
  }
});
check("empty plots have no doorstep stubs and their street kerbs remain closed", () => {
  const empty = art.render(map, { occupiedSlots: [] });
  assert.ok(!elements(empty).some(item => /^\d+$/.test(item.node.getAttribute("data-slot") || "")));
  const emptyCurbs = part(rectangles(empty), "road-curb");
  for (const access of network.accesses) {
    const x = access.point[0], y = access.point[1];
    const roadTop = Math.min(...roads.filter(road => contains(road.bounds, x, y)).map(road => road.bounds[1]));
    assert.ok(emptyCurbs.some(curb => contains(curb.bounds, x, roadTop + 1)), `empty plot ${access.slot} has an unexplained curb opening`);
  }
});
check("forest and village paths stay open at both sides of the northern street", () => {
  assert.equal(network.pedestrian_accesses.length, 2);
  for (const access of network.pedestrian_accesses) {
    const [x, start] = access.doorstep, end = access.point[1];
    const corridor = [x - access.width / 2 + 1, Math.min(start, end), x + access.width / 2 - 1, Math.max(start, end)];
    assert.ok(!curbs.some(curb => overlap(curb.bounds, corridor)), `pedestrian curb barrier at ${access.id}`);
    const entry = part(rendered, "access-path");
    const surface = [...entry, ...roads];
    for (let y = Math.min(start, end); y <= Math.max(start, end); y++) {
      assert.ok(surface.some(item => contains(item.bounds, x, y)), `unpainted path ${access.id} at ${y}`);
    }
  }
});
check("every street, including the northern street, has centre dashes and a continuous fourteen-pixel footpath", () => {
  const walks = part(rendered, "sidewalk");
  for (const road of roads.filter(item => item.node.getAttribute("data-direction") === "horizontal" && item.node.getAttribute("data-surface") === "street")) {
    assert.ok(walks.some(walk => walk.bounds[1] === road.bounds[1] - 14 && walk.bounds[3] === road.bounds[1] && walk.bounds[0] >= road.bounds[0] && walk.bounds[2] <= road.bounds[2]), "missing continuous shoulder footpath");
    assert.ok(markings.some(line => within(line.bounds, road.bounds) && line.bounds[3] - line.bounds[1] === 2), "street has no visible centre dashes");
  }
  for (const walk of walks) {
    assert.ok(!roads.some(road => overlap(walk.bounds, road.bounds)), "footpath blocks carriageway");
    assert.ok(!network.accesses.some(access => overlap(walk.bounds, [access.doorstep[0] - 16, access.doorstep[1], access.point[0] + 16, access.point[1]])), "footpath edging crosses a driveway");
  }
  // Check the full new footway, including the breaks where real entrances take
  // over its paving; a single small sidewalk fragment must not satisfy the test.
  const walkable = [...walks, ...roads, ...part(rendered, "access-path")];
  for (const road of northernRoads) for (let x = road.bounds[0] + 1; x < road.bounds[2]; x += 2) {
    for (let y = northernStreet.top - 13; y < northernStreet.top; y += 2) {
      assert.ok(walkable.some(item => contains(item.bounds, x, y)), `northern footway has a gap at ${x},${y}`);
    }
  }
});
check("lamps, greenery, benches and signs never obstruct streets, footpaths or entrances", () => {
  const furniture = part(rendered, "street-furniture"), walks = part(rendered, "sidewalk");
  for (const type of ["garden-lamp", "low-greenery", "bench", "wooden-sign"]) assert.ok(part(furniture, type).length > 0, `missing ${type}`);
  for (const item of furniture) {
    assert.ok(![...roads, ...walks, ...part(rendered, "access-path")].some(obstacle => overlap(item.bounds, obstacle.bounds)), `furniture obstruction ${item.bounds}`);
  }
});

function smallGraph(points, links) {
  const nodes = points.map(([x, y], index) => ({ id: String(index), x, y }));
  const edges = links.map(([from, to, width = 32]) => ({ id: `${from}-${to}`, from: String(from), to: String(to), width, surface: "street", speed: 48 }));
  return { plot: [20, 20], slots: [], core: [1000, 1000, 10, 10], road_network: { version: 1, nodes, edges, accesses: [],
    junctions: nodes.map(node => ({ node: node.id, bounds: [node.x - 16, node.y - 16, node.x + 16, node.y + 16] })), bounds: [-160, -160, 160, 160] } };
}
check("independent T, cross and corner fixtures have genuinely open connected junctions", () => {
  for (const links of [[[0, 1], [0, 2], [0, 3]], [[0, 1], [0, 2], [0, 3], [0, 4]], [[0, 1], [0, 3]]]) {
    const fixture = smallGraph([[0, 0], [-100, 0], [100, 0], [0, -100], [0, 100]], links);
    const items = rectangles(art.render(fixture, { occupiedSlots: [] }));
    const barriers = part(items, "road-curb"), surface = part(items, "road-surface");
    for (const [, endpoint] of links) {
      const end = fixture.road_network.nodes[endpoint];
      for (let distance = 0; distance <= 80; distance += 2) {
        const cx = Math.sign(end.x) * distance, cy = Math.sign(end.y) * distance;
        for (let across = -12; across <= 12; across += 2) {
          const x = cx + (end.x === 0 ? across : 0), y = cy + (end.y === 0 ? across : 0);
          assert.ok(surface.some(item => contains(item.bounds, x, y)), `junction has a gap at ${x},${y}`);
          assert.ok(!barriers.some(item => contains(item.bounds, x, y)), `junction curb blocks ${x},${y}`);
        }
      }
    }
  }
});
check("front and rear police assets use their actual 28×48 pixel canvases", () => {
  const views = [];
  for (const file of ["police-car-front.svg", "police-car-back.svg"]) {
    const svg = fs.readFileSync(path.join(project, "web/assets", file), "utf8");
    assert.match(svg, /viewBox="0 0 28 48"/);
    assert.match(svg, /shape-rendering="crispEdges"/);
    assert.doesNotMatch(svg, /<filter|<image|<foreignObject|<script|href=|\btransform=/);
    const pixels = [...svg.matchAll(/<rect\b([^>]+)\/>/g)].map(match => {
      const attrs = Object.fromEntries([...match[1].matchAll(/([\w-]+)="([^"]*)"/g)].map(item => [item[1], item[2]]));
      const [x, y, width, height] = ["x", "y", "width", "height"].map(key => Number(attrs[key]));
      assert.ok([x, y, width, height].every(Number.isInteger), `${file}: nonpixel rectangle`);
      assert.ok(width > 0 && height > 0 && within([x, y, x + width, y + height], [0, 0, 28, 48]), `${file}: rectangle out of bounds`);
      return { x, y, width, height, color: attrs.fill };
    });
    assert.ok(pixels.length >= 25);
    views.push(pixels);
  }
  assert.ok(views[0].some(pixel => pixel.color === "#eddda3" && pixel.y >= 39), "front has no recognizable headlights");
  assert.ok(views[1].some(pixel => pixel.color === "#c4897d" && pixel.y >= 40), "rear has no recognizable taillights");
  assert.notDeepEqual(views[0], views[1], "front and rear must be different drawings");
});

const forestContext = { window: {} };
vm.runInNewContext(fs.readFileSync(path.join(project, "web/forest-art.js"), "utf8"), forestContext, { filename: "forest-art.js" });
const forestArt = forestContext.window.StarhavenForestArt;
function forestRectangles(markup) {
  // Parse the real generated SVG attributes, including nested translate/scale
  // groups, so a decorative group cannot silently conceal a street overlap.
  const stack = [[1, 1, 0, 0]], result = [];
  for (const match of markup.matchAll(/<(\/)?(g|rect)\b([^>]*)>/g)) {
    const [, closing, tag, body] = match;
    if (closing) {
      if (tag === "g") { assert.ok(stack.length > 1); stack.pop(); }
      continue;
    }
    const attrs = Object.fromEntries([...body.matchAll(/([\w:-]+)="([^"]*)"/g)].map(item => [item[1], item[2]]));
    const parent = stack[stack.length - 1];
    if (tag === "g") {
      let local = [1, 1, 0, 0];
      const remainder = (attrs.transform || "").replace(/(translate|scale)\(([^)]*)\)/g, (_, name, raw) => {
        const args = raw.trim().split(/[\s,]+/).map(Number);
        assert.ok(args.length >= 1 && args.length <= 2 && args.every(Number.isFinite));
        if (name === "translate") { local[2] += local[0] * args[0]; local[3] += local[1] * (args[1] || 0); }
        else { local[0] *= args[0]; local[1] *= args[1] ?? args[0]; }
        return "";
      });
      assert.match(remainder, /^\s*$/, "forest SVG uses an unvalidated transform");
      stack.push([parent[0] * local[0], parent[1] * local[1], parent[0] * local[2] + parent[2], parent[1] * local[3] + parent[3]]);
    } else {
      const [x, y, width, height] = ["x", "y", "width", "height"].map(key => Number(attrs[key]));
      assert.ok([x, y, width, height].every(Number.isFinite) && width > 0 && height > 0);
      result.push([parent[0] * x + parent[2], parent[1] * y + parent[3], parent[0] * (x + width) + parent[2], parent[1] * (y + height) + parent[3]]);
    }
  }
  assert.equal(stack.length, 1, "unclosed forest SVG group");
  return result;
}
check("the moved forest preserves its approach without covering the northern street, footway or homes", () => {
  const current = forestRectangles(forestArt.scene({ roadNetwork: true }));
  const legacy = forestRectangles(forestArt.scene());
  const northernWalks = part(rendered, "sidewalk").filter(walk => walk.bounds[1] >= northernStreet.top - 14 && walk.bounds[3] <= northernStreet.top);
  const roadAndFootway = [...northernRoads, ...northernWalks];
  assert.ok(!current.some(rect => roadAndFootway.some(road => overlap(rect, road.bounds))), "forest artwork paints over northern street paving or its footway");
  assert.ok(legacy.some(rect => northernRoads.some(road => overlap(rect, road.bounds))), "legacy fixture must exercise the fallback connector over the street");
  for (const rect of current) {
    assert.ok(within(rect, network.bounds), `moved forest leaves the world at ${rect}`);
    assert.ok(!plotBounds.some(plot => overlap(rect, plot)), `moved forest overlaps a home at ${rect}`);
  }
  const entry = network.pedestrian_accesses.find(access => access.id === "forest-path");
  const [x, y] = entry.doorstep;
  for (let px = x - entry.width / 2 + 1; px < x + entry.width / 2; px += 2) {
    assert.ok(current.some(rect => contains(rect, px, y - 1)), "forest avenue ends before the graph pedestrian entry");
    assert.ok(part(rendered, "access-path").some(item => contains(item.bounds, px, y + 1)), "road module fails to continue the forest avenue");
  }
});

// Exercise the actual application entry point as well as the isolated module.
// A forwarding spy checks that the real forest renderer receives graph mode;
// its actual SVG geometry is checked above rather than trusting call metadata.
check("worldBackground integrates the graph once without repainting legacy roads", () => {
  const app = fs.readFileSync(path.join(project, "web/app.js"), "utf8");
  const start = app.indexOf("  function worldBackground(map) {");
  const end = app.indexOf("\n  function updateMap()", start);
  assert.ok(start >= 0 && end > start, "cannot locate application background entry point");
  assert.match(app.slice(start, end), /StarhavenRoadArt\.render/);
  let renderCalls = 0, emptyPlots = 0, forestCalls = 0;
  const pixelRect = (x, y, width, height, fill) => {
    const node = createElementNS(NS, "rect");
    for (const [key, value] of Object.entries({ x, y, width, height, fill })) node.setAttribute(key, value);
    return node;
  };
  const integration = {
    window: {
      StarhavenRoadArt: { render(...args) { renderCalls++; return art.render(...args); } },
      StarhavenForestArt: { scene(options) {
        forestCalls++;
        assert.equal(options?.roadNetwork, true, "forest must defer its old crossing to graph pedestrian connections");
        return forestArt.scene(options);
      } },
    },
    ui: { state: { agents: occupied.map(slot => ({ plot: { slot } })) } },
    mapOrigin: value => value.origin,
    mulberry32: () => () => 0.5,
    svgNode: (name, attrs) => { const node = createElementNS(NS, name); for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value); return node; },
    pixelRect,
    pixelPath: (d, fill) => { const node = createElementNS(NS, "path"); node.setAttribute("d", d); node.setAttribute("fill", fill); return node; },
    pixelFlower: () => { throw new Error("legacy flowers must not overlay graph roads"); },
    meadowPlot: () => { emptyPlots++; return createElementNS(NS, "g"); },
  };
  vm.runInNewContext(`${app.slice(start, end)}\nthis.background = worldBackground;`, integration);
  const background = integration.background(map);
  assert.equal(renderCalls, 1);
  assert.equal(forestCalls, 1);
  assert.equal(emptyPlots, 0);
  const graphGroups = background.children.filter(node => node.getAttribute("class") === "town-road-network");
  assert.equal(graphGroups.length, 1);
  const forestLayers = background.children.filter(node => node.getAttribute("class") === "forest-park");
  assert.equal(forestLayers.length, 1);
  assert.equal(forestLayers[0].innerHTML, forestArt.scene({ roadNetwork: true }));
  for (const item of rectangles(graphGroups[0])) {
    assert.ok(within(item.bounds, network.bounds), "integrated roads exceed world bounds");
    assert.ok(!overlap(item.bounds, core), "integrated roads overlap the village");
  }
  const outsideGraph = background.children.filter(node => node !== graphGroups[0] && node.localName === "rect");
  assert.ok(outsideGraph.every(node => Number(node.getAttribute("width")) <= 6 || Number(node.getAttribute("width")) === map.world[0]), "legacy road rectangle outside road module");
  integration.ui.state.agents = [];
  integration.background(map);
  assert.equal(emptyPlots, map.slots.length, "vacant meadows were lost during road integration");
});

console.log(`${checks} road-art checks passed.`);

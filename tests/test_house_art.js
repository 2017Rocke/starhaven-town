"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// This deliberately models SVG DOM attributes, not renderer-specific .name or
// .attrs properties. It checks generated geometry, not browser layout/painting;
// visual review in a real browser remains necessary for artwork changes.
const SVG_NS = "http://www.w3.org/2000/svg";
const nodeData = new WeakMap();
const legacyAccesses = new Set();
function svgElement(namespaceURI, localName) {
  const node = {
    namespaceURI, localName,
    getAttribute(key) { return nodeData.get(this).attributes.get(key) ?? null; },
    setAttribute(key, value) { nodeData.get(this).attributes.set(key, String(value)); },
    get children() { return nodeData.get(this).children; },
    append(...children) { nodeData.get(this).children.push(...children); },
    appendChild(child) { this.append(child); return child; },
  };
  const proxy = new Proxy(node, {
    get(target, property, receiver) {
      if (property === "name" || property === "attrs") legacyAccesses.add(property);
      return Reflect.get(target, property, receiver);
    },
  });
  nodeData.set(proxy, { attributes: new Map(), children: [] });
  return proxy;
}
const browser = { window: {}, document: { createElementNS: svgElement } };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/house-art.js"), "utf8"), browser, { filename: "house-art.js" });
const art = browser.window.StarhavenHouseArt;
assert.equal(typeof art?.house, "function");
assert.equal(typeof art?.yard, "function");
assert.equal(typeof art?.layout, "function");

const IDENTITY = [1, 0, 0, 1, 0, 0];
function multiply(a, b) {
  return [a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1],
    a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3],
    a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5]];
}
function transform(value) {
  let matrix = IDENTITY;
  const remaining = (value || "").replace(/([A-Za-z]+)\(([^)]*)\)/g, (_, name, args) => {
    const numbers = args.trim().split(/[\s,]+/).map(Number);
    assert.ok(numbers.every(Number.isFinite), `invalid transform: ${value}`);
    let next;
    if (name === "translate" && (numbers.length === 1 || numbers.length === 2)) {
      next = [1, 0, 0, 1, numbers[0], numbers[1] || 0];
    } else if (name === "scale" && (numbers.length === 1 || numbers.length === 2)) {
      next = [numbers[0], 0, 0, numbers[1] ?? numbers[0], 0, 0];
    } else if (name === "matrix" && numbers.length === 6) {
      next = numbers;
    } else {
      assert.fail(`unhandled SVG transform (must not be silently ignored): ${value}`);
    }
    matrix = multiply(matrix, next);
    return "";
  });
  assert.match(remaining, /^[\s,]*$/, `invalid transform: ${value}`);
  return matrix;
}
function rectangles(root) {
  const result = [];
  function visit(node, parentMatrix, parentParts) {
    assert.equal(node.namespaceURI, SVG_NS);
    const matrix = multiply(parentMatrix, transform(node.getAttribute("transform")));
    const parts = new Set(parentParts);
    if (node.getAttribute("data-part")) parts.add(node.getAttribute("data-part"));
    if (node.localName === "rect") {
      const values = ["x", "y", "width", "height"].map(key => {
        assert.notEqual(node.getAttribute(key), null, `rect has no ${key}`);
        return Number(node.getAttribute(key));
      });
      assert.ok(values.every(Number.isFinite), `non-finite rectangle: ${values}`);
      const [x, y, width, height] = values;
      assert.ok(width > 0 && height > 0, `empty rectangle: ${values}`);
      // All drawn edges must remain axis aligned after nested transforms.
      assert.ok((matrix[1] === 0 && matrix[2] === 0) || (matrix[0] === 0 && matrix[3] === 0), "pixel edges must stay axis aligned");
      const corners = [[x, y], [x + width, y], [x, y + height], [x + width, y + height]]
        .map(([px, py]) => [matrix[0] * px + matrix[2] * py + matrix[4], matrix[1] * px + matrix[3] * py + matrix[5]]);
      const left = Math.min(...corners.map(p => p[0])), top = Math.min(...corners.map(p => p[1]));
      const right = Math.max(...corners.map(p => p[0])), bottom = Math.max(...corners.map(p => p[1]));
      result.push({ x: left, y: top, width: right - left, height: bottom - top, parts,
        lighting: /house-(window|lamp)-(light|glint)/.test(node.getAttribute("class") || "") });
    }
    for (const child of node.children) visit(child, matrix, parts);
  }
  visit(root, IDENTITY, new Set());
  return result;
}
function bounds(items) {
  assert.ok(items.length, "expected visible geometry");
  const x = Math.min(...items.map(item => item.x)), y = Math.min(...items.map(item => item.y));
  return { x, y, width: Math.max(...items.map(item => item.x + item.width)) - x,
    height: Math.max(...items.map(item => item.y + item.height)) - y };
}
function withinCanvas(rect, context) {
  assert.ok([rect.x, rect.y, rect.width, rect.height].every(Number.isFinite), `${context}: invalid rectangle`);
  assert.ok([rect.x, rect.y, rect.width, rect.height].every(value => Number.isInteger(value) && value % 2 === 0),
    `${context}: edges must stay on the shared two-pixel grid`);
  assert.ok(rect.width > 0 && rect.height > 0, `${context}: rectangle must be visible`);
  assert.ok(rect.x >= 0 && rect.y >= 0 && rect.x + rect.width <= 320 && rect.y + rect.height <= 280,
    `${context}: outside the 320 x 280 plot: ${JSON.stringify(rect)}`);
}
function pointInCanvas(point, context) {
  assert.ok(point && Number.isFinite(point.x) && Number.isFinite(point.y), `${context}: expected an {x, y} point`);
  assert.ok(point.x >= 0 && point.x <= 320 && point.y >= 0 && point.y <= 280, `${context}: outside the plot`);
}
const styles = ["command", "observatory", "architecture", "workshop", "farm", "music", "finance", "school", "cottage", "angling", "police", "inspection"];
const cases = styles.flatMap(style => [0, 1, 2, 3].map(variant => ({ house: { style, variant } })));
for (const [style, motif] of [["workshop", "music"], ["architecture", "finance"], ["cottage", "hiking"], ["police", "security"], ["inspection", "audit"]]) {
  cases.push({ house: { style, motif, variant: 0 } });
}
const fixtures = cases.map(agent => ({ agent, name: `${agent.house.style}/${agent.house.motif || "default"}/${agent.house.variant}`,
  house: rectangles(art.house(agent)), yard: rectangles(art.yard(agent)), layout: art.layout(agent) }));
let checks = 0, failures = 0;
function check(name, run) {
  try { run(); checks++; console.log(`PASS ${name}`); }
  catch (error) { failures++; console.error(`FAIL ${name}: ${error.message}`); }
}

check("all house styles and variants render within their shared plot", () => {
  for (const fixture of fixtures) {
    assert.ok(fixture.house.length > 0, `${fixture.name}: empty house`);
    for (const rect of [...fixture.house, ...fixture.yard]) withinCanvas(rect, fixture.name);
  }
});
check("layout anchors and interactive bounds are finite local coordinates", () => {
  for (const { name, layout } of fixtures) {
    withinCanvas(layout.hit, `${name}/hit`);
    for (const field of ["label", "resident", "doorstep"]) pointInCanvas(layout[field], `${name}/${field}`);
    assert.equal(layout.resident.x, layout.doorstep.x, `${name}: resident is outside the entrance lane`);
    assert.ok(layout.resident.y + 70 <= 280, `${name}: resident sprite extends below its plot`);
  }
});
check("doors, stairs and layout share the same entrance centre", () => {
  for (const { name, house, layout } of fixtures) {
    const door = bounds(house.filter(rect => rect.parts.has("door")));
    const steps = bounds(house.filter(rect => rect.parts.has("steps")));
    assert.equal(door.x + door.width / 2, 160, `${name}: off-centre door`);
    assert.equal(steps.x + steps.width / 2, 160, `${name}: off-centre stairs`);
    assert.equal(layout.doorstep.x, 160, `${name}: path anchor does not meet the door`);
    assert.ok(steps.y >= door.y + door.height, `${name}: steps overlap the door`);
    assert.ok(layout.doorstep.y >= steps.y && layout.doorstep.y <= steps.y + steps.height,
      `${name}: path anchor misses the stairs`);
    assert.ok(door.x >= layout.hit.x && door.x + door.width <= layout.hit.x + layout.hit.width &&
      door.y >= layout.hit.y && steps.y + steps.height <= layout.hit.y + layout.hit.height,
    `${name}: the entrance is outside its clickable building`);
  }
});
check("yard furniture leaves the central resident passage clear", () => {
  for (const { name, yard } of fixtures) {
    const furniture = yard.filter(item => item.parts.has("furniture"));
    assert.ok(furniture.length, `${name}: missing identifiable yard furniture`);
    for (const rect of furniture) {
      assert.ok(rect.x + rect.width <= 132 || rect.x >= 188,
        `${name}: furniture blocks the x132..188 passage at ${rect.x},${rect.y}`);
      assert.ok(rect.y + rect.height <= 260, `${name}: furniture extends into the road connection`);
    }
  }
});
check("yard paths align with the entrance", () => {
  for (const { name, yard, layout } of fixtures) {
    const paths = yard.filter(rect => rect.parts.has("path"));
    assert.ok(paths.length, `${name}: missing identifiable entrance path`);
    const pathBounds = bounds(paths);
    assert.equal(pathBounds.x + pathBounds.width / 2, layout.doorstep.x, `${name}: yard path is off-centre`);
    assert.ok(pathBounds.y >= layout.doorstep.y, `${name}: yard path overlaps the building`);
  }
});
check("painted landmarks leave the existing village street unobstructed", () => {
  for (const style of ["command", "observatory"]) {
    const agent = { house: { style, painted: true } };
    const equipment = rectangles(art.yard(agent));
    assert.ok(equipment.length > 0, `${style}: civic equipment is missing`);
    for (const rect of equipment) {
      withinCanvas(rect, style);
      assert.ok(rect.y >= art.layout(agent).doorstep.y, `${style}: equipment overlaps the building`);
      assert.ok(rect.x + rect.width <= 140 || rect.x >= 180, `${style}: equipment blocks the entrance`);
      assert.ok(rect.y + rect.height <= 272, `${style}: equipment extends beyond the forecourt`);
    }
    const house = rectangles(art.house(agent));
    assert.ok(house.length > 0, `${style}: missing landmark`);
    for (const rect of house) {
      withinCanvas(rect, style);
      assert.ok(rect.y + rect.height <= 208, `${style}: landmark extends into the street`);
    }
  }
});
check("police and inspection have distinct buildings and complete clickable bounds", () => {
  const signatures = [];
  for (const style of ["police", "inspection"]) {
    const agent = { house: { style } };
    assert.equal(art.house(agent).getAttribute("data-pixel-art"), `house-${style}`, `${style}: fell back to another house`);
    const geometry = rectangles(art.house(agent)), hit = art.layout(agent).hit;
    for (const rect of geometry) {
      assert.ok(rect.x >= hit.x && rect.y >= hit.y && rect.x + rect.width <= hit.x + hit.width && rect.y + rect.height <= hit.y + hit.height,
        `${style}: visible structure lies outside its clickable building`);
    }
    signatures.push(JSON.stringify(geometry.map(({ x, y, width, height }) => [x, y, width, height])));
  }
  assert.notEqual(signatures[0], signatures[1], "the two offices must have different silhouettes and furnishings");
});
check("night glass stays clear of later foreground decorations", () => {
  for (const { name, house } of fixtures) {
    for (let index = 0; index < house.length; index++) {
      const glass = house[index];
      if (!glass.lighting) continue;
      for (const foreground of house.slice(index + 1)) {
        if (foreground.lighting) continue;
        assert.ok(glass.x + glass.width <= foreground.x || glass.x >= foreground.x + foreground.width ||
          glass.y + glass.height <= foreground.y || glass.y >= foreground.y + foreground.height,
        `${name}: promoting night glass would cover a foreground decoration`);
      }
    }
  }
});
check("new residents and patrol car share crisp, bounded SVG artwork", () => {
  for (const [name, width, height] of [["officer", 24, 34], ["inspector", 24, 34], ["police-car", 48, 28]]) {
    const source = fs.readFileSync(path.join(__dirname, `../web/assets/${name}.svg`), "utf8");
    assert.match(source, new RegExp(`viewBox="0 0 ${width} ${height}"`), `${name}: inconsistent sprite scale`);
    assert.match(source, /shape-rendering="crispEdges"/);
    assert.doesNotMatch(source, /<(?:script|filter|image|foreignObject)\b|\b(?:transform|href|rx|ry)=/,
      `${name}: sprite must remain self-contained, with square pixel edges`);
    const shapes = [...source.matchAll(/<rect\b([^>]+)\/>/g)];
    assert.ok(shapes.length > 20, `${name}: missing sprite detail`);
    for (const [, attributes] of shapes) {
      const values = Object.fromEntries([...attributes.matchAll(/([\w:-]+)="([^"]*)"/g)].map(match => [match[1], match[2]]));
      const [x, y, w, h] = ["x", "y", "width", "height"].map(key => Number(values[key]));
      assert.ok([x, y, w, h].every(Number.isInteger) && w > 0 && h > 0, `${name}: non-pixel sprite geometry`);
      assert.ok(x >= 0 && y >= 0 && x + w <= width && y + h <= height, `${name}: artwork exceeds its sprite canvas`);
    }
  }
});
check("rendering uses real SVG attribute access", () => {
  assert.deepEqual([...legacyAccesses], [], "renderer read fake .name/.attrs properties, which do not exist on SVG elements");
});
console.log(`${checks} house artwork checks passed; ${failures} failed.`);
if (failures) process.exitCode = 1;

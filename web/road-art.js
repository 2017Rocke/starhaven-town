"use strict";

// Street art and navigation consume the same centre-line graph. Geometry stays
// in world coordinates: no SVG filters, clipping, or renderer-specific DOM APIs.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.StarhavenRoadArt = api;
})(typeof window !== "undefined" ? window : null, function () {
  const NS = "http://www.w3.org/2000/svg";
  const colors = {
    gravel: "#bfc0b2", grain: "#d2d0bf", grainDark: "#aeb3a5",
    stone: "#b3b6a5", slab: "#d1cfb9", slabLight: "#dedbc5",
    curbLight: "#ded9c4", curbDark: "#a6ad9c", marking: "#ddd7b9",
    walk: "#d8d3bb", walkSeam: "#c1c3ac", green: "#92a37a",
    wood: "#a28b65", woodLight: "#c4aa7c", post: "#7d886b",
  };
  function element(name, attrs) {
    const node = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
    return node;
  }
  function group(part) { return element("g", { "data-part": part }); }
  function rect(parent, x, y, width, height, fill, attrs) {
    if (width <= 0 || height <= 0) return;
    const node = element("rect", { x, y, width, height, fill, ...attrs });
    parent.appendChild(node);
    return node;
  }
  function overlap(a, b) { return a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1]; }
  function merge(intervals) {
    const result = [];
    for (const [start, end] of intervals.slice().sort((a, b) => a[0] - b[0])) {
      if (end <= start) continue;
      const last = result[result.length - 1];
      if (last && start <= last[1]) last[1] = Math.max(last[1], end);
      else result.push([start, end]);
    }
    return result;
  }
  function subtract(intervals, cuts) {
    let result = intervals;
    for (const [start, end] of merge(cuts)) {
      result = result.flatMap(([a, b]) => {
        if (start >= b || end <= a) return [[a, b]];
        const parts = [];
        if (start > a) parts.push([a, start]);
        if (end < b) parts.push([end, b]);
        return parts;
      });
    }
    return result;
  }
  function graphRoads(network) {
    const nodes = new Map((network.nodes || []).map(node => [node.id, node]));
    const bands = new Map();
    for (const edge of network.edges || []) {
      const a = nodes.get(edge.from), b = nodes.get(edge.to), width = Number(edge.width);
      if (!a || !b || ![a.x, a.y, b.x, b.y, width].every(Number.isFinite) || width <= 0) continue;
      if ((a.x === b.x) === (a.y === b.y)) continue;
      const vertical = a.x === b.x, centre = vertical ? a.x : a.y;
      const key = `${vertical ? "v" : "h"}:${centre}:${width}:${edge.surface}`;
      if (!bands.has(key)) bands.set(key, { vertical, centre, width, surface: edge.surface, spans: [] });
      bands.get(key).spans.push([Math.min(vertical ? a.y : a.x, vertical ? b.y : b.x) - width / 2,
        Math.max(vertical ? a.y : a.x, vertical ? b.y : b.x) + width / 2]);
    }
    const roads = [];
    for (const band of bands.values()) for (const [start, end] of merge(band.spans)) {
      const low = band.centre - band.width / 2, high = band.centre + band.width / 2;
      roads.push({ ...band, start, end, bounds: band.vertical ? [low, start, high, end] : [start, low, end, high] });
    }
    return roads;
  }
  function axisCorridor(access, width) {
    const a = access.doorstep, b = access.point;
    if (!Array.isArray(a) || !Array.isArray(b) || ![...a, ...b, width].every(Number.isFinite)) return null;
    if (a[0] === b[0] && a[1] !== b[1]) return [a[0] - width / 2, Math.min(a[1], b[1]), a[0] + width / 2, Math.max(a[1], b[1])];
    if (a[1] === b[1] && a[0] !== b[0]) return [Math.min(a[0], b[0]), a[1] - width / 2, Math.max(a[0], b[0]), a[1] + width / 2];
    return null;
  }
  function approach(access, roads) {
    const a = access.doorstep, b = access.point, vertical = a[0] === b[0];
    const dimension = vertical ? 1 : 0, cross = vertical ? 0 : 1;
    const positive = b[dimension] > a[dimension];
    const crossings = roads.filter(road => a[cross] >= road.bounds[cross] && a[cross] <= road.bounds[cross + 2])
      .map(road => positive ? road.bounds[dimension] : road.bounds[dimension + 2])
      .filter(value => positive ? value >= a[dimension] && value <= b[dimension] : value <= a[dimension] && value >= b[dimension]);
    if (!crossings.length) return null;
    const end = (positive ? Math.min(...crossings) : Math.max(...crossings)) + (positive ? 2 : -2);
    const target = [...b]; target[dimension] = end;
    return axisCorridor({ doorstep: a, point: target }, access.width);
  }
  function boundary(roads) {
    const lines = new Map();
    for (const road of roads) {
      const [left, top, right, bottom] = road.bounds;
      for (const [side, coordinate, start, end] of [["top", top, left, right], ["bottom", bottom, left, right], ["left", left, top, bottom], ["right", right, top, bottom]]) {
        const horizontal = side === "top" || side === "bottom";
        const cuts = [];
        for (const other of roads) {
          const b = other.bounds;
          const outside = side === "top" ? b[1] < coordinate && b[3] >= coordinate
            : side === "bottom" ? b[1] <= coordinate && b[3] > coordinate
              : side === "left" ? b[0] < coordinate && b[2] >= coordinate
                : b[0] <= coordinate && b[2] > coordinate;
          if (outside) cuts.push(horizontal ? [b[0], b[2]] : [b[1], b[3]]);
        }
        const key = `${side}:${coordinate}`;
        if (!lines.has(key)) lines.set(key, { side, coordinate, horizontal, spans: [] });
        lines.get(key).spans.push(...subtract([[start, end]], cuts));
      }
    }
    return [...lines.values()].map(line => ({ ...line, spans: merge(line.spans) }));
  }
  function render(map, options) {
    const output = element("g", { class: "town-road-network", "shape-rendering": "crispEdges", "aria-hidden": "true" });
    const network = map?.road_network;
    if (!network || network.version !== 1) return output;
    const roads = graphRoads(network);
    if (!roads.length) return output;
    const occupied = new Set(options?.occupiedSlots || []);
    const accesses = (network.accesses || []).filter(item => occupied.has(item.slot)).map(item => ({ ...item, width: 32 }));
    accesses.push(...(network.pedestrian_accesses || []).map(item => ({ ...item, width: Number(item.width) || 26 })));
    const corridors = accesses.map(access => axisCorridor(access, access.width)).filter(Boolean);
    const plots = (map.slots || []).map(([x, y]) => [x - map.plot[0] / 2, y - map.plot[1] / 2, x + map.plot[0] / 2, y + map.plot[1] / 2]);
    const core = map.core && [map.core[0], map.core[1], map.core[0] + map.core[2], map.core[1] + map.core[3]];
    const world = network.bounds || [-Infinity, -Infinity, Infinity, Infinity];
    const forbidden = [...plots, ...(core ? [core] : []), ...roads.map(road => road.bounds), ...corridors];
    const inWorld = b => b[0] >= world[0] && b[1] >= world[1] && b[2] <= world[2] && b[3] <= world[3];
    const safe = b => inWorld(b) && !forbidden.some(area => overlap(b, area));
    const surfaces = group("road-surface"), texture = group("road-texture"), walks = group("sidewalk"), entrances = group("access-path"), curbs = group("road-curb"), markings = group("road-marking"), furniture = group("street-furniture");
    output.append(surfaces, texture, walks, entrances, curbs, markings, furniture);

    // Main roads cover their narrower shared-lane joints. Surface rectangles
    // exactly match the graph's square caps, including its dead ends.
    const ordered = roads.slice().sort((a, b) => Number(a.surface !== "shared") - Number(b.surface !== "shared"));
    for (const road of ordered) {
      const [left, top, right, bottom] = road.bounds;
      rect(surfaces, left, top, right - left, bottom - top, colors.gravel,
        { "data-surface": road.surface, "data-direction": road.vertical ? "vertical" : "horizontal" });
    }
    for (const road of roads) {
      // Narrow shared lanes use the same flat road material as the main street.
      // Dense staggered slabs read as a brick wall when the map is zoomed out.
      for (let step = Math.ceil((road.start + 12) / 46) * 46; step < road.end - 12; step += 46) {
        for (const side of [-1, 1]) {
          const across = road.centre + side * (road.width / 2 - 12);
          rect(texture, road.vertical ? across : step, road.vertical ? step : across, road.vertical ? 2 : 6, road.vertical ? 6 : 2,
            side === -1 ? colors.grain : colors.grainDark);
        }
      }
    }
    for (const road of roads.filter(item => !item.vertical && item.surface === "street")) {
      // Meet the curb directly so the northern footway stays connected at the
      // outer-ring corners, including the topmost street's capped junctions.
      const y = road.bounds[1] - 14, height = 14;
      const blockers = [...roads.map(item => item.bounds), ...plots, ...(core ? [core] : []), ...corridors];
      const cuts = blockers.filter(b => b[1] < y + height && b[3] > y).map(b => [b[0], b[2]]);
      const spans = subtract([[Math.max(road.start, world[0]), Math.min(road.end, world[2])]], cuts);
      for (const [start, end] of spans) {
        if (y < world[1] || y + height > world[3]) continue;
        rect(walks, start, y, end - start, height, colors.walk);
        rect(walks, start, y, end - start, 2, colors.slabLight);
        for (let x = Math.ceil(start / 24) * 24; x + 2 < end; x += 24) rect(walks, x, y + 2, 2, height - 2, colors.walkSeam);
      }
    }
    for (const access of accesses) {
      if (!axisCorridor(access, access.width)) continue;
      const path = approach(access, roads);
      if (!path) continue;
      const [left, top, right, bottom] = path;
      const entry = element("g", { "data-slot": access.slot ?? "pedestrian", "data-width": access.width });
      entrances.appendChild(entry);
      if (access.surface === "street") {
        rect(entry, left, top, right - left, bottom - top, colors.gravel);
        rect(entry, left, top, 2, bottom - top, colors.curbLight);
        rect(entry, right - 2, top, 2, bottom - top, colors.curbDark);
        continue;
      }
      rect(entry, left, top, right - left, bottom - top, colors.stone);
      rect(entry, left + 2, top, right - left - 4, bottom - top, colors.slab);
      for (let y = top + 6; y + 2 < bottom; y += 8) rect(entry, left + 2, y, right - left - 4, 2, colors.walkSeam);
    }
    for (const line of boundary(roads)) {
      const cuts = corridors.filter(b => line.horizontal ? b[1] <= line.coordinate && b[3] >= line.coordinate
        : b[0] <= line.coordinate && b[2] >= line.coordinate).map(b => line.horizontal ? [b[0], b[2]] : [b[1], b[3]]);
      for (const [start, end] of subtract(line.spans, cuts)) {
        const inward = line.side === "bottom" || line.side === "right" ? -2 : 0;
        const sharedEdge = roads.some(road => road.surface === "shared" && (
          line.horizontal ? (line.coordinate === road.bounds[1] || line.coordinate === road.bounds[3]) && start >= road.bounds[0] && end <= road.bounds[2]
            : (line.coordinate === road.bounds[0] || line.coordinate === road.bounds[2]) && start >= road.bounds[1] && end <= road.bounds[3]));
        rect(curbs, line.horizontal ? start : line.coordinate + inward, line.horizontal ? line.coordinate + inward : start,
          line.horizontal ? end - start : 2, line.horizontal ? 2 : end - start,
          sharedEdge ? colors.grainDark : line.side === "top" || line.side === "left" ? colors.curbLight : colors.curbDark);
      }
    }
    const junctions = (network.junctions || []).map(junction => junction.bounds).filter(Array.isArray);
    for (const road of roads.filter(item => item.surface === "street")) {
      for (let step = Math.ceil((road.start + 12) / 32) * 32; step + 14 < road.end - 12; step += 32) {
        const dash = road.vertical ? [road.centre - 2, step, road.centre, step + 14] : [step, road.centre - 2, step + 14, road.centre];
        if (junctions.some(area => overlap(dash, area)) || corridors.some(area => overlap(dash, area))) continue;
        rect(markings, dash[0], dash[1], dash[2] - dash[0], dash[3] - dash[1], colors.marking);
      }
    }
    // Low garden lamps fit entirely in the southern 18px shoulder. The northern
    // shoulder is reserved for walking; house and driveway bounds are protected.
    let furnitureIndex = 0;
    const lowestRoad = Math.max(...roads.filter(road => !road.vertical).map(road => road.bounds[3]));
    for (const road of roads.filter(item => !item.vertical && item.surface === "street")) {
      for (let x = Math.ceil((road.start + 80) / 240) * 240 + 60; x + 40 < road.end - 32; x += 480) {
        const y = road.bounds[3] + 2;
        const kind = furnitureIndex++ % 3;
        const box = kind === 0 ? [x, y, x + 12, y + 14] : kind === 1 ? [x, y + 6, x + 20, y + 14] : [x, y, x + 34, y + 14];
        if (!safe(box)) continue;
        const item = group(kind === 0 ? "garden-lamp" : kind === 1 ? "low-greenery" : "bench"); furniture.appendChild(item);
        if (kind === 0) {
          rect(item, x + 4, y + 6, 4, 8, colors.post);
          rect(item, x + 2, y, 8, 2, colors.post);
          rect(item, x, y + 2, 12, 6, colors.post);
          rect(item, x + 2, y + 2, 8, 4, "#e5d2a0");
          rect(item, x + 2, y + 12, 8, 2, "#a2ab88");
        } else if (kind === 1) {
          rect(item, x + 2, y + 10, 16, 4, "#b5c29b");
          rect(item, x, y + 8, 12, 4, colors.green);
          rect(item, x + 8, y + 6, 10, 6, "#a5b58b");
          rect(item, x + 4, y + 6, 4, 2, "#d2c48f");
        } else {
          rect(item, x + 2, y, 30, 4, colors.woodLight);
          rect(item, x + 2, y + 4, 30, 2, colors.wood);
          rect(item, x, y + 8, 34, 4, colors.woodLight);
          rect(item, x + 4, y + 12, 4, 2, colors.post);
          rect(item, x + 26, y + 12, 4, 2, colors.post);
          rect(item, x + 4, y + 6, 2, 2, colors.post);
          rect(item, x + 28, y + 6, 2, 2, colors.post);
        }
      }
      // A small wayfinding board uses the roomier southern edge of the map.
      if (road.bounds[3] === lowestRoad) {
        const x = Math.ceil((road.start + 120) / 2) * 2, y = road.bounds[3] + 22;
        if (safe([x, y, x + 24, y + 18])) {
          const sign = group("wooden-sign"); furniture.appendChild(sign);
          rect(sign, x + 10, y + 6, 4, 12, colors.post);
          rect(sign, x, y, 24, 10, colors.wood);
          rect(sign, x + 2, y + 2, 20, 6, colors.woodLight);
          rect(sign, x + 6, y + 4, 10, 2, "#e0c99b");
          rect(sign, x + 16, y + 2, 2, 6, "#e0c99b");
        }
      }
    }
    return output;
  }
  return { render };
});

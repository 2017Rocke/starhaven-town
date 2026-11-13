"use strict";

// Geometry and clocks only. Navigation never creates or updates inspection work.
((root, factory) => {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.StarhavenRoadNavigation = api;
})(typeof window === "object" ? window : globalThis, () => {
  const EPS = 1e-6, STEP = 4;
  const DIRECTIONS = [[1, 0], [0, 1], [-1, 0], [0, -1]];
  const NAMES = ["right", "down", "left", "up"];
  const cache = new Map();
  const objectCache = new WeakMap();
  const distance = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);
  const point = node => [node.x, node.y];
  const same = (a, b) => distance(a, b) < EPS;
  const heading = (a, b) => a[0] < b[0] ? 0 : a[0] > b[0] ? 2 : a[1] < b[1] ? 1 : 3;
  const carBox = (p, direction) => direction % 2 ? [p[0] - 14, p[1] - 24, p[0] + 14, p[1] + 24] : [p[0] - 24, p[1] - 14, p[0] + 24, p[1] + 14];

  function covered(box, surfaces, world) {
    if (box[0] < world[0] - EPS || box[1] < world[1] - EPS || box[2] > world[2] + EPS || box[3] > world[3] + EPS) return false;
    const overlaps = surfaces.filter(r => r[0] <= box[2] && r[2] >= box[0] && r[1] <= box[3] && r[3] >= box[1]);
    const ys = [...new Set([box[1], box[3], ...overlaps.flatMap(r => [Math.max(box[1], r[1]), Math.min(box[3], r[3])])])].sort((a, b) => a - b);
    if (ys.length === 1) ys.push(ys[0]);
    for (let i = 1; i < ys.length; i++) {
      const y = (ys[i - 1] + ys[i]) / 2;
      const intervals = overlaps.filter(r => r[1] <= y + EPS && r[3] >= y - EPS).sort((a, b) => a[0] - b[0]);
      let x = box[0];
      for (const r of intervals) { if (r[0] > x + EPS) break; x = Math.max(x, r[2]); }
      if (x < box[2] - EPS) return false;
    }
    return true;
  }
  function footprintSafe(context, p, direction, q = p) {
    const a = context.mode === "car" ? carBox(p, direction) : [p[0] - .1, p[1] - .1, p[0] + .1, p[1] + .1];
    const b = context.mode === "car" ? carBox(q, direction) : [q[0] - .1, q[1] - .1, q[0] + .1, q[1] + .1];
    return covered([Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])], context.surfaces, context.bounds);
  }
  function graphFor(network, mode, home, access) {
    const nodes = new Map(network.nodes.map(node => [node.id, node]));
    const adjacent = new Map([...nodes.keys()].map(id => [id, []]));
    const surfaces = [];
    for (const raw of network.edges) {
      const a = nodes.get(raw.from), b = nodes.get(raw.to);
      if (!a || !b || (a.x !== b.x && a.y !== b.y)) throw new Error("Road edges must join known orthogonal nodes");
      const edge = { ...raw, length: distance(point(a), point(b)), speed: raw.speed > 0 ? raw.speed : raw.surface === "shared" ? 24 : 48 };
      if (!edge.length) continue;
      adjacent.get(a.id).push({ edge, to: b.id }); adjacent.get(b.id).push({ edge, to: a.id });
      const half = edge.width / 2;
      surfaces.push(edge.bounds || [Math.min(a.x, b.x) - half, Math.min(a.y, b.y) - half, Math.max(a.x, b.x) + half, Math.max(a.y, b.y) + half]);
      // Only horizontal 64px streets have a published north-side footway.
      // Outer rings remain shared: their outside edge is the world boundary.
      if (mode === "walk" && a.y === b.y && edge.width >= 64) surfaces.push([Math.min(a.x, b.x), a.y - half - 14, Math.max(a.x, b.x), a.y - half]);
    }
    for (const junction of network.junctions || []) if (junction.bounds) surfaces.push(junction.bounds);
    const entry = nodes.get(access.node);
    if (!entry) throw new Error("Resident road access is missing");
    surfaces.push([home[0] - 16, Math.min(home[1] - 24, entry.y - 48), home[0] + 16, entry.y]);
    return { nodes, adjacent, surfaces, bounds: network.bounds, mode, access, home, network, turns: new Map() };
  }
  function shortestPath(context, from, to, accept = () => true, incoming = null) {
    const begin = `${from}|${incoming ?? -1}`, states = new Map([[begin, { node: from, direction: incoming }]]);
    const distances = new Map([[begin, 0]]), previous = new Map(), pending = new Set([begin]);
    let finish = null;
    while (pending.size) {
      const current = [...pending].sort((a, b) => distances.get(a) - distances.get(b) || a.localeCompare(b))[0];
      pending.delete(current);
      const state = states.get(current);
      if (state.node === to) { finish = current; break; }
      const narrow = context.adjacent.get(state.node).every(item => item.edge.width < 48);
      for (const { edge, to: next } of context.adjacent.get(state.node) || []) {
        if (!accept(edge)) continue;
        const dir = heading(point(context.nodes.get(state.node)), point(context.nodes.get(next)));
        if (context.mode === "car" && narrow && state.direction !== null && dir === (state.direction + 2) % 4) continue;
        const nextKey = `${next}|${dir}`;
        const value = distances.get(current) + edge.length;
        if (value + EPS < (distances.get(nextKey) ?? Infinity)) {
          distances.set(nextKey, value); previous.set(nextKey, current); states.set(nextKey, { node: next, direction: dir }); pending.add(nextKey);
        }
      }
    }
    if (finish === null) throw new Error("Road destination is disconnected");
    const result = [finish];
    while (result[0] !== begin) result.unshift(previous.get(result[0]));
    return result.map(key => states.get(key).node);
  }
  function connection(context, from, to) {
    const link = context.adjacent.get(from)?.find(item => item.to === to);
    if (!link) throw new Error("Missing road connection");
    const a = point(context.nodes.get(from)), b = point(context.nodes.get(to)), dir = heading(a, b), vector = DIRECTIONS[dir];
    const trim = Math.min(48, Math.max(4, Math.floor(link.edge.length / 12) * 4));
    const offset = context.mode === "car" ? link.edge.width >= 64 ? [-vector[1] * 16, vector[0] * 16] : [0, 0]
      : a[1] === b[1] && link.edge.width >= 64 ? [0, -link.edge.width / 2 - 8] : [0, 0];
    return { edge: link.edge, dir, start: [a[0] + vector[0] * trim + offset[0], a[1] + vector[1] * trim + offset[1]],
      end: [b[0] - vector[0] * trim + offset[0], b[1] - vector[1] * trim + offset[1]] };
  }
  function segment(a, b, dir, cap, extra = {}) {
    return { a: [...a], b: [...b], direction: NAMES[dir], heading: dir, length: distance(a, b), cap, ...extra };
  }
  class Heap {
    constructor() { this.items = []; }
    push(value) { const a = this.items; a.push(value); let i = a.length - 1; while (i) { const p = (i - 1) >> 1; if (a[p].rank <= value.rank) break; a[i] = a[p]; i = p; } a[i] = value; }
    pop() { const a = this.items, first = a[0], last = a.pop(); if (a.length) { let i = 0; while (i * 2 + 1 < a.length) { let c = i * 2 + 1; if (c + 1 < a.length && a[c + 1].rank < a[c].rank) c++; if (a[c].rank >= last.rank) break; a[i] = a[c]; i = c; } a[i] = last; } return first; }
  }
  // Small junction-only search. A state includes the real 48x28 / 28x48 body;
  // heading can change only where BOTH footprints fit the paved union. Short
  // diagonal steering stays inside the same union throughout the whole segment.
  function turn(context, nodeId, start, startDir, end, endDir, cap) {
    if (same(start, end) && startDir === endDir) return [];
    const key = JSON.stringify([nodeId, start, startDir, end, endDir, cap]);
    if (context.turns.has(key)) return context.turns.get(key).map(s => ({ ...s }));
    if (startDir === endDir && (start[0] === end[0] || start[1] === end[1]) && heading(start, end) === startDir && footprintSafe(context, start, startDir, end)) {
      return [segment(start, end, startDir, cap)];
    }
    const centre = point(context.nodes.get(nodeId));
    const encode = (p, d) => `${p[0]},${p[1]},${d}`;
    const begin = { p: start, d: startDir, cost: 0, rank: distance(start, end), previous: null };
    const queue = new Heap(); queue.push(begin);
    const best = new Map([[encode(start, startDir), 0]]), safe = new Map();
    const fits = (p, d) => { const k = encode(p, d); if (!safe.has(k)) safe.set(k, footprintSafe(context, p, d)); return safe.get(k); };
    let found = null, visited = 0;
    while (queue.items.length && visited++ < 18000) {
      const state = queue.pop();
      if (state.cost > best.get(encode(state.p, state.d)) + EPS) continue;
      if (same(state.p, end) && state.d === endDir) { found = state; break; }
      const forward = DIRECTIONS[state.d], side = [-forward[1], forward[0]];
      const moves = [[forward[0] * STEP, forward[1] * STEP, state.d, STEP]];
      for (const sign of [-1, 1]) {
        moves.push([0, 0, (state.d + sign + 4) % 4, 8]);
        moves.push([side[0] * STEP * sign, side[1] * STEP * sign, state.d, STEP * 2]);
        moves.push([(forward[0] + side[0] * sign) * STEP, (forward[1] + side[1] * sign) * STEP, state.d, STEP * 1.6]);
      }
      for (const [dx, dy, dir, cost] of moves) {
        const p = [state.p[0] + dx, state.p[1] + dy];
        if (Math.abs(p[0] - centre[0]) > 72 || Math.abs(p[1] - centre[1]) > 80 || !fits(p, dir)) continue;
        if (dir === state.d && !footprintSafe(context, state.p, dir, p)) continue;
        const value = state.cost + cost, k = encode(p, dir);
        if (value + EPS >= (best.get(k) ?? Infinity)) continue;
        best.set(k, value); queue.push({ p, d: dir, cost: value, rank: value + distance(p, end), previous: state });
      }
    }
    if (!found) throw new Error(`No safe ${context.mode} turn at ${nodeId}: ${JSON.stringify([start, NAMES[startDir], end, NAMES[endDir]])}`);
    const states = []; for (let current = found; current; current = current.previous) states.unshift(current);
    const result = states.slice(1).map((state, index) => segment(states[index].p, state.p, state.d, cap, same(states[index].p, state.p) ? { hold: .08 } : {}));
    context.turns.set(key, result);
    return result.map(s => ({ ...s }));
  }
  function markGate(segments, node, p, dir) {
    if (segments.length) segments[segments.length - 1].gate = { node, point: [...p], heading: dir };
  }
  function trace(context, walk, start, startDir, finish = null) {
    const result = []; let p = start, dir = startDir;
    for (let i = 1; i < walk.length; i++) {
      const link = connection(context, walk[i - 1], walk[i]);
      const connector = turn(context, walk[i - 1], p, dir, link.start, link.dir, Math.min(24, link.edge.speed));
      result.push(...connector);
      const travel = segment(link.start, link.end, link.dir, link.edge.speed, { edge: link.edge.id, surface: link.edge.surface });
      markGate([travel], walk[i], link.end, link.dir); result.push(travel);
      p = link.end; dir = link.dir;
    }
    if (finish) { const last = turn(context, walk[walk.length - 1], p, dir, finish.point, finish.heading, 24); markGate(last, walk[walk.length - 1], finish.point, finish.heading); result.push(...last); }
    return result;
  }
  function cycle(context, entry) {
    const nodes = [...context.nodes.values()], left = Math.min(...nodes.map(n => n.x)), right = Math.max(...nodes.map(n => n.x));
    const rows = [...new Set(nodes.map(n => n.y))].filter(y => {
      const a = nodes.find(n => n.x === left && n.y === y), b = nodes.find(n => n.x === right && n.y === y);
      if (!a || !b) return false;
      try { shortestPath(context, a.id, b.id, edge => context.nodes.get(edge.from).y === y && context.nodes.get(edge.to).y === y); return true; } catch { return false; }
    }).sort((a, b) => Math.abs(a - entry.y) - Math.abs(b - entry.y) || a - b);
    if (rows.length < 2) throw new Error("Patrol requires two connected streets");
    const east = entry.x >= (left + right) / 2;
    const corners = [[east ? right : left, rows[0]], [east ? right : left, rows[1]], [east ? left : right, rows[1]], [east ? left : right, rows[0]], [east ? right : left, rows[0]]];
    const ids = corners.map(([x, y]) => nodes.find(n => n.x === x && n.y === y).id), walk = [ids[0]];
    for (let i = 1; i < ids.length; i++) walk.push(...shortestPath(context, ids[i - 1], ids[i]).slice(1));
    return walk;
  }
  function plan(agent, map, layout, mode = "car") {
    const network = map?.road_network;
    if (!network?.nodes?.length || !network.edges?.length) return null;
    const access = network.accesses.find(item => item.slot === agent.plot.slot)
      || network.accesses.find(item => Math.abs(item.doorstep[0] - agent.plot.pos[0] - map.plot[0] / 2) < EPS && Math.abs(item.doorstep[1] - agent.plot.pos[1] - map.plot[1]) < EPS);
    if (!access) return null;
    const home = [agent.plot.pos[0] + layout.resident.x, agent.plot.pos[1] + layout.resident.y + 70];
    const localKey = JSON.stringify([access.slot, home, mode]);
    const local = objectCache.get(network) || new Map(); objectCache.set(network, local);
    if (local.has(localKey)) return local.get(localKey);
    const key = JSON.stringify([network, access.slot, home, mode]);
    if (cache.has(key)) { const saved = cache.get(key); local.set(localKey, saved); return saved; }
    const context = graphFor(network, mode, home, access), entry = context.nodes.get(access.node), walk = cycle(context, entry);
    const finalLink = connection(context, walk[walk.length - 2], walk[walk.length - 1]);
    const start = { point: finalLink.end, heading: finalLink.dir };
    const narrowAccess = mode === "car" && context.adjacent.get(entry.id).every(item => item.edge.width < 48);
    const roadPort = narrowAccess ? point(entry) : [entry.x, entry.y - 48];
    const outgoingWalk = shortestPath(context, entry.id, walk[0]);
    const initialDir = narrowAccess ? heading(point(entry), point(context.nodes.get(outgoingWalk[1]))) : 1;
    const drive = segment(home, roadPort, 1, 24, { driveway: true, onFoot: mode === "walk" || narrowAccess });
    const outbound = [drive, ...trace(context, outgoingWalk, roadPort, initialDir, start)];
    const loopSegments = trace(context, walk, start.point, start.heading);
    const result = { navigation: true, home, approach: [home, access.point], loop: [start.point, ...loopSegments.map(s => s.b)],
      context, outbound, loopSegments, walk, start, narrowAccess, roadPort, returns: new Map() };
    cache.set(key, result); if (cache.size > 64) cache.delete(cache.keys().next().value);
    local.set(localKey, result);
    return result;
  }
  const duration = (s, speed) => s.hold || s.length / Math.min(speed, s.cap);
  const totalTime = (segments, speed) => segments.reduce((sum, s) => sum + duration(s, speed), 0);
  function position(segments, elapsed, speed) {
    let remaining = Math.max(0, elapsed);
    for (let index = 0; index < segments.length; index++) {
      const s = segments[index], seconds = duration(s, speed);
      if (remaining < seconds || index === segments.length - 1) {
        const fraction = seconds ? Math.min(1, remaining / seconds) : 1;
        return { point: [s.a[0] + (s.b[0] - s.a[0]) * fraction, s.a[1] + (s.b[1] - s.a[1]) * fraction], direction: s.direction, heading: s.heading,
          index, fraction, moving: !s.hold && s.length > EPS, onFoot: !!s.onFoot, segment: s };
      }
      remaining -= seconds;
    }
    throw new Error("Navigation path is empty");
  }
  function active(route, elapsed, speed) {
    const outgoing = totalTime(route.outbound, speed), phase = elapsed < outgoing ? "outbound" : "patrolling";
    const segments = phase === "outbound" ? route.outbound : route.loopSegments;
    const seconds = phase === "outbound" ? elapsed : (elapsed - outgoing) % totalTime(segments, speed);
    return { ...position(segments, seconds, speed), phase, segments };
  }
  function returnPath(route, elapsed, speed) {
    const key = `${elapsed}:${speed}`;
    if (route.returns.has(key)) return route.returns.get(key);
    const pose = active(route, elapsed, speed), context = route.context;
    let result;
    if (pose.segment.driveway) {
      // A car cannot turn around in a 32px driveway. Keep its body heading and
      // reverse along the same short approach; pedestrians can turn normally.
      const reversing = context.mode === "car" && !pose.segment.onFoot;
      result = [segment(pose.point, route.home, reversing ? pose.heading : 3, 24, { onFoot: !!pose.segment.onFoot, driveway: true, reversing })];
    }
    else {
      const first = { ...pose.segment, a: pose.point, length: distance(pose.point, pose.segment.b) };
      if (first.hold) first.hold *= 1 - pose.fraction;
      const prefix = [first]; let index = pose.index;
      while (!prefix[prefix.length - 1].gate && ++index < pose.segments.length) prefix.push(pose.segments[index]);
      const gate = prefix[prefix.length - 1].gate;
      if (!gate) throw new Error("Return has no road checkpoint");
      const entry = context.nodes.get(context.access.node), roadPort = route.roadPort;
      const walk = shortestPath(context, gate.node, entry.id, () => true, gate.heading);
      const arrivalHeading = route.narrowAccess ? walk.length > 1 ? heading(point(context.nodes.get(walk[walk.length - 2])), point(entry)) : gate.heading : 3;
      result = [...prefix, ...trace(context, walk, gate.point, gate.heading, { point: roadPort, heading: arrivalHeading }),
        segment(roadPort, route.home, 3, 24, { driveway: true, onFoot: context.mode === "walk" || route.narrowAccess })];
    }
    route.returns.set(key, result); if (route.returns.size > 8) route.returns.delete(route.returns.keys().next().value);
    return result;
  }
  function returning(route, finishedAfter, elapsed, speed) {
    const segments = returnPath(route, finishedAfter, speed);
    if (elapsed >= totalTime(segments, speed)) return { point: [...route.home], direction: "down", phase: "home", moving: false };
    return { ...position(segments, elapsed, speed), phase: "returning" };
  }
  return { plan, active, returning, returnPath, shortestPath, position, totalTime, footprintSafe, covered, carBox };
});

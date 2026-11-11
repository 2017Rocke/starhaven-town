"use strict";

// Coordinates are the original town's coordinates; the northern extension is
// negative Y. This module is pure: rendering never commits or creates cargo.
((root, factory) => {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.StarhavenScoutJourney = api;
})(typeof window === "object" ? window : globalThis, () => {
  const ROUTE = Object.freeze([[1590, 850], [1510, 850], [1510, 730], [1498, 730], [1498, 646], [1420, 646], [1420, 300], [1380, 300], [1380, -84], [1320, -84], [1320, -220]].map(Object.freeze));
  const clamp = (v, min = 0, max = 1) => Math.max(min, Math.min(max, Number(v) || 0));
  const epoch = value => typeof value === "number" ? value : (Date.parse(value) || 0);
  const distance = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);
  const length = points => points.slice(1).reduce((total, point, index) => total + distance(points[index], point), 0);
  function along(points, progress) {
    let remaining = length(points) * clamp(progress);
    for (let i = 1; i < points.length; i++) {
      const size = distance(points[i - 1], points[i]);
      if (remaining <= size && size) {
        const t = remaining / size;
        return { point: [points[i - 1][0] + (points[i][0] - points[i - 1][0]) * t, points[i - 1][1] + (points[i][1] - points[i - 1][1]) * t], direction: points[i][0] < points[i - 1][0] ? "left" : points[i][0] > points[i - 1][0] ? "right" : "front" };
      }
      remaining -= size;
    }
    return { point: [...points[points.length - 1]], direction: "front" };
  }
  function prefix(progress) {
    const result = [[...ROUTE[0]]];
    let remaining = length(ROUTE) * clamp(progress);
    for (let i = 1; i < ROUTE.length; i++) {
      const size = distance(ROUTE[i - 1], ROUTE[i]);
      if (remaining < size) { result.push(along([ROUTE[i - 1], ROUTE[i]], remaining / size).point); break; }
      result.push([...ROUTE[i]]); remaining -= size;
    }
    return result;
  }
  function patrol(elapsed) {
    // Stops at three collection clearings keep a long download from looking
    // like a metronome. All movement remains on the same woodland footpath.
    const schedule = [[0, 0], [2, 0], [6, -80], [9, -80], [13, 0], [15, 0], [19, 80], [22, 80], [26, 0], [28, 0]];
    const t = Math.max(0, elapsed / 1000) % 28;
    for (let i = 1; i < schedule.length; i++) {
      const [end, x] = schedule[i], [start, previous] = schedule[i - 1];
      if (t <= end) return { point: [1320 + previous + (x - previous) * (t - start) / (end - start), -220], direction: x < previous ? "left" : x > previous ? "right" : "front", moving: x !== previous };
    }
    return { point: [1320, -220], direction: "front", moving: false };
  }
  function sample(journey, now = Date.now(), reduced = false) {
    const j = journey || {}, start = epoch(j.started_at), forest = epoch(j.forest_arrival_at), returning = epoch(j.return_started_at), arrival = epoch(j.arrival_at), home = epoch(j.home_at);
    let phase = "home", moving = false, pose = { point: [...ROUTE[0]], direction: "front" };
    if (!(j.phase === "home" && j.can_start === true) && start && forest && now >= start && !(home && now >= home)) {
      if (arrival && now >= arrival) phase = "unloading";
      else if (returning && now >= returning) {
        phase = "returning"; moving = true;
        const progress = clamp(j.return_from_progress ?? 1);
        let path = prefix(progress).reverse();
        if (progress >= 1 && forest) path = [patrol(returning - forest).point, ...path];
        pose = along(path, (now - returning) / Math.max(1, arrival - returning));
      } else if (forest && now >= forest) {
        phase = "working"; pose = patrol(now - forest); moving = pose.moving;
      } else {
        phase = "outbound"; moving = true;
        pose = along(ROUTE, (now - start) / Math.max(1, forest - start));
      }
    }
    if (reduced) {
      // Accessibility mode shows meaningful places without continuous motion.
      pose = { point: [...((phase === "working" || (phase === "returning" && Number(j.return_from_progress ?? 1) >= 1)) ? ROUTE[ROUTE.length - 1] : ROUTE[0])], direction: "front" }; moving = false;
    }
    return { ...pose, phase, moving, cargo: Math.max(0, Number(j.cargo_count) || 0), outcome: j.outcome || "", message: j.message || "", canStart: phase === "home" && j.can_start !== false };
  }
  return { ROUTE, along, prefix, patrol, sample, length, epoch };
});

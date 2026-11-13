"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { sample, routeFor, redact, subjectText, officeForAgent, agentForOffice } = require("../web/security-workspace.js");
const start = Date.parse("2026-11-12T09:30:00+08:00");
const time = seconds => new Date(start + seconds * 1000).toISOString();
const route = { home: [100, 100], approach: [[100, 100], [100, 140]], loop: [[100, 140], [40, 140], [180, 140], [100, 140]] };
const active = { id: "run-1", office: "police", phase: "scanning", started_at: time(0), finished_at: null };
const at = (job, seconds, reduced = false) => sample(job, route, start + seconds * 1000, reduced, 20);
const near = (a, b) => assert.ok(Math.hypot(a[0] - b[0], a[1] - b[1]) < .01, `${a} != ${b}`);
let checks = 0;
function check(name, run) { run(); checks++; console.log(`PASS ${name}`); }

check("both offices resolve only their assigned residents", () => {
  assert.equal(officeForAgent("resident-officer"), "police");
  assert.equal(officeForAgent("resident-inspector"), "inspection");
  assert.equal(officeForAgent("resident-unknown"), null);
  assert.equal(agentForOffice("police"), "resident-officer");
  assert.equal(agentForOffice("inspection"), "resident-inspector");
  assert.equal(agentForOffice("town"), null);
});
check("no persisted running job means no invented monitoring or patrol", () => {
  for (const job of [null, {}, { ...active, phase: "idle" }, { ...active, phase: "unexpected" }, { ...active, started_at: null }]) {
    const pose = at(job, 100);
    assert.equal(pose.phase, "home"); assert.equal(pose.working, false); assert.equal(pose.moving, false); assert.equal(pose.canStart, true);
    near(pose.point, route.home);
  }
});
check("active work first follows the driveway, then the same road", () => {
  near(at(active, 1).point, [100, 120]);
  assert.equal(at(active, 1).phase, "outbound");
  near(at(active, 3).point, [80, 140]);
  assert.equal(at(active, 3).phase, "patrolling");
  for (let t = 2; t < 130; t += .3) {
    const pose = at(active, t);
    assert.equal(pose.point[1], 140);
    assert.ok(pose.point[0] >= 40 && pose.point[0] <= 180);
    assert.equal(pose.working, true); assert.equal(pose.canStart, false);
  }
});
check("every terminal outcome returns from the actual finish position", () => {
  for (const phase of ["completed", "partial", "failed", "interrupted"]) {
    const job = { ...active, phase, finished_at: time(3) };
    near(at(job, 3).point, at(active, 3).point);
    assert.equal(at(job, 3).phase, "returning"); assert.equal(at(job, 3).working, false);
    near(at(job, 4).point, [100, 140]);
    near(at(job, 6).point, route.home);
    assert.equal(at(job, 6).phase, "home"); assert.equal(at(job, 6).canStart, true);
  }
});
check("fast failures return along the partial driveway without teleporting", () => {
  const job = { ...active, phase: "failed", finished_at: time(1) };
  near(at(job, 1).point, at(active, 1).point);
  near(at(job, 1.5).point, [100, 110]);
  near(at(job, 2).point, route.home);
});
check("reopening a run reconstructs the same motion without modifying work", () => {
  const job = { ...active, phase: "completed", finished_at: time(3) }, saved = JSON.stringify(job);
  assert.deepEqual(at(job, 4.5), at(JSON.parse(saved), 4.5));
  assert.equal(JSON.stringify(job), saved);
  assert.equal(at(job, 300).phase, "home");
});
check("reduced motion keeps work truthful and actors stationary", () => {
  near(at(active, 3, true).point, at(active, 90, true).point);
  assert.equal(at(active, 90, true).moving, false);
  assert.equal(at(active, 90, true).working, true);
  const job = { ...active, phase: "completed", finished_at: time(3) };
  assert.equal(at(job, 4, true).phase, "returning"); assert.equal(at(job, 4, true).working, false);
  near(at(job, 4, true).point, route.home);
});
check("routes follow actual housing avenues and avoid the village centre", () => {
  const map = { world: [3008, 2460], origin: [-64, -520], core: [960, 330, 960, 660], plot: [320, 280],
    road: { width: 64, verge: 18 }, streets: [308, 688, 1068, 1448, 1828], rings: [-64, 2880],
    slots: [[800, 910], [800, 530], [2080, 530], [800, 1290], [1202, 1670]] };
  const layout = { resident: { x: 160, y: 162 } };
  for (const [cx, cy] of map.slots) {
    const agent = { plot: { pos: [cx - 160, cy - 140] } };
    const path = routeFor(agent, map, layout);
    assert.equal(path.approach[0][0], cx); assert.equal(path.approach[1][0], cx);
    // The car drives down its own driveway and onto the middle of its own street.
    const street = map.streets.find(top => top >= cy + 140);
    assert.equal(path.approach[1][1], street + map.road.width / 2);
    for (const point of path.loop) {
      assert.equal(point[1], path.approach[1][1]);
      assert.ok(point[0] >= -40 && point[0] <= 2920);
      if (cy === 530) assert.ok(cx < 960 ? point[0] < 960 : point[0] > 1920);
    }
  }
  // Without the published grid, the old corridor guess still picks a sane street.
  const legacy = { world: [2880, 2200], origin: [0, -520], core: [960, 330, 960, 660], plot: [320, 280],
    slots: [[800, 830], [800, 490]] };
  const fallback = routeFor({ plot: { pos: [640, 690] } }, legacy, layout);
  const bottom = 690 + 280, worldBottom = -520 + 2200;
  assert.ok(fallback.approach[1][1] > bottom && fallback.approach[1][1] < worldBottom);
});
check("legacy northern routes and eastern vehicles stay clear of the square and world edge", () => {
  const map = { world: [3008, 2460], origin: [-64, -520], core: [960, 330, 960, 660], plot: [320, 280],
    road: { width: 64 }, streets: [308, 688, 1068, 1448, 1828], slots: [[1197,150],[1563,150],[1920,150],[2720,910],[2720,530],[2720,1290]] };
  for (const [cx,cy] of map.slots) {
    const route = routeFor({ plot: { pos: [cx-160,cy-140] } }, map, { resident: { x:160,y:162 } });
    for (const [x,y] of route.loop) {
      assert.ok(x - 24 >= -64 && x + 24 <= 2944);
      assert.ok(!(x + 24 > 960 && x - 24 < 1920 && y + 14 > 330 && y - 14 < 990));
    }
    if (cy === 150) assert.equal(route.approach[1][1],310);
  }
});
check("invalid network data keeps work truthful without breaking town rendering", () => {
  const home = { id: "resident-inspector", plot: { slot:0,pos:[640,770] } };
  const map = { plot:[320,280],road_network:{nodes:[{id:"broken",x:800,y:1100}],edges:[{from:"missing",to:"broken"}],accesses:[]} };
  const route = routeFor(home,map,{resident:{x:160,y:162}});
  assert.equal(route.blocked,true);
  const pose = sample(active,route,start+5000,false,72);
  assert.equal(pose.working,true); assert.equal(pose.moving,false); assert.equal(pose.canStart,false); assert.equal(pose.onFoot,true);
  near(pose.point,route.home);
});
check("an active animation flushes stale status even between throttled paints", () => {
  // Execute the actual animation scheduler with a controlled clock and queue;
  // mounting the office DOM is unrelated to this request-timeout regression.
  const source=fs.readFileSync(path.join(__dirname,"../web/security-workspace.js"),"utf8");
  const schedulers=["\n","\r\n"].map(newline=>{
    const scheduler=source.replace(/\r?\n/g,newline).match(/  (function animate\(\) \{[\s\S]*?\r?\n  \})\r?\n  function locate/);
    assert.ok(scheduler,"the production animation scheduler must be exercised with LF and CRLF checkouts");
    return scheduler[1];
  });
  for(const scheduler of schedulers) for(const reduced of [false,true]) {
    let clock=14990,confirmed=0,nextId=1;
    const queued=new Map(),painted=[],state={animation:null,frame:14985};
    const fresh=()=>clock-confirmed<15000;
    const animate=vm.runInNewContext(`(${scheduler})`,{
      state,freshState:fresh,document:{hidden:false},OFFICES:{police:{},inspection:{}},
      actionFor:()=>({canStart:false}),motionReduced:()=>reduced,
      requestAnimationFrame:callback=>{const id=nextId++;queued.set(id,callback);return id;},
      paintMap:()=>painted.push({fresh:fresh(),time:clock})
    });
    animate();assert.equal(queued.size,1);
    clock=15001;
    const callback=queued.get(state.animation);queued.delete(state.animation);callback(clock);
    assert.deepEqual(painted,[{fresh:false,time:15001}]);assert.equal(state.animation,null);assert.equal(queued.size,0);
    animate();assert.equal(painted.length,1,"starting while stale must not repeatedly paint");assert.equal(queued.size,0);
    confirmed=clock;animate();assert.equal(queued.size,1,"a confirmed response can resume the scheduler");
  }
});
check("evidence display redacts common credential formats and limits output", () => {
  const inputs = ["Authorization: Bearer secret-token", "https://host.test/a?api_key=secret-token&ok=1", '{"password":"secret-token"}', "https://user:secret-token@host.test/a"];
  for (const input of inputs) { assert.ok(!redact(input).includes("secret-token")); assert.ok(redact(input).includes("已隐藏")); }
  assert.equal(redact("x".repeat(5000)).length, 4000);
  assert.equal(subjectText({ name: "report", host: "example.com", secret: "must-not-display" }), "report · example.com");
});
console.log(`${checks} security workspace checks passed.`);

"use strict";
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const path = require("node:path");
const nav = require("../web/road-navigation.js");
const security = require("../web/security-workspace.js");
const root = path.join(__dirname, "..");
// Exercise the real server contract. No duplicated street grid can silently
// continue passing after a backend map change.
const program = "import json; from server import ROAD_NETWORK,PLOT_SLOTS,CORE_X,CORE_Y,CORE_W,CORE_H,WORLD_W,WORLD_H,WORLD_X,WORLD_Y,PLOT_W,PLOT_H; print(json.dumps(dict(road_network=ROAD_NETWORK,slots=PLOT_SLOTS,core=[CORE_X,CORE_Y,CORE_W,CORE_H],world=[WORLD_W,WORLD_H],origin=[WORLD_X,WORLD_Y],plot=[PLOT_W,PLOT_H])))";
let output;
try { output = execFileSync(process.env.STARHAVEN_TEST_PYTHON || "python3", ["-B", "-c", program], { cwd: root, encoding: "utf8" }); }
catch (error) { if (error.code !== "ENOENT" || process.env.STARHAVEN_TEST_PYTHON) throw error; output = execFileSync("python", ["-B", "-c", program], { cwd: root, encoding: "utf8" }); }
const map = JSON.parse(output), network = map.road_network;
const layout = { resident: { x:160,y:162 } };
const makeAgent = (slot, mode) => ({ id: mode === "car" ? "resident-inspector" : "resident-officer", plot: { slot, pos: [map.slots[slot][0]-160,map.slots[slot][1]-140] } });
const near = (a,b) => assert.ok(Math.hypot(a[0]-b[0],a[1]-b[1]) < .0001, `${a} != ${b}`);
const inside = (p,r) => p[0]>=r[0]-.001&&p[0]<=r[2]+.001&&p[1]>=r[1]-.001&&p[1]<=r[3]+.001;
const plans = new Map();
const pavingCache = new WeakMap();
const get = (slot,mode) => plans.get(`${slot}:${mode}`);
let checks = 0;
function check(name,run) { run(); checks++; console.log(`PASS ${name}`); }

// Independent sampled footprint check against the published paving. Include
// the resident's own 32px driveway, never other yards or a guessed shortcut.
function paved(route,p,mode) {
  let surfaces=pavingCache.get(route);
  if(!surfaces) {
  surfaces = [...network.edges.map(e=>e.bounds),...network.junctions.map(j=>j.bounds)];
  if(mode==="walk") for(const edge of network.edges) {
    const a=network.nodes.find(n=>n.id===edge.from),b=network.nodes.find(n=>n.id===edge.to);
    if(a.y===b.y&&edge.width>=64) surfaces.push([Math.min(a.x,b.x),a.y-edge.width/2-14,Math.max(a.x,b.x),a.y-edge.width/2]);
  }
  const entry=network.nodes.find(n=>n.id===route.context.access.node);
  surfaces.push([route.home[0]-16,Math.min(route.home[1]-24,entry.y-48),route.home[0]+16,entry.y]);
  pavingCache.set(route,surfaces);
  }
  return inside(p,network.bounds)&&surfaces.some(r=>inside(p,r));
}
function safeSegments(route,segments,mode) {
  for(const s of segments) for(const t of [0,.25,.5,.75,1]) {
    const p=[s.a[0]+(s.b[0]-s.a[0])*t,s.a[1]+(s.b[1]-s.a[1])*t];
    if(mode==="walk"||s.onFoot) {assert.ok(paved(route,p,mode),`foot left paving ${p}`);continue;}
    const vertical=s.direction==="up"||s.direction==="down",hx=vertical?14:24,hy=vertical?24:14;
    // Every two-pixel row and column, including all four body corners.
    for(let x=-hx;x<=hx;x+=2) for(let y=-hy;y<=hy;y+=2) assert.ok(paved(route,[p[0]+x,p[1]+y],mode),`car body left paving at ${p}, heading ${s.direction}, body pixel ${x},${y}`);
  }
}
check("all 28 entrances plan a real two-street circuit with both ring roads",()=>{
  const started=performance.now();
  assert.equal(map.slots.length,28);
  assert.ok(network.edges.every(edge=>edge.width===64&&edge.surface==="street"),"a narrow shared lane remains in the real road network");
  for(const mode of ["car","walk"]) for(let slot=0;slot<map.slots.length;slot++) {
    const route=nav.plan(makeAgent(slot,mode),map,layout,mode); plans.set(`${slot}:${mode}`,route);
    assert.ok(route.navigation);
    const travelled=route.loopSegments.filter(s=>s.edge).map(s=>network.edges.find(e=>e.id===s.edge));
    const rows=new Set(),rings=new Set();
    for(const edge of travelled) {const a=network.nodes.find(n=>n.id===edge.from),b=network.nodes.find(n=>n.id===edge.to);if(a.y===b.y)rows.add(a.y);else rings.add(a.x);}
    assert.ok(rows.size>=2);assert.deepEqual([...rings].sort((a,b)=>a-b),[-32,2912]);
    for(let i=1;i<route.outbound.length;i++)near(route.outbound[i-1].b,route.outbound[i].a);
    near(route.outbound.at(-1).b,route.loopSegments[0].a);near(route.loopSegments.at(-1).b,route.loopSegments[0].a);
  }
  assert.ok(performance.now()-started<15000,"route planning exceeded the cold-start budget");
});
check("vehicles use both right-hand lanes on every street and pedestrians use the drawn north footway",()=>{
  for(const [key,route] of plans) for(const s of [...route.outbound,...route.loopSegments].filter(s=>s.edge)) {
    const edge=network.edges.find(e=>e.id===s.edge),a=network.nodes.find(n=>n.id===edge.from),b=network.nodes.find(n=>n.id===edge.to);
    assert.equal(s.cap,edge.speed,"a widened street retained the old shared-lane speed");
    if(key.endsWith(":car")) {
      if(a.y===b.y)assert.equal(s.a[1],a.y+(s.direction==="right"?16:-16));
      else assert.equal(s.a[0],a.x+(s.direction==="up"?16:-16));
    }else if(a.y===b.y)assert.equal(s.a[1],a.y-40);
  }
});
check("full bodies, including short diagonal turns, remain on paving at all entrances",()=>{
  // Repeated circuits share geometry; checking every entrance still checks each
  // distinct launch junction and every lane direction used by either mode.
  const checked=new Set();
  for(const [key,route] of plans) {
    const mode=key.split(":")[1];
    const segments=[...route.outbound,...route.loopSegments].filter(s=>{const id=JSON.stringify([mode,s.a,s.b,s.direction,s.onFoot]);if(checked.has(id))return false;checked.add(id);return true;});
    safeSegments(route,segments,mode);
  }
});
check("all 28 entrances, including the northern row, let cars drive out to the full-width street",()=>{
  for(let slot=0;slot<map.slots.length;slot++) {
    const route=get(slot,"car"),access=network.accesses.find(a=>a.slot===slot),row=network.streets.find(s=>s.centre===access.point[1]);
    assert.equal(row.width,64);assert.equal(route.narrowAccess,false);
    assert.equal(route.outbound[0].driveway,true);assert.equal(route.outbound[0].onFoot,false);
    assert.equal(route.outbound[0].direction,"down");
    const driveSeconds=nav.totalTime([route.outbound[0]],72);
    assert.ok(driveSeconds>0);
    assert.equal(nav.active(route,driveSeconds-.001,72).onFoot,false);
    assert.equal(nav.active(route,driveSeconds,72).onFoot,false);
  }
});
check("a car whose job ends in the driveway reverses without flipping its heading",()=>{
  for(let slot=0;slot<map.slots.length;slot++) {
    const route=get(slot,"car"),finish=nav.totalTime([route.outbound[0]],72)/2,before=nav.active(route,finish,72),after=nav.returning(route,finish,0,72);
    assert.equal(before.segment.driveway,true);assert.equal(before.onFoot,false);assert.equal(before.direction,"down");
    near(before.point,after.point);assert.equal(after.direction,before.direction);
    const back=nav.returnPath(route,finish,72);assert.equal(back[0].reversing,true);
    const halfway=nav.returning(route,finish,nav.totalTime(back,72)/2,72);
    assert.equal(halfway.direction,before.direction);assert.ok(halfway.point[1]<before.point[1]);
  }
  const walker=get(6,"walk"),walking=nav.returning(walker,1,0,42);
  assert.equal(walking.onFoot,true);assert.equal(walking.direction,"up");
});
check("finish and failure return continuously from real positions through the graph",()=>{
  for(const [key,route] of plans) {
    const mode=key.split(":")[1],speed=mode==="car"?72:42,out=nav.totalTime(route.outbound,speed),lap=nav.totalTime(route.loopSegments,speed);
    const finishes=[0,.1,1.5,out-.001,out,out+.001,out+lap*.25,out+lap*.66,out+lap+2];
    for(const finish of finishes) {
      const back=nav.returnPath(route,Math.max(0,finish),speed),active=nav.active(route,Math.max(0,finish),speed);
      near(back[0].a,active.point);near(back.at(-1).b,route.home);
      for(let i=1;i<back.length;i++)near(back[i-1].b,back[i].a);
      for(const s of back) assert.ok(s.onFoot||nav.footprintSafe(route.context,s.a,s.heading,s.b),`unsafe return from ${key} at ${finish}`);
      const seconds=nav.totalTime(back,speed);
      near(nav.returning(route,Math.max(0,finish),seconds,speed).point,route.home);
      assert.equal(nav.returning(route,Math.max(0,finish),seconds,speed).phase,"home");
      assert.equal(back.at(-1).onFoot,mode==="walk");
    }
  }
});
check("all junction and driveway boundaries preserve position when a job ends",()=>{
  for(const slot of [4,5,6,7]) {
    const route=get(slot,"car");let elapsed=0;
    for(const s of [...route.outbound,...route.loopSegments]) {
      elapsed+=nav.totalTime([s],72);
      for(const delta of [-.0001,0,.0001]) {
        const finish=Math.max(0,elapsed+delta),before=nav.active(route,finish,72),after=nav.returning(route,finish,0,72);
        near(before.point,after.point);
      }
    }
  }
});
check("persisted job clocks reconstruct the same motion without mutating the work",()=>{
  const epoch=Date.parse("2026-11-13T09:00:00+08:00"),route=get(5,"car");
  for(const phase of ["preparing","scanning","filing","completed","partial","failed","interrupted"]) {
    const job={id:"real-job",phase,started_at:new Date(epoch).toISOString(),finished_at:["preparing","scanning","filing"].includes(phase)?null:new Date(epoch+98000).toISOString()},saved=JSON.stringify(job);
    const pose=security.sample(job,route,epoch+100000,false,72);
    assert.deepEqual(pose,security.sample(JSON.parse(saved),route,epoch+100000,false,72));assert.equal(JSON.stringify(job),saved);
    const reduced=security.sample(job,route,epoch+100000,true,72);assert.equal(reduced.moving,false);assert.equal(reduced.working,pose.working);assert.equal(reduced.phase,pose.phase);
  }
  assert.equal(security.sample(null,route,epoch,false,72).phase,"home");
});
check("time integration honors speed limits and remains continuous at changes",()=>{
  const route=get(4,"car");let seconds=0;
  for(const s of route.loopSegments) {
    const duration=nav.totalTime([s],72);
    if(s.length)assert.ok(Math.abs(duration-s.length/Math.min(72,s.cap))<.000001);
    seconds+=duration;
    if(seconds<nav.totalTime(route.loopSegments,72)-.001)near(nav.position(route.loopSegments,seconds-.000001,72).point,nav.position(route.loopSegments,seconds,72).point);
  }
  const before=performance.now();for(let i=0;i<5000;i++)nav.plan(makeAgent(5,"car"),map,layout,"car");
  assert.ok(performance.now()-before<1000,"animation frames should reuse the planned route");
});
console.log(`${checks} road navigation checks passed.`);

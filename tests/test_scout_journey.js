"use strict";
const assert = require("node:assert/strict");
const { ROUTE, sample, along, prefix, length } = require("../web/scout-journey.js");
const start = Date.parse("2026-11-11T09:00:00+08:00");
const at = seconds => new Date(start + seconds * 1000).toISOString();
const active = { started_at: at(0), forest_arrival_at: at(12), return_started_at: null, arrival_at: null, home_at: null, return_from_progress: 1, cargo_count: 0, can_start: false };
const done = { ...active, return_started_at: at(14), arrival_at: at(26), home_at: at(28), cargo_count: 3 };
const near = (a,b) => assert.ok(Math.hypot(a[0]-b[0],a[1]-b[1]) < 0.05, `${a} != ${b}`);
let checks = 0;
function check(name, run) { run(); checks++; console.log(`PASS ${name}`); }
check("legacy materials stay home without a journey", () => {
  assert.equal(sample({ started_at: at(0), forest_arrival_at: null, can_start: true }, start + 50000).phase, "home");
  assert.equal(sample(null).phase, "home");
});
check("road segments are orthogonal and leave the northern housing gap clear", () => {
  ROUTE.slice(1).forEach((p,i) => assert.ok(p[0] === ROUTE[i][0] || p[1] === ROUTE[i][1]));
  for (let t=0;t<=1;t+=.001) { const [x,y]=along(ROUTE,t).point; if (y>=18 && y<=262) assert.ok(x>1357 && x<1403); assert.ok(!(x>=1389 && x<=1483 && y>=663 && y<=746), "route crosses village fountain"); }
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

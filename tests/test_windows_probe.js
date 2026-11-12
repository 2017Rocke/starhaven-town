"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

// Run the exact JS passed by the native executable to WebView2, not a copy.
const source = fs.readFileSync(path.join(__dirname, "../desktop/WindowsHost.cs"), "utf8");
const literal = source.match(/internal const string SelfTestProbe = @"((?:[^"]|"")*)";/);
assert.ok(literal, "native self-test probe is missing");
const probe = literal[1].replace(/""/g, '"');
const ordinary = ["linghang", "scout", "resident-jiji", "resident-dongri", "resident-code", "resident-climber"];
const identities = [...ordinary, "resident-officer", "resident-inspector"];

function inspect(options = {}) {
  const homes = options.homes || identities;
  const actors = options.actors || ordinary;
  const offices = options.offices || ["police", "inspection"];
  const workspaces = options.workspaces || ["police", "inspection"];
  const picture = { complete: true, naturalWidth: 32, ...options.picture };
  const document = {
    title: "星栖小镇",
    querySelectorAll(selector) {
      if (selector === ".plot[data-resident-home]") return homes.map(id => ({ dataset: { residentHome: id } }));
      if (selector === ".map-resident[data-agent]") return actors.map(id => ({ dataset: { agent: id } }));
      throw new Error(`Unexpected selector: ${selector}`);
    },
    querySelector(selector) {
      if (selector === ".map-art") return options.map === null ? null : { complete: true, naturalWidth: 2880, ...options.map };
      const actor = selector.match(/^\.security-patrol\[data-security-office=(police|inspection)\]$/);
      if (actor) return offices.includes(actor[1]) ? { querySelector: tag => tag === "img" ? picture : null } : null;
      const workspace = selector.match(/^#security-(police|inspection) \.security-job$/);
      if (workspace) return workspaces.includes(workspace[1]) ? {} : null;
      throw new Error(`Unexpected selector: ${selector}`);
    }
  };
  return JSON.parse(vm.runInNewContext(probe, { document, window: { __starhavenTestErrors: options.errors || [] } }));
}

let checks = 0;
function check(name, run) { run(); checks++; console.log(`PASS ${name}`); }

check("six ordinary actors plus two separate patrol actors render eight identified residents", () => {
  const result = inspect();
  assert.equal(result.ready, true);
  assert.equal(result.residents, 8);
  assert.deepEqual(result.resident_ids, [...identities].sort());
  assert.deepEqual(result.security_actors, ["police", "inspection"]);
  assert.deepEqual(result.security_workspaces, ["police", "inspection"]);
});
check("each missing patrol actor prevents readiness even when all houses exist", () => {
  for (const office of ["police", "inspection"]) assert.equal(inspect({ offices: [office] }).ready, false);
});
check("each missing office workspace prevents readiness", () => {
  for (const office of ["police", "inspection"]) assert.equal(inspect({ workspaces: [office] }).ready, false);
});
check("unloaded or broken patrol artwork prevents readiness", () => {
  assert.equal(inspect({ picture: { complete: false } }).ready, false);
  assert.equal(inspect({ picture: { naturalWidth: 0 } }).ready, false);
});
check("a house without its ordinary resident actor prevents readiness", () => {
  assert.equal(inspect({ actors: ordinary.slice(1) }).ready, false);
});
check("unloaded, broken or missing map artwork prevents readiness", () => {
  for (const map of [null, { complete: false }, { naturalWidth: 0 }]) assert.equal(inspect({ map }).ready, false);
});
check("repeated rendering markers do not inflate the resident count", () => {
  const result = inspect({ homes: [...identities, identities[0], ""], actors: [...ordinary, ordinary[0]] });
  assert.equal(result.ready, true);
  assert.equal(result.residents, 8);
});
check("empty maps are not ready and resident counts are not hardcoded", () => {
  assert.equal(inspect({ homes: [] }).ready, false);
  const result = inspect({ homes: [...identities, "visitor"], actors: [...ordinary, "visitor"] });
  assert.equal(result.ready, true);
  assert.equal(result.residents, 9);
  assert.ok(result.resident_ids.includes("visitor"));
});
check("browser errors remain available to the native host", () => {
  assert.deepEqual(inspect({ errors: ["load failed"] }).errors, ["load failed"]);
});
console.log(`${checks} native Windows probe checks passed.`);

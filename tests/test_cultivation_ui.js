"use strict";
// Exercise the production UI against a small DOM and a stateful API. This
// checks save/refresh/conflict behavior; it does not claim browser layout QA.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
  constructor(tag, cls = "", text = "") {
    this.tagName = tag; this.className = cls; this.children = []; this.listeners = new Map(); this.attributes = new Map(); this.value = "";
    this._text = String(text); this.disabled = false; this.open = false;
    this.classList = { add: cls => { this.className += " " + cls; } };
  }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  prepend(...nodes) { for (const node of nodes) node.parent = this; this.children.unshift(...nodes); }
  replaceChildren(...nodes) { this._text = ""; this.children = []; this.append(...nodes); }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set textContent(text) { this._text = String(text); this.children = []; }
  setAttribute(key, value) { this.attributes.set(key, String(value)); }
  addEventListener(event, callback) { const list = this.listeners.get(event) || []; list.push(callback); this.listeners.set(event, list); }
  async emit(event) { for (const callback of this.listeners.get(event) || []) await callback({ preventDefault() {} }); }
  showModal() { this.open = true; }
  close() { this.open = false; return this.emit("close"); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); }
  focus() {}
  reportValidity() { return walk(this).every(node => !node.required || node.disabled || node.value.trim()); }
}
const walk = node => [node, ...node.children.flatMap(walk)];
const find = (root, predicate) => { const found = walk(root).find(predicate); assert.ok(found, "expected UI element"); return found; };
const button = (root, text) => find(root, node => node.tagName === "button" && node.textContent === text);
const field = (root, label) => find(root, node => node.tagName === "label" && node.children[0]?.textContent === label).children[1];
const clone = value => JSON.parse(JSON.stringify(value));

function setup() {
  const root = new Element("div"), body = new Element("body"), intervals = [], requests = [], toasts = [];
  let previewFailure = false;
  const data = {
    policy: { enabled: false, paused: false }, integrity: true, confirmations: [],
    residents: [{ id: "artist", name: "梵高", role: "像素美术师" }],
    cultivation: { revision: 1, town_goal: "提升岗位能力", residents: [{ agent_id: "artist", name: "梵高", role_label: "像素美术师", mode: "recommended", long_term: "", focus: "", success_criteria: "", recommendation: { long_term: "提高像素布局质量", focus: "", success_criteria: "入口畅通" } }] },
  };
  const api = async (url, options = {}) => {
    requests.push({ url, body: clone(options.body || {}) });
    if (url === "/api/growth") return clone(data);
    if (url === "/api/growth/cultivation") {
      const incoming = options.body;
      if (incoming.expected_revision !== data.cultivation.revision) { const error = new Error("培养方向已更新"); error.status = 409; throw error; }
      if (incoming.agent_id) Object.assign(data.cultivation.residents[0], clone(incoming));
      else data.cultivation.town_goal = incoming.town_goal;
      data.cultivation.revision++;
      return clone(data.cultivation);
    }
    if (url === "/api/growth/cultivation/preview") {
      if (previewFailure) throw new Error("预览连接中断");
      return { agent_id: "artist", direction_revision: data.cultivation.revision, notice: "建议预览，不会调用模型", days: Array.from({ length: 7 }, (_, n) => ({ day: `2026-11-${17 + n}`, existing: n === 0, tasks: [{ title: "院落布局 " + n, skill_label: "入口避让", difficulty: 1, reason: "近期重点为门径", difference: "切换到双入口场景", deliverable: "布局说明", checks: ["入口净宽"], assessment: "等待人工验收" }] })) };
    }
    throw new Error("Unexpected API " + url);
  };
  const window = { addEventListener() {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/growth-workspace.js"), "utf8"), {
    window, document: { body, hidden: false, querySelector: selector => selector === "#growth-office" ? root : null },
    setInterval: callback => intervals.push(callback), crypto: { randomUUID: () => "test-request" },
  });
  const workspace = window.StarhavenGrowthWorkspace;
  workspace.init({ api, el: (tag, cls, text) => new Element(tag, cls, text), toast: text => toasts.push(text), getView: () => "growth", getSelected: () => "artist" });
  return { workspace, root, body, data, requests, toasts, interval: () => intervals[0](), failPreview: value => { previewFailure = value; }, dialog: () => find(body, node => node.tagName === "dialog" && node.open) };
}

async function main() {
  const env = setup();
  await env.workspace.open();
  assert.ok(env.root.textContent.includes("全镇培养总目标"));
  assert.ok(env.root.textContent.includes("沿用岗位推荐"));
  assert.equal(env.requests.filter(r => r.url !== "/api/growth").length, 0);

  await env.workspace.editDirection("artist");
  let dialog = env.dialog(), form = find(dialog, node => node.tagName === "form");
  let mode = field(dialog, "方向来源"), direction = field(dialog, "长期方向");
  assert.equal(direction.disabled, true);
  mode.value = "custom"; await mode.emit("change");
  direction.value = "优先改进道路与入口"; await direction.emit("input");
  assert.equal(direction.disabled, false);
  assert.equal(button(dialog, "预览已保存方向").disabled, true);
  // The normal 5-second refresh must not replace an open editor or its draft.
  env.interval(); await env.workspace.open();
  assert.equal(env.dialog(), dialog); assert.equal(direction.value, "优先改进道路与入口");
  await form.emit("submit");
  assert.equal(env.data.cultivation.residents[0].long_term, "优先改进道路与入口");
  assert.equal(env.data.cultivation.residents[0].focus, "");
  assert.equal(env.data.cultivation.residents[0].mode, "custom");
  assert.equal(env.data.policy.enabled, false);
  assert.equal(walk(dialog).filter(n => n.className.includes("growth-preview-day")).length, 7);
  assert.ok(dialog.textContent.includes("已有计划 · 保留生成时的方向与任务要求"));
  assert.ok(dialog.textContent.includes("安排原因：近期重点为门径"));
  assert.ok(dialog.textContent.includes("与上次区别：切换到双入口场景"));
  assert.equal(env.requests.some(r => r.url.includes("owner") || r.url === "/api/growth/plan"), false);
  console.log("PASS saved direction previews seven days without granting permissions or starting training");
  console.log("PASS refresh preserves drafts and optional fields remain optional");

  // Another window updates cultivation after this dialog captured its revision.
  direction.value = "我的新方向"; await direction.emit("input");
  env.data.cultivation.revision++;
  env.data.cultivation.residents[0].long_term = "另一个窗口的方向";
  await form.emit("submit");
  assert.equal(direction.value, "我的新方向");
  assert.equal(env.data.cultivation.residents[0].long_term, "另一个窗口的方向");
  assert.ok(dialog.textContent.includes("另一个窗口的方向"));
  await button(dialog, "已核对，保留我的输入继续编辑").emit("click");
  await form.emit("submit");
  assert.equal(env.data.cultivation.residents[0].long_term, "我的新方向");
  console.log("PASS revision conflict preserves drafts and requires reviewing latest values before resave");

  await button(dialog, "恢复岗位推荐").emit("click");
  assert.equal(mode.value, "recommended"); assert.equal(direction.disabled, true);
  await form.emit("submit");
  assert.equal(env.data.cultivation.residents[0].mode, "recommended");
  assert.equal(env.data.cultivation.residents[0].long_term, "提高像素布局质量");
  console.log("PASS restoring a recommendation uses the same save contract");

  env.failPreview(true);
  mode.value = "custom"; await mode.emit("change");
  direction.value = "已经保存，但预览可能失败"; await direction.emit("input");
  await form.emit("submit");
  assert.equal(env.data.cultivation.residents[0].long_term, "已经保存，但预览可能失败");
  assert.ok(dialog.textContent.includes("培养方向已保存"));
  assert.ok(dialog.textContent.includes("暂时无法预览"));
  assert.equal(button(dialog, "保存并预览").disabled, false);
  console.log("PASS preview failure does not turn a successful save into a failed save");

  await dialog.close();
  await button(env.root, "设置全镇目标").emit("click");
  dialog = env.dialog(); form = find(dialog, node => node.tagName === "form");
  const goal = field(dialog, "全镇培养总目标"); goal.value = "围绕真实项目提升能力"; await goal.emit("input");
  await form.emit("submit");
  assert.equal(env.data.cultivation.town_goal, "围绕真实项目提升能力");
  assert.ok(env.root.textContent.includes("围绕真实项目提升能力"));
  console.log("PASS town goal and resident direction share the same versioned configuration");
}

main().catch(error => { console.error(error); process.exitCode = 1; });

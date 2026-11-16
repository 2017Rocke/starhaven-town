"use strict";
// Stateful UI checks cover drafts, stale writes, duplicate submission, identity
// binding, safe text rendering and visibility-gated polling without real saves.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const clone = value => JSON.parse(JSON.stringify(value));
const walk = node => [node, ...node.children.flatMap(walk)];
class Element {
  constructor(tag, cls = "", text = "") {
    this.tagName = tag; this.className = cls; this._text = String(text ?? ""); this.children = []; this.listeners = new Map(); this.attributes = new Map(); this.dataset = {}; this.value = ""; this.disabled = false;
  }
  append(...nodes) { for (const node of nodes) { if (node.parent) node.parent.children = node.parent.children.filter(item => item !== node); node.parent = this; this.children.push(node); } }
  replaceChildren(...nodes) { this.children.forEach(node => { node.parent = null; }); this.children = []; this._text = ""; this.append(...nodes); }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set textContent(text) { this._text = String(text); this.children = []; }
  set innerHTML(_) { throw new Error("UI must not parse model or user content as HTML"); }
  setAttribute(key, value) { this.attributes.set(key, String(value)); }
  addEventListener(event, callback) { const list = this.listeners.get(event) || []; list.push(callback); this.listeners.set(event, list); }
  async emit(event, extra = {}) { const value = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra }; for (const callback of this.listeners.get(event) || []) if (callback) await callback(value); }
  querySelectorAll(selector) {
    return walk(this).slice(1).filter(node => selector.startsWith(".") ? node.className.split(" ").includes(selector.slice(1)) : selector === "details[open]" ? node.tagName === "details" && node.open : selector === "[data-dev-field]" ? !!node.dataset.devField : false);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  showModal() { this.open = true; }
  close() { this.open = false; return this.emit("close"); }
  remove() { this.parent.children = this.parent.children.filter(node => node !== this); }
  focus() {}
  click() { return this.emit("click"); }
  reportValidity() { return walk(this).every(node => !node.required || node.disabled || node.value.trim() || node.type === "checkbox" && node.checked); }
}
const find = (root, predicate) => { const found = walk(root).find(predicate); assert.ok(found, "expected UI element"); return found; };
const button = (root, label) => find(root, node => node.tagName === "button" && node.textContent === label);
const field = (root, label) => find(root, node => node.tagName === "label" && node.children[0]?.textContent === label).children[1];
const tick = () => new Promise(resolve => setImmediate(resolve));
async function settle() { for (let n = 0; n < 6; n++) await tick(); }
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

function setup(withAttachments = false) {
  const root = new Element("div"), residentRoot = new Element("div"), body = new Element("body"), listeners = new Map(), timers = new Map(), storage = new Map(), documents = new Map(), calls = [], native = [];
  let timerId = 0, view = "development", gate = null, uploadGate = null, failure = null, ownerGate = null, ownerFailure = null, runResult = { id: "run1", status: "review", summary: "候选变更", diff: "--- a/file\n+++ b/file\n+new", checks: [{ name: "功能检查", status: "not_run", detail: "执行器尚未配置" }] };
  const document = { hidden: false, body, activeElement: null, getElementById: id => id === "resident-workspace" ? residentRoot : root, addEventListener: (name, fn) => listeners.set(name, fn) };
  const residents = [{ id: "yang-old", name: "杨辉" }, { id: "jiji-old", name: "吉吉" }, { id: "resident-lili", name: "李力" }, { id: "ali-old", name: "阿力哥" }, { id: "linghang", name: "领航" }];
  const project = { id: "p1", title: "演示项目", members: { product: "yang-old", developer: "jiji-old", tester: "resident-lili", commander: "linghang" } };
  const task = { id: "t1", project_id: "p1", title: "示例需求", revision: 1, active_revision: null, status: "draft", run_status: "idle", next_owner: "product", requirements: "需求正文", revisions: [{ revision: 1, requirements: "需求正文", analysis: { summary: "已有分析" } }], questions: [], artifacts: [{ id: "a1", revision: 1, kind: "requirement_analysis", content: { summary: '<img src=x onerror="alert(1)">' } }], jobs: [], discussions: [] };
  const workspace = { projects: [project], tasks: [task], residents, capabilities: { requirements: true, test_planning: true, development: true, deployment: false, acceptance: false } };
  const connection = { connected: false, repositories: [], provider: { model: "test-model", base_url: "https://model.example.test/v1" } };
  const api = async (url, options = {}) => {
    calls.push({ url, body: clone(options.body || {}) });
    if (url === "/api/dev/workspace") return clone(workspace);
    if (url === "/api/dev/tasks/t1") return clone(task);
    if (url === "/api/dev/connection/p1") return clone(connection);
    if (url === "/api/dev/projects/p1/disconnect") { connection.connected = false; return { connected: false }; }
    if (url === "/api/dev/projects") return { id: "p2", ...clone(options.body) };
    if (url === "/api/dev/tasks") { if (gate) await gate.promise; if (failure) throw failure; return { ...clone(task), attachment_ids: clone(options.body.attachment_ids) }; }
    if (url === "/api/documents") {
      if (uploadGate) await uploadGate.promise;
      const data = options.body, id = `doc${documents.size + 1}`, doc = { id, name: data.name, status: "ready", scope: { agent_id: data.agent_id, project_id: data.project_id, classification: data.classification }, fragments: [], images: [] };
      documents.set(id, doc); return clone(doc);
    }
    if (url.startsWith("/api/documents/")) return clone(documents.get(url.split("/")[3].split("?")[0]));
    if (url === "/api/owner/intents") { if (ownerGate) await ownerGate.promise; if (ownerFailure) throw ownerFailure; return { id: "intent1" }; }
    if (url === "/api/dev/tasks/t1/action") {
      if (gate) await gate.promise;
      if (failure) throw failure;
      if (options.body.revision !== task.revision) { const e = new Error("需求已更新"); e.status = 409; throw e; }
      if (options.body.action === "feedback") task.discussions.push({ id: "d1", role: "user", revision: task.revision, content: options.body.content });
      if (options.body.action === "revise") { task.revision++; task.requirements = options.body.requirements; task.attachment_ids = clone(options.body.attachment_ids || []); task.revisions.push({ revision: task.revision, requirements: task.requirements, attachment_ids: task.attachment_ids }); }
      if (options.body.action === "pause") task.interaction.status = "paused";
      if (options.body.action === "resume") task.interaction.status = "queued";
      if (options.body.action === "answer") task.questions.find(item => item.id === options.body.question_id).answer = options.body.answer;
      return clone(task);
    }
    if (url.startsWith("/api/dev/runs/")) return clone(runResult);
    throw new Error(`unexpected API ${url}`);
  };
  const window = { chrome: { webview: { postMessage: item => native.push(item) } } };
  class FileReader { readAsDataURL(file) { this.result = `data:text/plain;base64,${Buffer.from(file.text || "fixture").toString("base64")}`; this.onload(); } }
  const context = { window, document, FileReader, console, sessionStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) }, setTimeout: callback => { timers.set(++timerId, callback); return timerId; }, clearTimeout: id => timers.delete(id) };
  if (withAttachments) {
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/resident-workspace.js"), "utf8"), context);
    window.StarhavenResidentWorkspace.init({ api, el: (tag, cls, text) => new Element(tag, cls, text), toast: () => {}, getState: () => ({ agents: residents }), getView: () => view, getSelected: () => "yang-old" });
  }
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/dev-workspace.js"), "utf8"), context);
  const ui = window.StarhavenDevWorkspace;
  ui.init({ api, el: (tag, cls, text) => new Element(tag, cls, text), toast: () => {}, selectAgent: () => {}, getState: () => ({ agents: residents }), getView: () => view, getSelected: () => null });
  return { root, body, calls, task, connection, workspace, ui, document, documents, timers, native, listeners, setView: value => { view = value; }, setGate: value => { gate = value; }, setUploadGate: value => { uploadGate = value; }, setFailure: value => { failure = value; }, setOwnerGate: value => { ownerGate = value; }, setOwnerFailure: value => { ownerFailure = value; }, disableNative: () => { delete window.chrome; }, setRunResult: value => { runResult = value; } };
}

(async () => {
  const env = setup(); await env.ui.open();
  assert.match(env.root.textContent, /当前责任人：杨辉/);
  assert.match(env.root.textContent, /受阻 · 尚未接入/);
  assert.ok(env.root.textContent.includes('<img src=x onerror="alert(1)">'), "model markup remains literal text");
  assert.equal(button(env.root, "生成本地修改").disabled, true);
  await button(env.root, "＋ 为项目建档").emit("click");
  const projectDialog = env.body.children[0];
  for (const label of ["产品 · 杨辉", "开发 · 吉吉", "测试 · 李力", "总览 · 领航"]) assert.equal(field(projectDialog, label).value, "", "never bind a role automatically by name");
  const tester = field(projectDialog, "测试 · 李力");
  assert.equal(tester.children.find(option => option.textContent.includes("李力")).value, "resident-lili");
  assert.equal(tester.children.find(option => option.textContent.includes("阿力哥")).value, "ali-old");
  await projectDialog.close();

  env.task.active_revision = 1; await env.ui.refresh();
  const feedback = field(env.root, "给吉吉的修改意见"); feedback.value = "检查空列表"; await feedback.emit("input");
  const gate = deferred(); env.setGate(gate);
  const first = button(env.root, "请求吉吉修改").emit("click");
  assert.equal(button(env.root, "请求吉吉修改").disabled, true);
  const nextInput = field(env.root, "给吉吉的修改意见"); nextInput.value = "还有分页边界"; await nextInput.emit("input");
  gate.resolve(); await first; env.setGate(null);
  assert.equal(field(env.root, "给吉吉的修改意见").value, "还有分页边界", "input typed during an asynchronous save is retained");
  assert.equal(env.task.discussions[0].content, "检查空列表");

  env.task.revision = 2; env.task.requirements = "另一处已修改";
  await env.ui.refresh();
  await button(env.root, "请求吉吉修改").emit("click");
  assert.match(env.root.textContent, /本次操作没有覆盖新版本/);
  assert.equal(field(env.root, "给吉吉的修改意见").value, "还有分页边界");
  assert.equal(env.calls.filter(call => call.body.action === "feedback").at(-1).body.revision, 1, "polling does not silently rebase a dirty draft");
  await button(env.root, "已核对最新内容，保留输入继续").emit("click");
  await button(env.root, "请求吉吉修改").emit("click");
  assert.equal(env.calls.filter(call => call.body.action === "feedback").at(-1).body.revision, 2);

  const retryInput = field(env.root, "给吉吉的修改意见"); retryInput.value = "网络重试"; await retryInput.emit("input");
  env.setFailure(new Error("网络断开"));
  await button(env.root, "请求吉吉修改").emit("click");
  const sent = env.calls.filter(call => call.body.action === "feedback").at(-1).body;
  env.setFailure(null); await button(env.root, "请求吉吉修改").emit("click");
  const retried = env.calls.filter(call => call.body.action === "feedback").at(-1).body;
  assert.equal(retried.request_id, sent.request_id, "unknown network results reuse the idempotency key");

  env.task.active_revision = 1; env.connection.connected = true;
  await env.ui.refresh();
  assert.equal(button(env.root, "生成本地修改").disabled, true, "a new unconfirmed draft cannot start development");
  env.task.active_revision = 2; await env.ui.refresh();
  assert.equal(button(env.root, "生成本地修改").disabled, false);
  env.task.jobs = [{ id: "job1", kind: "develop", role: "developer", revision: 2, status: "completed" }];
  env.task.artifacts.push({ id: "candidate1", kind: "development_candidate", job_id: "job1", revision: 2, content: { run_id: "run1" } });
  await env.ui.refresh(); await button(env.root, "检查本地修改").emit("click"); await settle();
  const review = env.body.children[0]; assert.match(review.textContent, /功能检查：未执行/);
  await button(review, "核对并应用本地修改").emit("click");
  const approval = env.calls.find(call => call.url === "/api/owner/intents");
  assert.equal(approval.body.action, "dev-apply"); assert.equal(approval.body.payload.run_id, "run1");
  assert.equal(env.native[0].id, "intent1");

  env.setRunResult({ status: "apply_interrupted", diff: "candidate diff", applied_files: ["app/example.py"], recovery_files: ["app/.town-recovery-example"] });
  await button(env.root, "检查本地修改").emit("click"); await settle();
  const interrupted = env.body.children[0];
  assert.match(interrupted.textContent, /app\/example.py/); assert.match(interrupted.textContent, /app\/\.town-recovery-example/);
  assert.equal(walk(interrupted).some(node => node.tagName === "button" && node.textContent === "核对并应用本地修改"), false, "interrupted application cannot be replayed");
  await interrupted.close();
  await button(env.root, "断开源码授权").emit("click");
  assert.equal(env.calls.filter(call => call.url === "/api/dev/projects/p1/disconnect").length, 1);
  assert.equal(env.connection.connected, false); assert.equal(env.native.length, 1, "revoking source access does not request a new native grant");
  assert.equal(button(env.root, "生成本地修改").disabled, true);

  env.setView("town"); env.ui.viewChanged("town"); const count = env.calls.length;
  assert.equal(env.timers.size, 0); await env.ui.refresh(); assert.equal(env.calls.length, count, "hidden workbench does not poll");
  env.setView("development"); env.ui.viewChanged("development"); await settle(); assert.ok(env.timers.size);
  env.document.hidden = true; env.listeners.get("visibilitychange")(); assert.equal(env.timers.size, 0);
  const fresh = setup(); fresh.task.artifacts = []; fresh.task.revisions[0].analysis = {};
  await fresh.ui.open();
  assert.equal(button(fresh.root, "分析需求").disabled, false, "empty backend analysis is not a completed analysis");
  assert.equal(button(fresh.root, "确认需求").disabled, true);
  fresh.task.revision = 2; fresh.task.requirements = "新的需求正文";
  await fresh.ui.refresh();
  assert.equal(field(fresh.root, "完整的新需求").value, "新的需求正文", "untouched draft follows the refreshed baseline");
  fresh.task.status = "developing"; fresh.task.run_status = "blocked";
  await fresh.ui.refresh();
  assert.ok(!fresh.root.textContent.includes("本地开发中"), "a blocked development stage is not portrayed as active execution");

  const attached = setup(true); await attached.ui.open();
  await button(attached.root, "＋ 添加需求").emit("click");
  const createDialog = attached.body.children[0], createForm = find(createDialog, node => node.tagName === "form");
  const newTitle = field(createDialog, "需求名称"), newBody = field(createDialog, "原始需求");
  newTitle.value = "带附件需求"; await newTitle.emit("input"); newBody.value = "核对业务图文"; await newBody.emit("input");
  let createPicker = createDialog.querySelector(".resident-attachments"), fileInput = find(createPicker, node => node.type === "file");
  find(createPicker, node => node.type === "checkbox").checked = true;
  fileInput.files = [{ name: "business.md", size: 80 }];
  const uploadGate = deferred(); attached.setUploadGate(uploadGate);
  const upload = fileInput.emit("change"); await settle();
  assert.equal(button(createDialog, "保存需求").disabled, true);
  const beforeUploadSave = attached.calls.length; await createForm.emit("submit"); assert.equal(attached.calls.length, beforeUploadSave);
  uploadGate.resolve(); await upload; attached.setUploadGate(null);
  assert.deepEqual(attached.calls.find(call => call.url === "/api/documents").body, { name: "business.md", content_base64: Buffer.from("fixture").toString("base64"), classification: "requirement", agent_id: "yang-old", project_id: "p1" });
  await createDialog.close(); await button(attached.root, "＋ 添加需求").emit("click");
  const reopened = attached.body.children[0];
  assert.match(reopened.textContent, /business.md/, "closing and reopening a requirement preserves uploaded attachments");
  await find(reopened, node => node.tagName === "form").emit("submit");
  assert.deepEqual(attached.calls.find(call => call.url === "/api/dev/tasks").body.attachment_ids, ["doc1"]);
  assert.equal(attached.body.children.length, 0);

  let revisionPicker = attached.root.querySelector(".resident-attachments"), revisionInput = find(revisionPicker, node => node.type === "file");
  find(revisionPicker, node => node.type === "checkbox").checked = true;
  revisionInput.files = [{ name: "revision.pdf", size: 100 }]; await revisionInput.emit("change");
  attached.task.updated_at = "force-refresh"; await attached.ui.refresh();
  assert.match(attached.root.querySelector(".resident-attachments").textContent, /revision.pdf/, "polling retains the attachment picker and uploaded draft");
  const revised = field(attached.root, "完整的新需求"); revised.value = "需求修订并补充 PDF"; await revised.emit("input");
  await button(attached.root, "提交变更").emit("click");
  assert.deepEqual(attached.calls.filter(call => call.body.action === "revise").at(-1).body.attachment_ids, ["doc2"]);
  await button(attached.root, "查看需求附件").emit("click"); await settle();
  assert.equal(attached.calls.at(-1).url, "/api/documents/doc2?agent_id=yang-old&project_id=p1");
  await attached.body.children[0].close();

  attached.task.interaction = { id: "managed1", agent_id: "yang-old", revision: 7, status: "running", steps: [{ id: "s1", status: "completed", label: "读取材料" }, { id: "s2", status: "running", label: "核对问题" }], progress: { completed: 1, total: 2 }, public_progress: { phase: "receiving", text_chars: 12 }, partial: [{ summary: "材料片段" }], result: null };
  attached.task.revisions.at(-1).status = "draft"; attached.task.revisions.at(-1).analysis = { summary: "旧分析" };
  attached.task.artifacts.push({ id: "old", kind: "requirement_analysis", revision: 2, effective: false, content: { summary: "过期的完整分析" } });
  await attached.ui.refresh();
  assert.match(attached.root.textContent, /杨辉的分步分析.*已完成 1 \/ 2 个步骤/);
  assert.equal(button(attached.root, "确认需求").disabled, true, "a stale analysis or partial interaction cannot be confirmed");
  assert.equal(button(attached.root, "分析需求").disabled, true, "resume controls do not start a replacement interaction");
  await button(attached.root, "暂停").emit("click");
  let managedAction = attached.calls.filter(call => call.url.endsWith("/action")).at(-1);
  assert.equal(managedAction.url, "/api/dev/tasks/t1/action"); assert.equal(managedAction.body.action, "pause"); assert.equal(managedAction.body.revision, 2, "managed actions use the requirement revision");
  await button(attached.root, "继续未完成步骤").emit("click");
  managedAction = attached.calls.filter(call => call.url.endsWith("/action")).at(-1);
  assert.equal(managedAction.body.action, "resume"); assert.equal(attached.task.interaction.steps[0].status, "completed");
  attached.task.interaction.status = "waiting_input"; attached.task.interaction.questions = [{ id: "q-interaction", text: "确认范围", blocking: true }];
  attached.task.questions = [{ id: "q-task", revision: 2, text: "确认范围", blocking: true }]; await attached.ui.refresh();
  assert.equal(walk(attached.root).some(node => node.tagName === "button" && node.textContent === "保存答复并继续"), false, "development answers use the workbench question mapping");
  const managedAnswer = field(attached.root, "你的回答"); managedAnswer.value = "当前页面"; await managedAnswer.emit("input"); await button(attached.root, "保存回答").emit("click");
  managedAction = attached.calls.filter(call => call.url.endsWith("/action")).at(-1);
  assert.equal(managedAction.body.question_id, "q-task"); assert.equal(managedAction.body.action, "answer"); assert.equal(managedAction.body.revision, 2);
  assert.equal(attached.calls.some(call => call.url.startsWith("/api/interactions")), false, "managed development interactions never call the general action endpoint");

  const budget = setup(); await budget.ui.open();
  assert.match(budget.root.textContent, /研发额度：常规 · 单次输出沿用培养设置/);
  assert.doesNotMatch(budget.root.querySelector(".dev-budget-state").textContent, /32768/, "legacy regular mode must not claim the relaxed output limit is active");
  const beforeOpen = budget.calls.length;
  await button(budget.root, "研发额度").emit("click");
  let budgetDialog = budget.body.children[0], budgetForm = find(budgetDialog, node => node.tagName === "form");
  let budgetMode = field(budgetDialog, "研发模式"), outputLimit = field(budgetDialog, "放宽模式单次最大输出 Token");
  assert.equal(budgetMode.value, "regular"); assert.equal(outputLimit.value, "32768"); assert.equal(outputLimit.disabled, true);
  assert.deepEqual(outputLimit.children.map(option => option.value), ["4096", "8192", "16384", "32768"]);
  budgetMode.value = "relaxed"; await budgetMode.emit("change"); outputLimit.value = "16384";
  assert.equal(outputLimit.disabled, false); assert.match(budgetDialog.textContent, /每日调用次数.*每日 Token.*累计 Token.*单次总额限制.*仍记录用量/);
  assert.match(budgetDialog.textContent, /暂停控制.*源码权限继续生效/);
  assert.equal(budget.calls.length, beforeOpen, "editing budget settings does not send a request or change permissions");
  const ownerGate = deferred(); budget.setOwnerGate(ownerGate);
  const submitBudget = budgetForm.emit("submit");
  const duringSubmit = budget.calls.length; await budgetForm.emit("submit");
  assert.equal(budget.calls.length, duringSubmit, "a pending native intent request cannot be duplicated");
  assert.equal(button(budgetDialog, "核对并保存研发额度").disabled, true);
  ownerGate.resolve(); await submitBudget; budget.setOwnerGate(null);
  const budgetIntent = budget.calls.filter(call => call.url === "/api/owner/intents").at(-1);
  assert.deepEqual(budgetIntent.body, { action: "development-budget", payload: { relaxed: true, max_output_tokens: 16384 } });
  assert.equal(budget.native.length, 1); assert.equal(budget.native[0].type, "owner-intent");
  assert.match(budget.root.querySelector(".dev-budget-state").textContent, /研发额度：常规/, "creating an intent does not optimistically apply the requested budget");
  budget.workspace.development_budget = { relaxed: true, max_output_tokens: 16384, effective_output_tokens: 8192, revision: 2 };
  await budget.ui.refresh();
  assert.match(budget.root.textContent, /研发额度：放宽 · 当前单次输出 8192 Token · 研发单独记账/);
  await button(budget.root, "研发额度").emit("click");
  budgetDialog = budget.body.children[0]; budgetForm = find(budgetDialog, node => node.tagName === "form");
  budgetMode = field(budgetDialog, "研发模式"); outputLimit = field(budgetDialog, "放宽模式单次最大输出 Token");
  assert.equal(budgetMode.value, "relaxed"); assert.equal(outputLimit.value, "16384", "saved output choice is distinct from the effective limit");
  outputLimit.value = "65536"; const beforeInvalid = budget.calls.length; await budgetForm.emit("submit");
  assert.equal(budget.calls.length, beforeInvalid); assert.match(budgetDialog.textContent, /请选择有效的研发模式和输出额度/);
  outputLimit.value = "4096"; budgetMode.value = "regular"; await budgetMode.emit("change");
  budget.setOwnerFailure(new Error("确认请求暂不可用")); await budgetForm.emit("submit");
  assert.equal(budgetMode.value, "regular"); assert.equal(outputLimit.value, "4096"); assert.equal(outputLimit.disabled, true);
  assert.match(budgetDialog.textContent, /确认请求暂不可用/); assert.equal(button(budgetDialog, "核对并保存研发额度").disabled, false);
  budget.setOwnerFailure(null); await budgetForm.emit("submit");
  assert.deepEqual(budget.calls.filter(call => call.url === "/api/owner/intents").at(-1).body.payload, { relaxed: false, max_output_tokens: 4096 });
  budget.workspace.development_budget = { relaxed: false, max_output_tokens: 4096, effective_output_tokens: 1024, revision: 3 };
  await budget.ui.refresh(); assert.match(budget.root.textContent, /研发额度：常规 · 当前单次输出 1024 Token/);
  budget.disableNative(); await button(budget.root, "研发额度").emit("click");
  budgetDialog = budget.body.children[0]; budgetForm = find(budgetDialog, node => node.tagName === "form");
  const beforeBrowserSubmit = budget.calls.length; await budgetForm.emit("submit");
  assert.equal(budget.calls.length, beforeBrowserSubmit, "a browser without the native bridge cannot create an approval intent");
  assert.match(budgetDialog.textContent, /请在小镇桌面窗口完成这项确认/);
  console.log("development workbench UI checks passed");
})().catch(error => { console.error(error); process.exitCode = 1; });

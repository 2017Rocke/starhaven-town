"use strict";
// Exercise actual UI handlers with local fixtures; no server, model or private data.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const clone = value => JSON.parse(JSON.stringify(value));
const walk = node => [node, ...node.children.flatMap(walk)];
class Element {
  constructor(tag, cls = "", text = "") { this.tagName = tag; this.className = cls; this._text = String(text ?? ""); this.children = []; this.listeners = new Map(); this.attributes = new Map(); this.dataset = {}; this.value = ""; this.disabled = false; }
  append(...nodes) { for (const node of nodes) { if (node.parent) node.parent.children = node.parent.children.filter(item => item !== node); node.parent = this; this.children.push(node); } }
  replaceChildren(...nodes) { this.children.forEach(node => { node.parent = null; }); this.children = []; this._text = ""; this.append(...nodes); }
  get textContent() { return this._text + this.children.map(node => node.textContent).join(""); }
  set textContent(text) { this.replaceChildren(); this._text = String(text); }
  set innerHTML(_) { throw new Error("model or document content must remain text"); }
  setAttribute(key, value) { this.attributes.set(key, String(value)); }
  addEventListener(event, callback) { const callbacks = this.listeners.get(event) || []; callbacks.push(callback); this.listeners.set(event, callbacks); }
  async emit(event, extra = {}) { const value = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra }; for (const callback of this.listeners.get(event) || []) if (callback) await callback(value); return value; }
  querySelectorAll(selector) { return walk(this).slice(1).filter(node => selector.startsWith(".") ? node.className.split(" ").includes(selector.slice(1)) : selector === "[data-resident-field]" ? !!node.dataset.residentField : false); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  showModal() { this.open = true; } close() { this.open = false; return this.emit("close"); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(item => item !== this); }
  focus() { this.focused = true; } click() { return this.emit("click"); }
}
const find = (node, predicate) => { const result = walk(node).find(predicate); assert.ok(result, "expected UI element"); return result; };
const button = (node, text) => find(node, item => item.tagName === "button" && item.textContent === text);
const field = (node, text) => find(node, item => item.tagName === "label" && item.children[0]?.textContent === text).children[1];
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
function setup() {
  const root = new Element("div"), body = new Element("body"), calls = [], tasks = new Map(), documents = new Map(), timers = new Map(), events = new Map();
  const agents = [{ id: "a-old", name: "杨辉" }, { id: "b-existing", name: "李力" }, { id: "custom-91", name: "自定义伙伴" }];
  let view = "town", selected = agents[0].id, gate = null, failure = null, serial = 0, capability = { supports_streaming: false, supports_vision: false };
  const document = { body, hidden: false, activeElement: null, getElementById: () => root, addEventListener: (name, fn) => events.set(name, fn) };
  const api = async (url, options = {}) => {
    calls.push({ url, body: clone(options.body || {}) });
    if (url === "/api/model-settings") return { settings: { budget_capability: clone(capability) } };
    if (url === "/api/documents") {
      const data = options.body, id = `doc${documents.size + 1}`, image = /\.(png|jpe?g|webp)$/i.test(data.name);
      const doc = { id, name: data.name, status: data.name.includes("partial") ? "partial" : "ready", scope: { agent_id: data.agent_id, project_id: data.project_id, classification: data.classification }, fragments: [{ id: "f1", label: "第 1 页", text: '<img src=x onerror="bad()"> 原文', image_id: image ? "image1" : undefined }], images: image ? [{ id: "image1", mime: "image/png" }] : [], warnings: data.name.includes("partial") ? ["后续页未能提取"] : [] };
      documents.set(id, doc); return clone(doc);
    }
    if (url.startsWith("/api/documents/")) return clone(documents.get(decodeURIComponent(url.split("/")[3].split("?")[0])));
    if (url.startsWith("/api/interactions?")) return { sessions: [...tasks.values()].filter(item => item.agent_id === decodeURIComponent(url.split("agent_id=")[1])).map(clone) };
    if (url === "/api/interactions") {
      if (gate) await gate.promise; if (failure) throw failure;
      const data = options.body, id = `work${tasks.size + 1}`;
      const task = { id, agent_id: data.agent_id, goal: data.goal, revision: 1, status: "queued", attachment_ids: data.attachment_ids, steps: [], facts: [], inferences: [], questions: [], evidence: [], partial: [], result: null };
      tasks.set(id, task); return clone(task);
    }
    if (url.startsWith("/api/interactions/")) {
      const id = url.split("/")[3].split("?")[0], task = tasks.get(id);
      if (!options.method) { assert.equal(url.split("agent_id=")[1], encodeURIComponent(task.agent_id)); return clone(task); }
      if (gate) await gate.promise; if (failure) throw failure;
      const data = options.body; assert.equal(data.revision, task.revision); assert.equal(data.agent_id, task.agent_id);
      if (data.action === "pause") task.status = "paused";
      if (data.action === "resume") { task.status = "queued"; task.steps.filter(step => step.status !== "completed").forEach(step => { step.status = "pending"; }); }
      if (data.action === "answer") { task.questions.find(item => item.id === data.question_id).answer = data.answer; task.revision++; task.status = "queued"; }
      if (data.action === "revise") { task.goal = data.goal; task.attachment_ids = data.attachment_ids; task.revision++; task.status = "queued"; }
      return clone(task);
    }
    throw new Error(`unexpected API: ${url}`);
  };
  class FileReader { readAsDataURL(file) { this.result = `data:${file.type || "text/plain"};base64,${Buffer.from(file.text || "fixture").toString("base64")}`; this.onload(); } }
  const window = {}, context = { window, document, FileReader, console, setTimeout: fn => { timers.set(++serial, fn); return serial; }, clearTimeout: id => timers.delete(id) };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/resident-workspace.js"), "utf8"), context);
  const ui = window.StarhavenResidentWorkspace;
  ui.init({ api, el: (tag, cls, text) => new Element(tag, cls, text), getState: () => ({ agents }), getSelected: () => selected, getView: () => view, selectAgent: id => { selected = id; ui.agentChanged(id); }, showView: name => { view = name; ui.viewChanged(name); }, toast: () => {} });
  return { root, body, calls, tasks, documents, ui, timers, events, document, setView: name => { view = name; ui.viewChanged(name); }, setGate: value => { gate = value; }, setFailure: value => { failure = value; }, setCapability: value => { capability = value; }, select: id => { selected = id; ui.agentChanged(id); } };
}
(async () => {
  const env = setup(); env.ui.open("a-old"); await settle();
  assert.match(env.root.textContent, /杨辉的分步工作/);
  assert.match(env.root.textContent, /逐步接收回复尚未验证/); assert.match(env.root.textContent, /看图能力尚未验证.*图片相关步骤会暂停/);
  env.setCapability({ supports_streaming: true, supports_vision: false }); await env.ui.refresh();
  assert.match(env.root.textContent, /逐步接收回复能力已验证；实际接收状态以当前步骤为准/); assert.doesNotMatch(env.root.textContent, /看图能力已验证/);
  assert.equal(env.root.querySelector(".resident-agent-select").children.length, 3, "every saved resident can use the shared work panel");
  const goal = field(env.root, "这次想完成什么"); goal.value = "阅读材料，列出事实和待确认内容"; await goal.emit("input");
  let picker = env.root.querySelector(".resident-attachments"), input = find(picker, item => item.type === "file");
  input.files = [{ name: "notes.md", size: 50, text: "业务资料" }]; await input.emit("change");
  assert.match(picker.textContent, /已上传.*尚未完成任务分析/);
  const paste = await picker.emit("paste", { clipboardData: { items: [{ kind: "file", type: "image/png", getAsFile: () => ({ name: "screen.png", size: 80, type: "image/png" }) }] } });
  assert.equal(paste.defaultPrevented, true); assert.equal(env.documents.size, 2);
  assert.deepEqual(env.calls.filter(call => call.url === "/api/documents").map(call => [call.body.agent_id, call.body.classification]), [["a-old", "private"], ["a-old", "private"]]);
  env.select("custom-91"); await settle(); assert.match(env.root.textContent, /自定义伙伴的分步工作/);
  env.select("a-old"); await settle(); assert.equal(field(env.root, "这次想完成什么").value, goal.value);
  assert.match(env.root.querySelector(".resident-attachments").textContent, /notes.md.*screen.png/, "draft attachments survive resident switching");
  const createForm = find(env.root, item => item.tagName === "form"), gate = deferred(); env.setGate(gate);
  const creation = createForm.emit("submit"); const createCount = env.calls.length; await createForm.emit("submit"); assert.equal(env.calls.length, createCount, "pending creation cannot submit twice");
  gate.resolve(); await creation; env.setGate(null); await settle();
  const task = env.tasks.get("work1"); assert.deepEqual(clone(task.attachment_ids), ["doc1", "doc2"]);
  assert.match(env.root.querySelector(".resident-task-list").textContent, /阅读材料，列出事实和待确认内容/, "the actual sessions response populates task history");
  assert.match(env.root.textContent, /尚未生成步骤记录/); assert.doesNotMatch(env.root.textContent, /已完成结果/);
  task.status = "interrupted"; task.steps = [{ id: "s1", label: "阅读第一份材料", status: "completed", attempts: 1, sources: ["e1"] }, { id: "s2", label: "核对图片", status: "failed", attempts: 1, error: "图文能力尚未通过验证", sources: ["e2"] }];
  task.progress = { completed: 1, total: 2 }; task.facts = [{ text: "材料中确实存在文字", sources: ["e1"] }]; task.inferences = [{ text: "还需核对图片内容", sources: ["e2"] }];
  task.evidence = [{ id: "e1", label: "需求文稿 · 第 1 页", document_id: "doc1", fragment_id: "f1" }, { id: "e2", label: "截图 · 第 1 页", document_id: "doc2", image_id: "image1" }];
  task.partial = [{ summary: "只完成第一份材料" }]; task.result = { summary: "should-not-be-final" };
  await env.ui.refresh(); assert.match(env.root.textContent, /已完成 1 \/ 2 个步骤/); assert.match(env.root.textContent, /整体尚未完成/); assert.doesNotMatch(env.root.textContent, /should-not-be-final|已完成结果/);
  await button(env.root, "截图 · 第 1 页").emit("click"); await settle();
  const preview = env.body.children[0], image = find(preview, item => item.tagName === "img");
  assert.equal(image.src, "/api/documents/doc2/assets/image1?agent_id=a-old");
  assert.equal(find(preview, item => item.tagName === "a" && item.textContent === "下载原件").href, "/api/documents/doc2/assets?agent_id=a-old");
  assert.ok(preview.textContent.includes('<img src=x onerror="bad()">'), "extracted markup stays literal text"); await preview.close();
  await button(env.root, "继续未完成步骤").emit("click"); assert.equal(task.steps[0].status, "completed"); assert.equal(task.steps[0].attempts, 1);
  assert.equal(env.calls.filter(call => call.url.endsWith("/action")).at(-1).body.action, "resume");
  task.status = "running"; task.public_progress = { phase: "waiting", text_chars: 0 }; await env.ui.refresh();
  assert.match(env.root.textContent, /等待模型响应，当前步骤尚未完成/);
  task.public_progress = { phase: "receiving", text_chars: 240 }; await env.ui.refresh();
  assert.match(env.root.textContent, /正在接收回复 · 已收到 240 字，当前步骤尚未完成/); assert.doesNotMatch(env.root.textContent, /240%/);
  await button(env.root, "暂停").emit("click"); assert.equal(task.status, "paused");
  task.status = "waiting_input"; task.questions = [{ id: "q1", text: "这张图的目标页面是什么？", blocking: true, sources: ["e2"] }]; task.result = null; await env.ui.refresh();
  const answer = field(env.root, "你的答复"); answer.value = "用于订单详情页"; await answer.emit("input");
  env.setFailure(new Error("暂时断开")); await button(env.root, "保存答复并继续").emit("click");
  const firstAnswer = env.calls.filter(call => call.body.action === "answer").at(-1).body;
  assert.equal(field(env.root, "你的答复").value, "用于订单详情页");
  env.setFailure(null); await button(env.root, "保存答复并继续").emit("click");
  const secondAnswer = env.calls.filter(call => call.body.action === "answer").at(-1).body;
  assert.equal(firstAnswer.request_id, secondAnswer.request_id, "unknown action results reuse the same request id"); assert.equal(secondAnswer.revision, 1); assert.equal(task.revision, 2); assert.equal(task.questions[0].answer, "用于订单详情页");
  task.status = "completed"; task.steps[1].status = "completed"; task.progress.completed = 2; task.result = { summary: "材料核对完成", scope: ["页面信息"], interaction_session_id: "internal-session-id" }; await env.ui.refresh();
  assert.match(env.root.textContent, /已完成结果.*材料核对完成/);
  assert.doesNotMatch(env.root.textContent, /整体尚未完成/);
  assert.doesNotMatch(env.root.textContent, /internal-session-id|正在接收回复/, "internal bookkeeping and stale streaming state are hidden");
  env.setView("town"); const beforeHidden = env.calls.length; await env.ui.refresh(); assert.equal(env.calls.length, beforeHidden); assert.equal(env.timers.size, 0);

  const shared = env.ui.attachmentPicker({ agentId: "a-old", projectId: "project1", classification: "requirement" });
  await shared.upload([{ name: "spec.pdf", size: 100 }]); assert.equal(shared.ids().length, 0); assert.match(shared.node.textContent, /先确认.*业务资料/);
  find(shared.node, item => item.type === "checkbox").checked = true;
  await shared.upload([{ name: "partial.pdf", size: 100 }]); assert.equal(shared.ids().length, 1); assert.match(shared.node.textContent, /部分内容待核对/);
  const sharedRequest = env.calls.filter(call => call.url === "/api/documents").at(-1).body; assert.equal(sharedRequest.classification, "requirement"); assert.equal(sharedRequest.project_id, "project1");
  const count = env.calls.length; await shared.upload([{ name: "huge.pdf", size: 21 * 1024 * 1024 }, { name: "not-supported.exe", size: 10 }]);
  assert.equal(env.calls.length, count, "invalid or oversized files never dispatch an upload"); assert.equal(shared.hasErrors(), true);
  assert.match(shared.node.textContent, /源码截图.*不能作为业务附件/);

  const index = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8"), app = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
  assert.ok(index.includes('id="open-resident-work"') && index.includes('id="chat-form"'));
  assert.ok(app.includes('api("/api/chat"') && app.includes('StarhavenResidentWorkspace?.open(ui.selected)'));
  console.log("resident work UI checks passed");
})().catch(error => { console.error(error); process.exitCode = 1; });

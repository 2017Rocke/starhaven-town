"use strict";
// Local fixtures exercise imports, per-message scope and the actual chat sender.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const clone = value => JSON.parse(JSON.stringify(value));
const walk = node => [node, ...node.children.flatMap(walk)];
class Element {
  constructor(tag, cls = "", text = "") { this.tagName = tag; this.className = cls; this._text = String(text ?? ""); this.children = []; this.listeners = new Map(); this.dataset = {}; this.value = ""; this.disabled = false; }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  replaceChildren(...nodes) { this.children = []; this._text = ""; this.append(...nodes); }
  get textContent() { return this._text + this.children.map(node => node.textContent).join(""); }
  set textContent(value) { this.replaceChildren(); this._text = String(value); }
  set innerHTML(_) { throw new Error("skill text must never be parsed as HTML"); }
  setAttribute() {}
  addEventListener(name, fn) { const callbacks = this.listeners.get(name) || []; callbacks.push(fn); this.listeners.set(name, callbacks); }
  async emit(name) { for (const fn of this.listeners.get(name) || []) await fn({ preventDefault() {} }); }
  showModal() { this.open = true; } close() { this.open = false; return this.emit("close"); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); }
}
const find = (node, predicate) => { const value = walk(node).find(predicate); assert.ok(value, "expected skill UI control"); return value; };
const button = (node, text) => find(node, item => item.tagName === "button" && item.textContent === text);
const checkbox = (node, title) => find(node, item => item.tagName === "label" && item.textContent.includes(title)).children[0];
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); };
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
function setup() {
  const root = new Element("div"), body = new Element("body"), calls = [], agents = [{ id: "dynamic-jiji-91", name: "吉吉" }, { id: "product-2", name: "杨辉" }];
  const catalog = { allowed: true, installed: [], available: [{ name: "java-coding-standard", title: "Java 开发规范", description: '<img src=x onerror="bad()">', status: "available" }, { name: "triage", title: "排障指南", description: "只读检查说明", status: "available" }, { name: "broken", title: "损坏技能包", status: "blocked", reason: "技能目录包含不允许的链接" }] };
  let importGate = null, failure = null, selected = agents[0].id;
  const api = async (url, options = {}) => {
    calls.push({ url, body: clone(options.body || {}) });
    if (url.startsWith("/api/local-skills?")) return url.endsWith("product-2") ? { allowed: false, reason: "此居民不是开发岗位", available: [], installed: [] } : clone(catalog);
    if (url === "/api/local-skills/import") {
      if (importGate) await importGate.promise; if (failure) throw failure;
      assert.equal(options.body.agent_id, "dynamic-jiji-91");
      const imported = options.body.names.map(name => { const skill = catalog.available.find(item => item.name === name); return { ...skill, id: `id-${name}`, hash: "0123456789abcdef", files: [{ path: "SKILL.md", bytes: 1024 }, { path: "references/checks.md", bytes: 2048 }, { path: "references/too-large.md", bytes: 49 * 1024 }, { path: "scripts/inspect.py", bytes: 2000 }], status: name === "triage" ? "partial_dependencies" : "imported", warnings: name === "triage" ? ["指南可用，环境凭据未导入；脚本不会执行"] : [] }; });
      catalog.installed.push(...imported); return clone(imported);
    }
    throw new Error(`unexpected API: ${url}`);
  };
  const window = {}, document = { body, getElementById: () => root };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../web/local-skills.js"), "utf8"), { window, document, console });
  const ui = window.StarhavenLocalSkills;
  ui.init({ api, el: (tag, cls, text) => new Element(tag, cls, text), toast: () => {}, getState: () => ({ agents }), getSelected: () => selected });
  return { root, body, calls, catalog, ui, select: id => { selected = id; ui.agentChanged(id); }, setGate: value => { importGate = value; }, setFailure: value => { failure = value; } };
}
(async () => {
  const env = setup(); env.select("dynamic-jiji-91"); await settle();
  assert.equal(env.root.hidden, false); assert.match(env.root.textContent, /本次未使用技能/);
  await button(env.root, "技能工作区").emit("click");
  let dialog = env.body.children[0];
  assert.ok(dialog.textContent.includes('<img src=x onerror="bad()">'), "untrusted skill descriptions remain text");
  assert.equal(checkbox(dialog, "损坏技能包").disabled, true);
  const java = checkbox(dialog, "Java 开发规范"); java.checked = true; await java.emit("change");
  const triage = checkbox(dialog, "排障指南"); triage.checked = true; await triage.emit("change");
  const gate = deferred(); env.setGate(gate); const importButton = button(dialog, "导入所选技能"), imported = importButton.emit("click");
  const duringImport = env.calls.length; await importButton.emit("click"); assert.equal(env.calls.length, duringImport, "duplicate imports are blocked while the request is pending");
  gate.resolve(); await imported; env.setGate(null);
  assert.deepEqual(env.calls.find(call => call.url === "/api/local-skills/import").body.names, ["java-coding-standard", "triage"]);
  assert.equal(env.ui.selected("dynamic-jiji-91").length, 0, "importing never silently enables a skill for chat");
  assert.match(dialog.textContent, /指南可用，环境凭据未导入/);
  const selectJava = checkbox(dialog, "Java 开发规范"); selectJava.checked = true; await selectJava.emit("change");
  const selectTriage = checkbox(dialog, "排障指南"); assert.equal(selectTriage.disabled, false, "partial dependencies remain usable as guidance"); selectTriage.checked = true; await selectTriage.emit("change");
  assert.deepEqual(clone(env.ui.selected("dynamic-jiji-91")), ["id-java-coding-standard", "id-triage"]);
  assert.deepEqual(clone(env.ui.selectedFiles("dynamic-jiji-91")), { "id-java-coding-standard": ["SKILL.md"], "id-triage": ["SKILL.md"] }, "only each selected guide is included by default");
  assert.equal(dialog.textContent.includes("scripts/inspect.py"), false, "script resources are not offered as chat reference text");
  const reference = checkbox(dialog, "references/checks.md"); reference.checked = true; await reference.emit("change");
  assert.deepEqual(clone(env.ui.selectedFiles("dynamic-jiji-91"))["id-java-coding-standard"], ["SKILL.md", "references/checks.md"]);
  const tooLarge = checkbox(dialog, "references/too-large.md"); tooLarge.checked = true; await tooLarge.emit("change");
  assert.equal(tooLarge.checked, false); assert.match(dialog.textContent, /超过 48 KiB/);
  assert.match(env.root.textContent, /本次使用 2 个技能/);
  env.ui.setBusy("dynamic-jiji-91", true); selectJava.checked = false; await selectJava.emit("change"); assert.equal(selectJava.checked, true, "an in-flight chat snapshot cannot be changed"); env.ui.setBusy("dynamic-jiji-91", false);
  await dialog.close(); env.select("product-2"); await settle(); assert.equal(env.root.hidden, true); assert.equal(env.ui.selected("product-2").length, 0);
  env.select("dynamic-jiji-91"); await settle(); assert.equal(env.ui.selected("dynamic-jiji-91").length, 2);
  env.catalog.installed.push({ id: "id-third", title: "第三个技能", status: "imported" }, { id: "id-fourth", title: "第四个技能", status: "imported" });
  await button(env.root, "技能工作区").emit("click"); dialog = env.body.children[0];
  const third = checkbox(dialog, "第三个技能"); third.checked = true; await third.emit("change");
  const fourth = checkbox(dialog, "第四个技能"); fourth.checked = true; await fourth.emit("change");
  assert.equal(fourth.checked, false); assert.equal(env.ui.selected("dynamic-jiji-91").length, 3); assert.match(dialog.textContent, /最多选择 3 个/);
  env.ui.clearSelected("dynamic-jiji-91", ["id-java-coding-standard", "id-triage", "id-third"]); assert.equal(env.ui.selected("dynamic-jiji-91").length, 0);
  assert.deepEqual(clone(env.ui.selectedFiles("dynamic-jiji-91", ["id-java-coding-standard"])), { "id-java-coding-standard": ["SKILL.md"] }, "successful send clears additional references along with skills");
  await dialog.close();

  // Execute the app's real sender and prove retries retain the same skill scope.
  const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8"), sender = source.slice(source.indexOf("  async function sendMessage("), source.indexOf("  function startPolling("));
  let chosen = ["id-java-coding-standard"], chosenFiles = { "id-java-coding-standard": ["SKILL.md", "references/checks.md"] }, error = new Error("连接中断"), draft = "审查代码", serial = 0;
  const chatCalls = [], cleared = [], chatUI = { selected: "dynamic-jiji-91", requests: new Map(), submitting: new Set() }, input = { value: draft };
  const context = { ui: chatUI, window: { StarhavenLocalSkills: { selected: () => [...chosen], selectedFiles: (_id, ids) => Object.fromEntries(ids.map(id => [id, clone(chosenFiles[id] || ["SKILL.md"])])), setBusy: () => {}, clearSelected: (_id, ids) => { cleared.push([...ids]); chosen = chosen.filter(id => !ids.includes(id)); } } }, currentAgent: () => ({ id: chatUI.selected }), $: () => input, isBusy: () => chatUI.submitting.size > 0, showChatError: () => {}, updateComposer: () => {}, requestId: () => `request-${++serial}`, api: async (_url, options) => { chatCalls.push(clone(options.body)); if (error) throw error; return {}; }, loadMessages: async () => {}, startPolling: () => {}, getDraft: () => draft, saveDraft: (_id, value) => { draft = value; }, renderMessages: () => {} };
  vm.runInNewContext(`${sender}\nglobalThis.send = sendMessage;`, context);
  await context.send(); assert.equal(cleared.length, 0, "failed chat keeps selected skills");
  chosen = ["id-triage"]; chosenFiles = {}; error = null; await context.send();
  assert.equal(chatCalls[0].request_id, chatCalls[1].request_id); assert.deepEqual(chatCalls[1].local_skill_ids, ["id-java-coding-standard"], "an unknown-result retry preserves its original skill scope");
  assert.deepEqual(chatCalls[1].local_skill_files, { "id-java-coding-standard": ["SKILL.md", "references/checks.md"] }, "an unknown-result retry preserves the explicitly selected reference files");
  assert.deepEqual(cleared[0], ["id-java-coding-standard"]); assert.deepEqual(chosen, ["id-triage"], "only the skills in the accepted request are cleared");
  input.value = "下一条消息"; draft = input.value; await context.send(); assert.deepEqual(chatCalls.at(-1).local_skill_ids, ["id-triage"]); assert.equal(chosen.length, 0);
  input.value = "普通聊天"; draft = input.value; await context.send(); assert.deepEqual(chatCalls.at(-1).local_skill_ids, [], "skills are not automatically carried into the next message");
  assert.deepEqual(chatCalls.at(-1).local_skill_files, {});
  console.log("local skills UI checks passed");
})().catch(error => { console.error(error); process.exitCode = 1; });

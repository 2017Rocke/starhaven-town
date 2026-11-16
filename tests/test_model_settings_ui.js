"use strict";
// Exercise the real settings handlers in isolation from unrelated map rendering.
// No network service, credentials store or real save is used.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../web/app.js"), "utf8");
const handlers = source.slice(source.indexOf("  function updateModelProtocol()"), source.indexOf("  function saveDraft("));
assert.ok(handlers.includes("async function saveModelSettings"));
const wiring = source.match(/\$\("#model-protocol"\)\.addEventListener\("change", updateModelProtocol\);/)[0];
const copy = value => JSON.parse(JSON.stringify(value));
class Element {
  constructor(value = "") { this.value = value; this.disabled = false; this.hidden = false; this.textContent = ""; this.listeners = new Map(); }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  async emit(name) { return this.listeners.get(name)?.({ preventDefault() {} }); }
  focus() { this.focused = true; }
  showModal() { this.open = true; }
}
function setup(initial) {
  let mode = "codex", stored = copy(initial), error = null, saveGate = null;
  const fields = new Map(), calls = [], toasts = [], ui = { settingsLoaded: false, settingsLoading: false, settingsSaving: false, modelSettings: null };
  const field = id => { if (!fields.has(id)) fields.set(id, new Element()); return fields.get(id); };
  field("model-protocol").value = "openai"; field("model-auth-type").value = "api_key";
  const radio = value => { const item = new Element(value); Object.defineProperty(item, "checked", { get: () => mode === value, set: value => { if (value) mode = item.value; } }); return item; };
  const radios = { api: radio("api"), codex: radio("codex") };
  const $ = selector => selector === "input[name='model-mode']:checked" ? radios[mode] : selector.startsWith("input[") ? radios[selector.match(/value='([^']+)'/)[1]] : field(selector.slice(1));
  const api = async (url, options = {}) => {
    calls.push({ url, method: options.method || "GET", body: copy(options.body || {}) });
    assert.equal(url, "/api/model-settings", "protocol changes must not call other APIs or grant permissions");
    if (!options.method) return { settings: copy(stored) };
    if (saveGate) await saveGate;
    if (error) throw error;
    stored = { ...stored, ...copy(options.body), has_api_key: !!options.body.api_key || stored.has_api_key };
    delete stored.api_key;
    return { settings: copy(stored) };
  };
  const context = { ui, $, api, loadState: async () => {}, toast: message => toasts.push(message) };
  vm.runInNewContext(`${handlers}\n${wiring}\nglobalThis.testApi = { loadModelSettings, saveModelSettings, updateModelMode, openSettings };`, context);
  return { ...context.testApi, ui, field, calls, toasts, stored: () => stored, setBudgetCapability: value => { stored.budget_capability = value; }, setError: value => { error = value; }, setGate: value => { saveGate = value; }, selectProtocol: async value => { field("model-protocol").value = value; await field("model-protocol").emit("change"); }, save: () => context.testApi.saveModelSettings({ preventDefault() {} }) };
}
(async () => {
  const original = { mode: "api", base_url: "https://gateway.example.com/service/v1", model: "account-model", has_api_key: true, can_store_api_key: true, api_key: "must-never-return-to-form" };
  const env = setup(original); await env.loadModelSettings();
  assert.equal(env.field("model-protocol").value, "openai", "legacy configuration defaults to OpenAI");
  assert.equal(env.ui.modelSettings.auth_type, "bearer");
  assert.equal(env.field("model-auth-field").hidden, true);
  assert.equal(env.field("model-api-key").value, "", "even an unexpected returned secret must never populate the form");
  assert.match(env.field("api-key-status").textContent, /不回填/);
  assert.doesNotMatch(env.field("model-settings-state").textContent, /验证已通过|具备受控预算适配/, "legacy responses must not imply budget verification");
  const beforeChange = env.calls.length;
  await env.selectProtocol("anthropic");
  assert.equal(env.field("model-auth-field").hidden, false);
  assert.equal(env.field("model-auth-type").value, "api_key", "Anthropic defaults to standard x-api-key");
  assert.equal(env.field("model-base-url").value, original.base_url);
  assert.equal(env.field("model-name").value, original.model);
  assert.equal(env.calls.length, beforeChange, "changing a protocol performs no request");
  assert.match(env.field("model-protocol-help").textContent, /\/v1\/messages/);
  await env.save();
  let sent = env.calls.at(-1).body;
  assert.equal(sent.protocol, "anthropic"); assert.equal(sent.auth_type, "api_key");
  assert.equal(sent.base_url, original.base_url); assert.ok(!("api_key" in sent), "blank key preserves stored credential");

  env.field("model-auth-type").value = "bearer";
  env.field("model-base-url").value = "https://gateway.example.com/team/anthropic/v1/messages";
  env.field("model-api-key").value = "test-token-not-real";
  await env.save(); sent = env.calls.at(-1).body;
  assert.equal(sent.protocol, "anthropic"); assert.equal(sent.auth_type, "bearer"); assert.equal(sent.api_key, "test-token-not-real");
  assert.equal(env.field("model-api-key").value, "", "saved credential is cleared immediately");
  const reopened = setup(env.stored()); await reopened.loadModelSettings();
  assert.equal(reopened.field("model-protocol").value, "anthropic"); assert.equal(reopened.field("model-auth-type").value, "bearer");
  assert.equal(reopened.field("model-base-url").value, sent.base_url); assert.equal(reopened.field("model-api-key").value, "");

  await env.selectProtocol("openai");
  env.field("model-auth-type").value = "api_key";
  await env.save(); sent = env.calls.at(-1).body;
  assert.equal(sent.protocol, "openai"); assert.equal(sent.auth_type, "bearer", "OpenAI always uses bearer");
  assert.equal(env.field("model-base-url").value, "https://gateway.example.com/team/anthropic/v1/messages", "switching protocols does not rewrite an address");
  assert.equal(env.field("model-name").value, original.model);

  await env.selectProtocol("anthropic"); env.field("model-auth-type").value = "bearer";
  env.field("model-api-key").value = "another-test-token"; env.setError(new Error("保存失败"));
  await env.save();
  assert.equal(env.field("model-protocol").value, "anthropic"); assert.equal(env.field("model-auth-type").value, "bearer");
  assert.equal(env.field("model-api-key").value, ""); assert.match(env.field("model-settings-state").textContent, /连接选项已保留/);
  assert.equal(env.field("model-settings-fields").disabled, false); assert.equal(env.field("save-model-settings").disabled, false);
  assert.equal(env.field("model-settings-error").textContent, "保存失败");
  env.setError(null); const count = env.calls.length; env.field("model-base-url").value = ""; await env.save();
  assert.equal(env.calls.length, count, "an empty API address is not silently replaced with another provider");
  assert.equal(env.field("model-base-url").focused, true);

  const noStore = setup({ ...original, protocol: "anthropic", auth_type: "api_key", can_store_api_key: false });
  await noStore.loadModelSettings(); noStore.field("model-api-key").value = "ignored-test-token"; await noStore.save();
  assert.equal(noStore.field("model-api-key").disabled, true); assert.ok(!("api_key" in noStore.calls.at(-1).body));
  let release; const held = new Promise(resolve => { release = resolve; }); noStore.setGate(held);
  const first = noStore.save(); const attempts = noStore.calls.length; await noStore.save();
  assert.equal(noStore.calls.length, attempts, "a pending save cannot dispatch twice"); release(); await first;

  const official = setup({ ...original, budget_capability: { enforce_supported: true, profile: "openai-text-bpe-v1" } });
  await official.loadModelSettings();
  assert.match(official.field("model-settings-state").textContent, /已保存连接具备受控预算适配/);
  assert.doesNotMatch(official.field("model-settings-state").textContent, /本机用量验证已通过/);
  assert.match(official.field("model-settings-state").textContent, /仍需培养网络授权.*源码另需项目授权/);

  const verifiedCapability = { enforce_supported: true, profile: "messages-local-v1", local_verification: true, verified_at: "2026-11-16T08:00:00+00:00", expires_at: "2026-12-16T08:00:00+00:00", max_output_tokens: 4096, max_request_bytes: 65536 };
  const verified = setup({ ...original, protocol: "anthropic", auth_type: "bearer", budget_capability: verifiedCapability });
  await verified.loadModelSettings();
  let status = verified.field("model-settings-state").textContent;
  assert.match(status, /已保存连接的本机用量验证已通过/);
  assert.match(status, /有效至 2026-12-16/); assert.match(status, /4096 Token/); assert.match(status, /65536 字节/);
  assert.match(status, /仍需培养网络授权.*源码另需项目授权/);
  assert.equal(verified.calls.length, 1, "displaying verification must not start a probe or grant access");
  await verified.selectProtocol("openai");
  assert.match(verified.field("model-settings-state").textContent, /已保存连接/, "status describes the saved connection while fields are edited");
  verified.setBudgetCapability({ ...verifiedCapability, enforce_supported: false });
  await verified.save(); status = verified.field("model-settings-state").textContent;
  assert.match(status, /连接设置已保存.*尚无有效的受控预算适配/);
  assert.doesNotMatch(status, /验证已通过/, "expired or mismatched verification cannot override the backend decision");
  assert.equal(verified.calls.length, 2, "saving only updates settings");
  verified.setBudgetCapability(undefined); await verified.save();
  assert.equal(verified.ui.modelSettings.budget_capability, null, "a response without capability clears the previous status");
  assert.doesNotMatch(verified.field("model-settings-state").textContent, /验证已通过|具备受控预算适配/);

  const malformed = setup({ ...original, protocol: "anthropic", budget_capability: { ...verifiedCapability, expires_at: "not-a-date", max_output_tokens: "4096", max_request_bytes: -1 } });
  await malformed.loadModelSettings();
  assert.match(malformed.field("model-settings-state").textContent, /本机用量验证已通过/);
  assert.doesNotMatch(malformed.field("model-settings-state").textContent, /not-a-date|Invalid|NaN|-1 字节|4096 Token/, "optional invalid metadata must not leak into the status");
  const unsupported = setup({ ...original, protocol: "anthropic", budget_capability: { ...verifiedCapability, enforce_supported: false } });
  await unsupported.loadModelSettings();
  assert.match(unsupported.field("model-settings-state").textContent, /需先完成本机用量验证/);
  assert.doesNotMatch(unsupported.field("model-settings-state").textContent, /验证已通过/);
  console.log("model settings UI checks passed");
})().catch(error => { console.error(error); process.exitCode = 1; });

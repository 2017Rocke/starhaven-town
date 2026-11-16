"use strict";

// Work records come from the service. A document is never execution evidence.
(() => {
  const roles = { product: "产品 · 杨辉", developer: "开发 · 吉吉", tester: "测试 · 李力", commander: "总览 · 领航" };
  const statuses = { awaiting_commit: "等待你检查提交", superseded: "已被新版本替代", awaiting_requirement_confirmation: "等待确认需求", rework_required: "需要修改", draft: "需求草稿", analyzing: "分析需求中", awaiting_answers: "等待答疑", awaiting_confirmation: "等待确认需求", ready: "需求已确认", active: "需求已生效", confirmed: "需求已确认", planning_tests: "整理测试计划中", developing: "开发阶段", awaiting_review: "等待检查提交", awaiting_user_commit: "等待检查提交", blocked: "受阻", failed: "未完成", interrupted: "执行中断", cancelled: "已取消", queued: "排队中", running: "执行中", completed: "已完成", succeeded: "已完成", pending: "等待处理", needs_clarification: "等待答疑", requirements_ready: "等待确认需求", development_blocked: "本地开发受阻", development_ready: "可开始本地开发", test_planning: "整理测试计划中" };
  const labels = { summary: "摘要", scope: "需求范围", acceptance_criteria: "验收标准", acceptance: "验收标准", risks: "注意事项", assumptions: "待核对前提", out_of_scope: "本次不做", cases: "测试用例", title: "名称", preconditions: "前提条件", precondition: "前提条件", steps: "操作步骤", expected: "预期结果", expected_result: "预期结果", priority: "优先级", coverage: "覆盖范围", notes: "补充说明", content: "内容", description: "说明", requirements: "需求", questions: "待答疑问", text: "说明", impact: "影响范围", blocking: "是否阻塞", id: "编号", requirement: "对应需求" };
  const artifactNames = { requirement_analysis: "需求分析", test_plan: "测试计划", development_plan: "开发说明", development: "开发成果", development_report: "开发说明", feedback_response: "吉吉的答复" };
  const busyStates = new Set(["queued", "running", "preparing", "analyzing", "developing", "planning_tests"]);
  const developmentOutputLimits = [4096, 8192, 16384, 32768];
  const array = value => Array.isArray(value) ? value : [];
  const str = value => value == null ? "" : String(value);
  let host, root, workspace = null, selectedProject = "", selectedTask = "", task = null, connection = null;
  let loading = null, timer = null, pending = false, disconnecting = false, generation = 0, detailSignature = "", lastError = "", conflict = false;
  const drafts = new Map(), requests = new Map(), attachmentDrafts = new Map(), attachmentPanels = new Map();
  const h = (tag, cls, text) => host.el(tag, cls, text);
  const post = (url, body) => host.api(url, { method: "POST", body });
  const visible = () => host?.getView() === "development" && !document.hidden;
  const taskUrl = id => `/api/dev/tasks/${encodeURIComponent(id)}`;
  const project = () => array(workspace?.projects).find(item => item.id === selectedProject);
  const residents = () => array(workspace?.residents || workspace?.members || host.getState()?.agents);
  const residentName = id => ["owner", "user"].includes(id) ? "你" : residents().find(item => item.id === id)?.name || id || "尚未绑定";
  const requestId = () => globalThis.crypto?.randomUUID?.() || `dev-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  function requestFor(key, body) {
    const signature = JSON.stringify(body), previous = requests.get(key);
    if (previous?.signature === signature) return previous.id;
    const id = requestId(); requests.set(key, { signature, id }); return id;
  }
  function draft(key, fallback = "", revision = null) {
    if (!drafts.has(key)) {
      let saved;
      try { saved = JSON.parse(sessionStorage.getItem(`starhaven-dev:${key}`)); } catch { /* Memory drafts still work. */ }
      drafts.set(key, saved && typeof saved.value === "string" ? saved : { value: fallback, revision, dirty: false });
    }
    return drafts.get(key);
  }
  function saveDraft(key, value, revision = null) {
    const entry = draft(key, "", revision);
    if (!entry.dirty) entry.revision = revision;
    entry.value = value; entry.dirty = true;
    try { sessionStorage.setItem(`starhaven-dev:${key}`, JSON.stringify(entry)); } catch { /* Keep the in-memory copy. */ }
  }
  function clearDraft(key, submitted) {
    if (drafts.get(key)?.value !== submitted) return false;
    drafts.delete(key);
    try { sessionStorage.removeItem(`starhaven-dev:${key}`); } catch { /* No stored copy. */ }
    return true;
  }
  function button(text, action, primary = false) {
    const node = h("button", `dev-button${primary ? " primary" : ""}`, text); node.type = "button";
    node.addEventListener("click", action); return node;
  }
  function badge(text, tone = "") { return h("span", `dev-badge ${tone}`, text); }
  function note(text) { return h("p", "dev-note", text); }
  function field(parent, label, key, fallback = "", revision = null, multiline = false) {
    const group = h("label", "dev-field"), input = h(multiline ? "textarea" : "input");
    const entry = draft(key, fallback, revision);
    if (!entry.dirty) { entry.value = fallback; entry.revision = revision; }
    input.value = entry.value;
    input.dataset.devField = key; input.required = true; input.maxLength = multiline ? 50000 : 160;
    if (multiline) input.rows = 5; else input.type = "text";
    input.addEventListener("input", () => saveDraft(key, input.value, revision));
    group.append(h("span", "", label), input); parent.append(group); return input;
  }
  function section(title) { const box = h("section", "dev-card"); box.append(h("h3", "", title)); return box; }
  function requirementAttachments(parent, key, initial = [], onBusy = null) {
    const member = project()?.members?.product, agentId = typeof member === "object" ? member.agent_id || member.id : member;
    const create = window.StarhavenResidentWorkspace?.attachmentPicker;
    if (!create) { parent.append(note("附件面板尚未加载，可刷新页面后选择附件。")); return { ids: () => initial, busy: () => false, hasErrors: () => false, setDisabled: () => {} }; }
    if (!attachmentPanels.has(key)) attachmentPanels.set(key, create({ agentId, projectId: selectedProject, classification: "requirement", initial: attachmentDrafts.get(key) || initial,
      onChange: records => attachmentDrafts.set(key, records), onBusy }));
    const picker = attachmentPanels.get(key); picker.setCallbacks?.({ onBusy });
    parent.addEventListener("paste", picker.handlePaste);
    parent.append(picker.node); return picker;
  }
  function readDocument(value, depth = 0) {
    const box = h("div", "dev-document");
    if (value == null) return box;
    if (typeof value !== "object") { box.textContent = typeof value === "boolean" ? value ? "是" : "否" : str(value); return box; }
    if (depth > 7) return note("内容层级较多，请查看上层摘要。");
    if (Array.isArray(value)) {
      const list = h("ol", "dev-document-list");
      value.forEach(item => { const row = h("li"); row.append(readDocument(item, depth + 1)); list.append(row); }); box.append(list);
    } else {
      for (const [key, content] of Object.entries(value)) {
        if (content == null || ["revision", "request_id", "interaction_session_id", "call_ids"].includes(key)) continue;
        const group = h("div", "dev-document-field"); group.append(h("strong", "", labels[key] || "补充说明"), readDocument(content, depth + 1)); box.append(group);
      }
    }
    return box;
  }
  function modal(title) {
    const dialog = h("dialog", "town-dialog dev-dialog"), form = h("form", "dev-form"), heading = h("div", "dev-toolbar");
    dialog.setAttribute("aria-label", title);
    heading.append(h("h2", "", title), button("关闭", () => dialog.close()));
    const error = h("p", "dev-error"); error.setAttribute("role", "alert");
    form.append(heading); dialog.append(form); document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove());
    return { dialog, form, error, show: () => { form.append(error); dialog.showModal(); } };
  }
  async function createProject() {
    const { dialog, form, error, show } = modal("为项目建档");
    const title = field(form, "项目名称", "project:title");
    form.append(note("从已有居民中逐一选择。括号中是居民的固定标识；不会创建新居民，也不会按同名自动绑定。"));
    const selections = {};
    for (const [role, label] of Object.entries(roles)) {
      const group = h("label", "dev-field"), select = h("select"), empty = h("option", "", "请选择已有居民");
      empty.value = ""; select.required = true; select.append(empty);
      residents().forEach(member => { const option = h("option", "", `${member.name}（${member.id}）`); option.value = member.id; select.append(option); });
      select.value = draft(`project:${role}`).value;
      select.addEventListener("change", () => saveDraft(`project:${role}`, select.value));
      group.append(h("span", "", label), select); selections[role] = select; form.append(group);
    }
    const save = button("建立项目", () => {}, true); save.type = "submit"; form.append(save);
    form.addEventListener("submit", async event => {
      event.preventDefault(); if (save.disabled || !form.reportValidity()) return;
      const members = Object.fromEntries(Object.entries(selections).map(([role, select]) => [role, select.value]));
      if (new Set(Object.values(members)).size !== 4) { error.textContent = "四个职责请选择四位不同的居民。"; return; }
      const submittedTitle = title.value;
      const body = { title: submittedTitle.trim(), members };
      save.disabled = true; error.textContent = "";
      try {
        const result = await post("/api/dev/projects", { ...body, request_id: requestFor("project", body) });
        requests.delete("project"); selectedProject = result.project?.id || result.id;
        clearDraft("project:title", submittedTitle);
        Object.entries(members).forEach(([role, id]) => clearDraft(`project:${role}`, id));
        dialog.close(); await refresh(); host.toast("项目已建立，接下来可以添加需求。");
      } catch (e) { error.textContent = e.message; } finally { save.disabled = false; }
    }); show();
  }
  function createTask() {
    const id = selectedProject;
    if (!id) return;
    const { dialog, form, error, show } = modal("添加一条需求"), titleKey = `new:${id}:title`, bodyKey = `new:${id}:requirements`;
    const title = field(form, "需求名称", titleKey), requirements = field(form, "原始需求", bodyKey, "", null, true);
    requirements.placeholder = "粘贴业务背景、期望行为和完成标准，杨辉会据此整理需求与疑问。";
    const save = button("保存需求", () => {}, true), attachmentKey = `new:${id}:attachments`;
    const attachments = requirementAttachments(form, attachmentKey, [], busy => { save.disabled = busy; });
    save.type = "submit"; form.append(save);
    form.addEventListener("submit", async event => {
      event.preventDefault(); if (save.disabled || attachments.busy() || !form.reportValidity()) return;
      if (attachments.hasErrors()) { error.textContent = "请处理未成功上传的附件，或移除后再保存。"; return; }
      const submittedTitle = title.value, submittedBody = requirements.value;
      const body = { project_id: id, title: submittedTitle.trim(), requirements: submittedBody.trim(), attachment_ids: attachments.ids() };
      save.disabled = true; attachments.setDisabled(true); error.textContent = "";
      try {
        const result = await post("/api/dev/tasks", { ...body, request_id: requestFor(`new:${id}`, body) });
        requests.delete(`new:${id}`); selectedProject = id; selectedTask = result.task?.id || result.id; task = result.task || result;
        const same = title.value === submittedTitle && requirements.value === submittedBody;
        clearDraft(titleKey, submittedTitle); clearDraft(bodyKey, submittedBody);
        attachmentDrafts.delete(attachmentKey);
        attachmentPanels.delete(attachmentKey);
        if (same) dialog.close(); else error.textContent = "这条需求已保存。发送期间的新输入仍保留在这里。";
        await refresh(); host.toast("需求已保存，可以请杨辉开始分析。");
      } catch (e) { error.textContent = e.message; } finally { save.disabled = false; attachments.setDisabled(false); }
    }); show();
  }
  async function ownerApproval(action, payload) {
    const native = window.chrome?.webview || window.webkit?.messageHandlers?.townOwner;
    if (!native) throw new Error("请在小镇桌面窗口完成这项确认。");
    const intent = await post("/api/owner/intents", { action, payload });
    native.postMessage({ type: "owner-intent", id: intent.id });
    host.toast("请在桌面确认窗口核对本次操作。");
  }
  function developmentBudget() {
    const saved = workspace?.development_budget || {};
    return {
      relaxed: saved.relaxed === true,
      max_output_tokens: developmentOutputLimits.includes(saved.max_output_tokens) ? saved.max_output_tokens : 32768,
      effective_output_tokens: Number.isSafeInteger(saved.effective_output_tokens) && saved.effective_output_tokens > 0 ? saved.effective_output_tokens : null,
    };
  }
  function developmentBudgetSummary() {
    const budget = developmentBudget();
    const output = budget.effective_output_tokens ? `当前单次输出 ${budget.effective_output_tokens} Token`
      : budget.relaxed ? `放宽输出设置 ${budget.max_output_tokens} Token` : "单次输出沿用培养设置";
    return `研发额度：${budget.relaxed ? "放宽" : "常规"} · ${output} · 研发单独记账`;
  }
  function renderDevelopmentBudget() {
    const status = root.querySelector(".dev-budget-state"), control = root.querySelector(".dev-budget-settings");
    status.textContent = workspace ? developmentBudgetSummary() : "正在读取研发额度…";
    control.disabled = !workspace;
  }
  function configureDevelopmentBudget() {
    if (!workspace) return;
    const budget = developmentBudget(), { dialog, form, error, show } = modal("研发额度");
    form.append(note(`当前已生效：${developmentBudgetSummary()}`));
    const modeLabel = h("label", "dev-field"), mode = h("select");
    for (const [value, text] of [["regular", "常规"], ["relaxed", "放宽"]]) {
      const option = h("option", "", text); option.value = value; mode.append(option);
    }
    mode.value = budget.relaxed ? "relaxed" : "regular";
    modeLabel.append(h("span", "", "研发模式"), mode); form.append(modeLabel);
    const outputLabel = h("label", "dev-field"), output = h("select");
    developmentOutputLimits.forEach(limit => { const option = h("option", "", `${limit} Token`); option.value = String(limit); output.append(option); });
    output.value = String(budget.max_output_tokens);
    outputLabel.append(h("span", "", "放宽模式单次最大输出 Token"), output); form.append(outputLabel);
    const explanation = note(""); form.append(explanation);
    const updateMode = () => {
      const relaxed = mode.value === "relaxed";
      output.disabled = !relaxed;
      explanation.textContent = relaxed
        ? "研发不受聊天与训练设置中的每日调用次数、每日 Token、累计 Token 和单次总额限制，仍记录用量。聊天与训练继续使用原额度。"
        : "研发执行培养设置中的每日调用次数、每日 Token、累计 Token 和单次总额限制，单次输出沿用培养设置。研发与聊天、训练分开记账。";
    };
    mode.addEventListener("change", updateMode); updateMode();
    form.append(note("单次输出仍受模型适配上限约束。暂停控制、居民与模型服务授权、源码权限继续生效；文件和执行轮数等限制继续保留。保存需要在桌面确认窗口核对。"));
    const save = button("核对并保存研发额度", () => {}, true); save.type = "submit"; form.append(save);
    form.addEventListener("submit", async event => {
      event.preventDefault(); if (save.disabled) return;
      const maxOutputTokens = Number(output.value);
      if (!["regular", "relaxed"].includes(mode.value) || !developmentOutputLimits.includes(maxOutputTokens)) { error.textContent = "请选择有效的研发模式和输出额度。"; return; }
      save.disabled = true; mode.disabled = true; output.disabled = true; error.textContent = "";
      try { await ownerApproval("development-budget", { relaxed: mode.value === "relaxed", max_output_tokens: maxOutputTokens }); dialog.close(); }
      catch (e) { error.textContent = e.message; }
      finally { save.disabled = false; mode.disabled = false; updateMode(); }
    }); show();
  }
  async function disconnectRepository() {
    if (disconnecting || !selectedProject) return;
    const id = selectedProject;
    generation++; disconnecting = true; renderSidebar();
    try {
      await post(`/api/dev/projects/${encodeURIComponent(id)}/disconnect`, {});
      host.toast("源码授权已断开，已停止后续源码访问；已发送的请求无法收回。");
    } catch (e) {
      const error = root.querySelector(".dev-load-error"); error.textContent = e.message; error.hidden = false;
      host.toast(e.message);
    } finally { disconnecting = false; await refresh(); renderSidebar(); }
  }
  function connectRepository() {
    const id = selectedProject, { dialog, form, error, show } = modal("连接代码仓库");
    form.append(note("只有吉吉可读取和修改这里连接的源码。源码会发送给当前配置的 API 模型；请在桌面确认窗口核对仓库范围。"));
    const provider = connection?.current_provider || connection?.provider;
    if (provider) form.append(note(`当前模型：${provider.model || "尚未配置"} · ${provider.base_url || "尚未配置服务"}`));
    const entries = [], list = h("div", "dev-form"); form.append(list);
    function add(repository = {}) {
      const index = entries.length, row = h("div", "dev-repository-row");
      const name = field(row, "仓库短名（英文或数字）", `repo:${id}:${index}:name`, repository.name || "");
      name.pattern = "[A-Za-z0-9_-]+"; name.maxLength = 64;
      const path = field(row, "本机目录", `repo:${id}:${index}:path`, repository.path || ""); path.maxLength = 4096;
      entries.push({ name, path }); list.append(row);
    }
    if (array(connection?.repositories).length) connection.repositories.forEach(add); else add();
    form.append(button("再添加一个仓库", () => add()));
    const consent = h("label", "dev-consent"), check = h("input"); check.type = "checkbox"; check.required = true;
    consent.append(check, h("span", "", "我确认这些仓库的源码可发送给当前配置的模型，用于本项目研发。")); form.append(consent);
    const save = button("核对并连接仓库", () => {}, true); save.type = "submit"; form.append(save);
    form.addEventListener("submit", async event => {
      event.preventDefault(); if (save.disabled || !form.reportValidity() || !check.checked) return;
      save.disabled = true; error.textContent = "";
      try { await ownerApproval("dev-connect", { project_id: id, repositories: entries.map(item => ({ name: item.name.value.trim(), path: item.path.value.trim() })), allow_source: true }); dialog.close(); }
      catch (e) { error.textContent = e.message; } finally { save.disabled = false; }
    }); show();
  }
  async function reviewRun(runId, revision) {
    const id = task.id, { dialog, form, error, show } = modal("检查本地修改");
    const content = h("div"); content.append(note("正在读取这次修改…")); form.append(content); show();
    try {
      const result = await host.api(`/api/dev/runs/${encodeURIComponent(runId)}`);
      content.replaceChildren();
      if (result.summary) content.append(readDocument(result.summary));
      array(result.checks).forEach(check => {
        const state = { passed: "已检查", failed: "未通过", not_run: "未执行", blocked: "受阻" }[check.status] || "状态未确认";
        content.append(note(`${check.name || "检查"}：${state}${check.detail ? ` · ${check.detail}` : ""}`));
      });
      if (!array(result.checks).length) content.append(note("未提供功能自测证据，生成修改不代表检查通过。"));
      if (["applying", "apply_interrupted"].includes(result.status)) {
        content.append(badge("应用中断或状态待核对", "blocked"), note("请先检查本地差异和已保留的恢复文件。这批修改不会自动重放。"));
        content.append(h("h3", "", "已记录写入的文件"), array(result.applied_files).length ? readDocument(result.applied_files) : note("尚未记录完成写入的文件，仍需核对本地状态。"));
        if (array(result.recovery_files).length) content.append(h("h3", "", "保留的恢复文件"), readDocument(result.recovery_files));
      }
      const diff = result.diff || result.diff_text;
      if (diff) content.append(h("pre", "dev-diff", typeof diff === "string" ? diff : JSON.stringify(diff, null, 2)));
      else content.append(note("这次执行还没有可应用的修改，请查看执行说明。"));
      content.append(note("应用修改会写入本机仓库；之后由你检查并提交代码。"));
      if (diff && !result.applied && result.status === "review") {
        const apply = button("核对并应用本地修改", async () => {
          apply.disabled = true; error.textContent = "";
          try { await ownerApproval("dev-apply", { task_id: id, revision, run_id: runId }); dialog.close(); }
          catch (e) { error.textContent = e.message; } finally { apply.disabled = false; }
        }, true); content.append(apply);
      }
      if (result.applied) content.append(badge("修改已应用 · 等待你检查提交", "waiting"));
    } catch (e) { content.replaceChildren(); error.textContent = e.message; }
  }
  function hasAnalysis(current) {
    if (current.interaction && current.interaction.status !== "completed") return false;
    const revision = array(current.revisions).find(item => item.revision === current.revision);
    if (revision?.status === "draft") return false;
    return array(current.artifacts).some(item => item.kind === "requirement_analysis" && item.revision === current.revision && item.effective !== false) ||
      !!revision && (["review", "active"].includes(revision.status) || !revision.status && Object.keys(revision.analysis || {}).length > 0);
  }
  function liveJob(current) { return array(current?.jobs).find(job => busyStates.has(job.status)); }
  async function action(name, payload = {}, draftKey = null) {
    if (!task || pending) return;
    const id = task.id, entry = draftKey ? draft(draftKey) : null;
    const submittedDraft = entry?.value;
    const revision = entry?.dirty && entry.revision != null ? entry.revision : task.revision;
    const body = { action: name, revision, ...payload }, key = `action:${id}:${name}:${draftKey || payload.job_id || ""}`;
    generation++; pending = true; lastError = ""; renderDetail(true);
    try {
      const result = await post(`${taskUrl(id)}/action`, { ...body, request_id: requestFor(key, body) });
      requests.delete(key);
      if (draftKey) clearDraft(draftKey, submittedDraft);
      if (selectedTask === id) { task = result.task || result; conflict = false; }
      host.toast({ analyze: "已安排需求分析。", confirm: "需求版本已确认。", plan_tests: "已安排测试计划。", develop: "已安排本地开发，请查看执行记录。", revise: "新需求版本已保存，确认生效后才会替换当前基线。", answer: "答复已保存。", feedback: "修改意见已交给吉吉。", cancel: "已提交取消请求。", retry: "已请求重试。" }[name] || "已保存。");
    } catch (e) {
      if (selectedTask === id) { conflict = e.status === 409; lastError = conflict ? "记录已更新，本次操作没有覆盖新版本。你的输入已保留，请核对最新内容后再继续。" : e.message; }
    } finally {
      pending = false;
      await refresh(); renderDetail(true);
    }
  }
  function taskControl(text, name, payload = {}, draftKey = null, enabled = true, primary = false) {
    const control = button(text, () => action(name, typeof payload === "function" ? payload() : payload, draftKey), primary);
    control.disabled = pending || !enabled; return control;
  }
  function renderSidebar() {
    const box = root.querySelector(".dev-sidebar"); box.replaceChildren();
    const label = h("label", "dev-field"), select = h("select"); label.append(h("span", "", "当前项目"), select);
    const empty = h("option", "", "选择项目"); empty.value = ""; select.append(empty);
    array(workspace?.projects).forEach(item => { const option = h("option", "", item.title); option.value = item.id; select.append(option); }); select.value = selectedProject;
    select.addEventListener("change", () => { selectedProject = select.value; selectedTask = ""; task = null; connection = null; conflict = false; lastError = ""; generation++; refresh(); renderSidebar(); renderDetail(true); });
    box.append(label, button("＋ 为项目建档", createProject));
    if (!selectedProject) { box.append(note("先建立项目，绑定产品、开发、测试与领航。")); return; }
    const crew = h("div", "dev-crew");
    for (const [role, label] of Object.entries(roles)) {
      const member = project()?.members?.[role], id = typeof member === "object" ? member.agent_id || member.id : member;
      const item = button(`${label.split(" · ")[0]}：${residentName(id)}`, () => { if (id) host.selectAgent(id); }); item.disabled = !id; crew.append(item);
    }
    box.append(crew, button("连接代码仓库", connectRepository), note(connection?.connected ? "源码仓库已连接；操作前仍核对需求版本。" : "尚未连接源码仓库。需求整理与测试计划可以先进行。"));
    if (connection?.connected) {
      const disconnect = button(disconnecting ? "正在断开源码授权…" : "断开源码授权", disconnectRepository);
      disconnect.disabled = disconnecting; box.append(disconnect);
    }
    box.append(button("＋ 添加需求", createTask, true));
    const list = h("div", "dev-task-list"); list.setAttribute("aria-label", "项目需求");
    const items = array(workspace?.tasks).filter(item => item.project_id === selectedProject);
    items.forEach(item => {
      const entry = button("", () => { selectedTask = item.id; task = null; conflict = false; lastError = ""; generation++; renderSidebar(); renderDetail(true); refresh(); });
      entry.className += " dev-task-item"; entry.setAttribute("aria-pressed", String(item.id === selectedTask));
      entry.append(h("strong", "", item.title), h("span", "", `R${item.revision} · ${statuses[item.status] || "查看进度"}`)); list.append(entry);
    });
    if (connection?.message) box.append(note(connection.message));
    if (!items.length) list.append(note("项目里还没有需求。把第一条想法放进工作簿吧。")); box.append(list);
  }
  function progress(current) {
    const cap = { ...workspace?.capabilities, ...current.capabilities }, list = h("ol", "dev-progress"); list.setAttribute("aria-label", "研发阶段进度");
    const analysis = hasAnalysis(current);
    const plan = array(current.artifacts).some(item => item.kind === "test_plan" && item.revision === current.active_revision);
    const stages = [
      ["需求分析", analysis ? "已整理" : "待整理", ""],
      ["需求确认", current.active_revision ? `R${current.active_revision} 已生效` : "待确认", ""],
      ["测试计划", plan ? "已整理 · 待执行" : "待整理", ""],
      ["本地开发", cap.development && connection?.connected ? "查看执行记录" : "受阻 · 待接入", cap.development && connection?.connected ? "" : "blocked"],
      ["检查提交", "受阻 · 提交核验待接入", "blocked"],
      ["部署测试", cap.deployment ? "等待版本核验" : "受阻 · 尚未接入", cap.deployment ? "waiting" : "blocked"],
      ["独立验收", cap.acceptance ? "等待执行证据" : "受阻 · 尚未接入", cap.acceptance ? "waiting" : "blocked"],
    ];
    stages.forEach(([title, text, tone], index) => { const item = h("li", `dev-stage ${tone}`); item.append(h("span", "dev-stage-number", str(index + 1)), h("strong", "", title), h("small", "", text)); list.append(item); });
    return list;
  }
  function renderDetail(force = false) {
    const panel = root.querySelector(".dev-detail");
    const signature = JSON.stringify([task, connection, pending, conflict, lastError, selectedTask]);
    if (!force && signature === detailSignature) return; detailSignature = signature;
    const active = document.activeElement, focusKey = active?.dataset?.devField, selection = [active?.selectionStart, active?.selectionEnd];
    const expanded = new Set(Array.from(panel.querySelectorAll("details[open]")).map(node => node.dataset.devDetails));
    panel.replaceChildren();
    if (!task) { panel.append(note(selectedTask ? "正在读取这条需求…" : "选择或添加一条需求，查看版本、责任人和每次交接。")); return; }
    const heading = h("div", "dev-task-heading"), title = h("div");
    title.append(h("p", "eyebrow", "ONE REQUIREMENT, A SHARED RECORD"), h("h2", "", task.title));
    const status = busyStates.has(task.run_status) ? statuses[task.run_status] : statuses[task.status] || statuses[task.run_status] || "查看工作记录";
    heading.append(title, badge(status, task.blocked_reason ? "blocked" : "")); panel.append(heading);
    const owner = task.next_owner || task.current_role, ownerId = project()?.members?.[owner] || owner;
    panel.append(note(`当前草稿 R${task.revision} · ${task.active_revision ? `生效需求 R${task.active_revision}` : "尚未确认生效"} · 当前责任人：${residentName(ownerId)}`), progress(task));
    const message = h("p", "dev-error", lastError); message.setAttribute("role", "alert"); message.hidden = !lastError; panel.append(message);
    if (conflict) panel.append(button("已核对最新内容，保留输入继续", () => {
      for (const [key, entry] of drafts) if (key.startsWith(`task:${task.id}:`)) { entry.revision = task.revision; try { sessionStorage.setItem(`starhaven-dev:${key}`, JSON.stringify(entry)); } catch { /* Retain in memory. */ } }
      conflict = false; lastError = ""; renderDetail(true);
    }));
    if (task.blocked_reason) { const blocked = h("div", "dev-blocked"); blocked.append(badge("当前阻塞", "blocked"), note(task.blocked_reason)); panel.append(blocked); }
    if (task.interaction && window.StarhavenResidentWorkspace?.renderInteraction) {
      const steps = section("杨辉的分步分析");
      const member = project()?.members?.product, agentId = typeof member === "object" ? member.agent_id || member.id : member;
      window.StarhavenResidentWorkspace.renderInteraction(steps, task.interaction, { pending, scope: { agent_id: agentId, project_id: task.project_id },
        questions: false, onAction: (name, payload) => { if (["pause", "resume"].includes(name)) return action(name, payload); } });
      panel.append(steps);
    }
    const running = liveJob(task), controls = h("div", "dev-toolbar"), cancelled = task.status === "cancelled";
    controls.append(taskControl("分析需求", "analyze", {}, null, !running && !cancelled && !hasAnalysis(task) && !task.interaction, true));
    const unanswered = array(task.questions).some(q => q.revision === task.revision && q.blocking && !q.answer);
    const analyzed = hasAnalysis(task);
    controls.append(taskControl("确认需求", "confirm", {}, null, !running && !cancelled && !unanswered && !!analyzed && task.active_revision !== task.revision));
    controls.append(taskControl("生成测试计划", "plan_tests", {}, null, !running && !cancelled && !!task.active_revision));
    controls.append(taskControl("生成本地修改", "develop", {}, null, !running && !cancelled && !!task.active_revision && task.active_revision === task.revision && !unanswered && !!connection?.connected && !!(task.capabilities?.development ?? workspace?.capabilities?.development)));
    if (running) controls.append(taskControl("取消这条需求", "cancel"));
    const failed = array(task.jobs).slice().reverse().find(job => ["failed", "blocked", "interrupted"].includes(job.status) && job.revision === (job.kind === "analyze" ? task.revision : task.active_revision));
    if (failed && !running && !cancelled) controls.append(taskControl("重试未完成的工作", "retry", { job_id: failed.id }));
    panel.append(controls, note("这里的需求分析、测试计划和讨论属于文稿。只有实际修改、检查和运行证据，才能证明开发或验收完成。"));
    const requirement = section("需求版本"), versions = h("div", "dev-version-list");
    array(task.revisions).forEach(revision => {
      const details = h("details"); details.dataset.devDetails = `revision:${revision.revision}`; details.open = revision.revision === task.revision || expanded.has(details.dataset.devDetails);
      details.append(h("summary", "", `R${revision.revision}${revision.revision === task.active_revision ? " · 当前生效" : " · 需求记录"}`), readDocument(revision.requirements)); versions.append(details);
      const member = project()?.members?.product, agentId = typeof member === "object" ? member.agent_id || member.id : member;
      array(revision.attachment_ids).forEach(id => details.append(button("查看需求附件", () => window.StarhavenResidentWorkspace?.openDocument(id, { agent_id: agentId, project_id: task.project_id }))));
    });
    if (!array(task.revisions).length) versions.append(readDocument(task.requirements)); requirement.append(versions);
    const revise = h("details"); revise.dataset.devDetails = "revise"; revise.open = expanded.has("revise"); revise.append(h("summary", "", "提出需求变更"));
    const reviseKey = `task:${task.id}:revise`, revised = field(revise, "完整的新需求", reviseKey, task.requirements || "", task.revision, true);
    let reviseControl;
    const reviseAttachments = requirementAttachments(revise, `${reviseKey}:attachments:${task.revision}`, task.attachment_ids || array(task.revisions).find(item => item.revision === task.revision)?.attachment_ids || [], busy => { if (reviseControl) reviseControl.disabled = busy || pending || !!running || cancelled; });
    reviseControl = button("提交变更", () => {
      if (reviseAttachments.busy()) return;
      if (reviseAttachments.hasErrors()) { lastError = "请处理未成功上传的附件，或移除后再提交变更。"; renderDetail(true); return; }
      return action("revise", { requirements: revised.value, attachment_ids: reviseAttachments.ids() }, reviseKey);
    }); reviseControl.disabled = pending || !!running || cancelled || reviseAttachments.busy();
    reviseAttachments.setDisabled(pending || !!running || cancelled);
    revise.append(note("保存后产生新草稿；附件与分析绑定需求版本，重新分析并确认前，当前生效需求继续保留。"), reviseControl); requirement.append(revise); panel.append(requirement);
    const questions = section("待答疑问"), currentQuestions = array(task.questions).filter(q => q.revision === task.revision);
    if (!currentQuestions.length) questions.append(note("暂时没有待答疑问。分析需求后，杨辉会把需要你决定的事项放在这里。"));
    currentQuestions.forEach(question => {
      const row = h("div", "dev-question"); row.append(h("p", "", question.text), badge(question.answer ? "已回答" : question.blocking ? "确认前需回答" : "待补充", question.answer ? "" : "waiting"));
      if (question.answer) row.append(readDocument(question.answer));
      else {
        const key = `task:${task.id}:answer:${question.id}`, answer = field(row, "你的回答", key, "", task.revision, true);
        row.append(taskControl("保存回答", "answer", () => ({ question_id: question.id, answer: answer.value }), key, !running && !cancelled));
      } questions.append(row);
    }); panel.append(questions);
    const artifacts = section("需求分析与测试计划");
    const documents = array(task.artifacts).filter(item => ["requirement_analysis", "test_plan", "development_plan", "development_report", "feedback_response"].includes(item.kind));
    if (!documents.length) artifacts.append(note("保存后的分析与测试计划会显示在这里。测试计划不代表用例已经执行。"));
    documents.forEach(item => {
      const details = h("details"); details.dataset.devDetails = `artifact:${item.id}`; details.open = expanded.has(details.dataset.devDetails);
      const suffix = item.effective === false ? " · 已失效" : item.revision === task.revision && item.revision !== task.active_revision ? " · 草稿文稿" : item.revision !== (task.active_revision || task.revision) ? " · 历史版本" : "";
      details.append(h("summary", "", `${artifactNames[item.kind] || "工作文稿"} · R${item.revision}${suffix}`), readDocument(item.content)); artifacts.append(details);
    }); panel.append(artifacts);
    const execution = section("执行记录");
    if (!array(task.jobs).length) execution.append(note("还没有执行记录。"));
    array(task.jobs).forEach(job => {
      const row = h("div", "dev-job"), jobLabel = { analyze: "需求分析", plan_tests: "测试计划", develop: "候选修改" }[job.kind] || "工作"; row.append(h("strong", "", `${jobLabel} · R${job.revision}`), badge(statuses[job.status] || "状态待确认", ["failed", "blocked", "interrupted"].includes(job.status) ? "blocked" : ""));
      if (job.error) row.append(note(job.error));
      const candidate = array(task.artifacts).find(item => item.kind === "development_candidate" && item.job_id === job.id);
      const runId = candidate?.content?.run_id || job.result?.run_id || job.run_id;
      if (["development", "develop"].includes(job.kind) && ["completed", "succeeded"].includes(job.status) && runId) row.append(button("检查本地修改", () => reviewRun(runId, job.revision)));
      execution.append(row);
    }); panel.append(execution);
    const discussion = section("讨论记录");
    if (!array(task.discussions).length) discussion.append(note("需求澄清、修改意见与小队回复会保存在这本工作簿中。"));
    array(task.discussions).forEach(item => { const row = h("article", "dev-discussion"); row.append(h("strong", "", `${roles[item.role]?.split(" · ")[0] || "你"} · R${item.revision}`), readDocument(item.content)); discussion.append(row); });
    const feedbackKey = `task:${task.id}:feedback`, feedback = field(discussion, "给吉吉的修改意见", feedbackKey, "", task.revision, true);
    discussion.append(taskControl("请求吉吉修改", "feedback", () => ({ content: feedback.value }), feedbackKey, !running && !cancelled && !!task.active_revision)); panel.append(discussion);
    if (focusKey) {
      const input = Array.from(panel.querySelectorAll("[data-dev-field]")).find(item => item.dataset.devField === focusKey);
      if (input) { input.focus({ preventScroll: true }); if (selection[0] != null && input.setSelectionRange) input.setSelectionRange(...selection); }
    }
  }
  function schedule() {
    clearTimeout(timer); timer = null;
    if (visible()) timer = setTimeout(() => { if (visible()) refresh(); }, 5000);
  }
  async function refresh() {
    if (!host || !visible()) return;
    if (loading) return loading;
    const token = generation;
    loading = (async () => {
      try {
        const result = await host.api("/api/dev/workspace");
        if (token !== generation) return;
        workspace = result;
        renderDevelopmentBudget();
        if (!array(workspace.projects).some(item => item.id === selectedProject)) selectedProject = array(workspace.projects)[0]?.id || "";
        const list = array(workspace.tasks).filter(item => item.project_id === selectedProject);
        if (!list.some(item => item.id === selectedTask)) { selectedTask = list[0]?.id || ""; task = null; }
        const reads = await Promise.allSettled([
          selectedTask ? host.api(taskUrl(selectedTask)) : Promise.resolve(null),
          selectedProject ? host.api(`/api/dev/connection/${encodeURIComponent(selectedProject)}`) : Promise.resolve(null),
        ]);
        if (token !== generation) return;
        if (reads[0].status === "fulfilled") task = reads[0].value?.task || reads[0].value;
        else lastError = reads[0].reason.message;
        connection = reads[1].status === "fulfilled" ? reads[1].value : { connected: false, message: reads[1].reason.message };
        const error = root.querySelector(".dev-load-error"); error.hidden = true;
        renderSidebar(); renderDetail();
      } catch (e) {
        const error = root.querySelector(".dev-load-error"); error.textContent = e.message; error.hidden = false;
      }
    })();
    try { await loading; } finally { loading = null; if (token !== generation && visible()) refresh(); else schedule(); }
  }
  function init(options) {
    host = options; root = document.getElementById("dev-workspace"); if (!root) return;
    const heading = h("div", "dev-heading"), title = h("div");
    title.append(h("p", "eyebrow", "BUILD TOGETHER, ONE STEP AT A TIME"), h("h1", "", "小队研发台"), note("一条需求，一本工作簿。让每次确认、修改和交接都有记录。"));
    const budgetState = h("p", "dev-note dev-budget-state"); budgetState.setAttribute("role", "status"); title.append(budgetState);
    const tools = h("div", "dev-toolbar"), budgetSettings = button("研发额度", configureDevelopmentBudget); budgetSettings.className += " dev-budget-settings";
    tools.append(budgetSettings, button("刷新记录", refresh)); heading.append(title, tools);
    const error = h("p", "dev-error dev-load-error"); error.setAttribute("role", "alert"); error.hidden = true;
    const layout = h("div", "dev-layout"), sidebar = h("aside", "dev-sidebar"), detail = h("div", "dev-detail");
    sidebar.setAttribute("aria-label", "项目与需求"); layout.append(sidebar, detail); root.replaceChildren(heading, error, layout);
    renderDevelopmentBudget(); renderSidebar(); renderDetail(true);
    document.addEventListener("visibilitychange", () => { if (visible()) refresh(); else { clearTimeout(timer); timer = null; } });
  }
  function viewChanged(name) { if (name === "development") refresh(); else { clearTimeout(timer); timer = null; } }
  window.StarhavenDevWorkspace = { init, open: refresh, refresh, viewChanged };
})();

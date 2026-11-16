"use strict";

// Long tasks are separate from chat. Only service records establish progress.
(() => {
  const list = value => Array.isArray(value) ? value : [];
  const statuses = { queued: "等待执行", running: "正在处理", pending: "尚未处理", completed: "已完成", succeeded: "已完成", paused: "已暂停", waiting_input: "等待你补充", interrupted: "已中断", blocked: "需要处理", failed: "未完成", cancelled: "已取消", skipped: "已跳过", superseded: "已被新版本替代", partial: "部分内容待核对", ready: "内容已提取" };
  const running = new Set(["queued", "running"]);
  const resumable = new Set(["paused", "interrupted", "blocked", "failed"]);
  const fileTypes = /\.(txt|md|docx|pdf|png|jpe?g|webp)$/i;
  const MAX_FILE = 20 * 1024 * 1024, MAX_FILES = 16;
  const drafts = new Map(), requestKeys = new Map(), selections = new Map(), attachmentPanels = new Map();
  let host, root, agentId = "", items = [], current = null, selectedId = "", loading = null, timer = null, generation = 0, pending = false, error = "", signature = "", creator = null;
  const h = (tag, cls, text) => host.el(tag, cls, text);
  const note = text => h("p", "resident-note", text);
  const visible = () => host?.getView() === "resident-work" && !document.hidden;
  const resident = id => list(host.getState()?.agents).find(item => item.id === id);
  const post = (url, body) => host.api(url, { method: "POST", body });
  const identity = () => globalThis.crypto?.randomUUID?.() || `work-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  function requestFor(key, body) {
    const signature = JSON.stringify(body), prior = requestKeys.get(key);
    if (prior?.signature === signature) return prior.id;
    const id = identity(); requestKeys.set(key, { signature, id }); return id;
  }
  function button(text, action, primary = false) {
    const node = h("button", `resident-button${primary ? " primary" : ""}`, text); node.type = "button"; node.addEventListener("click", action); return node;
  }
  function card(title) { const node = h("section", "resident-card"); node.append(h("h3", "", title)); return node; }
  function textField(parent, label, key, initial = "") {
    const group = h("label", "resident-field"), input = h("textarea");
    input.rows = 4; input.maxLength = 60000; input.value = drafts.has(key) ? drafts.get(key) : initial; input.dataset.residentField = key;
    input.addEventListener("input", () => drafts.set(key, input.value)); group.append(h("span", "", label), input); parent.append(group); return input;
  }
  function dialog(title) {
    const node = h("dialog", "town-dialog resident-dialog"), heading = h("div", "resident-toolbar");
    node.setAttribute("aria-label", title); heading.append(h("h2", "", title), button("关闭", () => node.close())); node.append(heading);
    node.addEventListener("close", () => node.remove()); document.body.append(node); node.showModal(); return node;
  }
  function scopeQuery(scope = {}) {
    const params = [];
    if (scope.agent_id) params.push(`agent_id=${encodeURIComponent(scope.agent_id)}`);
    if (scope.project_id) params.push(`project_id=${encodeURIComponent(scope.project_id)}`);
    return params.length ? `?${params.join("&")}` : "";
  }
  async function openDocument(documentId, scope = {}, focus = null) {
    const modal = dialog("查看附件与来源"), content = h("div"); content.append(note("正在读取本机附件…")); modal.append(content);
    try {
      const result = await host.api(`/api/documents/${encodeURIComponent(documentId)}${scopeQuery(scope)}`), doc = result.document || result;
      content.replaceChildren(h("h3", "", doc.name || "附件"), note("附件预览与提取内容用于核对来源，不代表模型已经完整理解。"));
      const original = h("a", "resident-button", "下载原件"); original.href = `/api/documents/${encodeURIComponent(doc.id || documentId)}/assets${scopeQuery(scope)}`; original.download = doc.name || "附件"; content.append(original);
      list(doc.warnings).forEach(value => content.append(note(typeof value === "string" ? value : value.message || "部分内容未能提取")));
      if (focus?.label) content.append(note(`当前引用：${focus.label}`));
      const fragments = list(doc.fragments);
      const images = list(doc.images || doc.assets);
      for (const asset of images) {
        if (!asset.id || asset.mime && !/^image\/(png|jpeg|webp)$/.test(asset.mime)) continue;
        const figure = h("figure", "resident-preview"), img = h("img");
        img.src = `/api/documents/${encodeURIComponent(doc.id || documentId)}/assets/${encodeURIComponent(asset.id)}${scopeQuery(scope)}`;
        img.alt = `${doc.name || "附件"} · ${fragments.find(item => item.image_id === asset.id)?.label || "图片"}`; img.loading = "lazy";
        figure.append(img, h("figcaption", "", img.alt)); content.append(figure);
      }
      if (fragments.length) fragments.forEach(fragment => { const block = h("details", "resident-fragment"); block.open = !!focus && (focus.fragment_id === fragment.id || focus.image_id === fragment.image_id); block.append(h("summary", "", fragment.label || "提取片段"), h("pre", "resident-text", fragment.text || "该片段为图片，尚无可提取文字。")); content.append(block); });
      else if (doc.preview_text) content.append(h("pre", "resident-text", doc.preview_text));
      if (!images.length && !fragments.length && !doc.preview_text) content.append(note("尚无可显示的提取内容，请核对原文件及解析提示。"));
    } catch (e) { content.replaceChildren(h("p", "resident-error", e.message)); }
  }
  function readFile(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("没有读到文件，请重新选择。"));
      reader.onload = () => { const value = String(reader.result || ""); const comma = value.indexOf(","); comma < 0 ? reject(new Error("文件读取格式无效。")) : resolve(value.slice(comma + 1)); };
      reader.readAsDataURL(file);
    });
  }
  function attachmentPicker(options = {}) {
    const scope = { agent_id: options.agentId, project_id: options.projectId }, rows = list(options.initial).map(value => typeof value === "string" ? { id: value, name: "已保存附件", status: "ready" } : value);
    const container = h("div", "resident-attachments"), heading = h("div", "resident-toolbar"), input = h("input"), content = h("div", "resident-attachment-list"), message = h("p", "resident-error");
    let uploading = 0, locked = false;
    input.type = "file"; input.multiple = true; input.accept = ".txt,.md,.docx,.pdf,.png,.jpg,.jpeg,.webp"; input.setAttribute("aria-label", "选择任务附件");
    const pick = button("选择附件", () => input.click()), pasteHint = note("支持 TXT、MD、DOCX、PDF、PNG、JPG、WebP；可在此粘贴截图。每份最多 20 MiB，每个任务最多 16 份。"), consent = h("label", "resident-consent"), check = h("input");
    check.type = "checkbox";
    if (options.classification === "requirement") consent.append(check, h("span", "", "这些是供产品、开发和测试共同阅读的业务资料，不含源码、密钥或开发私有内容。源码截图请使用专属开发资料，不能作为业务附件上传。"));
    heading.append(pick); input.hidden = true; container.tabIndex = 0; container.setAttribute("aria-label", "附件选择与截图粘贴区域");
    container.append(heading, input, pasteHint); if (options.classification === "requirement") container.append(consent);
    else container.append(note("附件绑定当前居民，不会自动共享给其他角色。涉及项目源码时仍须遵守原生源码授权。"));
    container.append(message, content);
    function changed() { options.onChange?.(rows.filter(row => row.id)); options.onBusy?.(uploading > 0); }
    function render() {
      content.replaceChildren(); pick.disabled = locked || uploading > 0 || rows.length >= MAX_FILES; input.disabled = pick.disabled; check.disabled = locked || uploading > 0;
      rows.forEach(row => {
        const entry = h("div", "resident-attachment"), copy = h("div");
        copy.append(h("strong", "", row.name || "附件"), note(row.uploading ? "正在保存本机附件…" : row.error ? `上传未完成：${row.error}` : row.status === "partial" ? "已上传 · 部分内容待核对，尚未完成任务分析" : "已上传 · 可提取内容已就绪，尚未完成任务分析")); entry.append(copy);
        if (row.id) entry.append(button("预览", () => openDocument(row.id, scope)));
        if (!row.uploading) { const remove = button("移除", () => { rows.splice(rows.indexOf(row), 1); render(); changed(); }); remove.disabled = locked; entry.append(remove); }
        content.append(entry);
      });
    }
    async function upload(files) {
      if (locked || uploading) return;
      message.textContent = "";
      if (options.classification === "requirement" && !check.checked) { message.textContent = "请先确认这些附件属于可共享的业务资料；源码截图不能放入这里。"; return; }
      const incoming = Array.from(files || []);
      if (incoming.length + rows.length > MAX_FILES) { message.textContent = "每个任务最多添加 16 份附件，请先移除多余附件。"; return; }
      uploading++; render(); changed();
      try {
        for (const file of incoming) {
          const name = file.name || `截图-${Date.now()}.png`, row = { name, uploading: true }; rows.push(row); render();
          try {
            if (!fileTypes.test(name)) throw new Error("请使用支持的文档或图片格式。");
            if (!Number.isFinite(file.size) || file.size <= 0 || file.size > MAX_FILE) throw new Error("文件需为非空且不超过 20 MiB。");
            const body = { name, content_base64: await readFile(file), classification: options.classification || "private" };
            if (scope.agent_id) body.agent_id = scope.agent_id; if (scope.project_id) body.project_id = scope.project_id;
            let result;
            try { result = await post("/api/documents", body); } finally { delete body.content_base64; }
            const doc = result.document || result;
            if (!doc.id) throw new Error("服务未返回附件记录，请重新上传。");
            Object.assign(row, doc, { uploading: false });
          } catch (e) { row.uploading = false; row.error = e.message; }
          render(); changed();
        }
      } finally { uploading--; input.value = ""; render(); changed(); }
    }
    input.addEventListener("change", () => upload(input.files));
    const handlePaste = event => {
      if (event.defaultPrevented) return;
      const files = Array.from(event.clipboardData?.items || []).filter(item => item.kind === "file" && item.type.startsWith("image/")).map(item => item.getAsFile()).filter(Boolean);
      if (files.length) { event.preventDefault(); return upload(files); }
    };
    container.addEventListener("paste", handlePaste);
    render();
    return { node: container, ids: () => rows.filter(row => row.id).map(row => row.id), documents: () => rows.filter(row => row.id), busy: () => uploading > 0, hasErrors: () => rows.some(row => row.error), setDisabled: value => { locked = value; render(); }, setCallbacks: callbacks => Object.assign(options, callbacks), upload, handlePaste };
  }
  function cachedPicker(key, options) {
    if (!attachmentPanels.has(key)) attachmentPanels.set(key, attachmentPicker(options));
    const picker = attachmentPanels.get(key); picker.setCallbacks({ onBusy: options.onBusy, onChange: options.onChange }); return picker;
  }
  function contentValue(value, depth = 0) {
    if (value == null) return note("");
    if (typeof value !== "object") return h("p", "resident-text", String(value));
    if (depth > 6) return note("内容层级较多，请查看上层摘要。");
    if (Array.isArray(value)) { const box = h("ul", "resident-value-list"); value.forEach(item => { const row = h("li"); row.append(contentValue(item, depth + 1)); box.append(row); }); return box; }
    const box = h("div", "resident-value"), labels = { summary: "总结", content: "内容", answer: "答复", scope: "范围", acceptance_criteria: "验收条件", requirement_items: "需求条目", recommendations: "建议", next_steps: "下一步", title: "标题", text: "说明", facts: "有来源的事实", inferences: "推断", risks: "需要注意", limitations: "当前边界" };
    for (const [key, item] of Object.entries(value)) { if (["call_ids", "interaction_session_id", "sources", "evidence", "questions", "id", "revision"].includes(key)) continue; const row = h("div"); row.append(h("strong", "", labels[key] || "补充内容"), contentValue(item, depth + 1)); box.append(row); } return box;
  }
  function sourceLinks(parent, values, evidence, scope) {
    const links = h("div", "resident-source-links");
    list(values).forEach(value => {
      const key = typeof value === "string" ? value : value.id, source = evidence.find(item => item.id === key);
      if (!source) { links.append(h("span", "resident-note", `来源待定位：${key || "未提供标识"}`)); return; }
      links.append(button(source.label || "查看来源", () => {
        if (source.document_id) return openDocument(source.document_id, scope, source);
        const modal = dialog(source.label || "来源记录"); modal.append(contentValue(source.text || "此来源没有提供可预览正文。"));
      }));
    });
    if (links.children.length) parent.append(links);
  }
  function renderInteraction(parent, value, options = {}) {
    if (!value) { parent.append(note("尚无分步执行记录。开始任务后，这里显示实际完成的步骤。")); return; }
    const evidence = list(value.evidence), steps = list(value.steps), scope = options.scope || { agent_id: value.agent_id };
    const top = h("div", "resident-toolbar"); top.append(h("strong", "", statuses[value.status] || "状态待核对"));
    const counts = value.progress;
    if (Number.isSafeInteger(counts?.completed) && Number.isSafeInteger(counts?.total) && counts.total > 0 && counts.completed >= 0 && counts.completed <= counts.total) top.append(note(`已完成 ${counts.completed} / ${counts.total} 个步骤`));
    if (options.onAction && running.has(value.status)) { const pause = button("暂停", () => options.onAction("pause", {})); pause.disabled = !!options.pending; top.append(pause); }
    if (options.onAction && resumable.has(value.status)) { const resume = button("继续未完成步骤", () => options.onAction("resume", {}), true); resume.disabled = !!options.pending; top.append(resume); }
    parent.append(top);
    if (value.status === "running" && value.public_progress?.phase === "waiting") parent.append(note("等待模型响应，当前步骤尚未完成。"));
    if (value.status === "running" && value.public_progress?.phase === "receiving") {
      const chars = value.public_progress.text_chars;
      parent.append(note(`正在接收回复${Number.isSafeInteger(chars) && chars >= 0 ? ` · 已收到 ${chars} 字` : ""}，当前步骤尚未完成。`));
    }
    if (value.error) parent.append(h("p", "resident-error", value.error));
    if (value.status === "waiting_input") parent.append(note("任务正在等待你的答复。已完成的步骤会保留。"));
    const phases = h("ol", "resident-steps");
    steps.forEach(step => { const row = h("li", `resident-step ${running.has(step.status) ? "active" : ""}`); row.append(h("strong", "", step.label || "工作步骤"), h("span", "", statuses[step.status] || "状态待核对")); if (step.error) row.append(h("p", "resident-error", step.error)); if (step.attempts) row.append(note(`实际尝试 ${step.attempts} 次`)); sourceLinks(row, step.sources, evidence, scope); phases.append(row); });
    parent.append(steps.length ? phases : note("服务尚未生成步骤记录，没有可展示的完成进度。"));
    for (const [key, title, empty] of [["facts", "有来源的事实", "尚无已记录的事实。"], ["inferences", "推断与判断", "尚无已记录的推断。"]]) {
      const box = card(title), entries = list(value[key]);
      if (!entries.length) box.append(note(empty));
      entries.forEach(item => { const row = h("div", "resident-finding"); row.append(contentValue(typeof item === "string" ? item : item.text || item.content)); sourceLinks(row, item.sources, evidence, scope); box.append(row); }); parent.append(box);
    }
    const questions = card("待确认问题");
    if (!list(value.questions).length) questions.append(note(value.status === "completed" ? "本次没有待确认问题。" : "尚无已记录的问题，后续步骤仍可能需要你补充。"));
    list(value.questions).forEach(question => {
      const row = h("div", "resident-question"); row.append(h("p", "resident-text", question.text), note(question.answer ? "已回答" : question.blocking ? "需要答复后继续" : "可补充确认")); sourceLinks(row, question.sources, evidence, scope);
      if (question.answer) row.append(contentValue(question.answer));
      else if (options.onAction && options.questions !== false) {
        const key = `answer:${value.id}:${question.id}:${question.text}`, input = textField(row, "你的答复", key), save = button("保存答复并继续", () => {
          if (!input.value.trim()) { input.focus(); return; }
          return options.onAction("answer", { question_id: question.id, answer: input.value.trim() }, key);
        }, true); save.disabled = !!options.pending; row.append(save);
      } questions.append(row);
    }); parent.append(questions);
    if (value.status === "completed" && value.result != null) { const final = card("已完成结果"); final.append(contentValue(value.result)); parent.append(final); }
    else if (list(value.partial).length) { const partial = card("已完成片段 · 整体尚未完成"); partial.append(note("这些是已保存的阶段结果，不能作为完整交付。"), contentValue(value.partial)); parent.append(partial); }
    if (evidence.length) { const sources = card("来源索引"); evidence.forEach(item => sourceLinks(sources, [item.id], evidence, scope)); parent.append(sources); }
  }
  function renderList() {
    const select = root.querySelector(".resident-agent-select"), box = root.querySelector(".resident-task-list");
    select.replaceChildren(); list(host.getState()?.agents).forEach(agent => { const option = h("option", "", agent.name); option.value = agent.id; select.append(option); }); select.value = agentId;
    root.querySelector(".resident-work-title").textContent = `${resident(agentId)?.name || "居民"}的分步工作`;
    box.replaceChildren();
    items.forEach(item => { const entry = button("", () => { selectedId = item.id; selections.set(agentId, selectedId); current = null; creator = null; signature = ""; generation++; renderDetail(); refresh(); }); entry.className += " resident-task-item"; entry.setAttribute("aria-pressed", String(item.id === selectedId)); entry.append(h("strong", "", item.goal || item.title || "分步任务"), note(statuses[item.status] || "查看记录")); box.append(entry); });
    if (!items.length) box.append(note("还没有分步任务。写下目标，附上需要参考的资料。"));
  }
  function renderDetail(force = false) {
    const panel = root.querySelector(".resident-work-detail"), next = JSON.stringify([agentId, selectedId, current, pending, error]);
    if (!force && signature === next) return; signature = next;
    if (!selectedId && creator?.agent === agentId) { root.querySelector(".resident-work-error").textContent = error; return; }
    const active = document.activeElement, focusKey = active?.dataset?.residentField, start = active?.selectionStart, end = active?.selectionEnd;
    panel.replaceChildren(); root.querySelector(".resident-work-error").textContent = error;
    if (!agentId) { panel.append(note("先选择一位居民。")); return; }
    if (!selectedId) {
      const id = agentId, form = h("form", "resident-form"), key = `goal:${id}`, goal = textField(form, "这次想完成什么", key), save = button("开始分步工作", () => {}, true);
      goal.placeholder = "写下目标、已有信息和希望得到的结果。简单聊天仍可在右侧继续。"; goal.required = true;
      const picker = cachedPicker(`new:${id}`, { agentId: id, classification: "private", onBusy: busy => { save.disabled = busy || pending; } });
      save.disabled = picker.busy() || pending; form.addEventListener("paste", picker.handlePaste);
      form.append(picker.node, note("开始后会使用当前模型服务。图片是否可分析取决于服务的实际图文能力；无法读取的内容会列为待处理，不会当作已理解。"));
      save.type = "submit"; form.append(save); panel.append(form); creator = { agent: id, picker };
      form.addEventListener("submit", async event => {
        event.preventDefault(); if (pending || picker.busy() || !goal.value.trim()) return;
        if (picker.hasErrors()) { error = "请移除或重新上传未成功保存的附件，再开始任务。"; root.querySelector(".resident-work-error").textContent = error; return; }
        const body = { agent_id: id, goal: goal.value.trim(), attachment_ids: picker.ids() }, key = `create:${id}`;
        pending = true; save.disabled = true; goal.disabled = true; picker.setDisabled(true); error = "";
        try {
          const result = await post("/api/interactions", { ...body, request_id: requestFor(key, body) }), task = result.interaction || result;
          if (!task.id) throw new Error("服务未返回任务记录，请重试。");
          requestKeys.delete(key); drafts.delete(`goal:${id}`); attachmentPanels.delete(`new:${id}`); selections.set(id, task.id);
          if (agentId === id) { selectedId = task.id; current = task; creator = null; }
          host.toast("分步任务已建立，正在等待真实执行记录。");
        } catch (e) { error = e.message; }
        finally { pending = false; save.disabled = false; goal.disabled = false; picker.setDisabled(false); renderDetail(true); await refresh(); }
      }); return;
    }
    creator = null;
    if (!current) { panel.append(note("正在读取任务记录…")); return; }
    panel.append(h("h2", "", current.goal || "分步任务"), note(`任务版本 ${current.revision} · 附件上传与分析完成分别记录`));
    if (list(current.attachment_ids).length) { const attached = card("任务附件"); list(current.attachment_ids).forEach(id => attached.append(button("查看附件", () => openDocument(id, { agent_id: agentId })))); panel.append(attached); }
    renderInteraction(panel, current, { pending, onAction: perform, scope: { agent_id: agentId } });
    const revise = h("details", "resident-revise"); revise.append(h("summary", "", "修改目标并建立新版本"));
    const editKey = `revise:${current.id}:${current.revision}`, goal = textField(revise, "新的完整目标", editKey, current.goal || "");
    const picker = cachedPicker(`revise:${current.id}:${current.revision}`, { agentId, classification: "private", initial: current.attachment_ids }); picker.setDisabled(pending); revise.addEventListener("paste", picker.handlePaste);
    revise.append(picker.node, note("新版本会按新的目标与附件重新分析；旧版本记录保留。"));
    const submit = button("保存新版本", () => { if (!goal.value.trim()) return goal.focus(); if (picker.busy() || picker.hasErrors()) { error = "请先处理未完成的附件上传。"; root.querySelector(".resident-work-error").textContent = error; return; } return perform("revise", { goal: goal.value.trim(), attachment_ids: picker.ids() }, editKey); }); submit.disabled = pending || running.has(current.status); revise.append(submit); panel.append(revise);
    if (focusKey) { const input = Array.from(panel.querySelectorAll("[data-resident-field]")).find(item => item.dataset.residentField === focusKey); if (input) { input.focus(); if (start != null && input.setSelectionRange) input.setSelectionRange(start, end); } }
  }
  async function perform(action, payload = {}, draftKey = null) {
    if (!current || pending) return;
    const id = current.id, body = { agent_id: agentId, action, revision: current.revision, ...payload }, key = `action:${id}:${action}:${payload.question_id || ""}`;
    pending = true; error = ""; generation++; renderDetail(true);
    try {
      const result = await post(`/api/interactions/${encodeURIComponent(id)}/action`, { ...body, request_id: requestFor(key, body) }); requestKeys.delete(key);
      if (draftKey) drafts.delete(draftKey);
      if (selectedId === id) current = result.interaction || result;
    } catch (e) { error = e.status === 409 ? "记录已更新，你的输入已保留。请核对最新步骤后再试。" : e.message; }
    finally { pending = false; await refresh(); renderDetail(true); }
  }
  function schedule() { clearTimeout(timer); timer = null; if (visible()) timer = setTimeout(refresh, 4000); }
  async function refresh() {
    if (!host || !visible() || !agentId) return;
    if (loading) return loading;
    const token = generation, id = agentId, selected = selectedId;
    loading = (async () => {
      try {
        const reads = await Promise.allSettled([host.api(`/api/interactions?agent_id=${encodeURIComponent(id)}`), selected ? host.api(`/api/interactions/${encodeURIComponent(selected)}?agent_id=${encodeURIComponent(id)}`) : Promise.resolve(null), host.api("/api/model-settings")]);
        if (token !== generation || agentId !== id) return;
        if (reads[0].status === "fulfilled") items = list(reads[0].value.sessions || reads[0].value.interactions || reads[0].value.items || reads[0].value); else throw reads[0].reason;
        if (reads[1].status === "fulfilled" && reads[1].value) current = reads[1].value.interaction || reads[1].value;
        else if (reads[1].status === "rejected") error = reads[1].reason.message;
        const capability = root.querySelector(".resident-capability");
        if (reads[2].status === "fulfilled") {
          const features = (reads[2].value.settings || reads[2].value).budget_capability || {};
          capability.textContent = `${features.supports_streaming === true ? "逐步接收回复能力已验证；实际接收状态以当前步骤为准。" : "逐步接收回复尚未验证，文字任务会等待完整回复。"} ${features.supports_vision === true ? "看图能力已验证；附件理解仍以每步的来源与结论为准。" : "看图能力尚未验证：图片相关步骤会暂停等待处理，不能当作已经理解。"}`;
        } else capability.textContent = "暂未读到模型能力状态。附件上传只代表本机保存与提取；图片理解仍需通过看图验证。";
        renderList(); renderDetail();
      } catch (e) { error = e.message; root.querySelector(".resident-work-error").textContent = error; }
    })();
    try { await loading; } finally { loading = null; if (token !== generation && visible()) refresh(); else schedule(); }
  }
  function agentChanged(id) {
    if (!host || !id || id === agentId) return;
    agentId = id; selectedId = selections.get(id) || ""; current = null; items = []; creator = null; error = ""; signature = ""; generation++; renderList(); renderDetail(); if (visible()) refresh();
  }
  function init(options) {
    host = options; root = document.getElementById("resident-workspace"); if (!root) return;
    const heading = h("div", "resident-work-heading"), copy = h("div"), tools = h("div", "resident-toolbar"); copy.append(h("p", "eyebrow", "ONE GOAL, VISIBLE STEPS"), h("h1", "resident-work-title", "居民分步工作"), note("把长任务分步做完，每个结论都能回到材料里核对。"));
    tools.append(button("刷新记录", refresh), button("回到聊天", () => host.showView("town"))); heading.append(copy, tools);
    const layout = h("div", "resident-work-layout"), sidebar = h("aside", "resident-work-sidebar"), label = h("label", "resident-field"), select = h("select", "resident-agent-select"); label.append(h("span", "", "当前居民"), select); select.addEventListener("change", () => { host.selectAgent(select.value); agentChanged(select.value); });
    sidebar.append(label, button("＋ 新建分步任务", () => { selectedId = ""; selections.delete(agentId); current = null; creator = null; signature = ""; generation++; error = ""; renderList(); renderDetail(true); }), h("div", "resident-task-list"));
    const errorNode = h("p", "resident-error resident-work-error"), capability = h("p", "resident-note resident-capability", "正在读取当前模型的回复与看图能力…"); errorNode.setAttribute("role", "alert"); layout.append(sidebar, h("div", "resident-work-detail")); root.replaceChildren(heading, capability, errorNode, layout); renderList(); renderDetail();
    document.addEventListener("visibilitychange", () => { if (visible()) refresh(); else { clearTimeout(timer); timer = null; } });
  }
  function viewChanged(name) { if (name === "resident-work") { agentChanged(host.getSelected()); refresh(); } else { clearTimeout(timer); timer = null; } }
  window.StarhavenResidentWorkspace = { init, open: id => { agentChanged(id || host.getSelected()); host.showView("resident-work"); }, refresh, viewChanged, agentChanged, attachmentPicker, renderInteraction, openDocument };
})();

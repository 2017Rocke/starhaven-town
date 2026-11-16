"use strict";

// Imported skills are reference material selected explicitly for one message.
(() => {
  const list = value => Array.isArray(value) ? value : [];
  const records = new Map(), choices = new Map(), resourceChoices = new Map(), busy = new Set(), loading = new Map();
  let host, root, currentId = "";
  const h = (tag, cls, text) => host.el(tag, cls, text);
  const note = text => h("p", "local-skill-note", text);
  const selected = id => [...(choices.get(id) || [])];
  const resourceKey = (id, skillId) => `${id}:${skillId}`;
  const resourcePaths = (id, skillId) => [...(resourceChoices.get(resourceKey(id, skillId)) || new Set(["SKILL.md"]))];
  const selectedFiles = (id, ids = selected(id)) => Object.fromEntries(ids.map(skillId => [skillId, resourcePaths(id, skillId)]));
  function selectedBytes(id, ids = selected(id)) {
    return ids.reduce((total, skillId) => {
      const skill = list(records.get(id)?.installed).find(item => item.id === skillId), paths = resourcePaths(id, skillId);
      return total + list(skill?.files).filter(file => paths.includes(file.path)).reduce((bytes, file) => bytes + (Number.isSafeInteger(file.bytes) && file.bytes > 0 ? file.bytes : 0), 0);
    }, 0);
  }
  const name = id => list(host.getState()?.agents).find(item => item.id === id)?.name || "开发伙伴";
  function button(text, action) { const node = h("button", "local-skill-button", text); node.type = "button"; node.addEventListener("click", action); return node; }
  function renderChat() {
    if (!root) return;
    const state = records.get(currentId); root.replaceChildren(); root.hidden = !state?.allowed;
    if (!state?.allowed) return;
    const heading = h("div", "local-skill-heading"), ids = selected(currentId);
    heading.append(h("strong", "", ids.length ? `本次使用 ${ids.length} 个技能` : "本次未使用技能"), button("技能工作区", () => open(currentId)));
    root.append(heading);
    if (ids.length) root.append(note(ids.map(id => list(state.installed).find(item => item.id === id)?.title || "已选技能").join("、")), note(`已选指南与引用 ${(selectedBytes(currentId) / 1024).toFixed(1)} / 48 KiB`));
  }
  async function refresh(id) {
    if (!id) return null;
    if (loading.has(id)) return loading.get(id);
    const pending = (async () => {
      const value = await host.api(`/api/local-skills?agent_id=${encodeURIComponent(id)}`);
      records.set(id, value);
      const installed = new Set(list(value.installed).map(item => item.id));
      choices.set(id, new Set(value.allowed ? selected(id).filter(key => installed.has(key)) : []));
      if (id === currentId) renderChat();
      return value;
    })();
    loading.set(id, pending);
    try { return await pending; } finally { loading.delete(id); }
  }
  async function open(id = currentId) {
    if (!id) return;
    const dialog = h("dialog", "town-dialog local-skills-dialog"), heading = h("div", "local-skill-heading"), content = h("div"), error = h("p", "local-skill-error");
    heading.append(h("h2", "", `${name(id)}的技能工作区`), button("关闭", () => dialog.close()));
    dialog.setAttribute("aria-label", `${name(id)}的技能工作区`); error.setAttribute("role", "alert");
    dialog.append(heading, note("把本机技能包导入这位开发伙伴的私有工作区，再勾选本次聊天要参考的技能。每次最多 3 个；发送成功后自动取消勾选。"), content, error);
    dialog.addEventListener("close", () => dialog.remove()); document.body.append(dialog); dialog.showModal();
    content.append(note("正在读取技能工作区…"));
    const importNames = new Set(); let importing = false;
    function render(value) {
      content.replaceChildren();
      if (!value.allowed) { content.append(note(value.reason || "当前居民没有开发技能工作区。")); return; }
      const installedBox = h("section", "local-skill-section"), availableBox = h("section", "local-skill-section");
      installedBox.append(h("h3", "", "本次聊天使用"), note("技能作为参考文字提供给当前模型。每个技能默认包含指南，可按需附加引用文档；每技能最多 16 份文字，合计最多 48 KiB。执行脚本、联网、修改源码和 Git 操作仍需各自的授权。"));
      if (!list(value.installed).length) installedBox.append(note("还没有已导入的技能。先从下方选择本机技能包。"));
      list(value.installed).forEach(skill => {
        const item = h("div", "local-skill-item"), row = h("label", "local-skill-row"), check = h("input"), copy = h("div"), resources = h("details", "local-skill-resources");
        check.type = "checkbox"; check.checked = selected(id).includes(skill.id); check.disabled = busy.has(id) || importing;
        copy.append(h("strong", "", skill.title || skill.name || "本地技能"), note(skill.description || "暂无简介"));
        const hash = String(skill.hash || skill.source_hash || "").slice(0, 8);
        const count = Number.isSafeInteger(skill.files_count) ? skill.files_count : Array.isArray(skill.files) ? skill.files.length : null;
        copy.append(note(`已导入${count != null ? ` · ${count} 个文件` : ""}${hash ? ` · 版本 ${hash}` : ""}`));
        if (skill.status === "partial_dependencies") copy.append(note("指南可用，部分环境依赖未导入；不代表脚本可以执行。"));
        list(skill.warnings).forEach(warning => copy.append(note(typeof warning === "string" ? warning : warning.message || "部分依赖待补充")));
        resources.hidden = !check.checked;
        resources.append(h("summary", "", "附加引用文档（选用）"), note("始终包含 SKILL.md 技能指南。以下仅选择已导入的 Markdown 文字；脚本不会执行。"));
        const files = list(skill.files).filter(file => typeof file.path === "string" && file.path !== "SKILL.md" && /\.md$/i.test(file.path));
        if (!files.length) resources.append(note("这个版本没有其他可选的引用文档。"));
        files.forEach(file => {
          const label = h("label", "local-skill-resource"), field = h("input"); field.type = "checkbox"; field.checked = resourcePaths(id, skill.id).includes(file.path); field.disabled = busy.has(id) || importing;
          label.append(field, h("span", "", `${file.path}${Number.isSafeInteger(file.bytes) ? `（${(file.bytes / 1024).toFixed(1)} KiB）` : ""}`));
          field.addEventListener("change", () => {
            if (busy.has(id) || importing || !selected(id).includes(skill.id)) { field.checked = resourcePaths(id, skill.id).includes(file.path); return; }
            const key = resourceKey(id, skill.id), prior = new Set(resourcePaths(id, skill.id)), next = new Set(prior);
            field.checked ? next.add(file.path) : next.delete(file.path); resourceChoices.set(key, next);
            if (next.size > 16 || selectedBytes(id) > 48 * 1024) { resourceChoices.set(key, prior); field.checked = prior.has(file.path); error.textContent = next.size > 16 ? "每个技能最多选择 16 份文字，请减少引用文档。" : "本次技能文字超过 48 KiB，请减少技能或引用文档。"; return; }
            error.textContent = ""; if (currentId === id) renderChat();
          }); resources.append(label);
        });
        check.addEventListener("change", () => {
          if (busy.has(id) || importing) { check.checked = selected(id).includes(skill.id); return; }
          const chosen = choices.get(id) || new Set();
          if (check.checked && chosen.size >= 3 && !chosen.has(skill.id)) { check.checked = false; error.textContent = "每次聊天最多选择 3 个技能，请先取消一项。"; return; }
          if (check.checked && selectedBytes(id, [...new Set([...chosen, skill.id])]) > 48 * 1024) { check.checked = false; error.textContent = "本次技能文字超过 48 KiB，请减少技能或引用文档。"; return; }
          check.checked ? chosen.add(skill.id) : chosen.delete(skill.id); choices.set(id, chosen); resources.hidden = !check.checked; error.textContent = ""; if (currentId === id) renderChat();
        }); row.append(check, copy); item.append(row, resources); installedBox.append(item);
      });
      availableBox.append(h("h3", "", "从本机导入技能包"));
      if (!list(value.available).length) availableBox.append(note("没有发现可导入的本机技能包。"));
      const importButton = button(importing ? "正在导入…" : "导入所选技能", async () => {
        if (importing || !importNames.size) return;
        importing = true; error.textContent = ""; render(value);
        try {
          await host.api("/api/local-skills/import", { method: "POST", body: { agent_id: id, names: [...importNames] } });
          importNames.clear(); const next = await refresh(id); importing = false; render(next); host.toast("技能包已导入，可勾选用于本次聊天。");
        } catch (e) { importing = false; error.textContent = e.message; render(value); }
      });
      list(value.available).forEach(skill => {
        const row = h("label", "local-skill-row"), check = h("input"), copy = h("div");
        check.type = "checkbox"; check.checked = importNames.has(skill.name); check.disabled = importing || skill.status !== "available";
        copy.append(h("strong", "", skill.title || skill.name || "本地技能"), note(skill.description || "暂无简介"));
        if (skill.status !== "available") copy.append(h("p", "local-skill-error", skill.reason || "这个技能包暂时不能导入。"));
        list(skill.warnings).forEach(warning => copy.append(note(typeof warning === "string" ? warning : warning.message || "部分依赖待补充")));
        check.addEventListener("change", () => { if (importing || skill.status !== "available") return; check.checked ? importNames.add(skill.name) : importNames.delete(skill.name); importButton.disabled = !importNames.size; });
        row.append(check, copy); availableBox.append(row);
      });
      importButton.disabled = importing || !importNames.size; availableBox.append(importButton); content.append(installedBox, availableBox);
    }
    try { render(await refresh(id)); } catch (e) { content.replaceChildren(note("技能列表暂时没有读到。"), button("重新读取", async () => { error.textContent = ""; try { render(await refresh(id)); } catch (retryError) { error.textContent = retryError.message; } })); error.textContent = e.message; }
  }
  function agentChanged(id) {
    if (!host || !id || currentId === id) return;
    currentId = id; renderChat(); refresh(id).catch(() => {
      if (currentId !== id) return;
      root.hidden = false; root.replaceChildren(button("重新读取技能工作区", () => open(id)));
    });
  }
  window.StarhavenLocalSkills = {
    init(options) { host = options; root = document.getElementById("local-skills-chat"); if (root) renderChat(); },
    agentChanged, open, selected, selectedFiles,
    setBusy(id, value) { value ? busy.add(id) : busy.delete(id); },
    clearSelected(id, used = selected(id)) { const chosen = choices.get(id); used.forEach(key => { chosen?.delete(key); resourceChoices.delete(resourceKey(id, key)); }); if (id === currentId) renderChat(); },
  };
})();

"use strict";

// The scene follows persisted collection work. Timers only refresh state; they
// never manufacture materials, books, successful checks, or task completion.
(() => {
  const $ = selector => document.querySelector(selector);
  const activePhases = new Set(["queued", "searching", "collecting", "storing"]);
  const phases = { idle: "在观察站等你", queued: "准备出发", searching: "正在寻找项目", collecting: "正在取回材料", storing: "正在整理材料", completed: "本次采集已结束", partial: "部分材料已带回", failed: "采集遇到困难", interrupted: "上次采集中断" };
  const sceneState = { data: null, scene: "yard", library: "materials", loading: false, starting: false, saving: false, dirty: false, timer: null, detailRequest: 0, map: null, signature: "", listSignature: "" };
  let host;
  const arr = value => Array.isArray(value) ? value : [];
  const count = value => Math.max(0, Number(value) || 0);
  const phase = () => sceneState.data?.job?.phase || "idle";
  const busy = () => activePhases.has(phase());
  const date = value => {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? "时间未记录" : parsed.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Shanghai" });
  };
  function node(tag, cls, text) { return host.el(tag, cls, text); }
  function art(kind, cls = "scout-pixel-icon") {
    const span = node("span", cls);
    span.setAttribute("aria-hidden", "true");
    span.innerHTML = window.StarhavenScoutArt?.icon(kind) || "";
    return span;
  }
  function imageParams() {
    const data = sceneState.data || {};
    return { phase: phase(), materialCount: count(data.counts?.raw ?? arr(data.materials).filter(item => item.source_status !== "missing").length), rawCount: phase() === "storing" ? 1 : 0, bookCount: count(data.counts?.books ?? arr(data.books).length) };
  }
  function safeURL(value, report = false) {
    if (typeof value !== "string") return null;
    try {
      const url = new URL(value, location.origin);
      if (url.username || url.password) return null;
      if (report && url.origin === location.origin && /^\/reports\/\d{4}-\d{2}-\d{2}\/report\.html$/.test(url.pathname)) return url.href;
      if (url.protocol === "https:" && url.hostname === "github.com") return url.href;
    } catch { /* Display the label without an unsafe link. */ }
    return null;
  }
  function link(label, value, report = false) {
    const href = safeURL(value, report);
    const result = node(href ? "a" : "span", "scout-source-link", label);
    if (href) { result.href = href; result.target = "_blank"; result.rel = "noopener noreferrer"; }
    return result;
  }
  function error(message) {
    $("#scout-error").textContent = message || "";
    $("#scout-error").hidden = !message;
  }
  function counts() {
    const data = sceneState.data || {};
    $("#scout-material-count").textContent = count(data.counts?.materials ?? arr(data.materials).length);
    $("#scout-book-count").textContent = count(data.counts?.books ?? arr(data.books).length);
  }
  function selectLibrary(value) {
    sceneState.library = value;
    document.querySelectorAll("[data-scout-library]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.scoutLibrary === value)));
    $("#scout-library-title").textContent = value === "books" ? "书架上的研究手记" : "带回来的材料";
    $("#scout-library-copy").textContent = value === "books" ? "有内容、有出处的研究报告，在这里留存。" : "每捆木材，对应一份真实的项目资料。";
    renderLibrary();
  }
  function selectScene(value) {
    sceneState.scene = value;
    document.querySelectorAll("[data-scout-scene]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.scoutScene === value)));
    renderScene();
    selectLibrary(value === "room" ? "books" : "materials");
  }
  function hotspot(label, left, top, action, description) {
    const button = node("button", "scout-hotspot", label);
    button.type = "button";
    button.style.left = left + "%"; button.style.top = top + "%";
    button.setAttribute("aria-label", description || label);
    button.addEventListener("click", () => {
      action();
      $("#scout-library-title").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "nearest" });
    });
    return button;
  }
  function renderScene() {
    const params = imageParams();
    const room = sceneState.scene === "room";
    const signature = JSON.stringify([params, sceneState.scene]);
    if (signature === sceneState.signature) return;
    sceneState.signature = signature;
    $("#scout-stage").dataset.scene = sceneState.scene;
    $("#scout-stage").dataset.phase = phase();
    $("#scout-scene-art").innerHTML = window.StarhavenScoutArt?.[sceneState.scene](params) || "";
    const drawing = $("#scout-scene-art > svg");
    if (drawing) drawing.style.width = "100%";
    const labels = $("#scout-scene-labels");
    labels.replaceChildren();
    if (room) {
      labels.append(hotspot(`资料书架 · ${params.bookCount}`, 20, 8, () => selectLibrary("books")), hotspot("项目资料", 17, 65, () => selectLibrary("materials")));
    } else {
      labels.append(hotspot(`材料架 · ${params.materialCount}`, 38, 5, () => selectLibrary("materials")), hotspot("采集手推车", 81, 62, () => selectLibrary("materials")));
    }
    $("#scout-scene-caption").textContent = room ? "查资料、读源码，把好发现留在书架上。" : (busy() ? phases[phase()] : "原木是待研究的项目材料，书本是已有研究。首批材料等星探带回。");
    if (!busy() && params.materialCount && !room) $("#scout-scene-caption").textContent = `${params.materialCount} 份项目材料已入库，点击材料架查看来源和风险。`;
  }
  function renderJob() {
    const current = phase();
    const job = sceneState.data?.job || {};
    const stages = [["searching", "compass", "出门采集", "寻找、筛选 GitHub 项目"], ["collecting", "cart", "搬回原木", "取回源码与来源记录"], ["storing", "rack", "整理材料", "去重、分类、风险标注"]];
    const index = stages.findIndex(item => item[0] === current);
    const list = $("#scout-steps");
    list.replaceChildren();
    for (const [i, item] of stages.entries()) {
      const li = node("li", "scout-step");
      const isCurrent = index === i;
      if (isCurrent) li.setAttribute("aria-current", "step");
      // A failed/partial run is deliberately not painted as three successful steps.
      li.classList.toggle("is-done", (current === "completed" && (i === 0 || count(job.completed) > 0)) || index > i);
      li.classList.toggle("is-current", isCurrent);
      const text = node("div"); text.append(node("strong", "", item[2]), node("span", "", item[3]));
      li.append(art(item[1]), text); list.append(li);
    }
    let message = job.message || phases[current] || "在观察站等你";
    if (job.total && busy()) message += ` · 已入库 ${count(job.completed)} / ${count(job.total)}`;
    $("#scout-job-message").textContent = message;
    $("#scout-job-message").dataset.phase = current;
    const errors = arr(job.errors);
    $("#scout-job-errors").hidden = !errors.length;
    $("#scout-job-error-list").replaceChildren(...errors.map(item => node("p", "", typeof item === "string" ? item : `${item.project || item.category || "来源"}：${item.message || item.error || "暂未完成"}`)));
    const trail = arr(job.recent_events);
    $("#scout-job-trail").hidden = !trail.length;
    $("#scout-job-trail-list").replaceChildren(...trail.map(item => node("p", "", `${date(item.at)} · ${item.message || phases[item.phase] || "工作记录"}`)));
    if (host.getSelected() === "scout") {
      const status = $("#selected-profile .profile-status span:last-child");
      if (status) status.textContent = phases[current] || "在观察站等你";
    }
    $("#scout-start").disabled = !sceneState.data || busy() || sceneState.starting;
    $("#scout-start").textContent = sceneState.starting ? "正在准备…" : busy() ? "星探正在工作…" : ["failed", "interrupted", "partial"].includes(current) ? "再次出发 →" : "让星探出发 →";
  }
  function renderSettings() {
    if (sceneState.dirty || sceneState.saving) return;
    const settings = sceneState.data?.settings || {};
    $("#scout-daily-enabled").checked = settings.daily_enabled === true;
    $("#scout-daily-time").value = settings.daily_time || "09:00";
    $("#scout-daily-limit").value = String(settings.daily_limit || 3);
  }
  function riskBadge(material) {
    const risk = material.risk || {};
    const known = new Set(["high", "medium", "low", "info", "unknown", "review", "unreviewed", "needs_review"]);
    const level = known.has(risk.level) ? risk.level : "unknown";
    return node("span", `scout-risk risk-${level}`, risk.label || "待复核");
  }
  function renderLibrary() {
    const list = $("#scout-materials");
    const books = sceneState.library === "books";
    const items = arr(sceneState.data?.[books ? "books" : "materials"]);
    const query = $("#scout-search").value.trim().toLocaleLowerCase();
    const filtered = items.filter(item => [item.full_name, item.title, item.description, item.category, item.owner?.name, item.owner?.login].join(" ").toLocaleLowerCase().includes(query));
    const signature = JSON.stringify([sceneState.library, filtered, query, Boolean(sceneState.data)]);
    if (sceneState.listSignature === signature) return;
    sceneState.listSignature = signature;
    list.replaceChildren();
    if (!filtered.length) {
      const empty = node("div", "scout-empty");
      empty.append(art(books ? "books" : "logs", "scout-empty-icon"), node("strong", "", query ? "没有找到这份收藏" : books ? "书架留好了位置" : "材料架已经备好"), node("p", "", query ? "换个项目名、分类或作者试试。" : books ? "已有研究报告会收在这里。新采集的源码先放材料架，完成研究后再沉淀成书。" : sceneState.data ? "让星探出发，带回第一份真实项目材料。" : "正在读取观察站的收藏…"));
      list.append(empty);
    }
    for (const item of filtered) {
      const card = node("article", "scout-material-card");
      const heading = node("div", "scout-material-heading");
      heading.append(art(books ? "books" : "logs"), node("span", "scout-category", books ? "研究手记" : item.category || "开源项目"));
      if (!books) heading.append(riskBadge(item));
      card.append(heading, node("h3", "", books ? item.title || "星探研究手记" : item.full_name || "项目材料"), node("p", "scout-material-description", item.description || item.summary || "项目资料与来源已收录。"));
      const meta = node("div", "scout-material-meta");
      if (books) meta.append(node("span", "", item.date || ""));
      else {
        meta.append(node("span", "", item.stars === null || item.stars === undefined ? "Star 未记录" : `${count(item.stars).toLocaleString("zh-CN")} Star`), node("span", "", item.language || "语言未记录"), node("span", "", item.source_status === "missing" ? "源码文件已缺失" : `已收录 ${count(item.file_count)} 个文件`));
      }
      card.append(meta);
      const footer = node("div", "scout-material-footer");
      if (books) footer.append(link("翻开研究手记 ↗", item.url, true));
      else {
        footer.append(node("span", "", `采集于 ${date(item.collected_at)}`));
        const button = node("button", "text-button", "查看材料 →"); button.type = "button";
        button.addEventListener("click", () => openMaterial(item.id)); footer.append(button);
      }
      card.append(footer); list.append(card);
    }
  }
  function field(list, title, value) {
    if (value === undefined || value === null || value === "") return;
    list.append(node("dt", "", title), node("dd", "", Array.isArray(value) ? value.join("、") : value));
  }
  async function openMaterial(id) {
    const request = ++sceneState.detailRequest;
    const dialog = $("#scout-material-dialog");
    const content = $("#scout-material-detail");
    $("#scout-material-title").textContent = "正在翻开材料…";
    content.replaceChildren(node("p", "", "正在读取项目记录。"));
    if (!dialog.open) dialog.showModal();
    try {
      const result = await host.api(`/api/scout/materials/${encodeURIComponent(id)}`);
      if (request !== sceneState.detailRequest || !dialog.open) return;
      const material = result.material || result;
      $("#scout-material-title").textContent = material.full_name || "项目材料";
      content.replaceChildren();
      content.append(node("p", "scout-detail-description", material.description || ""));
      const links = node("div", "scout-detail-links");
      links.append(link("GitHub 项目 ↗", material.repo_url));
      if (material.sha) links.append(link("本次采集的源码版本 ↗", `${material.repo_url}/tree/${material.sha}`));
      content.append(links);
      const fields = node("dl", "scout-detail-fields");
      field(fields, "方向", material.category);
      field(fields, "作者 / 组织", material.owner?.name || material.owner?.login || (typeof material.owner === "string" ? material.owner : ""));
      field(fields, "公开介绍", material.owner?.bio);
      field(fields, "许可证", material.license);
      field(fields, "主要语言", material.language);
      field(fields, "收录时间", date(material.collected_at));
      field(fields, "固定版本", material.sha);
      field(fields, "源码文件", count(material.file_count));
      field(fields, "当前可用文件", material.files_available);
      field(fields, "材料状态", ({ ok: "已取得源码", partial: "部分源码可用，覆盖不完整", missing: "源码已缺失，仅保留收录记录", failed: "源码获取失败" })[material.source_status] || "待核实");
      content.append(fields);
      if (material.coverage || material.extraction) {
        const scope = node("details", "scout-finding");
        scope.append(node("summary", "", "源码取回与检查范围"));
        if (material.coverage) scope.append(node("p", "", `已静态扫描 ${count(material.coverage.files_scanned)} 个文本文件；扫描结果仍需人工复核。`));
        const reasons = { links_or_special_files: "链接或特殊文件", nonportable_path: "当前系统无法保存的文件名", windows_path_length_limit: "超过 Windows 路径长度的文件", file_size_limit: "超出单个文件大小上限", file_over_4mb: "超出单个文件大小上限", lfs_pointer_only: "只取得大文件引用", submodules_not_fetched: "未取回的子模块", archive_resource_limit: "达到提取数量或大小上限", file_count_limit: "达到文件数量上限" };
        for (const [key, value] of Object.entries(material.extraction?.skipped || {})) scope.append(node("p", "", `${reasons[key] || "其他未取回内容"}：${count(value)}`));
        content.append(scope);
      }
      const risk = material.risk || {};
      const riskSection = node("section", "scout-detail-section");
      riskSection.append(node("h3", "", "风险记录"), riskBadge(material));
      const limitations = arr(risk.limitations);
      riskSection.append(node("p", "", "以下是需要结合代码复核的线索，命中规则本身不代表恶意。"));
      if (limitations.length) {
        const scope = node("details", "scout-finding");
        scope.append(node("summary", "", "本次检查的范围与局限"));
        for (const text of limitations) scope.append(node("p", "", text));
        riskSection.append(scope);
      }
      const findings = arr(material.evidence || material.findings || risk.findings || material.scan?.findings);
      if (!findings.length) riskSection.append(node("p", "", "没有可展示的扫描线索。此记录不代表已完成完整安全审计。"));
      if (findings.length > 30) riskSection.append(node("p", "", `本页展示前 30 条线索，共收录 ${findings.length} 条证据。`));
      for (const finding of findings.slice(0, 30)) {
        const detail = node("details", "scout-finding");
        detail.append(node("summary", "", finding.title || finding.rule || "待复核线索"));
        if (finding.explanation || finding.reason || finding.note) detail.append(node("p", "", finding.explanation || finding.reason || finding.note));
        if (finding.path) detail.append(link(`${finding.path}${finding.line ? ":" + finding.line : ""}`, finding.url));
        if (finding.snippet) detail.append(node("pre", "scout-code", finding.snippet));
        riskSection.append(detail);
      }
      content.append(riskSection);
      const readme = material.readme || material.readme_text;
      if (readme) {
        const section = node("details", "scout-readme");
        section.append(node("summary", "", "查看项目原始说明（待研究材料）"), node("pre", "scout-code scout-readme-text", typeof readme === "string" ? readme : readme.text || ""));
        content.append(section);
      }
      const files = arr(material.files || material.file_list);
      if (files.length) {
        const section = node("details", "scout-readme");
        section.append(node("summary", "", "查看收录时的文件目录"), node("pre", "scout-code", files.map(file => typeof file === "string" ? file : file.path).join("\n")));
        content.append(section);
      }
      content.append(node("p", "scout-safety-note", "这里只展示原始材料和初筛线索。行业解读、核心代码讲解和采用建议，需要后续研究与复核。"));
    } catch (err) {
      if (request !== sceneState.detailRequest) return;
      $("#scout-material-title").textContent = "材料暂时未能打开";
      content.replaceChildren(node("p", "form-error", err.message));
    }
  }
  function renderMap(world, map) {
    if (!world || !map || !window.StarhavenScoutArt) return;
    sceneState.map = { world, map };
    let yard = world.querySelector(".scout-map-yard");
    if (!yard) {
      yard = node("button", "scout-map-yard"); yard.type = "button"; yard.dataset.mapControl = "";
      yard.setAttribute("aria-label", "打开星探的材料架");
      yard.addEventListener("click", () => { selectScene("yard"); host.showView("scout"); });
      world.append(yard);
    }
    yard.style.left = `${map.core[0] + 570}px`; yard.style.top = `${map.core[1] + 370}px`;
    const params = imageParams();
    const signature = JSON.stringify(params);
    if (yard.dataset.signature !== signature) {
      yard.innerHTML = window.StarhavenScoutArt.yard(params);
      yard.append(node("span", "scout-map-label", `材料架 · ${params.materialCount}`));
      yard.dataset.signature = signature;
    }
    const sprite = world.querySelector('.map-resident[data-agent="scout"]');
    if (sprite) {
      sprite.dataset.scoutPhase = phase();
      const mapName = sprite.querySelector(".map-name");
      if (mapName && busy()) mapName.textContent = `星探 · ${phases[phase()]}`;
      else if (mapName) mapName.textContent = host.getState()?.agents?.find(agent => agent.id === "scout")?.name || "星探";
      const bubble = sprite.querySelector(".resident-bubble");
      if (bubble) bubble.textContent = sceneState.data?.job?.message || phases[phase()] || "来观察站看看新发现。";
      let tool = sprite.querySelector(".scout-carried-tool");
      if (busy()) {
        if (!tool) { tool = node("span", "scout-carried-tool"); tool.setAttribute("aria-hidden", "true"); sprite.append(tool); }
        tool.innerHTML = window.StarhavenScoutArt.icon(phase() === "searching" ? "compass" : phase() === "storing" ? "rack" : "cart");
      } else tool?.remove();
    }
  }
  async function refresh() {
    if (!host || sceneState.loading) return;
    clearTimeout(sceneState.timer);
    sceneState.loading = true;
    try {
      const data = await host.api("/api/scout/workspace");
      sceneState.data = data;
      error(""); counts(); renderJob(); renderSettings(); renderScene(); renderLibrary();
      if (sceneState.map) renderMap(sceneState.map.world, sceneState.map.map);
    } catch (err) {
      error(err.message); $("#scout-start").disabled = true;
    } finally {
      sceneState.loading = false;
      sceneState.timer = setTimeout(() => { if (document.hidden) { sceneState.timer = null; return; } refresh(); }, busy() ? 1800 : 15000);
    }
  }
  async function start() {
    if (busy() || sceneState.starting) return;
    sceneState.starting = true; error(""); renderJob();
    try {
      await host.api("/api/scout/collect", { method: "POST", body: {} });
      await refresh();
    } catch (err) { error(err.message); }
    finally { sceneState.starting = false; renderJob(); }
  }
  async function saveSettings(event) {
    event.preventDefault();
    if (sceneState.saving) return;
    sceneState.saving = true; $("#scout-save-schedule").disabled = true;
    try {
      await host.api("/api/scout/settings", { method: "PATCH", body: { daily_enabled: $("#scout-daily-enabled").checked, daily_time: $("#scout-daily-time").value, daily_limit: Number($("#scout-daily-limit").value) } });
      sceneState.dirty = false; host.toast("星探的出发安排已保存。"); error("");
    } catch (err) { error(err.message); }
    finally { sceneState.saving = false; $("#scout-save-schedule").disabled = false; await refresh(); }
  }
  function init(helpers) {
    host = helpers;
    document.querySelectorAll("[data-scout-scene]").forEach(button => button.addEventListener("click", () => selectScene(button.dataset.scoutScene)));
    document.querySelectorAll("[data-scout-library]").forEach(button => button.addEventListener("click", () => selectLibrary(button.dataset.scoutLibrary)));
    $("#scout-start").addEventListener("click", start);
    $("#scout-schedule").addEventListener("submit", saveSettings);
    $("#scout-schedule").addEventListener("input", () => { sceneState.dirty = true; });
    $("#scout-search").addEventListener("input", renderLibrary);
    $("#scout-close-material").addEventListener("click", () => $("#scout-material-dialog").close());
    $("#scout-material-dialog").addEventListener("close", () => { sceneState.detailRequest++; });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
    window.addEventListener("online", refresh);
    refresh();
  }
  window.StarhavenScoutWorkspace = { init, refresh, renderMap };
})();

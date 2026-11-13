"use strict";

(() => {
  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
  const ui = {
    state: null,
    selected: null,
    view: "town",
    jobs: new Map(),
    submitting: new Set(),
    requests: new Map(),
    messages: new Map(),
    drafts: new Map(),
    chatErrors: new Map(),
    dialogMode: "edit",
    dialogAgent: null,
    dialogAvatar: null,
    toastTimer: null,
    stateLoading: false,
    skins: [],
    skin: "meadow",
    skinLoad: 0,
    skinRestored: false,
    skinUserChoice: false,
    skinSaveQueue: Promise.resolve(),
    settingsLoaded: false,
    settingsLoading: false,
    settingsSaving: false,
    modelSettings: null,
  };
  const avatarChoices = [
    { id: "captain", name: "领航队长", color: "#71896a" },
    { id: "scout", name: "旅行研究员", color: "#c5a16f" },
    { id: "maker", name: "手作匠人", color: "#8ba198" },
    { id: "gardener", name: "花园伙伴", color: "#a1b782" },
    { id: "engineer", name: "代码工程师", color: "#718ba1" },
    { id: "musician", name: "摇滚乐手", color: "#a08baf" },
    { id: "architect", name: "蓝图建筑师", color: "#8ba6a6" },
    { id: "rancher", name: "牧场伙伴", color: "#b99d69" },
    { id: "scholar", name: "书卷学者", color: "#a297b5" },
    { id: "artist", name: "田园画家", color: "#bc947d" },
    { id: "angler", name: "钓鱼佬", color: "#5f8f96" },
    { id: "officer", name: "巡街警官", color: "#627e92" },
    { id: "inspector", name: "网络稽查员", color: "#7d8b79" },
  ];
  const avatarFiles = new Set(avatarChoices.map(choice => choice.id));
  const chosenAvatar = agent => avatarFiles.has(agent?.avatar) ? agent.avatar : agent?.id === "scout" ? "scout" : "captain";
  const draftKey = id => `starhaven:draft:${id}`;
  const number = value => Math.max(0, Number(value) || 0);
  const currentAgent = () => ui.state?.agents?.find(agent => agent.id === ui.selected);
  const isBusy = id => ui.jobs.has(id) || ui.submitting.has(id);
  const skinStorageKey = "starhaven:skin";

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function icon(name) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    node.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#i-${name}`);
    node.append(use);
    return node;
  }

  function avatar(agent, className = "") {
    const image = el("img", className);
    const chosen = chosenAvatar(agent);
    image.src = `/assets/${chosen}.svg`;
    image.alt = `${agent.name}的像素形象`;
    return image;
  }

  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch(path, {
        ...options,
        signal: controller.signal,
        cache: "no-store",
        headers: { "Content-Type": "application/json", "X-Town-Client": "pixel-town", ...options.headers },
        body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      });
      let data;
      try { data = await response.json(); } catch { data = {}; }
      if (!response.ok) {
        const error = new Error(data.error || "暂时没有连接上，请稍后再试。");
        error.confirmed = true;
        error.status = response.status;
        throw error;
      }
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("连接有点慢，你的输入已经保留，请再试一次。");
      if (error instanceof TypeError) throw new Error("小镇暂时离线了，你的输入已经保留。");
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }

  function toast(message) {
    clearTimeout(ui.toastTimer);
    $("#toast").textContent = message;
    $("#toast").hidden = false;
    ui.toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 3400);
  }

  async function loadSkins() {
    try {
      const response = await fetch("/skins/manifest.json", { cache: "no-store" });
      if (!response.ok) throw new Error("小镇衣橱暂时没有打开，暖绿皮肤仍可使用。");
      const manifest = await response.json();
      const ids = new Set();
      const skins = (Array.isArray(manifest.skins) ? manifest.skins : []).filter(skin => {
        if (!skin || typeof skin.id !== "string" || !/^[a-z][a-z0-9-]{0,39}$/.test(skin.id)) return false;
        if (ids.has(skin.id) || skin.css !== `/skins/${skin.id}.css`) return false;
        if (typeof skin.name !== "string" || !skin.name.trim() || skin.name.length > 40) return false;
        ids.add(skin.id);
        return true;
      }).slice(0, 20);
      if (!skins.length) throw new Error("没有找到可用皮肤，暖绿皮肤仍可使用。");
      ui.skins = skins;
      const fallback = skins.some(skin => skin.id === manifest.default) ? manifest.default : skins[0].id;
      let saved;
      try { saved = localStorage.getItem(skinStorageKey); } catch { /* The default skin still works. */ }
      renderSkins();
      await applySkin(skins.some(skin => skin.id === saved) ? saved : fallback, false);
      restoreSavedSkin();
    } catch (error) {
      $("#skin-options").replaceChildren(el("p", "settings-help", "当前使用：暖绿原野"));
      $("#skin-error").textContent = error.message;
      $("#skin-error").hidden = false;
    }
  }

  function renderSkins() {
    const container = $("#skin-options");
    container.replaceChildren();
    for (const skin of ui.skins) {
      const button = el("button", "skin-option");
      button.type = "button";
      button.dataset.skinOption = skin.id;
      button.setAttribute("aria-pressed", String(ui.skin === skin.id));
      const preview = el("span", "skin-preview");
      preview.setAttribute("aria-hidden", "true");
      preview.append(el("span", "preview-house"), el("span", "preview-star"));
      const label = el("span", "skin-label");
      label.append(el("strong", "", skin.name), el("small", "", skin.description || "小镇的新风景"));
      button.append(preview, label, icon("check"));
      button.addEventListener("click", () => applySkin(skin.id, true));
      container.append(button);
    }
  }

  async function applySkin(id, persist) {
    const skin = ui.skins.find(item => item.id === id);
    if (!skin) return;
    if (persist) {
      ui.skinUserChoice = true;
      ui.skinRestored = true;
    }
    const request = ++ui.skinLoad;
    $("#skin-error").hidden = true;
    let candidate;
    try {
      // Only registered local CSS is accepted. Skins cannot import styles or contact remote hosts.
      const response = await fetch(skin.css, { cache: "no-store" });
      if (!response.ok) throw new Error("这件皮肤暂时没有准备好，请换一件试试。");
      const css = await response.text();
      if (css.length > 100000 || /@import\b|@namespace\b|(?:url|src|image|image-set|-webkit-image-set)\s*\(|\b(?:https?|data|file|ftp):|\\/i.test(css)) {
        throw new Error("这件皮肤包含不支持的外部资源，请使用小镇内置皮肤。");
      }
      if (request !== ui.skinLoad) return;
      // Use the checked bytes, preventing a stylesheet from changing between validation and loading.
      candidate = el("style");
      candidate.dataset.townSkin = id;
      candidate.textContent = css;
      document.head.append(candidate);
      document.documentElement.dataset.skin = id;
      $$("style[data-town-skin]").forEach(sheet => { if (sheet !== candidate) sheet.remove(); });
      ui.skin = id;
      $$("[data-skin-option]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.skinOption === id)));
      $("meta[name='theme-color']").content = getComputedStyle(document.documentElement).getPropertyValue("--paper").trim() || "#f5f2e9";
      try { localStorage.setItem(skinStorageKey, id); } catch { /* SQLite is the durable preference store. */ }
      if (persist) {
        saveSkinPreference(skin, request);
      }
    } catch (error) {
      candidate?.remove();
      if (request !== ui.skinLoad) return;
      $("#skin-error").textContent = error.message || "皮肤暂时没有加载好，请稍后再试。";
      $("#skin-error").hidden = false;
    }
  }

  function restoreSavedSkin() {
    // Restore once per page load. Background state refreshes must not undo a new choice.
    if (ui.skinRestored || ui.skinUserChoice || !ui.state || !ui.skins.length) return;
    ui.skinRestored = true;
    const saved = ui.skins.find(skin => skin.id === ui.state.selected_skin);
    if (saved) applySkin(saved.id, false);
  }

  function saveSkinPreference(skin, request) {
    // Serial writes keep fast successive selections in the same order as the visible result.
    ui.skinSaveQueue = ui.skinSaveQueue.then(async () => {
      if (ui.skin !== skin.id) return;
      try {
        await api("/api/preferences", { method: "POST", body: { selected_skin: skin.id } });
        if (request === ui.skinLoad) toast(`小镇换上了${skin.name}，下次打开还会保留。`);
      } catch (error) {
        if (request !== ui.skinLoad) return;
        $("#skin-error").textContent = `外观已切换，但暂时未保存。${error.message} 再点一次这件皮肤即可重试。`;
        $("#skin-error").hidden = false;
      }
    });
  }

  function updateModelMode() {
    const apiMode = $("input[name='model-mode']:checked").value === "api";
    $("#api-settings-fields").hidden = !apiMode;
    $("#api-settings-fields").disabled = !apiMode;
    $("#codex-mode-copy").hidden = apiMode;
    $("#model-api-key").disabled = ui.modelSettings?.can_store_api_key === false;
  }

  function openSettings() {
    if (!$("#settings-dialog").open) $("#settings-dialog").showModal();
    if (!ui.settingsLoaded && !ui.settingsLoading) loadModelSettings();
  }

  async function loadModelSettings() {
    if (ui.settingsLoading || ui.settingsSaving) return;
    ui.settingsLoading = true;
    $("#model-settings-fields").disabled = true;
    $("#save-model-settings").disabled = true;
    $("#model-settings-error").hidden = true;
    $("#reload-model-settings").hidden = true;
    $("#model-settings-state").textContent = "正在读取模型设置…";
    try {
      const result = await api("/api/model-settings");
      const settings = result.settings || result;
      ui.modelSettings = {
        mode: settings.mode === "api" ? "api" : "codex",
        has_api_key: !!settings.has_api_key,
        can_store_api_key: settings.can_store_api_key !== false,
      };
      $(`input[name='model-mode'][value='${ui.modelSettings.mode}']`).checked = true;
      $("#model-base-url").value = settings.base_url || "https://api.openai.com/v1";
      $("#model-name").value = settings.model || "";
      $("#model-api-key").value = "";
      $("#model-api-key").placeholder = settings.has_api_key ? "已设置，留空保持原密钥" : "粘贴你的 API 密钥";
      $("#api-key-status").textContent = settings.has_api_key ? "已设置 · 不回填" : "尚未设置";
      $("#api-key-help").textContent = settings.storage_message || "macOS 上密钥存入系统钥匙串，不会回填到页面；其他平台使用环境变量 STARHAVEN_API_KEY。留空可继续使用已有密钥。";
      if (settings.can_store_api_key === false) $("#model-api-key").placeholder = "此平台请通过环境变量配置密钥";
      ui.settingsLoaded = true;
      $("#model-settings-fields").disabled = false;
      $("#save-model-settings").disabled = false;
      $("#model-settings-state").textContent = "设置仅保存在这台电脑，保存后可发一条消息检查连接。";
      updateModelMode();
    } catch (error) {
      $("#model-settings-error").textContent = error.message;
      $("#model-settings-error").hidden = false;
      $("#reload-model-settings").hidden = false;
      $("#model-settings-state").textContent = "模型设置尚未读取，换装和角色档案仍可使用。";
    } finally { ui.settingsLoading = false; }
  }

  async function saveModelSettings(event) {
    event.preventDefault();
    if (!ui.settingsLoaded || ui.settingsSaving) return;
    const body = {
      mode: $("input[name='model-mode']:checked").value,
      base_url: $("#model-base-url").value.trim() || "https://api.openai.com/v1",
      model: $("#model-name").value.trim(),
    };
    if (body.mode === "api" && !body.model) { $("#model-name").focus(); return; }
    if (body.mode === "api" && !$("#model-api-key").disabled && $("#model-api-key").value.trim()) {
      body.api_key = $("#model-api-key").value.trim();
    }
    $("#model-api-key").value = "";
    ui.settingsSaving = true;
    $("#save-model-settings").disabled = true;
    $("#model-settings-fields").disabled = true;
    $("#model-settings-error").hidden = true;
    $("#model-settings-state").textContent = "正在保存连接设置…";
    try {
      const result = await api("/api/model-settings", { method: "POST", body });
      const settings = result.settings || result;
      ui.modelSettings = { ...ui.modelSettings, mode: body.mode, has_api_key: !!settings.has_api_key };
      $("#api-key-status").textContent = settings.has_api_key ? "已设置 · 不回填" : "尚未设置";
      $("#model-api-key").placeholder = settings.has_api_key ? "已设置，留空保持原密钥" : (ui.modelSettings.can_store_api_key ? "粘贴你的 API 密钥" : "此平台请通过环境变量配置密钥");
      $("#model-settings-state").textContent = "连接设置已保存。给伙伴发一条消息，就能检查是否连接成功。";
      await loadState();
      toast("连接设置已保存，去和伙伴聊聊吧。");
    } catch (error) {
      $("#model-settings-error").textContent = error.message;
      $("#model-settings-error").hidden = false;
      $("#model-settings-state").textContent = "地址和模型名称已保留。若本次填写了密钥，请重新输入后再试。";
    } finally {
      delete body.api_key;
      ui.settingsSaving = false;
      $("#save-model-settings").disabled = false;
      $("#model-settings-fields").disabled = false;
      updateModelMode();
    }
  }

  function saveDraft(id, value) {
    if (!id) return;
    ui.drafts.set(id, value);
    try { sessionStorage.setItem(draftKey(id), value); } catch { /* Draft remains in memory. */ }
  }

  function getDraft(id) {
    if (ui.drafts.has(id)) return ui.drafts.get(id);
    try { return sessionStorage.getItem(draftKey(id)) || ""; } catch { return ""; }
  }

  function requestId() {
    return window.crypto?.randomUUID ? window.crypto.randomUUID() : `town-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }

  function showView(name) {
    if (!["town", "scout", "police", "inspection", "tasks", "growth"].includes(name)) return;
    ui.view = name;
    $$("[data-view]").forEach(button => {
      const selected = button.dataset.view === name;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    $$(".view").forEach(section => { section.hidden = section.id !== `view-${name}`; });
    if (name === "town" && typeof mapView !== "undefined") {
      // 若地图曾在隐藏状态渲染（视口宽度为 0），切回小镇时补一次定位；已定位过则保留浏览位置
      applyMapTransform();
    }
    history.replaceState(null, "", `#${name}`);
    if (name === "scout") {
      window.StarhavenScoutWorkspace?.refresh();
      if (ui.state && ui.selected !== "scout" && ui.state.agents.some(agent => agent.id === "scout")) selectAgent("scout");
    }
    const securityAgent = window.StarhavenSecurityWorkspace?.agentForOffice(name);
    if (securityAgent) {
      window.StarhavenSecurityWorkspace.open(name);
      if (ui.state?.agents.some(agent => agent.id === securityAgent) && ui.selected !== securityAgent) selectAgent(securityAgent);
    }
    if (window.innerWidth <= 800) $(".main-content").scrollIntoView({ behavior: reducedMotion() ? "instant" : "smooth" });
  }

  function reducedMotion() { return matchMedia("(prefers-reduced-motion: reduce)").matches; }

  function showChatError(agentId, message) {
    if (message) ui.chatErrors.set(agentId, message);
    else ui.chatErrors.delete(agentId);
    if (agentId !== ui.selected) return;
    const alert = $("#chat-alert");
    alert.replaceChildren();
    alert.hidden = !message;
    if (message) alert.append(el("span", "", message));
  }

  async function loadState({ initial = false } = {}) {
    if (ui.stateLoading) return;
    ui.stateLoading = true;
    try {
      const data = await api("/api/state");
      if (!Array.isArray(data.agents)) throw new Error("小镇的居民资料暂时没有读取完整，请重新连接。");
      ui.state = data;
      restoreSavedSkin();
      const previous = ui.selected;
      if (initial && ui.view === "scout" && data.agents.some(agent => agent.id === "scout")) ui.selected = "scout";
      const securityAgent = window.StarhavenSecurityWorkspace?.agentForOffice(ui.view);
      if (initial && securityAgent && data.agents.some(agent => agent.id === securityAgent)) ui.selected = securityAgent;
      if (!data.agents.some(agent => agent.id === ui.selected)) {
        ui.selected = data.agents.some(agent => agent.id === data.selected_agent_id) ? data.selected_agent_id : data.agents[0]?.id || null;
      }
      renderState();
      $("#connection-banner").hidden = true;
      if (initial || previous !== ui.selected) {
        $("#chat-input").value = getDraft(ui.selected);
        updateComposer();
        if (ui.selected) await loadMessages(ui.selected);
      }
    } catch (error) {
      $("#connection-error").textContent = error.message;
      $("#connection-banner").hidden = false;
      if (!ui.state) {
        $("#selected-profile").replaceChildren(el("div", "profile-loading", "小镇还在连接中，稍等一下。"));
        $("#provider-status").replaceChildren(el("span", "status-dot"), el("span", "", "暂时离线 · 重新连接后就能见面"));
        $("#provider-status").classList.add("offline");
      }
    } finally { ui.stateLoading = false; }
  }

  function renderState() {
    const { agents = [], town = {}, provider = {}, team = {} } = ui.state;
    $("#town-name").textContent = town.name || "星栖小镇";
    document.title = `${town.name || "星栖小镇"} · 和智能体一起成长`;
    $("#resident-count").textContent = `${agents.length} 位伙伴在这里`;
    $("#panel-resident-count").textContent = `${agents.length} 位居民`;
    const teamLabel = team.label || "领航陪你规划工作，星探继续探索新发现。";
    $("#team-status").textContent = teamLabel;
    $("#task-mode-label").textContent = teamLabel;
    const connected = provider.status === "connected";
    $("#provider-status").classList.toggle("offline", !provider.available);
    $("#provider-status").replaceChildren(el("span", "status-dot"), el("span", "", connected ? "真实对话已连接 · 让灵感在这里发生" : (provider.available ? "伙伴已就位 · 发条消息认识一下" : (provider.message || "伙伴暂时无法回复 · 你的输入会保留"))));
    $("#provider-status").title = (provider.message || provider.label || "") + " 对话使用所选模型服务的额度或计费，可在小镇设置中修改。";
    renderResidents();
    renderProfile();
    renderWork();
    renderGrowth();
    updateMap();
    updateComposer();
  }

  function renderResidents() {
    const list = $("#resident-list");
    const signature = JSON.stringify(ui.state.agents.map(({ id, name, avatar }) => [id, name, avatar]));
    if (ui.residentsSignature !== signature) {
      const focused = document.activeElement?.dataset.resident;
      list.replaceChildren();
      for (const agent of ui.state.agents) {
        const button = el("button", "resident-chip");
        button.type = "button";
        button.dataset.resident = agent.id;
        button.setAttribute("aria-label", `与${agent.name}对话，找到住处`);
        button.append(avatar(agent), el("span", "", agent.name));
        button.addEventListener("click", () => { selectAgent(agent.id); if (ui.view === "town") revealResident(agent.id); });
        list.append(button);
        if (focused === agent.id) button.focus({ preventScroll: true });
      }
      ui.residentsSignature = signature;
    }
    $$("[data-resident]", list).forEach(button => {
      const selected = button.dataset.resident === ui.selected;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
  }

  function renderProfile() {
    const agent = currentAgent();
    if (!agent) return;
    const container = $("#selected-profile");
    const top = el("div", "profile-top");
    const portrait = el("div", "profile-avatar");
    portrait.append(avatar(agent));
    const details = el("div");
    const heading = el("div", "profile-name");
    heading.append(el("h2", "", agent.name), el("span", "", agent.id === "linghang" ? "小镇队长" : agent.id === "scout" ? "探索伙伴" : "小镇居民"));
    details.append(heading, el("p", "profile-role", agent.role || "一起成长的伙伴"));
    top.append(portrait, details);
    const status = el("div", "profile-status");
    status.append(el("span", "tiny-dot"), el("span", "", agent.work_status || "随时听听你的新想法"));
    const home = el("button", "profile-home");
    home.type = "button";
    home.setAttribute("aria-label", `前往${agent.house?.name || agent.home || "伙伴的家"}`);
    const homeCopy = el("span", "profile-home-copy");
    homeCopy.append(el("strong", "", agent.house?.name || agent.home || "伙伴的家"));
    if (agent.house?.feature) homeCopy.append(el("small", "", agent.house.feature));
    home.append(homeCopy, icon("arrow"));
    home.addEventListener("click", () => {
      const office = window.StarhavenSecurityWorkspace?.officeForAgent(agent.id);
      if (office) showView(office);
      else if (agent.id === "scout") showView("scout");
      else { showView("town"); revealResident(agent.id); }
    });
    container.replaceChildren(top, el("p", "profile-personality", agent.personality || agent.bio || "每次相处，都多了解彼此一点。"), home, status);
    $("#conversation-title").textContent = `和${agent.name}聊聊`;
    $("#edit-profile").disabled = agent.editable === false;
    $("#chat-input").placeholder = `告诉${agent.name}，你在想什么…`;
    renderSuggestions();
  }

  function buildingLabel(agent) {
    const label = el("span", "building-label");
    const dot = el("span", "tiny-dot");
    dot.style.background = agent.house?.palette?.accent || "#8a9a72";
    label.append(dot, el("span", "", agent.house?.name || agent.home || "小屋"), el("span", "label-arrow", "↗"));
    return label;
  }

  function residentSprite(agent) {
    const button = el("button", `map-resident${agent.id === ui.selected ? " selected" : ""}`);
    button.type = "button";
    button.dataset.agent = agent.id;
    button.setAttribute("aria-label", `与${agent.name}对话`);
    button.setAttribute("aria-pressed", String(agent.id === ui.selected));
    button.append(el("span", "resident-bubble", "在这里，随时可以聊聊"), avatar(agent), el("span", "map-name", agent.name));
    button.addEventListener("click", () => selectAgent(agent.id, true));
    return button;
  }

  // Scenery shares the original town's small pixel steps and muted material palette.
  const SVG_NS = "http://www.w3.org/2000/svg";
  function svgNode(tag, attrs = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  }
  const pixelRect = (x, y, width, height, fill) => svgNode("rect", { x, y, width, height, fill });
  const pixelPath = (d, fill) => svgNode("path", { d, fill });
  function shade(hex, amount) {
    const color = /^#[0-9a-f]{6}$/i.test(hex) ? hex : "#bba781";
    const target = amount < 0 ? [70, 82, 60] : [244, 240, 224];
    return "#" + [1, 3, 5].map((start, index) => {
      const value = parseInt(color.slice(start, start + 2), 16);
      return Math.round(value + (target[index] - value) * Math.abs(amount)).toString(16).padStart(2, "0");
    }).join("");
  }
  function pixelTree(x, y, kind = 0, fruit = false) {
    const tree = svgNode("g", { transform: `translate(${x} ${y})` });
    const palette = kind % 3 === 1 ? ["#708952", "#8fa66a", "#a4b67c", "#c1cf92"] : kind % 3 === 2 ? ["#82955b", "#a2b277", "#bcc58c", "#d4d79e"] : ["#6d8b59", "#88a46c", "#a0b77d", "#b7c98d"];
    tree.append(pixelRect(-26, 49, 58, 8, "#8ea66b38"), pixelRect(-4, 10, 12, 43, "#927c54"),
      pixelRect(4, 17, 5, 34, "#7b6c49"), pixelRect(-10, 32, 8, 7, "#927c54"), pixelRect(8, 25, 8, 6, "#7b6c49"),
      pixelPath("M-30 5h-8v-22h8v-16h14v-11h28v6h15v12h10v29h-8v15H10v7h-24V17h-16Z", palette[0]),
      pixelPath("M-31-17h8v-14h14v-8h20v7h15v12h6V3H18v11H-5V8h-19V0h-7Z", palette[1]),
      pixelPath("M-23-20h8v-11h23v6h14v11H12v9H-8v-6h-15Z", palette[2]),
      pixelRect(-10, -31, 10, 5, palette[3]), pixelRect(2, -26, 7, 5, palette[3]),
      pixelRect(-29, -12, 5, 7, palette[2]), pixelRect(19, -4, 6, 8, palette[2]), pixelRect(-11, 8, 7, 4, palette[2]));
    if (fruit) [[-18, -12], [8, -18], [18, 2], [-5, 4]].forEach(([fx, fy], index) => {
      tree.append(pixelRect(fx, fy, 6, 7, index % 2 ? "#d6ac72" : "#c99170"), pixelRect(fx, fy, 3, 3, "#e8c18a"), pixelRect(fx + 2, fy - 2, 2, 2, "#718951"));
    });
    return tree;
  }
  function pixelFlower(x, y, color = "#d9ad7f") {
    const flower = svgNode("g", { transform: `translate(${x} ${y})` });
    flower.append(pixelRect(2, 6, 2, 8, "#90a574"), pixelRect(-1, 10, 3, 3, "#a1b77d"),
      pixelRect(0, 2, 6, 6, color), pixelRect(2, 0, 2, 10, color), pixelRect(2, 4, 2, 2, "#f3df9a"));
    return flower;
  }
  function pixelShrub(x, y) {
    const shrub = svgNode("g", { transform: `translate(${x} ${y})` });
    shrub.append(pixelPath("M0 12h5V5h8V0h18v5h9v7h5v13h-7v5H7v-5H0Z", "#78935a"),
      pixelPath("M5 10h8V4h15v6h9v10H24v5H9v-5H5Z", "#97ae70"),
      pixelRect(13, 6, 10, 5, "#b2c285"), pixelRect(30, 15, 5, 4, "#a8bd7d"));
    return shrub;
  }
  function pixelFence(x, y, length, gap = false) {
    const fence = svgNode("g", { transform: `translate(${x} ${y})` });
    const segments = gap ? [[0, length / 2 - 22], [length / 2 + 22, length / 2 - 22]] : [[0, length]];
    for (const [start, width] of segments) {
      fence.append(pixelRect(start, 9, width, 4, "#b09b74"), pixelRect(start, 9, width, 2, "#d0bd91"), pixelRect(start, 20, width, 4, "#aa946d"));
      for (let px = start; px <= start + width - 6; px += 24) {
        fence.append(pixelRect(px, 4, 6, 25, "#ab916b"), pixelRect(px, 4, 3, 23, "#d1bc8e"), pixelRect(px + 1, 2, 4, 3, "#d9c79d"));
      }
    }
    return fence;
  }

  function plotOrigin(agent, map) {
    if (!agent.house?.painted) return agent.plot.pos;
    // Keep the original village landmarks and saved plot coordinates intact.
    // Only their artwork changes: the doorway sits just north of the main road.
    const r = agent.house.rect;
    const snap = value => Math.round(value / 2) * 2;
    return [snap(map.core[0] + (r.x + r.w / 2) * map.core[2] - 160),
      snap(map.core[1] + (r.y + r.h) * map.core[3] - 200)];
  }

  function houseLights(art) {
    // Keep the exact ancestor transforms of each pane while placing its light
    // outside the night tint. No blur or glow can soften the pixel outlines.
    const light = art.cloneNode(true);
    function prune(node) {
      node.removeAttribute("data-pixel-art");
      for (const child of [...node.children]) {
        if (child.matches(".house-window-light,.house-window-glint,.house-lamp-light")) continue;
        prune(child);
        if (!child.children.length) child.remove();
      }
    }
    prune(light);
    return light;
  }

  function plotSvg(agent, map) {
    // WKWebView reliably applies CSS filters to root SVGs, but not nested
    // SVG groups. Keep both layers in the same unscaled plot coordinate space.
    const attributes = { viewBox: "0 0 320 280", width: 320, height: 280, "shape-rendering": "crispEdges", "aria-hidden": "true" };
    const ground = svgNode("svg", { ...attributes, class: "plot-svg plot-daylight-art" });
    const variant = (agent.house?.variant || 0) % 4;
    const layout = window.StarhavenHouseArt.layout(agent);
    if (!agent.house?.painted) {
      // Residential lawns stay inside their existing plots. The two landmarks
      // use the village's original ground, river and paths without covering it.
      ground.append(pixelPath("M20 24h68V12h134v8h62v20h20v150h-14v25h-62v11H78v-9H28v-21H12V58h8Z", "#d2e1b1"),
        pixelPath("M34 42h76V28h101v12h67v132h-22v35H92v-13H34Z", "#d9e6bd"));
      const rand = mulberry32(81 + variant);
      for (let i = 0; i < 46; i++) {
        const x = 16 + Math.floor(rand() * 94) * 3, y = 21 + Math.floor(rand() * 66) * 3;
        ground.append(pixelRect(x, y, 3, 3, i % 4 ? "#c3d69f" : "#e3ebca"));
      }
      ground.append(pixelRect(layout.doorstep.x - 12, layout.doorstep.y, 24, 280 - layout.doorstep.y, "#dfd1a9"),
        pixelRect(layout.doorstep.x - 9, layout.doorstep.y, 18, 280 - layout.doorstep.y, "#e7ddbb"));
    } else {
      const [, originY] = plotOrigin(agent, map);
      const streetY = map.core[1] + 380;
      const pathEnd = Math.min(280, streetY - originY);
      if (pathEnd > layout.doorstep.y) {
        ground.append(pixelRect(layout.doorstep.x - 12, layout.doorstep.y, 24, pathEnd - layout.doorstep.y, "#c9c69e"),
          pixelRect(layout.doorstep.x - 9, layout.doorstep.y, 18, pathEnd - layout.doorstep.y, "#e7ddbb"));
      }
    }
    const house = window.StarhavenHouseArt.house(agent), yard = window.StarhavenHouseArt.yard(agent);
    ground.append(house, yard);
    const lights = svgNode("svg", { ...attributes, class: "plot-svg plot-night-lights" });
    lights.append(houseLights(house), houseLights(yard));
    const layers = document.createDocumentFragment();
    layers.append(ground, lights);
    return layers;
  }

  function renderPlot(agent, map) {
    if (!map || !agent.plot?.pos) return null;
    const layout = window.StarhavenHouseArt.layout(agent);
    const [x, y] = plotOrigin(agent, map);
    const plot = el("div", `plot${agent.house?.painted ? " painted" : ""}${agent.id === ui.selected ? " selected" : ""}`);
    plot.dataset.residentHome = agent.id;
    plot.style.left = `${x}px`; plot.style.top = `${y}px`;
    plot.style.width = "320px"; plot.style.height = "280px";
    plot.append(plotSvg(agent, map));
    const hit = el("button", "home-hit");
    hit.type = "button"; hit.dataset.agent = agent.id;
    hit.style.left = `${layout.hit.x}px`; hit.style.top = `${layout.hit.y}px`;
    hit.style.width = `${layout.hit.width}px`; hit.style.height = `${layout.hit.height}px`;
    const office = window.StarhavenSecurityWorkspace?.officeForAgent(agent.id);
    hit.setAttribute("aria-label", office ? `进入${agent.house.name || agent.home}，查看巡检与日报` : agent.id === "scout" ? "进入星探的观察站，查看材料架和资料书架" : `进入${agent.house.name || agent.home}，与${agent.name}对话`);
    const label = buildingLabel(agent);
    label.style.left = `${layout.label.x - layout.hit.x}px`;
    label.style.top = `${layout.label.y - layout.hit.y}px`;
    hit.append(label);
    hit.addEventListener("click", () => {
      if (office) { selectAgent(agent.id); showView(office); }
      else if (agent.id === "scout") { selectAgent(agent.id); showView("scout"); }
      else selectAgent(agent.id, true);
    });
    plot.append(hit);
    if (agent.id !== "scout" && !office) {
      const sprite = residentSprite(agent);
      sprite.style.left = `${layout.resident.x}px`; sprite.style.top = `${layout.resident.y}px`;
      plot.append(sprite);
    }
    return plot;
  }

  const mapView = { x: 0, y: 0, scale: 1, ready: false, drag: null, frame: null, layout: null, background: null, plots: new Map(), canvasWidth: 0, canvasHeight: 0 };

  const mapOrigin = map => Array.isArray(map?.origin) ? map.origin : [0, 0];

  function applyMapTransform() {
    const scene = $("#town-scene"), world = $("#map-world"), map = ui.state?.map;
    if (!scene || !world || !map || !scene.clientWidth || !scene.clientHeight) return;
    const width = scene.clientWidth, height = scene.clientHeight;
    // One scale for both axes keeps tree crowns, characters and pixels square.
    const scale = Math.min(width / map.core[2], height / map.core[3]);
    if (!mapView.ready) {
      mapView.x = width / 2 - (map.core[0] + map.core[2] / 2 - mapOrigin(map)[0]) * scale;
      mapView.y = height / 2 - (map.core[1] + map.core[3] / 2 - mapOrigin(map)[1]) * scale;
      mapView.ready = true;
    } else if (width !== mapView.canvasWidth || height !== mapView.canvasHeight) {
      const centerX = (mapView.canvasWidth / 2 - mapView.x) / mapView.scale;
      const centerY = (mapView.canvasHeight / 2 - mapView.y) / mapView.scale;
      mapView.x = width / 2 - centerX * scale;
      mapView.y = height / 2 - centerY * scale;
    }
    mapView.scale = scale; mapView.canvasWidth = width; mapView.canvasHeight = height;
    mapView.x = Math.round(Math.min(0, Math.max(width - map.world[0] * scale, mapView.x)));
    mapView.y = Math.round(Math.min(0, Math.max(height - map.world[1] * scale, mapView.y)));
    world.style.transform = `translate(${mapView.x}px, ${mapView.y}px) scale(${scale})`;
    world.style.setProperty("--map-ui-scale", String(Math.max(1, 1 / scale)));
    const homeX = width / 2 - (map.core[0] + map.core[2] / 2 - mapOrigin(map)[0]) * scale;
    const homeY = height / 2 - (map.core[1] + map.core[3] / 2 - mapOrigin(map)[1]) * scale;
    scene.classList.toggle("panned", Math.abs(mapView.x - homeX) > 45 || Math.abs(mapView.y - homeY) > 45);
  }

  function scheduleMapTransform() {
    if (mapView.frame !== null) return;
    mapView.frame = requestAnimationFrame(() => { mapView.frame = null; applyMapTransform(); });
  }

  function returnToVillage() {
    window.StarhavenScoutWorkspace?.stopFollowing();
    mapView.ready = false;
    applyMapTransform();
  }

  function centerMap(point) {
    const scene = $("#town-scene"), map = ui.state?.map;
    if (!scene?.clientWidth || !map) return;
    mapView.x = scene.clientWidth / 2 - (point[0] - mapOrigin(map)[0]) * mapView.scale;
    mapView.y = scene.clientHeight / 2 - (point[1] - mapOrigin(map)[1]) * mapView.scale;
    mapView.ready = true;
    applyMapTransform();
  }

  function revealResident(id) {
    if (id === "scout") { window.StarhavenScoutWorkspace?.locate(); return; }
    const office = window.StarhavenSecurityWorkspace?.officeForAgent(id);
    if (office) { window.StarhavenSecurityWorkspace.locate(office); return; }
    const agent = ui.state?.agents.find(item => item.id === id), scene = $("#town-scene"), map = ui.state?.map;
    if (!agent?.plot?.pos || !map || !scene.clientWidth) return;
    const [x, y] = plotOrigin(agent, map);
    const layout = window.StarhavenHouseArt.layout(agent);
    centerMap([x + layout.resident.x, y + layout.resident.y + 35]);
  }

  function initMapPan() {
    const scene = $("#town-scene");
    if (!scene || scene.dataset.panReady) return;
    scene.dataset.panReady = "1";
    window.addEventListener("keydown", event => { if (event.key === "Tab") mapView.keyboardNavigation = true; }, true);
    scene.addEventListener("pointerdown", () => { mapView.keyboardNavigation = false; }, true);
    $("#map-home").addEventListener("click", returnToVillage);
    scene.addEventListener("dragstart", event => event.preventDefault());
    scene.addEventListener("pointerdown", event => {
      if (event.button !== 0 || !event.isPrimary || event.target.closest("[data-map-control]")) return;
      mapView.drag = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, baseX: mapView.x, baseY: mapView.y, moved: false };
    });
    window.addEventListener("pointermove", event => {
      const drag = mapView.drag;
      if (!drag || drag.pointerId !== event.pointerId) return;
      const dx = event.clientX - drag.startX, dy = event.clientY - drag.startY;
      if (!drag.moved && Math.hypot(dx, dy) < 7) return;
      if (!drag.moved) {
        drag.moved = true;
        window.StarhavenScoutWorkspace?.stopFollowing();
        scene.classList.add("grabbing");
        // Capture only after a drag begins; a simple click keeps its real button target.
        try { scene.setPointerCapture(event.pointerId); } catch { /* Window events still follow the pointer. */ }
      }
      mapView.x = drag.baseX + dx; mapView.y = drag.baseY + dy;
      scheduleMapTransform();
    });
    const endDrag = event => {
      const drag = mapView.drag;
      if (!drag || (event.pointerId !== undefined && drag.pointerId !== event.pointerId)) return;
      mapView.drag = null;
      scene.classList.remove("grabbing");
      if (drag.moved) {
        ui.suppressClickUntil = Date.now() + 300;
        applyMapTransform();
      }
      try { if (scene.hasPointerCapture(drag.pointerId)) scene.releasePointerCapture(drag.pointerId); } catch { /* Already released. */ }
    };
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", endDrag);
    window.addEventListener("blur", endDrag);
    scene.addEventListener("lostpointercapture", endDrag);
    scene.addEventListener("click", event => {
      if (event.detail !== 0 && Date.now() < (ui.suppressClickUntil || 0)) {
        event.stopPropagation(); event.preventDefault();
      }
    }, true);
    scene.addEventListener("keydown", event => {
      if (event.key === "Escape") { endDrag(event); return; }
      if (event.target !== scene || event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.key === "Home") { event.preventDefault(); returnToVillage(); return; }
      const directions = { ArrowLeft: [1, 0], ArrowRight: [-1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] };
      if (!directions[event.key]) return;
      event.preventDefault();
      window.StarhavenScoutWorkspace?.stopFollowing();
      const [dx, dy] = directions[event.key], distance = event.shiftKey ? 180 : 72;
      mapView.x += dx * distance; mapView.y += dy * distance;
      applyMapTransform();
    });
    scene.addEventListener("focusin", event => {
      const button = event.target.closest("[data-agent]");
      if (!button || !mapView.keyboardNavigation) return;
      scene.scrollLeft = 0; scene.scrollTop = 0;
      const viewport = scene.getBoundingClientRect(), box = button.getBoundingClientRect();
      if (box.left < viewport.left + 12 || box.right > viewport.right - 12 || box.top < viewport.top + 44 || box.bottom > viewport.bottom - 48) revealResident(button.dataset.agent);
    });
    if (typeof ResizeObserver === "function") new ResizeObserver(scheduleMapTransform).observe(scene);
    else window.addEventListener("resize", scheduleMapTransform);
  }

  function mulberry32(seed) {
    let a = seed;
    return function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function meadowPlot(index, x, y) {
    const patch = svgNode("g", { transform: `translate(${x} ${y})` });
    const put = (...items) => patch.append(...items);
    const colors = ["#ddaf8a", "#dcc38a", "#d6ae8c", "#b8adc0"];
    const rand = mulberry32(index * 17 + 61);
    const kind = index % 5;
    if (kind === 0) {
      // Orchard: the exact same layered crowns as the original village trees.
      put(pixelPath("M34 76h50V54h143v17h46v95h-18v24H63v-14H34Z", "#cedfae"));
      [[69, 68], [178, 82], [253, 57], [109, 150]].forEach(([tx, ty], i) => put(pixelTree(tx, ty, i, true)));
      put(pixelFence(198, 174, 91), pixelRect(215, 199, 24, 17, "#b79d73"), pixelRect(215, 202, 24, 3, "#d4bd8e"), pixelRect(218, 196, 6, 5, "#c7926e"), pixelRect(229, 196, 6, 5, "#d4a972"));
    } else if (kind === 1) {
      // Individual seedlings and broken soil edges, never flat green stripes.
      put(pixelPath("M47 48h210v9h12v124h-14v11H55v-9H40V65h7Z", "#d8c6a0"));
      for (let row = 0; row < 4; row++) {
        const yy = 63 + row * 30, xx = 52 + row % 2 * 5;
        put(pixelRect(xx, yy, 202, 20, "#b99f78"), pixelRect(xx + 3, yy, 194, 3, "#cbb18a"), pixelRect(xx + 6, yy + 18, 187, 2, "#ad936e"));
        for (let col = 0; col < 8; col++) {
          const px = xx + 10 + col * 24;
          put(pixelRect(px, yy + 12, 9, 3, "#a48e66"), pixelRect(px + 3, yy + 5, 3, 10, "#7b975f"),
            pixelRect(px - 2, yy + 2, 6, 6, "#99b178"), pixelRect(px + 5, yy, 6, 6, "#8faa6d"), pixelRect(px - 1, yy + 2, 3, 2, "#b8c98f"));
          if (row % 2) put(pixelRect(px + 4, yy + 12, 3, 4, "#cba46e"));
        }
      }
      put(pixelFence(38, 31, 241), pixelRect(278, 144, 3, 44, "#9a8965"), pixelRect(273, 143, 13, 6, "#b4b6a0"), pixelRect(274, 137, 2, 7, "#9fa68e"), pixelRect(280, 137, 2, 7, "#9fa68e"));
    } else if (kind === 2) {
      put(pixelPath("M52 66h84V43h89v16h31v82h-21v45h-78v-13H58v-29H40V88h12Z", "#cedfae"));
      for (let i = 0; i < 44; i++) {
        const fx = 50 + Math.floor(rand() * 192), fy = 54 + Math.floor(rand() * 121);
        put(pixelFlower(fx, fy, colors[i % colors.length]));
      }
      put(pixelTree(267, 152, 1), pixelFence(38, 196, 151));
    } else if (kind === 3) {
      // Four stepped shorelines share the original river's water and bank colors.
      put(pixelPath("M73 53h45V42h68v11h45v14h25v22h13v57h-12v21h-28v14h-70v11h-49v-13H78v-15H57v-25H45V96h13V72h15Z", "#b3c397"),
        pixelPath("M78 62h45V51h58v11h44v14h23v20h12v43h-12v22h-26v12h-65v10h-42v-12H84v-15H64v-23H54v-30h13V80h11Z", "#d2d7b4"),
        pixelPath("M83 70h45V59h48v11h44v13h20v20h12v30h-12v23h-24v9h-62v10h-34v-12H90v-14H72v-23H62v-16h13V87h8Z", "#8fb5ae"),
        pixelPath("M91 80h43V68h35v11h43v13h20v21h12v16h-12v20h-23v9h-58v9h-24v-12H99v-14H81v-23H72v-4h12V93h7Z", "#a6cbca"),
        pixelPath("M106 85h37V77h22v9h38v15h22v25h-16v15h-68v9h-28v-15H91v-28h15Z", "#b6d6d1"));
      [[105, 94, 25], [162, 118, 32], [120, 143, 18], [197, 96, 16]].forEach(([rx, ry, w]) => put(pixelRect(rx, ry, w, 3, "#d4e7d9"), pixelRect(rx + 7, ry + 7, w - 10, 2, "#c5e0d4")));
      [[73, 72], [246, 142], [93, 173]].forEach(([rx, ry]) => put(pixelRect(rx, ry - 14, 3, 22, "#849d70"), pixelRect(rx + 6, ry - 20, 3, 27, "#a1b480"), pixelRect(rx + 5, ry - 23, 5, 8, "#a58f64")));
      put(pixelPath("M188 137h12v4h6v6h-10v4h-13v-8h5Z", "#8da982"), pixelRect(194, 138, 4, 4, "#d5b794"), pixelTree(274, 50, 0));
    } else {
      put(pixelTree(65, 72, 1), pixelTree(240, 144, 2), pixelShrub(142, 159), pixelShrub(180, 172),
        pixelPath("M91 172h21v5h8v16H85v-13h6Z", "#b4b79a"), pixelRect(91, 175, 19, 5, "#d0cfb3"), pixelRect(111, 180, 8, 11, "#a4a98c"));
      for (let i = 0; i < 7; i++) put(pixelFlower(115 + i * 12, 73 + i % 3 * 10, colors[i % 4]));
    }
    return patch;
  }

  function worldBackground(map) {
    const [width, height] = map.world;
    const svg = svgNode("svg", { class: "world-bg", viewBox: `${mapOrigin(map)[0]} ${mapOrigin(map)[1]} ${width} ${height}`, width, height, "shape-rendering": "crispEdges", "aria-hidden": "true" });
    svg.style.top = `${mapOrigin(map)[1]}px`;
    svg.style.left = `${mapOrigin(map)[0]}px`;
    svg.append(pixelRect(mapOrigin(map)[0], mapOrigin(map)[1], width, height, "#d6e4b8"));
    const rand = mulberry32(20261110);
    for (let i = 0; i < 70; i++) {
      const x = Math.floor(rand() * width / 6) * 6 + mapOrigin(map)[0], y = Math.floor(rand() * height / 6) * 6 + mapOrigin(map)[1];
      const patch = pixelPath("M0 12h18V0h42v6h24v12h12v24H78v12H36v-6H12V36H0Z", i % 2 ? "#cedfae" : "#d0e0b0");
      patch.setAttribute("transform", `translate(${x} ${y})`); svg.append(patch);
    }
    // Sparse three-pixel flecks match the density of town.svg, with no square lawn borders.
    const flecks = ["#c3d69f", "#bfcf9d", "#e3ebca", "#c9dca9"];
    const [originX, originY] = mapOrigin(map);
    for (let i = 0; i < 2100; i++) {
      const x = Math.floor(rand() * width / 3) * 3 + originX, y = Math.floor(rand() * height / 3) * 3 + originY;
      if (x > map.core[0] && x < map.core[0] + map.core[2] && y > map.core[1] && y < map.core[1] + map.core[3]) continue;
      svg.append(pixelRect(x, y, 3, 3, flecks[i % 4]));
      if (i % 3 === 0) svg.append(pixelRect(x + 6, y + 3, 3, 3, flecks[i % 4]));
    }
    // A street is a two-way road: pavement, kerbs, and a dashed centre line that
    // separates the two lanes. Width comes from the server so the lane count and
    // the patrol logic cannot disagree about where the road is.
    const roadWidth = map.road?.width || 64, verge = map.road?.verge || 18;
    const street = (x, y, w, h) => {
      svg.append(pixelRect(x, y, w, h, "#dfd1a9"));
      const vertical = h > w;
      if (vertical) {
        // Kerbs run down the two long sides; the centre line splits the lanes.
        svg.append(pixelRect(x, y, 2, h, "#e7ddbb"), pixelRect(x + w - 2, y, 2, h, "#d0c197"));
        const middle = x + Math.round(w / 2) - 1;
        for (let step = 14; step < h - 14; step += 30) {
          svg.append(pixelRect(middle, y + step, 2, Math.min(14, y + h - (y + step)), "#c9b98e"));
        }
        for (let step = 20; step < h - 20; step += 46) {
          svg.append(pixelRect(x + 6, y + step, 2, 5, "#e9dfbd"), pixelRect(x + w - 10, y + step + 3, 2, 8, "#cbbb91"));
        }
      } else {
        svg.append(pixelRect(x, y, w, 2, "#e7ddbb"), pixelRect(x, y + h - 2, w, 2, "#d0c197"));
        const middle = y + Math.round(h / 2) - 1;
        for (let step = 14; step < w - 14; step += 30) {
          svg.append(pixelRect(x + step, middle, Math.min(14, x + w - (x + step)), 2, "#c9b98e"));
        }
        for (let step = 20; step < w - 20; step += 46) {
          svg.append(pixelRect(x + step, y + 6, 5, 2, "#e9dfbd"), pixelRect(x + step + 3, y + h - 10, 8, 2, "#cbbb91"));
        }
      }
    };
    // Streets and ring roads are derived from the plot rows, so changing PLOT_H
    // or the row pitch can never leave a road cutting through a house.
    const rowCentres = [...new Set((map.slots || []).map(([, cy]) => cy))].sort((a, b) => a - b);
    const streets = map.streets?.length ? map.streets.map(y => ({ top: y, height: roadWidth }))
      : rowCentres.map(cy => ({ top: Math.round(cy + map.plot[1] / 2 + verge), height: roadWidth }));
    const core = map.core;
    for (const { top, height } of streets) {
      // A street that would pass behind the village square stops at its edge and
      // resumes on the far side: through traffic is meant to use the ring roads,
      // which keeps the square itself a pedestrian plaza.
      const behindPlaza = top < core[1] + core[3] && top + height > core[1];
      if (behindPlaza) {
        street(0, top, core[0], height);
        street(core[0] + core[2], top, width - core[0] - core[2], height);
      } else {
        street(0, top, width, height);
      }
    }
    // Ring roads close the grid at both edges, so every street meets every other
    // and no patrol has to reverse on a dead end.
    const rings = map.rings?.length ? map.rings : [0, width - roadWidth];
    const topStreet = streets.length ? streets[0].top : 0;
    const bottomStreet = streets.length ? streets[streets.length - 1].top + roadWidth : height;
    for (const x of rings) street(x, topStreet, roadWidth, bottomStreet - topStreet);
    const occupied = new Set(ui.state.agents.filter(agent => Number.isInteger(agent.plot?.slot)).map(agent => agent.plot.slot));
    (map.slots || []).forEach(([cx, cy], index) => {
      if (!occupied.has(index)) svg.append(meadowPlot(index, cx - map.plot[0] / 2, cy - map.plot[1] / 2));
      // Every row now has its own street, so every doorstep gets a paved driveway
      // from the last stone step down to the kerb.
      const stub = Math.round(cy + map.plot[1] / 2);
      const own = streets.find(item => item.top >= stub && item.top - stub <= verge + 22);
      if (occupied.has(index) && own) {
        // The driveway stops at the near kerb: crossing the whole street would
        // read as a road joining from the other side, which no house has.
        const length = own.top - stub + 2;
        svg.append(pixelRect(cx - 12, stub, 24, length, "#dfd1a9"), pixelRect(cx - 9, stub, 18, length, "#e7ddbb"));
      }
    });
    // Flowers line the verges beside each street instead of sitting on a lawn or
    // a doorstep: the 18px strip between a plot's bottom edge and its kerb. They
    // stay between the two ring roads so nothing is ever painted on tarmac.
    const flowerLeft = rings[0] + roadWidth + 24;
    const flowerRight = Math.min(rings[1], width) - 24;
    for (let i = 0; i < 38; i++) {
      const band = streets[i % Math.max(1, streets.length)];
      if (!band || flowerRight <= flowerLeft) break;
      const x = flowerLeft + Math.floor(rand() * (flowerRight - flowerLeft));
      svg.append(pixelFlower(x, band.top - 13 + (i % 2) * 2, i % 2 ? "#dcc38a" : "#ddaf8a"));
    }
    if (mapOrigin(map)[1] < 0 && window.StarhavenForestArt) {
      const forest = svgNode("g", { class: "forest-park" });
      forest.innerHTML = window.StarhavenForestArt.scene(); svg.append(forest);
    }
    return svg;
  }

  function updateMap() {
    const worldRoot = $("#map-world"), map = ui.state?.map;
    if (!worldRoot || !map) return;
    let world = worldRoot.querySelector(".map-land");
    const layoutKey = JSON.stringify(map);
    if (mapView.layout !== layoutKey) {
      worldRoot.replaceChildren();
      world = el("div", "map-land");
      world.style.left = `${-mapOrigin(map)[0]}px`; world.style.top = `${-mapOrigin(map)[1]}px`;
      world.style.width = `${map.world[0]}px`; world.style.height = `${map.world[1]}px`;
      worldRoot.append(world);
      mapView.plots.clear(); mapView.background = null; mapView.layout = layoutKey;
      worldRoot.style.width = `${map.world[0]}px`; worldRoot.style.height = `${map.world[1]}px`;
      const art = el("img", "map-art");
      art.src = "/assets/town.svg"; art.alt = ""; art.draggable = false;
      art.setAttribute("aria-hidden", "true");
      art.style.left = `${map.core[0]}px`; art.style.top = `${map.core[1]}px`;
      art.style.width = `${map.core[2]}px`; art.style.height = `${map.core[3]}px`;
      world.append(art);
    }
    const backgroundKey = JSON.stringify(ui.state.agents.map(agent => agent.plot?.slot).filter(Number.isInteger).sort((a, b) => a - b));
    if (mapView.background !== backgroundKey) {
      $(".world-bg", world)?.remove(); world.prepend(worldBackground(map)); mapView.background = backgroundKey;
    }
    const present = new Set();
    for (const agent of ui.state.agents) {
      present.add(agent.id);
      const signature = JSON.stringify([agent.name, agent.avatar, agent.home, agent.plot, agent.house]);
      let record = mapView.plots.get(agent.id);
      if (!record || record.signature !== signature) {
        const plot = renderPlot(agent, map);
        if (!plot) continue;
        const oldFocus = record?.node.contains(document.activeElement) ? document.activeElement.classList.contains("map-resident") ? ".map-resident" : ".home-hit" : null;
        if (record) record.node.replaceWith(plot); else world.append(plot);
        record = { node: plot, signature }; mapView.plots.set(agent.id, record);
        if (oldFocus) $(oldFocus, plot)?.focus({ preventScroll: true });
      }
      record.node.classList.toggle("selected", agent.id === ui.selected);
      $$("[data-agent]", record.node).forEach(button => {
        button.classList.toggle("selected", agent.id === ui.selected);
        button.setAttribute("aria-pressed", String(agent.id === ui.selected));
      });
    }
    for (const [id, record] of mapView.plots) if (!present.has(id)) { record.node.remove(); mapView.plots.delete(id); }
    const scout = ui.state.agents.find(agent => agent.id === "scout");
    if (scout) {
      let traveller = world.querySelector(".scout-traveller");
      if (!traveller) { traveller = residentSprite(scout); traveller.classList.add("scout-traveller"); world.append(traveller); }
      const face = traveller.querySelector("img");
      if (face && !face.src.endsWith(`/assets/${chosenAvatar(scout)}.svg`)) face.src = `/assets/${chosenAvatar(scout)}.svg`;
      traveller.classList.toggle("selected", scout.id === ui.selected);
      traveller.setAttribute("aria-pressed", String(scout.id === ui.selected));
    }
    window.StarhavenScoutWorkspace?.renderMap(world, map);
    window.StarhavenSecurityWorkspace?.renderMap(world, map);
    applyMapTransform();
  }

  function renderSuggestions() {
    const list = $("#suggestions");
    list.replaceChildren();
    const agent = currentAgent();
    if (!agent || isBusy(agent.id)) return;
    const suggestions = Array.isArray(agent.suggestions) ? agent.suggestions.slice(0, 2) : [];
    for (const suggestion of suggestions) {
      const button = el("button", "suggestion", suggestion);
      button.type = "button";
      button.addEventListener("click", () => {
        $("#chat-input").value = suggestion;
        saveDraft(agent.id, suggestion);
        updateComposer();
        $("#chat-input").focus();
      });
      list.append(button);
    }
  }

  async function selectAgent(id, scroll = false) {
    if (!ui.state?.agents.some(agent => agent.id === id)) return;
    if (id === ui.selected) {
      if (scroll && window.innerWidth <= 800) $(".companion-panel").scrollIntoView({ behavior: reducedMotion() ? "instant" : "smooth" });
      return;
    }
    saveDraft(ui.selected, $("#chat-input").value);
    ui.selected = id;
    $("#chat-input").value = getDraft(id);
    renderResidents();
    renderProfile();
    updateMap();
    updateComposer();
    showChatError(id, ui.chatErrors.get(id));
    if (ui.messages.has(id)) renderMessages(id);
    else $("#chat-messages").replaceChildren(el("p", "loading-copy", "正在翻开你们的聊天记录…"));
    api("/api/preferences", { method: "POST", body: { selected_agent_id: id } }).catch(() => {});
    if (scroll && window.innerWidth <= 800) $(".companion-panel").scrollIntoView({ behavior: reducedMotion() ? "instant" : "smooth" });
    await loadMessages(id);
  }

  async function loadMessages(agentId, { quiet = false } = {}) {
    try {
      const data = await api(`/api/agents/${encodeURIComponent(agentId)}/messages`);
      ui.messages.set(agentId, Array.isArray(data.messages) ? data.messages : []);
      if (data.active_job && ["queued", "running"].includes(data.active_job.status)) startPolling(agentId, data.active_job.id);
      if (agentId === ui.selected) renderMessages(agentId, !quiet);
    } catch (error) {
      if (agentId === ui.selected) showChatError(agentId, `聊天记录暂时没有读到：${error.message}`);
    }
  }

  function renderMessages(agentId, forceScroll = true) {
    if (agentId !== ui.selected) return;
    const container = $("#chat-messages");
    const wasNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 70;
    const agent = currentAgent();
    if (!agent) return;
    const messages = ui.messages.get(agentId) || [];
    container.replaceChildren();
    if (messages.length === 0) {
      container.append(el("div", "chat-date", "一段新的对话，从这里开始"));
      const greeting = buildMessage(agent, { role: "assistant", content: agent.greeting || `你好，我是${agent.name}。今天想一起做点什么？` });
      container.append(greeting, el("p", "greeting-note", "这是伙伴的见面招呼。\n发送消息，就能开始真实的对话。"));
    } else {
      let date = "";
      for (const message of messages) {
        const current = formatDate(message.created_at);
        if (current && current !== date) {
          container.append(el("div", "chat-date", current));
          date = current;
        }
        container.append(buildMessage(agent, message));
      }
    }
    if (isBusy(agentId)) {
      const pending = el("div", "message pending-message");
      const dots = el("div", "typing-bubble");
      dots.append(el("span"), el("span"), el("span"));
      pending.append(dots, el("div", "thinking-label", `${agent.name}正在认真想一想…`));
      container.append(pending);
    }
    if (forceScroll || wasNearBottom) container.scrollTop = container.scrollHeight;
    renderSuggestions();
    updateComposer();
  }

  function buildMessage(agent, message) {
    const user = message.role === "user";
    const wrapper = el("article", `message ${user ? "user" : "assistant"}`);
    const meta = el("div", "message-meta");
    if (!user) meta.append(avatar(agent));
    meta.append(el("span", "", user ? "你" : agent.name));
    if (message.created_at) {
      const time = el("time", "", formatTime(message.created_at));
      time.dateTime = message.created_at;
      meta.append(time);
    }
    wrapper.append(meta, el("div", "message-content", message.content || ""));
    const labels = { failed: "这条消息未获得回复", cancelled: "这次回复已停止", pending: "正在等待回复" };
    if (user && labels[message.status]) {
      const status = el("div", "message-status", labels[message.status]);
      if (["failed", "cancelled"].includes(message.status)) {
        const retry = el("button", "retry-message", "重新发送");
        retry.type = "button";
        retry.disabled = isBusy(agent.id);
        retry.addEventListener("click", () => sendMessage(message.content, { retry: true }));
        status.append(retry);
      }
      wrapper.append(status);
    }
    return wrapper;
  }

  function parseDate(value) {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }
  function formatDate(value) {
    const date = parseDate(value);
    return date ? date.toLocaleDateString("zh-CN", { month: "long", day: "numeric" }) : "";
  }
  function formatTime(value) {
    const date = parseDate(value);
    return date ? date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }) : "";
  }

  function updateComposer() {
    const agent = currentAgent();
    const busy = agent ? isBusy(agent.id) : false;
    $("#chat-input").disabled = !agent;
    $("#send-message").hidden = busy;
    $("#send-message").disabled = !agent || busy || !$("#chat-input").value.trim();
    $("#stop-message").hidden = !busy;
    $("#stop-message").disabled = !ui.jobs.has(ui.selected);
    $("#chat-input").style.height = "auto";
    $("#chat-input").style.height = `${Math.min(140, Math.max(42, $("#chat-input").scrollHeight))}px`;
  }

  async function sendMessage(provided, { retry = false } = {}) {
    const agent = currentAgent();
    const content = (provided === undefined ? $("#chat-input").value : provided).trim();
    if (!agent || !content || isBusy(agent.id)) return;
    if (content.length > 8000) { showChatError(agent.id, "这段话有点长，请拆成 8000 字以内再发给伙伴。"); return; }
    const id = agent.id;
    const existing = ui.requests.get(id);
    const request = !retry && existing?.content === content ? existing : { id: requestId(), content };
    ui.requests.set(id, request);
    ui.submitting.add(id);
    showChatError(id, null);
    updateComposer();
    try {
      const result = await api("/api/chat", { method: "POST", body: { agent_id: id, message: content, request_id: request.id } });
      ui.requests.delete(id);
      if (!retry && getDraft(id).trim() === content) {
        saveDraft(id, "");
        if (ui.selected === id) $("#chat-input").value = "";
      }
      if (result.job_id) startPolling(id, result.job_id);
      await loadMessages(id);
    } catch (error) {
      if (error.confirmed) ui.requests.delete(id);
      showChatError(id, error.message);
    } finally {
      ui.submitting.delete(id);
      if (ui.selected === id) { updateComposer(); renderMessages(id, false); }
    }
  }

  function startPolling(agentId, jobId) {
    if (ui.jobs.get(agentId)?.id === jobId) return;
    const old = ui.jobs.get(agentId);
    if (old?.timer) clearTimeout(old.timer);
    const job = { id: jobId, timer: null, failures: 0 };
    ui.jobs.set(agentId, job);
    if (agentId === ui.selected) updateComposer();
    job.timer = setTimeout(() => pollJob(agentId, job), 700);
  }

  async function pollJob(agentId, job) {
    if (ui.jobs.get(agentId) !== job) return;
    try {
      const result = await api(`/api/jobs/${encodeURIComponent(job.id)}`);
      if (ui.jobs.get(agentId) !== job) return;
      job.failures = 0;
      if (["completed", "failed", "cancelled"].includes(result.status)) {
        ui.jobs.delete(agentId);
        if (result.status === "failed") showChatError(agentId, result.error || "伙伴这次没能完成回复，可以重新发送。" );
        else if (result.status === "cancelled") showChatError(agentId, "这次回复已停止，随时可以开启新的对话。");
        else showChatError(agentId, null);
        await loadMessages(agentId);
        await loadState();
        return;
      }
      if (agentId === ui.selected && ui.chatErrors.get(agentId)?.startsWith("连接中断")) showChatError(agentId, null);
      job.timer = setTimeout(() => pollJob(agentId, job), 1600);
    } catch (error) {
      if (ui.jobs.get(agentId) !== job) return;
      if (error.status === 404) {
        ui.jobs.delete(agentId);
        showChatError(agentId, "这次对话的连接已结束，请重新发送。你的聊天记录仍然保留。");
        await loadMessages(agentId);
        return;
      }
      job.failures += 1;
      showChatError(agentId, `连接中断，正在重新寻找伙伴。${error.message}`);
      job.timer = setTimeout(() => pollJob(agentId, job), Math.min(15000, 2500 * job.failures));
    }
  }

  async function cancelCurrentJob() {
    const id = ui.selected;
    const job = ui.jobs.get(id);
    if (!job) return;
    $("#stop-message").disabled = true;
    try {
      await api(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, { method: "POST", body: {} });
      if (job.timer) clearTimeout(job.timer);
      await pollJob(id, job);
    } catch (error) { showChatError(id, error.message); }
    finally { updateComposer(); }
  }

  function emptyState(iconName, title, copy, action) {
    const node = el("div", "empty-state");
    node.append(icon(iconName), el("h3", "", title), el("p", "", copy));
    if (action) {
      const button = el("button", "text-button", action.label);
      button.type = "button";
      button.append(icon("arrow"));
      button.addEventListener("click", action.run);
      node.append(button);
    }
    return node;
  }

  function safeLink(value) {
    if (!value || typeof value !== "string") return null;
    try {
      const url = new URL(value, window.location.origin);
      return ["http:", "https:"].includes(url.protocol) ? url.href : null;
    } catch { return null; }
  }

  function renderWork() {
    const list = $("#work-list");
    list.replaceChildren();
    const work = Array.isArray(ui.state.work_items) ? ui.state.work_items : [];
    if (!work.length) list.append(emptyState("board", "第一张任务卡，等待你们一起写下", "这里会记录实际安排的工作和验收结果。先和领航聊聊本周想完成什么。", {
      label: "和领航规划一下", run: async () => {
        await selectAgent("linghang", true);
        $("#chat-input").value = "我们一起制定本周计划吧。请先了解我的目标和每位伙伴的职责，再给出具体交付物与验收方式。";
        saveDraft(ui.selected, $("#chat-input").value);
        updateComposer();
        $("#chat-input").focus();
      },
    }));
    for (const item of work) {
      const card = el("article", "work-card");
      card.append(el("h3", "", item.title || item.name || "小镇工作"));
      if (item.summary || item.description) card.append(el("p", "", item.summary || item.description));
      const meta = el("div", "card-meta");
      const statuses = { planned: "已计划", pending: "待开始", in_progress: "进行中", running: "进行中", completed: "已完成", accepted: "已验收", blocked: "等待协助", failed: "需要跟进" };
      if (item.status) meta.append(el("span", "status-tag", statuses[item.status] || item.status));
      const owner = ui.state.agents.find(agent => agent.id === (item.agent_id || item.owner_id || item.owner));
      if (owner || item.owner_name) meta.append(el("span", "", owner?.name || item.owner_name));
      if (item.due_date || item.deadline) meta.append(el("span", "", `约定：${item.due_date || item.deadline}`));
      card.append(meta);
      list.append(card);
    }
    const reports = $("#report-list");
    reports.replaceChildren();
    const items = Array.isArray(ui.state.reports) ? ui.state.reports : [];
    if (!items.length) reports.append(emptyState("book", "好发现，会在这里留下痕迹", "已完成的研究与交付报告，会收进伙伴的手记。"));
    for (const report of items) {
      const href = safeLink(report.url);
      const card = el(href ? "a" : "article", "report-card");
      if (href) { card.href = href; card.target = "_blank"; card.rel = "noopener noreferrer"; }
      if (report.date) card.append(el("span", "report-date", report.date));
      card.append(el("h3", "", report.title || "研究手记"));
      if (report.summary) card.append(el("p", "", report.summary));
      if (href) card.append(icon("arrow"));
      reports.append(card);
    }
  }

  function renderGrowth() {
    const list = $("#growth-residents");
    list.replaceChildren();
    for (const agent of ui.state.agents) {
      const card = el("article", "growth-card");
      const heading = el("div", "growth-card-header");
      const detail = el("div");
      detail.append(el("h3", "", agent.name), el("p", "", agent.role || "小镇伙伴"));
      heading.append(avatar(agent), detail);
      const metrics = el("div", "growth-metrics");
      for (const [key, label, interaction] of [["accepted_tasks", "已验收工作", false], ["verified_improvements", "已验证改进", false], ["messages", "聊天消息", true], ["days_together", "相伴天数", true]]) {
        const metric = el("div", `growth-metric${interaction ? " interaction" : ""}`);
        metric.append(el("strong", "", number(agent.stats?.[key])), el("span", "", label));
        metrics.append(metric);
      }
      const button = el("button", "text-button", "了解这位伙伴");
      button.append(icon("arrow"));
      button.addEventListener("click", async () => { await selectAgent(agent.id); openProfile(false); });
      card.append(heading, metrics, button);
      list.append(card);
    }
    const improvements = $("#improvement-list");
    improvements.replaceChildren();
    const items = Array.isArray(ui.state.improvements) ? ui.state.improvements : [];
    if (!items.length) improvements.append(emptyState("leaf", "新芽慢慢长，进步认真记", "通过实际案例验证的技能与工作方法改进，会记录在这里。"));
    for (const item of items) {
      const card = el("article", "improvement-card");
      card.append(el("h3", "", item.title || item.name || "一次能力改进"));
      if (item.summary || item.description) card.append(el("p", "", item.summary || item.description));
      const meta = el("div", "card-meta");
      if (item.status) meta.append(el("span", "status-tag", item.status === "verified" ? "已验证" : item.status));
      if (item.date || item.verified_at) meta.append(el("span", "", item.date || formatDate(item.verified_at)));
      card.append(meta);
      improvements.append(card);
    }
  }

  function openProfile(create) {
    const agent = currentAgent();
    if (!create && !agent) return;
    ui.dialogMode = create ? "create" : "edit";
    ui.dialogAgent = create ? null : agent.id;
    ui.dialogAvatar = create ? "captain" : chosenAvatar(agent);
    $("#profile-form").reset();
    $("#profile-error").hidden = true;
    $("#profile-dialog-title").textContent = create ? "邀请一位新伙伴" : `${agent.name}的角色档案`;
    $("#dialog-copy").textContent = create ? "每位伙伴都有自己的名字、风格和擅长的事。先从认识彼此开始。" : "你的设定会成为伙伴对话时的参考。共同记忆由你明确添加和修改。";
    $("#avatar-picker").hidden = false;
    $$("input[name='avatar']").forEach(input => { input.checked = input.value === ui.dialogAvatar; });
    $("#role-field").hidden = !create;
    $("#memories-field").hidden = create;
    $("#save-profile").textContent = create ? "欢迎来到小镇 →" : "保存档案 →";
    $("#save-profile").disabled = false;
    if (!create) {
      $("#agent-name").value = agent.name || "";
      $("#agent-personality").value = agent.personality || "";
      $("#agent-bio").value = agent.bio || "";
      $("#agent-memories").value = Array.isArray(agent.memories) ? agent.memories.join("\n") : "";
    }
    $("#profile-dialog").showModal();
  }

  async function saveProfile(event) {
    event.preventDefault();
    const create = ui.dialogMode === "create";
    const name = $("#agent-name").value.trim();
    if (!name) { $("#agent-name").focus(); return; }
    const body = { name, personality: $("#agent-personality").value.trim(), bio: $("#agent-bio").value.trim() };
    const selectedAvatar = $("input[name='avatar']:checked")?.value || ui.dialogAvatar;
    if (create || selectedAvatar !== ui.dialogAvatar) body.avatar = selectedAvatar;
    if (create) {
      body.role = $("#agent-role").value.trim() || "小镇伙伴";
      body.color = avatarChoices.find(choice => choice.id === selectedAvatar).color;
    } else {
      body.memories = $("#agent-memories").value.split("\n").map(line => line.trim()).filter(Boolean);
      if (body.memories.length > 20 || body.memories.some(line => line.length > 500)) {
        $("#profile-error").textContent = "共同记忆最多保留 20 条，每条请控制在 500 字以内。";
        $("#profile-error").hidden = false;
        return;
      }
    }
    $("#save-profile").disabled = true;
    $("#profile-error").hidden = true;
    try {
      const result = await api(create ? "/api/agents" : `/api/agents/${encodeURIComponent(ui.dialogAgent)}`, { method: create ? "POST" : "PATCH", body });
      $("#profile-dialog").close();
      await loadState();
      if (create && result.agent?.id) { await selectAgent(result.agent.id, true); revealResident(result.agent.id); }
      else if (ui.selected) renderMessages(ui.selected, false);
      toast(create ? `${name}搬进小镇啦，去打个招呼吧。` : "角色档案已保存，伙伴会带着这些记忆与你相处。");
    } catch (error) {
      $("#profile-error").textContent = error.message;
      $("#profile-error").hidden = false;
    } finally { $("#save-profile").disabled = false; }
  }

  for (const choice of avatarChoices) {
    const label = el("label", "avatar-choice");
    const input = el("input");
    input.type = "radio";
    input.name = "avatar";
    input.value = choice.id;
    input.defaultChecked = choice.id === "captain";
    input.setAttribute("aria-label", choice.name);
    const picture = avatar({ name: choice.name, avatar: choice.id });
    picture.alt = "";
    label.append(input, picture, el("span", "", choice.name));
    $("#avatar-options").append(label);
  }

  $$("[data-view]").forEach(button => button.addEventListener("click", () => showView(button.dataset.view)));
  $$("[data-open-view]").forEach(button => button.addEventListener("click", () => showView(button.dataset.openView)));
  $$("[data-agent]").forEach(button => button.addEventListener("click", () => selectAgent(button.dataset.agent, true)));
  $(".brand-mark").addEventListener("click", event => { event.preventDefault(); showView("town"); });
  $(".rail-nav").addEventListener("keydown", event => {
    const tabs = $$("[data-view]");
    const index = tabs.indexOf(document.activeElement);
    if (index < 0 || !["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : tabs.length - 1)) % tabs.length;
    showView(tabs[next].dataset.view);
    tabs[next].focus();
  });
  $("#add-agent").addEventListener("click", () => openProfile(true));
  $("#add-agent-nav").addEventListener("click", () => openProfile(true));
  $("#edit-profile").addEventListener("click", () => openProfile(false));
  $$("[data-close-dialog]").forEach(button => button.addEventListener("click", () => $("#profile-dialog").close()));
  $("#profile-dialog").addEventListener("click", event => {
    if (event.target !== $("#profile-dialog")) return;
    const box = event.target.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) event.target.close();
  });
  $("#profile-form").addEventListener("submit", saveProfile);
  $("#open-settings").addEventListener("click", openSettings);
  $("#close-settings").addEventListener("click", () => $("#settings-dialog").close());
  $("#settings-dialog").addEventListener("close", () => {
    $("#model-api-key").value = "";
    if (location.hash === "#settings") history.replaceState(null, "", `#${ui.view}`);
  });
  $("#settings-dialog").addEventListener("click", event => {
    if (event.target !== $("#settings-dialog")) return;
    const box = event.target.getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) event.target.close();
  });
  $("#model-settings-form").addEventListener("submit", saveModelSettings);
  $("#reload-model-settings").addEventListener("click", loadModelSettings);
  $$("input[name='model-mode']").forEach(input => input.addEventListener("change", updateModelMode));
  window.addEventListener("hashchange", () => { if (location.hash === "#settings") openSettings(); });
  $("#chat-form").addEventListener("submit", event => { event.preventDefault(); sendMessage(); });
  $("#chat-input").addEventListener("input", () => { saveDraft(ui.selected, $("#chat-input").value); updateComposer(); });
  $("#chat-input").addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); sendMessage(); }
  });
  $("#stop-message").addEventListener("click", cancelCurrentJob);
  $("#reload-state").addEventListener("click", () => loadState({ initial: !ui.state }));
  window.addEventListener("online", () => { loadState(); if (ui.selected) loadMessages(ui.selected); });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && ui.state) { loadState(); if (ui.selected) loadMessages(ui.selected, { quiet: true }); }
  });
  window.addEventListener("beforeunload", () => saveDraft(ui.selected, $("#chat-input").value));
  $("#today-label").textContent = new Date().toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "long" });
  const showSettingsInitially = location.hash === "#settings";
  window.StarhavenScoutWorkspace?.init({ api, el, toast, showView, centerMap, getState: () => ui.state, getView: () => ui.view, getSelected: () => ui.selected });
  window.StarhavenSecurityWorkspace?.init({ api, el, toast, showView, centerMap, selectAgent, getAvatar: chosenAvatar, getState: () => ui.state, getView: () => ui.view, getSelected: () => ui.selected });
  showView(showSettingsInitially ? "town" : location.hash.slice(1) || "town");
  initMapPan();
  loadSkins();
  loadState({ initial: true });
  if (showSettingsInitially) openSettings();
})();

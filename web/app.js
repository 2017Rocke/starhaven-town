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
  const avatarFiles = new Set(["captain", "scout", "maker", "gardener"]);
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
    const chosen = avatarFiles.has(agent.avatar) ? agent.avatar : agent.id === "scout" ? "scout" : "captain";
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
    if (!["town", "tasks", "growth"].includes(name)) return;
    ui.view = name;
    $$("[data-view]").forEach(button => {
      const selected = button.dataset.view === name;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    $$(".view").forEach(section => { section.hidden = section.id !== `view-${name}`; });
    history.replaceState(null, "", `#${name}`);
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
    list.replaceChildren();
    for (const agent of ui.state.agents) {
      const button = el("button", `resident-chip${agent.id === ui.selected ? " selected" : ""}`);
      button.type = "button";
      button.setAttribute("aria-pressed", String(agent.id === ui.selected));
      button.setAttribute("aria-label", `与${agent.name}对话`);
      button.append(avatar(agent), el("span", "", agent.name));
      button.addEventListener("click", () => selectAgent(agent.id));
      list.append(button);
    }
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
    container.replaceChildren(top, el("p", "profile-personality", agent.personality || agent.bio || "每次相处，都多了解彼此一点。"), status);
    $("#conversation-title").textContent = `和${agent.name}聊聊`;
    $("#edit-profile").disabled = agent.editable === false;
    $("#chat-input").placeholder = `告诉${agent.name}，你在想什么…`;
    renderSuggestions();
  }

  function updateMap() {
    $$(".extra-resident").forEach(node => node.remove());
    for (const button of $$("[data-agent]")) {
      const agent = ui.state.agents.find(item => item.id === button.dataset.agent);
      button.hidden = !agent;
      button.classList.toggle("selected", button.dataset.agent === ui.selected);
      button.setAttribute("aria-pressed", String(button.dataset.agent === ui.selected));
      if (agent && button.classList.contains("map-resident")) {
        const oldImage = $("img", button);
        if (oldImage) oldImage.replaceWith(avatar(agent));
        $(".map-name", button).textContent = agent.name;
        button.setAttribute("aria-label", `与${agent.name}对话`);
      }
    }
    const extra = ui.state.agents.filter(agent => !["linghang", "scout"].includes(agent.id));
    const shown = extra.slice(0, 5);
    const selected = extra.find(agent => agent.id === ui.selected);
    if (selected && !shown.includes(selected)) shown[shown.length - 1] = selected;
    const positions = [[27, 67], [72, 65], [56, 62], [19, 56], [59, 77]];
    shown.forEach((agent, index) => {
      const button = el("button", `map-resident extra-resident${agent.id === ui.selected ? " selected" : ""}`);
      button.dataset.agent = agent.id;
      button.setAttribute("aria-label", `与${agent.name}对话`);
      button.setAttribute("aria-pressed", String(agent.id === ui.selected));
      button.style.left = `${positions[index][0]}%`;
      button.style.top = `${positions[index][1]}%`;
      button.append(el("span", "resident-bubble", "在这里，随时可以聊聊"), avatar(agent), el("span", "map-name", agent.name));
      button.addEventListener("click", () => selectAgent(agent.id, true));
      $(".town-scene").append(button);
    });
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
    $("#profile-form").reset();
    $("#profile-error").hidden = true;
    $("#profile-dialog-title").textContent = create ? "邀请一位新伙伴" : `${agent.name}的角色档案`;
    $("#dialog-copy").textContent = create ? "每位伙伴都有自己的名字、风格和擅长的事。先从认识彼此开始。" : "你的设定会成为伙伴对话时的参考。共同记忆由你明确添加和修改。";
    $("#avatar-picker").hidden = !create;
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
    if (create) {
      body.role = $("#agent-role").value.trim() || "小镇伙伴";
      body.avatar = $("input[name='avatar']:checked").value;
      body.color = ({ captain: "#71896a", scout: "#c5a16f", maker: "#8ba198", gardener: "#a1b782" })[body.avatar];
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
      if (create && result.agent?.id) await selectAgent(result.agent.id, true);
      else if (ui.selected) renderMessages(ui.selected, false);
      toast(create ? `${name}搬进小镇啦，去打个招呼吧。` : "角色档案已保存，伙伴会带着这些记忆与你相处。");
    } catch (error) {
      $("#profile-error").textContent = error.message;
      $("#profile-error").hidden = false;
    } finally { $("#save-profile").disabled = false; }
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
  showView(showSettingsInitially ? "town" : location.hash.slice(1) || "town");
  loadSkins();
  loadState({ initial: true });
  if (showSettingsInitially) openSettings();
})();

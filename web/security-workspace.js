"use strict";

// The animation is a view of persisted work, never a source of scan results.
// Pure route/sampling helpers are also available to dependency-free Node tests.
((root, factory) => {
  const api = factory(typeof module === "object" && module.exports ? require("./road-navigation.js") : root.StarhavenRoadNavigation);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.StarhavenSecurityWorkspace = api;
})(typeof window === "object" ? window : globalThis, navigation => {
  const OFFICES = Object.freeze({
    police: { agent: "resident-officer", name: "警察署", avatar: "officer", title: "文件巡检", copy: "沿着材料的来路，检查文件与内容变化。", verb: "开始文件巡检" },
    inspection: { agent: "resident-inspector", name: "稽查署", avatar: "inspector", title: "密钥与网络审计", copy: "检查密钥与外发线索，查阅小镇的网络往来。", verb: "开始密钥与外发巡检" },
  });
  const ACTIVE = new Set(["preparing", "scanning", "filing"]);
  const TERMINAL = new Set(["completed", "partial", "failed", "interrupted"]);
  const blockedRoutes = new WeakMap();
  const PHASES = { idle: "在署内待命", preparing: "正在准备巡检", scanning: "正在检查", filing: "正在整理记录", completed: "本次检查已结束", partial: "本次覆盖不完整", failed: "本次检查未完成", interrupted: "上次检查已中断" };
  const STATUS = { review_required: "需要复核", limited_no_findings: "本次范围未发现线索", incomplete: "覆盖不完整", blocked: "请求已阻止", observed: "已记录", allowed: "请求已放行" };
  const FRESHNESS = { current: "当前内容指纹匹配", stale: "内容已变化 · 需重新检查", rules_changed: "检查规则已更新 · 需重新检查", missing: "原文件已缺失", not_applicable: "不适用文件时效", unverified: "尚未核对当前内容" };
  const KINDS = { file_review: "文件巡检记录", daily_report: "值勤日报", network_check: "网络检查", network_result: "网络访问结果", external_notice: "外部访问提示" };
  const SKIPS = { binary: "二进制文件", binary_file: "二进制文件", binary_content: "含二进制内容", too_large: "文件超出大小上限", file_too_large: "文件超出大小上限", file_size_limit: "文件超出大小上限", file_limit: "达到文件数量上限", file_count_limit: "达到文件数量上限", byte_limit: "达到内容大小上限", symlink: "链接文件", links_or_special_files: "链接或特殊文件", unreadable: "无法读取", missing: "文件缺失", unsupported: "不支持的文件类型", unsupported_encoding: "不支持的文字编码", timeout: "达到检查时间上限", time_limit: "达到检查时间上限", entry_limit: "达到目录条目上限", root_limit: "达到检查目录上限", workspace_limit: "达到工作区数量上限", unsupported_workspace: "不支持的工作区", workspace_unavailable: "工作区暂不可读", scope_unavailable: "检查目录暂不可读", directory_unavailable: "子目录暂不可读", excluded_directory: "规则排除的目录", interrupted: "巡检中断", internal_error: "检查异常" };
  const LIMITS = { files: "文件数量上限", entries: "目录条目上限", roots: "检查目录上限", file_bytes: "单个文件字节上限", total_bytes: "总读取字节上限", findings_per_file: "单个文件线索上限", seconds: "最长检查秒数", max_files: "文件数量上限", max_file_bytes: "单个文件字节上限", max_bytes: "总读取字节上限", max_total_bytes: "总读取字节上限", max_seconds: "最长检查秒数", max_depth: "目录深度上限", max_findings: "记录线索上限", max_events: "网络事件上限", text_only: "仅检查文本" };
  const array = value => Array.isArray(value) ? value : [];
  const number = value => Math.max(0, Number(value) || 0);
  const epoch = value => typeof value === "number" ? value : Date.parse(value) || 0;
  const officeForAgent = id => Object.keys(OFFICES).find(key => OFFICES[key].agent === id) || null;
  const agentForOffice = id => OFFICES[id]?.agent || null;
  const distance = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);
  const length = points => points.slice(1).reduce((sum, point, i) => sum + distance(points[i], point), 0);
  function along(points, travelled) {
    let remaining = Math.max(0, travelled);
    for (let i = 1; i < points.length; i++) {
      const size = distance(points[i - 1], points[i]);
      if (size && remaining < size) {
        const t = remaining / size, a = points[i - 1], b = points[i];
        return { point: [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t], direction: b[0] < a[0] ? "left" : b[0] > a[0] ? "right" : b[1] < a[1] ? "up" : "down" };
      }
      remaining -= size;
    }
    return { point: [...points[points.length - 1]], direction: "down" };
  }
  function routeFor(agent, map, layout) {
    const blockedKey = JSON.stringify([agent.id, agent.plot.pos, layout.resident]);
    const failed = map.road_network && blockedRoutes.get(map.road_network)?.get(blockedKey);
    if (failed) return failed;
    try {
      const planned = navigation?.plan(agent, map, layout, agent.id === "resident-inspector" ? "car" : "walk");
      if (planned) return planned;
      if (map.road_network) throw new Error("Resident has no valid road access");
    } catch {
      // Bad or partially upgraded road data must not interrupt state loading or
      // send an actor across the square. The inspection itself remains real.
      const home = [agent.plot.pos[0] + layout.resident.x, agent.plot.pos[1] + layout.resident.y + 70];
      const route = { home, approach: [home, home], loop: [home, home], blocked: true };
      if (map.road_network && typeof map.road_network === "object") {
        const failures = blockedRoutes.get(map.road_network) || new Map();
        failures.set(blockedKey, route); blockedRoutes.set(map.road_network, failures);
      }
      return route;
    }
    const [x, y] = agent.plot.pos, width = map.world[0], plotHeight = map.plot[1];
    const home = [x + layout.resident.x, y + layout.resident.y + 70];
    const bottom = y + plotHeight;
    const rows = [...new Set(array(map.slots).map(point => point[1]))].sort((a, b) => a - b);
    const roadWidth = map.road?.width || 20;
    // The server publishes the street grid, so the car drives on the painted road
    // rather than on a corridor this file guessed. Older saves without the grid
    // keep the derived fallback.
    const owned = array(map.streets).find(top => top >= bottom && top - bottom <= 40);
    const next = rows.find(cy => cy - plotHeight / 2 > bottom);
    const worldBottom = (map.origin?.[1] || 0) + map.world[1];
    let street = owned !== undefined ? owned + roadWidth / 2
      : next === undefined ? bottom + (worldBottom - bottom) / 2 : (bottom + next - plotHeight / 2) / 2;
    // A legacy map's north row has only a forty-pixel gap before the square.
    if (bottom < map.core[1] && street >= map.core[1] - 14) street = bottom + (map.core[1] - bottom) / 2;
    const inVillageRows = street > map.core[1] && street < map.core[1] + map.core[3];
    const originX = map.origin?.[0] || 0;
    const minX = inVillageRows && home[0] > map.core[0] + map.core[2] ? map.core[0] + map.core[2] + 24 : originX + 24;
    const maxX = inVillageRows && home[0] < map.core[0] ? map.core[0] - 24 : originX + width - 24;
    const entrance = [home[0], Math.round(street)];
    const left = [Math.max(minX, home[0] - 280), entrance[1]], right = [Math.min(maxX, home[0] + 280), entrance[1]];
    return { home, approach: [home, entrance], loop: [entrance, left, right, entrance], sharedAccess: street < map.core[1] && map.core[1] - street < 24 };
  }
  function patrolPose(route, elapsed, speed) {
    if (route.blocked) return { point: [...route.home], direction: "down", phase: "home", moving: false, onFoot: true, navigationBlocked: true };
    if (route.navigation) {
      const pose = navigation.active(route, elapsed / 1000, speed);
      return { point: pose.point, direction: pose.direction, phase: pose.phase, moving: pose.moving, onFoot: pose.onFoot };
    }
    const travelled = Math.max(0, elapsed) / 1000 * speed, approachLength = length(route.approach);
    if (travelled < approachLength) return { ...along(route.approach, travelled), phase: "outbound", onFoot: !!route.sharedAccess };
    return { ...along(route.loop, (travelled - approachLength) % Math.max(1, length(route.loop))), phase: "patrolling" };
  }
  function sample(job, route, now = Date.now(), reduced = false, speed = 42) {
    let pose = { point: [...route.home], direction: "down", phase: "home", moving: false, working: false };
    const start = epoch(job?.started_at), finish = epoch(job?.finished_at);
    if (start && ACTIVE.has(job?.phase)) {
      const action = patrolPose(route, now - start, speed);
      pose = { ...action, moving: now > start && action.moving !== false, working: true };
    } else if (start && finish >= start && TERMINAL.has(job?.phase)) {
      if (route.navigation) {
        const action = navigation.returning(route, (finish - start) / 1000, Math.max(0, now - finish) / 1000, speed);
        pose = { point: action.point, direction: action.direction, phase: action.phase, moving: action.moving, onFoot: action.onFoot, working: false };
      } else {
        const from = patrolPose(route, finish - start, speed).point;
        const path = from[1] === route.home[1] || from[0] === route.home[0] ? [from, route.home] : [from, route.approach[1], route.home];
        const travelled = Math.max(0, now - finish) / 1000 * speed;
        if (travelled < length(path)) {
          pose = { ...along(path, travelled), phase: "returning", moving: true, working: false };
          if (route.sharedAccess && pose.direction === "up") pose.onFoot = true;
        }
      }
    }
    // Reduced motion preserves the real phase while keeping the actor at one
    // meaningful place. No animation state is written back to the backend.
    if (reduced) pose = { ...pose, point: [...(pose.working ? route.navigation ? route.start.point : route.approach[1] : route.home)], direction: route.navigation && pose.working ? ["right", "down", "left", "up"][route.start.heading] : pose.direction, moving: false, onFoot: !pose.working || !!route.sharedAccess };
    return { ...pose, canStart: !pose.working && pose.phase === "home" };
  }
  function redact(value, max = 4000) {
    return String(value ?? "").slice(0, max)
      .replace(/\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+/gi, "$1 [已隐藏]")
      .replace(/((?:api[_-]?key|access[_-]?token|token|password|passwd|secret|authorization|cookie)["']?\s*[=:]\s*["']?)([^\s&,;"'}]+)/gi, "$1[已隐藏]")
      .replace(/(https?:\/\/)[^\s/@:]+:[^\s/@]+@/gi, "$1[已隐藏]@");
  }
  function subjectText(subject) {
    if (typeof subject === "string") return redact(subject, 1000);
    if (!subject || typeof subject !== "object") return "未记录对象";
    return [subject.name, subject.host, subject.method, subject.path].filter(value => typeof value === "string").map(value => redact(value, 400)).join(" · ") || "检查对象";
  }

  let host;
  const state = { data: null, loading: false, generation: 0, error: "", timer: null, animation: null, frame: 0, map: null, clockOffset: 0, confirmedAt: 0, poseTime: 0, online: false, refs: {}, offices: {} };
  const officeState = id => state.offices[id] || (state.offices[id] = { dirty: false, saving: false, starting: false, error: "", records: [], cursor: null, pages: 0, loading: false, recordsError: "", recordSignature: "", reportSignature: "", updateKey: "", filter: "all" });
  const officeData = id => array(state.data?.offices).find(office => office.id === id);
  const motionReduced = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const freshState = () => state.online && Date.now() + state.clockOffset - state.confirmedAt < 15000;
  function currentTime() {
    if (freshState()) state.poseTime = Date.now() + state.clockOffset;
    return state.poseTime || state.confirmedAt || Date.now();
  }
  const date = value => epoch(value) ? new Date(epoch(value)).toLocaleString("zh-CN", { year: "numeric", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Shanghai" }) : "时间未记录";
  const node = (tag, cls, text) => host.el(tag, cls, text);
  function button(text, cls, handler) {
    const result = node("button", cls, text); result.type = "button"; result.addEventListener("click", handler); return result;
  }
  function field(list, label, value, code = false) {
    if (value === undefined || value === null || value === "") return;
    const content = node("dd"); content.append(node(code ? "code" : "span", "", value));
    list.append(node("dt", "", label), content);
  }
  function empty(text) { return node("p", "security-empty", text); }
  function textList(values, cls = "security-notes") {
    const list = node("ul", cls);
    for (const value of array(values)) list.append(node("li", "", redact(value)));
    return list;
  }
  function coverageView(coverage) {
    const c = coverage || {}, section = node("div", "security-coverage");
    const fields = node("dl", "security-fields");
    field(fields, "已发现文件", c.files_seen);
    field(fields, "已检查文件", c.files_scanned);
    field(fields, "已读内容", typeof c.bytes_scanned === "number" ? `${c.bytes_scanned.toLocaleString("zh-CN")} 字节` : undefined);
    if (c.enumeration_complete !== undefined) field(fields, "范围覆盖", c.enumeration_complete === true ? "已完成本条记录声明的检查范围" : "存在未覆盖内容，请查看跳过项与限制");
    field(fields, "检查范围", c.scope ? redact(c.scope) : undefined);
    field(fields, "外部网络活动", c.network_observed === false ? "未观察到外部工具的实际网络行为" : undefined);
    if (number(c.findings_suppressed)) field(fields, "未展开线索", `${c.findings_suppressed} 条（达到记录上限）`);
    section.append(fields);
    const skipped = Object.entries(c.skipped || {}).filter(([, value]) => number(value) > 0);
    if (skipped.length) {
      section.append(node("h4", "", "未检查的内容"), textList(skipped.map(([reason, count]) => `${SKIPS[reason] || reason}：${count}`)));
    }
    const limits = Object.entries(c.limits || {});
    if (limits.length) {
      const detail = node("details", "security-limits"); detail.append(node("summary", "", "本次检查限制"));
      detail.append(textList(limits.map(([key, value]) => `${LIMITS[key] || key}：${Array.isArray(value) ? value.join("、") : typeof value === "object" ? "已设定范围限制" : typeof value === "boolean" ? value ? "是" : "否" : value}`)));
      section.append(detail);
    }
    return section;
  }
  function recordCard(record) {
    const detail = node("details", "security-record"); detail.dataset.recordId = String(record.id);
    const summary = node("summary", "security-record-summary"), heading = node("span", "security-record-heading");
    heading.append(node("strong", "", KINDS[record.kind] || "巡检记录"), node("span", "security-record-date", date(record.created_at)));
    const status = node("span", "security-result", STATUS[record.status] || "状态待核对"); status.dataset.status = record.status || "unknown";
    const subject = record.kind === "daily_report" ? "本次值勤汇总" : record.kind === "external_notice" && !record.subject ? "外部工具调用" : subjectText(record.subject);
    summary.append(heading, node("span", "security-record-subject", subject), status);
    detail.append(summary);
    const body = node("div", "security-record-body");
    if (record.summary) body.append(node("p", "security-record-copy", redact(record.summary)));
    const freshness = node("p", "security-freshness", FRESHNESS[record.freshness] || FRESHNESS.unverified);
    freshness.dataset.freshness = record.freshness || "unverified"; body.append(freshness);
    const fields = node("dl", "security-fields");
    field(fields, "记录日期", date(record.created_at)); if (record.subject) field(fields, "检查对象", subjectText(record.subject));
    field(fields, "检查规则版本", redact(record.rule_version || "旧记录未标明规则版本"));
    if (record.agent_id) field(fields, "调用居民", host.getState()?.agents?.find(agent => agent.id === record.agent_id)?.name || redact(record.agent_id));
    field(fields, "请求方法", record.method); field(fields, "响应状态", record.status_code);
    field(fields, "关联网络检查", record.check_id); field(fields, "说明", record.error ? redact(record.error) : undefined);
    field(fields, "内容指纹 SHA-256", record.sha256, true); field(fields, "巡检编号", record.job_id, true);
    field(fields, "记录指纹", record.record_hash, true); field(fields, "前一记录指纹", record.previous_hash, true);
    body.append(fields, coverageView(record.coverage));
    const findings = array(record.findings);
    if (findings.length) {
      body.append(node("h4", "", "线索与脱敏证据"));
      for (const finding of findings) {
        const section = node("section", "security-finding");
        section.append(node("strong", "", redact(finding.title || finding.rule || "待复核线索")));
        const meta = [({ high: "高关注", medium: "需关注", low: "一般线索", info: "信息" })[finding.severity], finding.rule, finding.line ? `第 ${finding.line} 行` : ""].filter(Boolean).join(" · ");
        if (meta) section.append(node("p", "security-evidence-meta", redact(meta)));
        if (finding.reason) section.append(node("p", "", redact(finding.reason)));
        if (finding.snippet) section.append(node("pre", "security-evidence", redact(finding.snippet)));
        body.append(section);
      }
    }
    body.append(node("p", "security-record-note", String(record.kind).startsWith("network") ? "放行或阻止描述这次网络请求的处理结果，不代表材料通过安全认证。" : "记录仅对应当时的内容与检查范围；未发现线索不代表没有风险。"));
    detail.append(body); return detail;
  }
  function mount(id) {
    const config = OFFICES[id], root = document.getElementById(`security-${id}`), refs = {};
    if (!root) return;
    state.refs[id] = refs; root.replaceChildren();
    const heading = node("div", "security-heading"), title = node("div");
    title.append(node("p", "eyebrow", id === "police" ? "POLICE STATION" : "INSPECTION BUREAU"), node("h1", "", config.name), node("p", "", config.copy));
    heading.append(title, button("回到小镇 →", "text-button", () => host.showView("town"))); root.append(heading);
    const top = node("div", "security-desk"), scene = node("div", `security-station security-station-${id}`);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 320 280"); svg.setAttribute("aria-hidden", "true"); svg.setAttribute("shape-rendering", "crispEdges");
    if (window.StarhavenHouseArt) svg.append(window.StarhavenHouseArt.house({ house: { style: id, variant: 0 } }), window.StarhavenHouseArt.yard({ house: { style: id, variant: 0 } }));
    const portrait = node("img", "security-station-person"); portrait.src = `/assets/${config.avatar}.svg`; portrait.alt = "";
    refs.sceneState = node("span", "security-station-state", "正在读取值勤记录");
    scene.append(svg, portrait, refs.sceneState);
    const desk = node("div", "security-job");
    desk.append(node("p", "security-kicker", config.title));
    refs.phase = node("h2", "", "正在读取记录…"); refs.message = node("p", "security-job-message");
    refs.message.setAttribute("role", "status");
    refs.progress = node("p", "security-progress"); refs.motion = node("p", "security-motion");
    refs.start = button(config.verb, "primary-button security-start", () => start(id)); refs.start.disabled = true;
    desk.append(refs.phase, refs.message, refs.progress, refs.motion, refs.start, button("在地图上查看 →", "text-button security-locate", () => locate(id)));
    top.append(scene, desk); root.append(top);
    refs.error = node("p", "form-error"); refs.error.setAttribute("role", "alert"); refs.error.hidden = true; root.append(refs.error);
    const scope = node("div", "security-scope");
    scope.append(node("strong", "", "值勤范围"), node("p", "", id === "police" ? "检查小镇内纳入范围的文件与已采集内容，不执行材料中的代码。检查结果保存在警察署记录里。" : "手动与定时巡检检查材料中的疑似密钥和外发线索。小镇已接入的网络请求另行留档；外部软件不在观察范围内。没有巡检时，警车在署内待命。"));
    const currentCoverage = node("details", "security-current-coverage"); currentCoverage.append(node("summary", "", "本次覆盖与跳过情况"));
    refs.coverage = node("div"); currentCoverage.append(refs.coverage); scope.append(currentCoverage);
    root.append(scope);
    const form = node("form", "security-schedule"), enabledLabel = node("label", "security-schedule-toggle");
    refs.enabled = node("input"); refs.enabled.type = "checkbox";
    const enabledCopy = node("span"); enabledCopy.append(node("strong", "", "每天定时值勤"), node("small", "", "小镇运行时执行 · 北京时间"));
    enabledLabel.append(refs.enabled, enabledCopy);
    const timeLabel = node("label", "security-time"); timeLabel.append(node("span", "", "出发时间"));
    refs.time = node("input"); refs.time.type = "time"; refs.time.required = true; refs.time.value = "09:30"; timeLabel.append(refs.time);
    refs.save = node("button", "secondary-button", "保存安排"); refs.save.type = "submit";
    refs.scheduleNote = node("p", "security-schedule-note", "关闭小镇后暂停；此处保存两署各自的每日安排。");
    form.append(enabledLabel, timeLabel, refs.save, refs.scheduleNote);
    form.addEventListener("input", () => { officeState(id).dirty = true; });
    form.addEventListener("submit", event => { event.preventDefault(); saveSchedule(id); }); root.append(form);
    refs.metrics = node("div", "security-metrics"); refs.metricScope = node("p", "security-metric-scope"); root.append(refs.metrics, refs.metricScope);
    const reportHeading = node("div", "section-heading"); reportHeading.append(node("h2", "", `${config.name}日报`), node("span", "", "真实巡检结束后归档"));
    refs.report = node("div", "security-report"); root.append(reportHeading, refs.report);
    const recordsHeading = node("div", "security-records-heading"), recordsTitle = node("div");
    recordsTitle.append(node("h2", "", "值勤记录"), node("p", "", "保留日期、检查范围与内容指纹；这里只展示记录，不提供改写结论的操作。"));
    refs.reload = button("刷新记录", "secondary-button", () => loadRecords(id)); recordsHeading.append(recordsTitle, refs.reload); root.append(recordsHeading);
    const filterLabel = node("label", "security-filter"); filterLabel.append(node("span", "", "记录类型"));
    refs.filter = node("select");
    for (const kind of id === "police" ? ["all", "file_review", "daily_report"] : ["all", "file_review", "network_check", "network_result", "external_notice", "daily_report"]) {
      const option = node("option", "", kind === "all" ? "全部记录" : KINDS[kind]); option.value = kind; refs.filter.append(option);
    }
    refs.filter.addEventListener("change", () => { officeState(id).filter = refs.filter.value; renderRecords(id); });
    filterLabel.append(refs.filter); root.append(filterLabel);
    refs.integrity = node("p", "security-integrity"); refs.integrity.setAttribute("role", "status");
    refs.recordsError = node("p", "form-error"); refs.recordsError.setAttribute("role", "alert"); refs.recordsError.hidden = true;
    refs.records = node("div", "security-records"); refs.more = button("查看更早记录", "secondary-button security-more", () => loadRecords(id, true)); refs.more.hidden = true;
    root.append(refs.integrity, refs.recordsError, refs.records, refs.more);
    const limits = node("details", "security-office-limits"); limits.append(node("summary", "", "这份记录能说明什么"));
    refs.limitList = node("div"); limits.append(refs.limitList); root.append(limits);
    refs.notice = node("p", "security-record-note"); root.append(refs.notice);
  }
  function actionFor(id) {
    const map = state.map?.map, agent = host?.getState()?.agents?.find(item => item.id === OFFICES[id].agent);
    if (!map || !agent?.plot?.pos || !window.StarhavenHouseArt) return { point: [0, 0], phase: "home", moving: false, working: false, canStart: !ACTIVE.has(officeData(id)?.job?.phase) };
    return sample(officeData(id)?.job, routeFor(agent, map, window.StarhavenHouseArt.layout(agent)), currentTime(), motionReduced(), id === "inspection" ? 72 : 42);
  }
  function renderAction(id, action = actionFor(id)) {
    const refs = state.refs[id], local = officeState(id), data = officeData(id);
    if (!refs) return;
    const fresh = freshState();
    const movement = { home: "在署内待命", outbound: "沿道路前往巡检路段", patrolling: id === "inspection" ? "警车沿道路巡行" : "警官正在步行巡街", returning: "本次工作已结束，正在返署" };
    refs.motion.textContent = !fresh ? "行动状态暂未确认，等待重新连接。" : action.navigationBlocked ? "道路暂不可通行，留在署内处理本次工作。" : movement[action.phase];
    refs.sceneState.textContent = !fresh ? "值勤状态未确认" : action.phase === "returning" ? "正在返署" : action.working ? "本次值勤进行中" : "在署内待命";
    refs.start.disabled = !data || !fresh || local.starting || !action.canStart;
    refs.phase.textContent = !fresh && data ? `上次记录 · ${PHASES[data.job?.phase] || "状态待核对"}` : data ? PHASES[data.job?.phase] || "状态待核对" : "等待值勤记录";
    refs.start.textContent = !fresh ? "等待同步值勤状态" : local.starting ? "正在准备…" : action.working ? "本次值勤进行中…" : action.phase === "returning" ? "等待返署…" : OFFICES[id].verb;
  }
  function renderOffice(id) {
    const refs = state.refs[id], data = officeData(id), local = officeState(id);
    if (!refs) return;
    refs.error.textContent = local.error || state.error; refs.error.hidden = !refs.error.textContent;
    refs.enabled.disabled = refs.time.disabled = refs.save.disabled = !data || !state.online || local.saving;
    refs.phase.textContent = data ? PHASES[data.job?.phase] || "状态待核对" : "等待值勤记录";
    refs.message.textContent = data ? redact(data.job?.message || (id === "police" ? "还没有正在进行的文件巡检。" : "还没有正在进行的网络审计。")) : "连接后即可查看巡检与安排。";
    const job = data?.job;
    refs.progress.textContent = job?.started_at ? `已检查 ${number(job.files_scanned)} 项 · 发现 ${number(job.findings)} 条线索 · 跳过 ${typeof job.skipped === "object" ? Object.values(job.skipped || {}).reduce((sum, value) => sum + number(value), 0) : number(job.skipped)} 项` : "尚无本次检查进度";
    refs.coverage.replaceChildren(job?.started_at ? coverageView(job.coverage) : empty("开始值勤后，这里显示实际检查范围。"));
    if (!local.dirty && !local.saving && data) { refs.enabled.checked = data.schedule?.enabled === true; refs.time.value = data.schedule?.time || "09:30"; }
    refs.scheduleNote.textContent = `关闭小镇后暂停；两署分别安排。${data?.schedule?.last_run_date ? `上次定时值勤：${data.schedule.last_run_date}。` : ""}`;
    renderMetrics(id);
    const reportSignature = JSON.stringify(data?.latest_report || null);
    if (local.reportSignature !== reportSignature) {
      local.reportSignature = reportSignature;
      refs.report.replaceChildren(data?.latest_report ? recordCard(data.latest_report) : empty("还没有值勤日报。完成一次巡检后，这里会保留真实汇总。"));
    }
    refs.integrity.textContent = state.data?.integrity?.message || "记录校验状态尚未读取。";
    refs.integrity.dataset.valid = String(state.data?.integrity?.valid);
    refs.limitList.replaceChildren(textList(state.data?.limitations || ["有限范围的检查不能代替人工复核，也不代表整台电脑的安全状态。"]));
    refs.notice.textContent = state.data?.resident_notice || "聊天角色与执行程序各司其职；值勤状态以这里的实际记录为准。";
    renderAction(id);
  }
  function renderMetrics(id) {
    const refs = state.refs[id], data = officeData(id), local = officeState(id);
    const stale = local.pages ? local.records.filter(record => ["stale", "missing", "rules_changed"].includes(record.freshness)).length : null;
    refs.metrics.replaceChildren(...[["已存记录", data?.counts?.records], ["最近记录中的线索", data?.counts?.findings], ["已读记录过期 / 缺失", stale]].map(([label, count]) => {
      const metric = node("div"); metric.append(node("strong", "", count == null ? "—" : number(count)), node("span", "", label)); return metric;
    }));
    refs.metricScope.textContent = data ? `线索汇总范围：最近 ${number(data.counts?.summary_records)} 条记录。过期与缺失仅核对已读取的 ${local.records.length} 条，不代表全部存档。` : "汇总范围尚未读取。";
  }
  function renderRecords(id) {
    const refs = state.refs[id], local = officeState(id);
    if (!refs) return;
    refs.recordsError.textContent = local.recordsError; refs.recordsError.hidden = !local.recordsError;
    refs.reload.disabled = refs.more.disabled = local.loading;
    refs.more.hidden = local.cursor === null || local.cursor === undefined;
    refs.more.textContent = local.loading ? "正在读取…" : "查看更早记录";
    const records = local.filter === "all" ? local.records : local.records.filter(record => record.kind === local.filter);
    const signature = JSON.stringify([records, local.filter, local.pages, local.loading]);
    if (signature === local.recordSignature) return;
    local.recordSignature = signature;
    const open = new Set([...refs.records.querySelectorAll("details[open]")].map(item => item.dataset.recordId));
    refs.records.replaceChildren(...records.map(record => { const card = recordCard(record); card.open = open.has(String(record.id)); return card; }));
    if (!records.length) refs.records.append(empty(local.loading ? "正在翻开值勤簿…" : local.recordsError ? "暂时无法读取记录，请重试。" : local.pages ? "这一页没有符合条件的记录。" : "还没有读取值勤记录。"));
  }
  async function loadRecords(id, more = false) {
    const local = officeState(id); if (!host || local.loading || (more && local.cursor == null)) return;
    local.loading = true; local.recordsError = ""; renderRecords(id);
    const cursor = more ? `&before=${encodeURIComponent(local.cursor)}` : "";
    try {
      const result = await host.api(`/api/security/records?office=${id}&limit=30${cursor}`);
      const records = array(result.records).filter(record => record.office === id);
      local.records = more ? [...local.records, ...records.filter(record => !local.records.some(old => old.id === record.id))] : records;
      local.cursor = result.next_before ?? null; local.pages = more ? local.pages + 1 : 1;
      state.refs[id].reload.textContent = "刷新记录";
      if (result.integrity) { state.refs[id].integrity.textContent = result.integrity.message || "记录校验状态已更新。"; state.refs[id].integrity.dataset.valid = String(result.integrity.valid); }
    } catch (error) { local.recordsError = error.message; }
    finally { local.loading = false; renderRecords(id); renderMetrics(id); }
  }
  async function refresh() {
    if (!host || state.loading) return;
    clearTimeout(state.timer); state.loading = true;
    const generation = state.generation;
    try {
      const data = await host.api("/api/security");
      if (generation !== state.generation) return;
      if (!Array.isArray(data.offices)) throw new Error("两署记录暂时没有读取完整，请重新连接。");
      state.data = data; state.error = ""; state.online = true;
      state.clockOffset = epoch(data.server_now) ? epoch(data.server_now) - Date.now() : 0;
      state.confirmedAt = Date.now() + state.clockOffset;
      for (const id of Object.keys(OFFICES)) {
        renderOffice(id);
        const local = officeState(id), data = officeData(id), key = JSON.stringify([data?.counts, data?.job?.id, data?.job?.phase, data?.latest_report?.id]);
        if (host.getView() === id && (local.updateKey !== key || !local.pages)) {
          if (local.pages <= 1) loadRecords(id); else state.refs[id].reload.textContent = "有新状态 · 刷新记录";
        }
        local.updateKey = key;
      }
      paintMap(); animate();
    } catch (error) {
      if (generation !== state.generation) return;
      state.error = error.message; state.online = false;
      for (const id of Object.keys(OFFICES)) renderOffice(id);
      paintMap();
    } finally {
      state.loading = false;
      const active = Object.keys(OFFICES).some(id => !actionFor(id).canStart);
      state.timer = setTimeout(() => { if (!document.hidden) refresh(); }, active ? 1800 : 12000);
    }
  }
  async function start(id) {
    const local = officeState(id); if (local.starting || !freshState() || !actionFor(id).canStart) return;
    local.starting = true; local.error = ""; state.generation++; renderOffice(id);
    try {
      const result = await host.api("/api/security/patrol", { method: "POST", body: { office: id } });
      for (const job of array(result.jobs)) { const office = officeData(job.office); if (office) office.job = job; }
      state.confirmedAt = Date.now() + state.clockOffset;
      paintMap(); animate();
      await refresh();
    } catch (error) { local.error = error.message; }
    finally { local.starting = false; renderOffice(id); }
  }
  async function saveSchedule(id) {
    const local = officeState(id), refs = state.refs[id]; if (local.saving || !state.online) return;
    local.saving = true; local.error = ""; state.generation++; renderOffice(id);
    try {
      const result = await host.api("/api/security/schedule", { method: "POST", body: { office: id, enabled: refs.enabled.checked, time: refs.time.value } });
      const saved = array(result.offices).find(office => office.id === id), office = officeData(id);
      if (office && saved?.schedule) office.schedule = saved.schedule;
      local.dirty = false; host.toast(`${OFFICES[id].name}的每日安排已保存。`);
    } catch (error) { local.error = error.message; }
    finally { local.saving = false; await refresh(); renderOffice(id); }
  }
  function renderMap(world, map) {
    if (!host || !world || !map) return;
    state.map = { world, map };
    for (const [id, config] of Object.entries(OFFICES)) {
      const agent = host.getState()?.agents?.find(item => item.id === config.agent);
      let actor = world.querySelector(`[data-security-office="${id}"]`);
      if (!agent) { actor?.remove(); continue; }
      if (!actor) {
        actor = button("", "security-patrol", () => host.selectAgent(config.agent, true)); actor.dataset.securityOffice = id; actor.dataset.mapControl = "";
        const picture = node("img"); picture.alt = "";
        actor.append(picture, node("span", "security-patrol-name", agent.name)); world.append(actor);
      }
      actor.querySelector(".security-patrol-name").textContent = agent.name;
    }
    paintMap(); animate();
  }
  function paintMap() {
    for (const [id, config] of Object.entries(OFFICES)) {
      const action = actionFor(id), actor = state.map?.world.querySelector(`[data-security-office="${id}"]`);
      renderAction(id, action);
      if (!actor) continue;
      const agent = host.getState()?.agents?.find(item => item.id === config.agent);
      const car = id === "inspection" && action.phase !== "home" && !action.onFoot;
      const vehicle = action.direction === "up" ? "police-car-back" : action.direction === "down" ? "police-car-front" : "police-car";
      const source = `/assets/${car ? vehicle : agent && host.getAvatar ? host.getAvatar(agent) : config.avatar}.svg`, picture = actor.querySelector("img");
      if (picture.getAttribute("src") !== source) picture.setAttribute("src", source);
      actor.style.left = `${Math.round(action.point[0])}px`; actor.style.top = `${Math.round(action.point[1])}px`;
      actor.classList.toggle("is-car", car); actor.classList.toggle("is-moving", freshState() && action.moving);
      actor.classList.toggle("selected", host.getSelected?.() === config.agent);
      actor.dataset.direction = action.direction; actor.dataset.phase = action.phase;
      const label = !freshState() ? "值勤状态暂未确认" : action.phase === "returning" ? "本次工作结束，正在返署" : action.working ? PHASES[officeData(id)?.job?.phase] : "在署内待命";
      actor.setAttribute("aria-label", `与${agent?.name || config.name}对话 · ${label}`); actor.title = label;
    }
  }
  function animate() {
    if (state.animation || !freshState() || document.hidden || !Object.keys(OFFICES).some(id => !actionFor(id).canStart)) return;
    state.animation = requestAnimationFrame(now => {
      state.animation = null;
      // Freshness may expire between two throttled paints while a request is
      // still pending. Flush the uncertain status once before stopping frames.
      if (!freshState()) { paintMap(); return; }
      if (now - state.frame >= (motionReduced() ? 1000 : 40)) { state.frame = now; paintMap(); }
      animate();
    });
  }
  function locate(id) {
    if (!OFFICES[id]) return;
    host.showView("town");
    const agent = host.getState()?.agents?.find(item => item.id === OFFICES[id].agent);
    if (agent?.plot?.pos) host.centerMap(actionFor(id).point);
  }
  function open(id) {
    if (!OFFICES[id] || !host) return;
    document.getElementById(`view-${id}`)?.scrollIntoView({ block: "start", behavior: "auto" });
    loadRecords(id); refresh();
  }
  function init(helpers) {
    host = helpers;
    for (const id of Object.keys(OFFICES)) { mount(id); renderOffice(id); renderRecords(id); }
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) { if (state.animation) cancelAnimationFrame(state.animation); state.animation = null; }
      else { refresh(); animate(); }
    });
    matchMedia("(prefers-reduced-motion: reduce)").addEventListener?.("change", () => { paintMap(); animate(); });
    window.addEventListener("online", refresh);
    refresh();
  }
  return { init, open, refresh, renderMap, locate, officeForAgent, agentForOffice, routeFor, sample, redact, subjectText, along, length };
});

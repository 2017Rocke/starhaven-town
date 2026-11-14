"use strict";

(() => {
  let host, root, state, tab = "plans", loading = false;
  const selectedSources = new Map();
  const statuses = { queued: "待执行", running: "执行中", review: "待人工验收", accepted: "已验收", failed: "检查未通过", blocked: "已阻止", interrupted: "重启中断", cancelled: "已取消", available: "可用", archived: "已归档", pending: "待发布", broken: "完整性异常", draft: "待审查", active: "已启用", disabled: "已停用" };
  const categories = { results: "成果", materials: "资料", books: "知识手记", skills: "技能" };
  const kinds = { note: "笔记", analysis: "分析", specification: "需求规格", "test-report": "测试报告", exercise: "练习", skill: "参考技能" };
  const h = (tag, cls, content) => host.el(tag, cls, content);
  const post = (path, body = {}) => host.api(path, { method: "POST", body });
  const name = id => state?.residents.find(r => r.id === id)?.name || id;
  const parse = value => typeof value === "string" ? JSON.parse(value) : value;
  function button(label, action, cls = "growth-button") {
    const b = h("button", cls, label); b.type = "button";
    b.addEventListener("click", async () => { b.disabled = true; try { await action(); } catch (e) { host.toast(e.message); } finally { b.disabled = false; } });
    return b;
  }
  function field(form, label, value = "", type = "text") {
    const group = h("label", "growth-field"); group.append(h("span", "", label));
    const input = h(type === "textarea" ? "textarea" : "input");
    if (type !== "textarea") input.type = type;
    input.value = value; input.required = true; group.append(input); form.append(group); return input;
  }
  function select(form, label, values, value) {
    const group = h("label", "growth-field"); group.append(h("span", "", label));
    const input = h("select");
    Object.entries(values).forEach(([key, label]) => { const option = h("option", "", label); option.value = key; input.append(option); });
    if (value) input.value = value;
    group.append(input); form.append(group); return input;
  }
  function checks(form, label, values, selected) {
    const group = h("fieldset", "growth-checks"); group.append(h("legend", "", label));
    const items = [];
    Object.entries(values).forEach(([key, label]) => {
      const item = h("label"); const input = h("input"); input.type = "checkbox"; input.value = key; input.checked = selected.includes(key);
      item.append(input, document.createTextNode(label)); group.append(item); items.push(input);
    }); form.append(group); return () => items.filter(v => v.checked).map(v => v.value);
  }
  function modal(title, build, submitLabel = "保存") {
    const dialog = h("dialog", "town-dialog growth-dialog"); dialog.setAttribute("aria-label", title);
    const form = h("form", "growth-form"); const heading = h("div", "growth-toolbar");
    heading.append(h("h2", "", title), button("关闭", () => dialog.close())); form.append(heading);
    const submit = build(form, dialog);
    if (submit) {
      const error = h("p", "growth-error"); error.setAttribute("role", "alert");
      const save = h("button", "growth-button primary", submitLabel); save.type = "submit";
      form.append(error, save);
      form.addEventListener("submit", async e => {
        e.preventDefault(); if (!form.reportValidity()) return;
        save.disabled = true; error.textContent = "";
        try { await submit(); dialog.close(); await refresh(); } catch (e) { error.textContent = e.message; } finally { save.disabled = false; }
      });
    }
    dialog.append(form); document.body.append(dialog); dialog.addEventListener("close", () => dialog.remove()); dialog.showModal();
    return dialog;
  }
  async function approval(action, payload) {
    const native = window.chrome?.webview || window.webkit?.messageHandlers?.townOwner;
    if (!native || !state?.native_available) throw new Error("请在桌面原生窗口完成确认；浏览器中可以查看和暂停。");
    const intent = await post("/api/owner/intents", { action, payload });
    native.postMessage({ type: "owner-intent", id: intent.id });
    host.toast("请核对原生窗口中的授权内容");
  }
  async function refresh() {
    if (!host || loading) return;
    loading = true;
    try { state = await host.api("/api/growth"); render(); }
    catch (e) { if (!state) root.replaceChildren(h("p", "growth-error", e.message), button("重新读取", refresh)); }
    finally { loading = false; }
  }
  async function settings() {
    const model = await host.api("/api/model-settings");
    const p = state.policy;
    modal("培养范围与预算", form => {
      form.append(h("p", "growth-note", "成长署每天为选中的居民安排岗位练习。聊天与训练共享每位居民的额度；修改和恢复运行需要原生确认。"));
      const direction = field(form, "整体培养方向", p.direction, "textarea");
      const residents = checks(form, "允许参与的居民", Object.fromEntries(state.residents.map(r => [r.id, r.name + " · " + r.role])), p.residents);
      const enabled = select(form, "自动安排与执行", { yes: "开启", no: "关闭" }, p.enabled ? "yes" : "no");
      const strict = select(form, "网络模式", { yes: "严格：仅已授权居民与端点", no: "兼容：普通聊天仅审计，不能自动训练" }, p.strict ? "yes" : "no");
      const endpoint = field(form, "允许使用的 API 服务地址", p.endpoints[0]?.base_url || model.base_url || "");
      const modelName = field(form, "允许使用的模型", p.endpoints[0]?.model || model.model || "");
      form.append(h("p", "growth-note", "当前受控训练支持 OpenAI 官方 API 的 gpt-4o / gpt-4o-mini 文本模型及已列入适配档案的固定版本。其他兼容服务仍可普通聊天，但暂不能保证其用量上限，因此不会自动训练。"));
      endpoint.required = false; modelName.required = false;
      const tokens = field(form, "每位居民每日 Token 预算", p.daily_tokens, "number"); tokens.min = "1000"; tokens.max = "5000000";
      const calls = field(form, "每位居民每日调用上限（一次独立训练通常需要三次）", p.daily_calls, "number"); calls.min = "1"; calls.max = "100";
      const perRequest = field(form, "单次完整输入与输出上限", p.request_total_cap, "number"); perRequest.min = "1000"; perRequest.max = "200000";
      const lifetime = field(form, "每位居民累计 Token 上限", p.lifetime_tokens, "number"); lifetime.min = "1000"; lifetime.max = "1000000000";
      const tasks = field(form, "每日基础任务数", p.daily_tasks, "number"); tasks.min = "1"; tasks.max = "3";
      const output = field(form, "单次最大输出 Token", p.max_output_tokens, "number"); output.min = "128"; output.max = "8192";
      const retries = select(form, "单项任务最多尝试次数", { 1: "1 次，不自动重试", 2: "最多 2 次，失败后手动重试" }, String(p.max_attempts));
      form.append(h("p", "growth-note", "额度按完整输入的 UTF-8 字节数保守预留，并非供应商账单金额。无用量回执时保留预留量，服务关闭期间不会继续训练。"));
      return () => approval("policy", { direction: direction.value, residents: residents(), enabled: enabled.value === "yes", strict: strict.value === "yes", endpoints: endpoint.value.trim() ? [{ base_url: endpoint.value.trim(), model: modelName.value.trim() }] : [], daily_tokens: Number(tokens.value), daily_calls: Number(calls.value), daily_tasks: Number(tasks.value), max_output_tokens: Number(output.value), max_attempts: Number(retries.value), request_total_cap: Number(perRequest.value), lifetime_tokens: Number(lifetime.value) });
    }, "发起原生确认");
  }
  function contract(agent) {
    modal(name(agent) + "的产出约定", form => {
      const category = select(form, "成果分类", categories, "results");
      const kind = select(form, "成果类型", kinds, "exercise");
      const count = field(form, "至少几份不同内容", "1", "number"); count.min = "1"; count.max = "100";
      const bytes = field(form, "每份至少 UTF-8 字节数", "128", "number"); bytes.min = "1";
      const days = field(form, "最近多少天", "7", "number"); days.min = "1"; days.max = "90";
      const accepted = select(form, "验收要求", { yes: "必须已验收", no: "记录产出即可" }, "yes");
      const weekdays = checks(form, "安排日期", { 1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日" }, ["1", "2", "3", "4", "5"]);
      const active = select(form, "约定状态", { yes: "启用", no: "关闭考核" }, "yes");
      form.append(h("p", "growth-note", "每份成果单独检查长度，再按内容去重计数。交付副本与手工导入不计为模型原创产出。"));
      form.append(h("p", "growth-note", "新约定次日生效，今天的统计口径保持固定。关闭考核也会保留历史版本与证据。"));
      return () => approval("contract", { agent_id: agent, weekdays: weekdays().map(Number), active: active.value === "yes", requirements: [{ category: category.value, kind: kind.value, count: Number(count.value), min_bytes: Number(bytes.value), days: Number(days.value), accepted: accepted.value === "yes" }] });
    }, "确认产出约定");
  }
  function importText() {
    modal("导入文字资料", form => {
      const owner = select(form, "所属居民", Object.fromEntries(state.residents.map(r => [r.id, r.name])), host.getSelected());
      const title = field(form, "资料标题"); const content = field(form, "资料正文", "", "textarea"); content.maxLength = 14000;
      form.append(h("p", "growth-note", "新资料默认仅所属居民本地可读。需要发给模型或交付其他居民时，请再设置资料权限。"));
      const request = crypto.randomUUID();
      return () => post("/api/artifacts/import", { owner: owner.value, title: title.value, content: content.value, request_id: request });
    });
  }
  function saveReply(message) {
    if (!state) { refresh().then(() => { if (state) saveReply(message); }); return; }
    modal("保存回复为成果", form => {
      const title = field(form, "成果标题", message.content.slice(0, 35));
      const category = select(form, "分类", categories, "results"); const kind = select(form, "类型", kinds, "note");
      category.addEventListener("change", () => { if (category.value === "skills") kind.value = "skill"; });
      form.append(h("p", "growth-note", "作者、原始回复、指纹与来源由服务记录。保存后可创建修订版；原始版本会保留。"));
      const request = crypto.randomUUID();
      return () => post("/api/artifacts/save-reply", { message_id: message.id, title: title.value, category: category.value, kind: kind.value, request_id: request });
    });
  }
  function permissions(artifact) {
    const p = parse(artifact.policy);
    modal("资料流转权限 · " + artifact.title, form => {
      const readers = checks(form, "允许读取的居民", Object.fromEntries(state.residents.map(r => [r.id, r.name])), p.readers);
      const destinations = checks(form, "允许外发的目的地", Object.fromEntries([["local", "本地存储"], ...state.policy.endpoints.map(e => [e.base_url, e.base_url])]), p.destinations);
      const delivery = select(form, "交付权限", { no: "禁止交付", yes: "允许交付给获准读者" }, p.deliverable ? "yes" : "no");
      form.append(h("p", "growth-note", "来源资料的限制会继续传递给修订版、交付副本和引用它生成的回复。授权当前副本不会放宽其来源权限。"));
      return () => approval("artifact-policy", { artifact_id: artifact.id, readers: readers(), destinations: destinations(), deliverable: delivery.value === "yes" });
    }, "发起权限确认");
  }
  async function detail(artifact) {
    const data = await host.api("/api/artifacts/" + artifact.id);
    artifact = data;
    modal(artifact.title, (form, dialog) => {
      form.append(h("p", "growth-note", `持有者 ${name(artifact.owner)} · 原始作者 ${name(data.original_author)} · ${statuses[artifact.status]} · ${artifact.bytes} 字节 · 来源 ${artifact.source_type}`));
      const content = h("pre", "growth-content", data.content); form.append(content);
      const actions = h("div", "growth-toolbar");
      actions.append(button("设置资料权限", () => { dialog.close(); permissions(artifact); }), button("创建修订版", () => {
        dialog.close(); modal("修订 · " + artifact.title, form => {
          const title = field(form, "标题", artifact.title); const content = field(form, "正文", data.content, "textarea"); content.maxLength = 14000;
          const request = crypto.randomUUID();
          return () => post("/api/artifacts/revise", { artifact_id: artifact.id, title: title.value, content: content.value, request_id: request });
        });
      }), button("验收", () => {
        dialog.close(); modal("验收 · " + artifact.title, form => {
          const note = field(form, "对照什么条件、检查了什么结果", "", "textarea");
          return () => approval("accept", { artifact_id: artifact.id, note: note.value });
        }, "原生确认验收");
      }), button("撤销验收", async () => { await post(`/api/artifacts/${artifact.id}/withdraw`); dialog.close(); await refresh(); }), button("交付", () => {
        dialog.close(); modal("交付成果", form => {
          const recipient = select(form, "接收居民", Object.fromEntries(state.residents.map(r => [r.id, r.name])));
          const request = crypto.randomUUID();
          return () => post("/api/artifacts/deliver", { artifact_id: artifact.id, recipient: recipient.value, request_id: request });
        });
      }), button("用于聊天", () => {
        const agent = host.getSelected(); const list = selectedSources.get(agent) || [];
        selectedSources.set(agent, [...new Set([...list, artifact.id])].slice(0, 10)); dialog.close(); renderAttachments();
        host.toast("已加入当前居民下一条消息的引用；发送前仍会检查权限");
      }), button("归档", async () => { await post(`/api/artifacts/${artifact.id}/archive`); dialog.close(); await refresh(); }));
      if (artifact.category === "skills" && artifact.kind === "skill") actions.append(button("提交技能审查", async () => {
        dialog.close(); modal("提交参考技能", form => {
          const choices = { "": "不依赖其他技能", ...Object.fromEntries(state.skills.filter(s => s.agent_id === artifact.owner).map(s => [s.id, s.id.slice(0, 8) + " · " + statuses[s.status]])) };
          const dep = select(form, "依赖的精确版本", choices);
          return () => post("/api/skills/propose", { artifact_id: artifact.id, dependencies: dep.value ? [dep.value] : [] });
        });
      }));
      form.append(actions);
    });
  }
  function renderAttachments() {
    const target = document.querySelector("#growth-chat-sources"); if (!target) return;
    const agent = host.getSelected(); const ids = selectedSources.get(agent) || []; target.replaceChildren();
    if (ids.length) target.append(h("span", "", `本条消息引用 ${ids.length} 份资料`), button("清除", () => { selectedSources.delete(agent); renderAttachments(); }));
  }
  function project() {
    modal("交给领航的项目", form => {
      const title = field(form, "项目名称"); const goal = field(form, "整体目标与验收标准", "", "textarea");
      const sources = checks(form, "参考资料（每个接收居民都需要读取权限）", Object.fromEntries(state.artifacts.filter(a => a.status === "available").slice(0, 20).map(a => [a.id, a.title])), []);
      form.append(h("p", "growth-note", "领航按岗位匹配最多三位已授权居民生成文本交付任务。实际执行遵守自动任务开关与共享预算；成果等待你验收。"));
      return () => post("/api/projects", { title: title.value, goal: goal.value, sources: sources() });
    }, "安排项目任务");
  }
  function card(title, description) {
    const item = h("article", "growth-card"); item.append(h("h3", "", title), h("p", "growth-note", description)); return item;
  }
  function contractDetails(agent) {
    modal(name(agent.id) + "的产出明细", form => {
      const outcomes = { met: "已达成", partial: "部分完成", no_output: "暂无合格产出", not_scheduled: "未安排考核" };
      const reasons = { classification: "分类或类型不符", too_short: "单份长度不足", demo_or_copy: "演示、人工整理或交付副本", not_accepted: "尚未验收", integrity: "完整性校验失败", duplicate_content: "内容重复" };
      function report(result, title) {
        const section = h("section", "growth-card");
        section.append(h("h3", "", title), h("p", "growth-note", outcomes[result.outcome] || result.outcome));
        result.requirements.forEach(r => {
          section.append(h("p", "", `${categories[r.category]} / ${kinds[r.kind]}：${r.actual}/${r.count} 份 · 最近 ${r.days} 天 · 每份至少 ${r.min_bytes} 字节${r.accepted ? " · 须已验收" : ""}`));
          r.evidence.forEach(e => {
            const artifact = state.artifacts.find(a => a.id === e.id);
            section.append(button(artifact?.title || e.id, () => detail(artifact || { id: e.id, title: "成果明细" })));
          });
          if (r.excluded.length) {
            const excluded = h("details", "growth-note"); excluded.append(h("summary", "", `${r.excluded.length} 条未计入记录`));
            r.excluded.forEach(e => excluded.append(h("p", "", `${state.artifacts.find(a => a.id === e.id)?.title || e.id}：${reasons[e.reason] || e.reason}`)));
            section.append(excluded);
          }
        });
        form.append(section);
      }
      if (agent.contract.effective_day) form.append(h("p", "growth-note", "产出约定将在 " + agent.contract.effective_day + " 生效。"));
      report(agent.contract, "当前统计");
      form.append(h("p", "growth-note", "以下为保留的最近统计快照。验收撤销或证据变化会追加修订；同一天可有多份记录。统计按北京时间，应用运行时更新。"));
      (state.daily_stats || []).filter(s => s.agent_id === agent.id).forEach(s => report(parse(s.result), s.day + " · 记录于 " + s.created_at));
    });
  }
  function render() {
    if (!root || !state) return;
    const p = state.policy; const header = h("div", "growth-toolbar");
    header.append(h("div", "", p.paused ? "培养已暂停" : p.enabled ? "成长署正在安排培养" : "自动培养未开启"), button("培养与网络设置", settings), button("立即暂停", async () => { await post("/api/growth/pause"); await refresh(); }), button("刷新", refresh));
    const intro = h("p", "growth-note", "你定方向与预算；成长署安排岗位训练，领航分配真实项目，稽查署检查网络与资料流转。");
    const navigation = h("div", "growth-toolbar"); navigation.setAttribute("aria-label", "成长署工作区");
    Object.entries({ plans: "培养计划", tasks: "训练与项目", artifacts: "成果与资料", skills: "参考技能", budget: "调用与预算" }).forEach(([key, label]) => {
      const b = button(label, () => { tab = key; render(); }, "growth-button" + (tab === key ? " active" : "")); b.setAttribute("aria-pressed", String(tab === key)); navigation.append(b);
    });
    const body = h("div", "growth-cards"); root.replaceChildren(header, intro, navigation, body);
    if (!state.integrity) root.prepend(h("p", "growth-error", "成长档案校验链异常，请暂停自动任务并检查数据。"));
    if (tab === "plans") {
      body.append(button("生成今日计划", async () => { const result = await post("/api/growth/plan"); host.toast(`已生成 ${result.created} 项任务`); await refresh(); }));
      state.residents.forEach(r => {
        const profile = r.profile, plan = state.plans.find(p => p.agent_id === r.id);
        const item = card(r.name + " · " + r.role, plan?.goal || "尚无培养计划；在设置中选择参与范围后，成长署会自动安排。");
        item.append(h("p", "", `已验收 ${profile.accepted} · 独立检查通过 ${profile.independently_passed} · 有对比改善的试验 ${profile.verified_trials}`));
        if (profile.gaps.length) item.append(h("p", "growth-note", "待补足：" + profile.gaps.join("、")));
        item.append(h("p", "growth-note", r.contract.configured ? `产出约定：${r.contract.satisfied ? "已满足" : "待完成"} · ` + r.contract.requirements.map(x => `${x.actual}/${x.count} 份`).join("；") : "尚未设置产出约定"));
        item.append(button("产出明细与日记录", () => contractDetails(r)), button("设置产出约定", () => contract(r.id)), button("暂停该居民", async () => { await post(`/api/residents/${r.id}/revoke`); await refresh(); })); body.append(item);
      });
    } else if (tab === "tasks") {
      body.append(button("给领航一个整体项目", project));
      state.tasks.forEach(t => {
        const item = card(t.title, `${name(t.agent_id)} · ${statuses[t.status] || t.status} · 尝试 ${t.attempts} 次`);
        if (t.error) item.append(h("p", "growth-error", t.error));
        if (t.evaluation) {
          const result = parse(t.evaluation);
          if (result.candidate) item.append(h("p", "", `基线 ${result.baseline.score}/${result.baseline.total} → 使用练习参考 ${result.candidate.score}/${result.candidate.total}`));
          item.append(h("p", "growth-note", result.note));
          if (result.evidence_id) item.append(button("查看独立验收证据", async () => {
            const evidence = await host.api("/api/training-evidence/" + result.evidence_id);
            modal("本次试验的完整证据", form => {
              form.append(h("p", "growth-note", "保留的基线、独立案例、参考成果版本及候选回答，可重新计算分数。"), h("pre", "growth-content", JSON.stringify(evidence, null, 2)));
            });
          }));
        }
        const artifact = state.artifacts.find(a => a.id === t.artifact_id);
        if (artifact) item.append(button("查看成果", () => detail(artifact)));
        if (["queued", "running"].includes(t.status)) item.append(button("取消任务", async () => { await post(`/api/training/${t.id}/cancel`); await refresh(); }));
        if (["failed", "blocked", "cancelled", "interrupted"].includes(t.status)) item.append(button("按当前权限重试", async () => { await post(`/api/training/${t.id}/retry`); await refresh(); }));
        body.append(item);
      });
      if (!state.tasks.length) body.append(h("p", "growth-note", "暂无任务。开启培养后会自动生成，也可以交给领航一个整体项目。"));
    } else if (tab === "artifacts") {
      body.append(button("导入文字资料", importText));
      state.artifacts.forEach(a => { const item = card(a.title, `${name(a.owner)} · ${categories[a.category]} / ${kinds[a.kind]} · ${statuses[a.status]} · ${a.bytes} 字节`); item.append(button("查看与操作", () => detail(a))); body.append(item); });
      if (!state.artifacts.length) body.append(h("p", "growth-note", "在已完成的助手回复下点击“保存成果”，或在这里导入资料。"));
    } else if (tab === "skills") {
      body.append(h("p", "growth-note", "技能是经过确认的参考文字，不会执行命令或增加工具权限。每位居民最多启用 5 个版本，每次引用至多 2 个文本（含依赖），总计 8 KiB。"));
      state.skills.forEach(s => {
        const a = state.artifacts.find(a => a.id === s.artifact_id), checks = parse(s.checks);
        const item = card(a?.title || s.id, `${name(s.agent_id)} · ${statuses[s.status]} · 版本 ${s.id.slice(0, 8)}`);
        item.append(h("p", "growth-note", [...checks.hard_errors, ...checks.risk_flags].join("；") || "机器检查未发现阻断项；仍需原生确认"));
        if (a) item.append(button("阅读全文", () => detail(a)));
        if (s.status === "active") item.append(button("立即停用", async () => { await post(`/api/skills/${s.id}/disable`); await refresh(); }));
        else if (!checks.hard_errors.length) item.append(button("确认启用", () => approval("enable-skill", { skill_id: s.id })));
        body.append(item);
      });
    } else {
      body.append(h("p", "growth-note", `每位居民每日上限：${p.daily_tokens.toLocaleString()} Token / ${p.daily_calls} 次。发送后取消仍可能计费；跨日结算归入调用开始的日期。下列为最近 100 次调用。`));
      body.append(h("p", "growth-note", p.strict ? "余额包含已发调用和在途预留；无回执的调用保守记账。" : "兼容模式展示按已记录用量计算的余额，暂不强制限额；未计量的外部调用不代表零费用。"));
      (state.balances || []).forEach(b => {
        const item = card(name(b.agent_id), `${b.day} · 今日已记账 ${b.charged_today.toLocaleString()} / 在途预留 ${b.reserved_today.toLocaleString()} Token`);
        item.append(h("p", "", `今日剩余 ${b.daily_remaining.toLocaleString()} Token / ${b.calls_remaining} 次 · 累计剩余 ${b.lifetime_remaining.toLocaleString()} Token`));
        if (b.unknown_calls || b.unmetered_calls) item.append(h("p", "growth-note", `历史无用量回执 ${b.unknown_calls} 次 · 外部未计量 ${b.unmetered_calls} 次`));
        body.append(item);
      });
      state.calls.forEach(c => body.append(card(`${name(c.agent_id)} · ${c.purpose === "training" ? "训练" : "聊天"}`, c.status === "unmetered" ? `${c.day} · Codex 外部调用：用量未计量，不代表免费` : `${c.day} · ${c.model} · ${c.status === "unknown" ? "用量未知，保守计账" : c.status === "reserved" ? "额度已预留" : c.status === "released" ? "发送前释放" : "已结算"} · 预留 ${c.reserved} / 记账 ${c.charged} Token`)));
    }
    const recent = h("details", "growth-note"); recent.append(h("summary", "", "最近确认记录"));
    const actionNames = { policy: "培养与网络设置", "artifact-policy": "资料权限", accept: "成果验收", contract: "产出约定", "enable-skill": "技能启用" };
    const decisionNames = { pending: "等待原生确认", approved: "已确认", rejected: "已取消", expired: "已过期" };
    state.confirmations.forEach(c => recent.append(h("p", "", `${actionNames[c.action] || c.action} · ${decisionNames[c.status] || c.status}`)));
    root.append(recent);
    renderAttachments();
  }
  window.StarhavenGrowthWorkspace = {
    init(context) {
      host = context; root = document.querySelector("#growth-office");
      window.addEventListener("town-owner-complete", refresh);
      window.chrome?.webview?.addEventListener("message", e => { if (e.data?.type === "owner-complete") refresh(); });
      setInterval(() => { if (!document.hidden && host.getView() === "growth") refresh(); }, 5000);
    },
    open: refresh, saveReply, renderAttachments,
    sources: agent => [...(selectedSources.get(agent) || [])],
    clearSources: agent => { selectedSources.delete(agent); renderAttachments(); },
  };
})();

import { $, api, el, tell, names, results } from "./shared.js";

let model = null,
  manualTask = null,
  manualSeq = 0,
  pending = false,
  generation = 0;

const verificationTitles = {
  needs_input: "任务等待输入或授权，请在来源应用继续", unknown: "尚不能确定任务结果，请检查来源应用",
  waiting: "等待真实任务", working: "已收到开始，等待本轮完成",
  verified: "已收到同一任务的开始与完成", cancelled: "本轮已取消，请另发一条完整任务",
  error: "本轮报告异常，请检查 AI 应用后重试", timeout: "工作状态已超时，尚未确认完成",
  incomplete: "事件不完整，尚不能确认此用法", disabled: "来源已关闭，请先开启接入",
};
let overviewPreview = null;
function stopOverviewPreview() {
  const previous = overviewPreview;
  overviewPreview = null;
  if (previous?.hardware) api("/api/preview", {action: "stop", id: previous.id}).catch(() => {});
  if (model) render(model);
}
function overviewState(data) {
  const preview = overviewPreview;
  if (!preview) return null;
  if (preview.hardware !== (data.settings.mode === "hardware") || data.settings.paused || (data.brightness_off ?? data.settings.brightness === 0)) {
    stopOverviewPreview(); return null;
  }
  if (preview.hardware) {
    if (data.display.preview === preview.id) preview.deadline = performance.now() + data.display.remaining_ms;
    else if (!preview.starting) { overviewPreview = null; return null; }
    return data.display.state;
  }
  if (data.display.key !== preview.originalKey || data.display.state !== "idle" || performance.now() >= preview.deadline) {
    overviewPreview = null; return null;
  }
  return performance.now() < preview.deadline - 5000 ? "working" : "done";
}
function overviewCountdown() {
  const remaining = overviewPreview ? Math.max(0, Math.ceil((overviewPreview.deadline - performance.now()) / 1000)) : 0;
  $("overviewCountdown").hidden = !remaining;
  $("overviewCountdown").textContent = remaining ? `演示剩余 ${remaining} 秒` : "";
  $("demo").textContent = overviewPreview ? "退出演示" : "开始演示 ↗";
}
function render(data) {
  model = data;
  const previewState = overviewState(data);
  overviewCountdown();
  $("appVersion").textContent = "DESKTOP · " + data.app_version;
  $("footerVersion").textContent = data.app_version;
  const d = data.device,
    mode = previewState || data.display.state,
    mute = data.settings.paused;
  const hardware = data.settings.mode === "hardware";
  const connected = hardware && d.online;
  const connecting = hardware && !connected && d.connecting && !data.maintenance;
  const connection = d.port?.startsWith("wifi://") ? "Wi-Fi" : d.port ? "USB" : "设备";
  document.body.dataset.mode = hardware ? "hardware" : "virtual";
  $("hardwareWarning").hidden = !hardware || connected || connecting;
  $("hardwareWarningText").textContent = data.maintenance
    ? "设备维护中，硬件灯光暂不可用。" : "尚未连接设备，硬件灯光暂不可用。";
  $("virtualDevice").hidden = hardware;
  $("hardwareControls").hidden = !hardware;
  $("modeHint").textContent = hardware
    ? "连接火焰灯接收任务提醒，屏幕同步显示状态。"
    : "屏幕烛火随任务变化，不查找或连接设备。";
  for (const button of document.querySelectorAll("[data-mode]")) {
    button.setAttribute("aria-pressed", String(button.dataset.mode === document.body.dataset.mode));
    button.disabled = pending || data.maintenance;
  }
  document.dispatchEvent(new CustomEvent("flame-mode", {detail: {hardware, device: d, paired: data.hardware_paired, devices: data.devices}}));
  $("deviceBadge").textContent = !hardware ? "虚拟模式 · 无需硬件" : data.maintenance && !connected
    ? "设备维护中"
    : connected
      ? "已连接 " + (d.connected_count ?? 1) + " 台设备"
      : connecting ? "正在连接设备" : "硬件未连接 · 屏幕提醒";
  const showConnection = connected && !(d.connected_count > 1);
  $("deviceBadge").title = showConnection ? connection + " 连接" : "";
  if (showConnection && d.port) {
    const mark = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    mark.setAttribute("viewBox", "0 0 24 24");
    mark.setAttribute("class", "connection-icon");
    mark.setAttribute("role", "img");
    mark.setAttribute("aria-label", connection + " 连接");
    mark.dataset.connection = connection === "Wi-Fi" ? "wifi" : "usb";
    mark.innerHTML = connection === "Wi-Fi"
      ? '<path d="M3 8a15 15 0 0 1 18 0M6 12a10 10 0 0 1 12 0M9 16a5 5 0 0 1 6 0"/><circle cx="12" cy="20" r=".7"/>'
      : '<rect x="3" y="4" width="6" height="7" rx="1"/><path d="M5 2v2m2-2v2M6 11v4a5 5 0 0 0 10 0v-2"/><rect x="13" y="7" width="6" height="6" rx="1"/><path d="M15 5v2m2-2v2"/>';
    $("deviceBadge").append(mark);
  }
  $("deviceBadge").classList.toggle("on", showConnection);
  $("scene").classList.remove("offline");
  $("welcome").textContent = hardware
    ? "屏幕与火焰灯一起接收任务，应用接入和偏好自动沿用。"
    : "接入常用 AI 应用，在任务完成时收到提醒。";
  $("sceneKind").textContent = data.display.simulated ? "测试提醒 · 不计入真实验证" : "实时任务 · 屏幕提醒";
  $("orb").className =
    "orb " + mode + (data.settings.reduced ? " reduced" : "");
  $("displayTitle").textContent = data.settings.paused
      ? "暂时安静"
      : {
          idle: "安静等候",
          working: "正在进行，慢慢来",
          done: "完成了，回来看看",
          error: "需要你看一眼",
        }[mode];
  $("displayHint").textContent = (data.brightness_off ?? data.settings.brightness === 0)
    ? "灯光亮度为 0%，请在「我的设备」中调节"
    : data.settings.quiet_enabled && data.display.key === "muted" && !mute
      ? "当前处于静默时段，事件仍会记录"
      : mute
      ? "恢复提醒后接收新的提醒"
      : {
          idle: "有任务完成时，我会提醒你",
          working: "柔和呼吸 · 不提前判断结果",
          done: "暖色火苗 · 提示后自动恢复",
          error: "慢节奏双脉冲 · 与成功提示区分",
        }[mode];
  $("delivery").textContent = !connected
    ? "APP 状态 · 无需设备回执"
    : data.display.device_confirmed
      ? "设备已接受调度"
      : "等待设备回执";
  $("active").textContent = data.active_count;
  $("queued").textContent = data.queue_count;
  $("deviceDetail").textContent = connected
    ? (d.port && d.port.startsWith("wifi://") ? "Wi-Fi · " : "USB · ") +
      d.device_id +
      " · " +
      d.firmware_version
    : connecting ? "正在确认设备连接…" : hardware ? d.last_error || "尚未连接设备，请连接火焰灯" : "虚拟模式 · 设备查找与连接已关闭";
  document
    .querySelectorAll("[data-state]")
    .forEach((b) => b.classList.toggle("selected", b.dataset.state === mode));
  for (const key of ["duration", "quiet_start", "quiet_end"]) {
    if (document.activeElement !== $(key)) $(key).value = data.settings[key];
  }
  for (const key of ["paused", "quiet_enabled", "hold_until_activity", "desktop_notifications"])
    $(key).checked = data.settings[key];
  $("sources").replaceChildren();
  const group = (id) => ["codex", "claude-code", "deepseek-harness", "qoder", "gemini-cli", "opencode"].includes(id) ? 0 : id.endsWith("-web") ? 1 : id.endsWith("-desktop") ? 2 : 3;
  const groupTitles = ["编程工具", "AI 网页", "桌面客户端", "本地 API 与测试来源"];
  const brands = new Set(["codex", "claude", "qoder", "gemini", "opencode", "chatgpt", "deepseek", "qianwen", "doubao", "yuanbao", "kimi", "zhipu"]);
  const brandAliases = {"claude-code": "claude", "deepseek-harness": "deepseek", "gemini-cli": "gemini"};
  let previousGroup = null;
  for (const source of [...data.sources].sort((a, b) => group(a.id) - group(b.id))) {
    const currentGroup = group(source.id);
    if (currentGroup !== previousGroup) {
      if (previousGroup !== null) $("sources").append(el("hr", "", "source-divider"));
      $("sources").append(el("h3", groupTitles[currentGroup], "source-category-title"));
    }
    previousGroup = currentGroup;
    const row = el("div", "", "source");
    const brand = brandAliases[source.id] || source.id.replace(/-(web|desktop)$/, "");
    if (brands.has(brand)) {
      const mark = document.createElement("img");
      mark.className = "source-icon brand-icon";
      mark.src = "/brands/" + brand + ".png";
      mark.alt = "";
      row.append(mark);
    } else {
      row.append(el("div", source.id === "local-script" ? ">_" : source.id === "demo" ? "▷" : "✧", "source-icon"));
    }
    const info = el("div", "", "source-info");
    const description =
      source.description +
      " · " + (verificationTitles[data.verification?.[source.id]?.status] || "测试不计入真实验证");
    info.append(el("b", source.name), el("small", description));
    row.append(info);
    const check = document.createElement("input");
    check.type = "checkbox";
    check.className = "switch";
    check.checked = source.enabled;
    check.setAttribute("aria-label", "启用" + source.name);
    check.onchange = async () => {
      const enabled = check.checked;
      const saved = await act("/api/settings", {
        source_enabled: {
          ...model.settings.source_enabled,
          [source.id]: enabled,
        },
      });
      if (saved && source.id === "chatgpt-desktop" && enabled) {
        await act("/api/desktop-clients/permissions", {});
      }
    };
    row.append(check);
    $("sources").append(row);
  }
  $("events").replaceChildren();
  if (!data.events.length) {
    const tr = document.createElement("tr"),
      td = el("td", "还没有事件。接入常用来源后，发起一条真实任务。", "empty");
    td.colSpan = 4;
    tr.append(td);
    $("events").append(tr);
  }
  for (const event of data.events) {
    const tr = document.createElement("tr");
    tr.append(
      el(
        "td",
        new Date(event.created * 1000).toLocaleTimeString("zh-CN", {
          hour12: false,
        }),
      ),
      el(
        "td",
        (data.sources.find((source) => source.id === event.source)?.name ||
          "未知来源") + (event.simulated ? "（自测）" : ""),
      ),
      el(
        "td",
        event.reason === "cancelled"
          ? "已取消"
          : event.source === "codex" && event.state === "done"
            ? "回复完成"
            : names[event.state],
        "state-" + event.state,
      ),
    );
    const td = document.createElement("td");
    td.append(el("span", results[event.status] || event.status, "pill"));
    tr.append(td);
    $("events").append(tr);
  }
  const latest = data.events[0];
  for (let i = 1; i <= 4; i++)
    $("step" + i).className =
      latest &&
      i <=
        (latest.status === "applied"
          ? 4
          : ["queued", "screen"].includes(latest.status)
            ? 3
            : latest.status === "unmatched"
              ? 1
              : 2)
        ? "ok"
        : "";
  $("step3").textContent = latest?.status === "screen" ? "③ APP 已呈现" : "③ 提醒调度";
}
async function refresh() {
  if (pending) return;
  const requestedGeneration = generation;
  try {
    const data = await api("/api/status");
    if (requestedGeneration !== generation || pending) return;
    render(data);
  } catch (error) {
    if (requestedGeneration !== generation || pending) return;
    $("deviceBadge").textContent = "Hub 未连接";
    $("deviceBadge").classList.remove("on");
    $("scene").classList.add("offline");
    $("displayTitle").textContent = "本地服务未连接";
    $("displayHint").textContent = "任务状态暂时无法更新，恢复服务后自动同步";
    tell("Hub 未连接，请重新打开 SeeEnd App。", true);
  }
}
async function act(path, body) {
  generation += 1;
  pending = true;
  try {
    const r = await api(path, body);
    tell(
      r.message || (path === "/api/demo"
        ? "模拟任务已开始，6 秒后产生明确的演示完成事件。"
        : "操作已接收，正在同步任务状态。"),
    );
    return r;
  } catch (error) {
    tell(error.message, true);
    return null;
  } finally {
    pending = false;
    await refresh();
  }
}
for (const button of document.querySelectorAll("[data-mode]")) {
  button.onclick = () => {
    if (pending || !model || model.settings.mode === button.dataset.mode) return;
    document.querySelectorAll("[data-mode]").forEach(b => { b.disabled = true; });
    return act("/api/settings", {mode: button.dataset.mode});
  };
}
$("connectHardware").onclick = () => document.querySelector('[data-tab="device"]').click();
$("backToModes").onclick = () => document.querySelector('[data-tab="home"]').click();
function integrationParameters(card) {
  const body = {source: card.dataset.cli};
  const config = card.querySelector('[data-cli-config]')?.value.trim();
  const profile = card.querySelector('[data-cli-profile]')?.value;
  if (config) body.config_dir = config;
  if (profile) body.profile = profile;
  return body;
}
function integrationReportText(data) {
  const lines = [data.summary || (data.configuration_ready ? '配置已就绪，尚需真实任务验证。' : '接入配置有问题。')];
  if (data.next_action) lines.push('下一步：' + data.next_action);
  for (const check of data.checks || []) {
    lines.push((check.status === 'ok' ? '✓ ' : check.status === 'pending' ? '○ ' : '! ') + check.message + (check.action ? '\n  ' + check.action : ''));
  }
  if (!data.checks?.length) lines.push(...(data.issues || []));
  lines.push(...Object.values(data.signals || {}).map(s => s.name + '：收到 ' + s.received + ' / 设备确认 ' + s.applied));
  lines.push(...(typeof data.limitations === 'string' ? [data.limitations] : data.limitations || []));
  if (data.trust_note) lines.push(data.trust_note);
  if (data.config_dir) lines.push('配置目录：' + data.config_dir);
  if (data.installed_profiles?.length) lines.push('已连接 Profile：' + data.installed_profiles.join('、'));
  if (data.evidence_scope) lines.push(data.evidence_scope);
  return lines.join('\n');
}
async function loadIntegrationGuide(card) {
  const query = new URLSearchParams(integrationParameters(card)).toString();
  const details = card.querySelector('[data-integration-guide]');
  const copy = details.querySelector('[data-copy-integration]');
  copy.disabled = true;
  try {
    const data = await api('/api/integration-guide?' + query);
    if (query !== new URLSearchParams(integrationParameters(card)).toString()) return;
    details.querySelector('p').textContent = ['自动备份，增量添加，不替换整个配置文件。',
      '配置文件：\n' + data.files.join('\n'), '修改范围：\n' + data.changes.join('\n')].join('\n\n');
    details.querySelector('[data-manual-guide]').textContent = data.manual + '\n\n' + data.preparation.join('\n');
    details.querySelector('pre').textContent = data.snippet;
    const link = details.querySelector('a');
    link.href = data.docs_url;
    copy.textContent = data.copy_label;
    copy.disabled = false;
    copy.onclick = async () => {
      try { await navigator.clipboard.writeText(data.snippet); copy.textContent = '已复制'; }
      catch { details.querySelector('pre').focus(); tell('请选中下方增量片段复制；不要替换整个配置文件。', true); }
    };
  } catch (error) { details.querySelector('p').textContent = '引导未加载：' + error.message; }
}
$('checkCodex').onclick = async () => {
  const button = $('checkCodex');
  button.disabled = true;
  $('codexCheck').textContent = '正在检查配置和真实接收记录…';
  try { $('codexCheck').textContent = integrationReportText(await api('/api/codex')); }
  catch (error) { $('codexCheck').textContent = '检查未完成：' + error.message; }
  finally { button.disabled = false; }
};
async function checkCli(source = null) {
  const selectedCard = source ? document.querySelector(`[data-cli="${source}"]`) : null;
  const query = selectedCard ? new URLSearchParams(integrationParameters(selectedCard)).toString() : null;
  if (selectedCard) selectedCard.querySelector('[data-cli-status]').textContent = '正在检查配置和真实接收记录…';
  try {
    const data = await api(query ? '/api/integration-check?' + query : '/api/cli-integrations');
    if (selectedCard && query !== new URLSearchParams(integrationParameters(selectedCard)).toString()) return;
    for (const item of data.integrations || [data]) {
      const card = document.querySelector(`[data-cli="${item.source_id}"]`);
      if (!card) continue;
      card.querySelector('[data-cli-status]').textContent = item.feedback || item.summary || (item.configuration_ready ? '配置已就绪，尚需真实任务验证。' : '接入配置有问题：' + (item.issues || []).join('；'));
      card.querySelector('[data-cli-action="install"]').textContent = item.connect_label || '连接';
      card.querySelector('[data-cli-status]').dataset.status = item.status || '';
      card.querySelector('[data-cli-details]').textContent = integrationReportText(item);
      const profile = card.querySelector('[data-cli-profile]');
      if (profile) {
        const selected = profile.value || item.profile || '';
        profile.replaceChildren();
        const placeholder = el('option', '请选择已有 Profile');
        placeholder.value = '';
        profile.append(placeholder);
        for (const name of item.profiles || []) {
          const option = el('option', name); option.value = name; profile.append(option);
        }
        profile.value = selected || (item.profiles?.length === 1 ? item.profiles[0] : '');
      }
    }
  } catch (error) {
    for (const card of document.querySelectorAll('[data-cli]')) {
      if (!source || card.dataset.cli === source) card.querySelector('[data-cli-status]').textContent = '检查未完成：' + error.message;
    }
  }
}
for (const card of document.querySelectorAll('[data-cli]')) {
  const details = el('details');
  details.dataset.integrationGuide = '';
  details.append(el('summary', '高级设置'));
  const config = card.querySelector('[data-cli-config]');
  if (config) details.append(config);
  details.append(el('p', '展开后加载配置详情。', 'muted'));
  const manual = el('details');
  manual.append(el('summary', '手动配置'));
  const manualGuide = el('p', '', 'muted'); manualGuide.dataset.manualGuide = '';
  manual.append(manualGuide);
  const copy = el('button', '复制增量配置 / 命令', 'btn'); copy.disabled = true;
  copy.dataset.copyIntegration = '';
  const link = el('a', '官方配置说明'); link.target = '_blank'; link.rel = 'noopener noreferrer';
  const snippet = el('pre'); snippet.tabIndex = 0;
  manual.append(copy, link, snippet);
  const extraActions = el('div', '', 'actions');
  for (const action of card.querySelectorAll('[data-cli-action="uninstall"], [data-trigger]')) extraActions.append(action);
  details.append(extraActions, manual, card.querySelector('[data-cli-details]').parentElement);
  card.append(details);
  details.ontoggle = () => { if (details.open) loadIntegrationGuide(card); };
  for (const field of card.querySelectorAll('[data-cli-config], [data-cli-profile]')) {
    field.onchange = () => { copy.disabled = true; if (details.open) loadIntegrationGuide(card); };
  }
  card.querySelector('[data-cli-check]').onclick = () => checkCli(card.dataset.cli);
  for (const button of card.querySelectorAll('[data-cli-action]')) {
    button.onclick = async () => {
      const body = integrationParameters(card);
      const status = card.querySelector('[data-cli-status]');
      if (card.querySelector('[data-cli-profile]') && !body.profile) { status.textContent = '请选择实际使用的已有 Profile。'; return; }
      button.disabled = true;
      status.textContent = '正在增量更新本应用接入…';
      try {
        const result = await act('/api/integrations/' + button.dataset.cliAction, body);
        if (result) { await checkCli(body.source); tell(result.message || '接入已更新。'); }
        else status.textContent = '自动接入未完成：' + $('message').textContent;
      } catch (error) { status.textContent = error.message; }
      finally { button.disabled = false; }
    };
  }
}
checkCli();
$("demo").onclick = async () => {
  if (overviewPreview) { stopOverviewPreview(); return; }
  if (!model || model.display.state !== "idle" || model.settings.paused || (model.brightness_off ?? model.settings.brightness === 0)) {
    tell("请等待当前任务结束，并恢复亮度和提醒后再演示。", true); return;
  }
  const preview = {id: crypto.randomUUID(), hardware: model.settings.mode === "hardware",
    originalKey: model.display.key, deadline: performance.now() + 10000, starting: true};
  overviewPreview = preview;
  if (preview.hardware) {
    const result = await act("/api/preview", {action: "start", id: preview.id});
    if (!result) overviewPreview = null;
  }
  preview.starting = false;
  render(model);
};
setInterval(() => {
  if (overviewPreview && !overviewPreview.hardware && model) render(model);
  overviewCountdown();
}, 250);
document.addEventListener("visibilitychange", () => { if (document.hidden) stopOverviewPreview(); });
document.addEventListener("click", event => { if (event.target.closest("[data-tab]")) stopOverviewPreview(); });
for (const key of [
  "duration",
  "quiet_start",
  "quiet_end",
  "paused",
  "quiet_enabled",
  "hold_until_activity",
  "desktop_notifications",
])
  $(key).onchange = () =>
    act("/api/settings", {
      [key]: ["paused", "quiet_enabled", "hold_until_activity", "desktop_notifications"].includes(key)
        ? $(key).checked
        : ["duration"].includes(key)
          ? Number($(key).value)
          : $(key).value,
    });
for (const button of document.querySelectorAll("[data-state]"))
  button.onclick = async () => {
    const state = button.dataset.state;
    if (manualTask) {
      manualSeq++;
    } else {
      manualSeq = 1;
    }
    const payload = { state, seq: manualSeq };
    if (manualTask) payload.task_id = manualTask;
    if (state === "idle") payload.reason = "cancelled";
    const r = await act("/api/test", payload);
    if (r) manualTask = state === "working" ? r.task_id : null;
  };
refresh().then(() => { if (model) tell("准备好了。无需硬件，接入常用应用后即可用真实任务验证。"); });
setInterval(refresh, 1000);

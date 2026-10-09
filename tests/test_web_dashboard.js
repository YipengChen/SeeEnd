const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { JSDOM } = require("jsdom");

function dashboard() {
  const dom = new JSDOM(fs.readFileSync("aiflame/assets/web/index.html", "utf8"), {runScripts: "outside-only", url: "http://127.0.0.1:8765/"});
  const context = dom.getInternalVMContext();
  context.fetch = () => new Promise(() => {});
  context.setInterval = () => 0;
  context.setTimeout = () => 0;
  vm.runInContext(fs.readFileSync("aiflame/assets/web/shared.js", "utf8").replaceAll("export ", ""), context);
  vm.runInContext(fs.readFileSync("aiflame/assets/web/app.js", "utf8").replace(/^import .*?;\n/, ""), context);
  const data = {
    app_version: "test", device: {online: false}, maintenance: false, hardware_paired: false,
    emergency: false, display: {state: "done", key: "evt", simulated: false}, active_count: 0, queue_count: 0,
    settings: {mode: "virtual", brightness: 20, duration: 10, reduced: false, quiet_enabled: false, paused: false,
      quiet_start: "22:00", quiet_end: "08:00", source_enabled: {codex: true}},
    sources: [{id: "codex", name: "Codex", enabled: true, description: "任务状态"}],
    events: [{id: "evt", source: "codex", state: "done", status: "screen", created: 1770000000}],
    verification: {codex: {status: "verified", started_at: 1769999990, finished_at: 1770000000, complete_tasks: 1, delivery: "screen"}},
  };
  return {dom, data, context, render: () => {context.fixture = data; vm.runInContext("render(fixture)", context);},
    get: (id) => dom.window.document.getElementById(id)};
}

test("automatic integration waits for plugin manager while ordinary checks time out promptly", async () => {
  const d = dashboard();
  d.context.setTimeout = (_, delay) => { d.context.lastTimeout = delay; return 1; };
  d.context.clearTimeout = () => {};
  d.context.fetch = async () => ({ok: true, json: async () => ({message: 'ok'})});
  await vm.runInContext("api('/api/integrations/install', {source: 'deepseek-harness', profile: 'default'})", d.context);
  assert.equal(d.context.lastTimeout, 35000);
  await vm.runInContext("api('/api/integration-check?source=codex')", d.context);
  assert.equal(d.context.lastTimeout, 7000);
  d.dom.window.close();
});



test("connected status uses Wi-Fi icons and removes them offline", () => {
  const d = dashboard();
  d.data.settings.mode = "hardware";
  for (const [port, connection] of [["wifi://192.0.2.1:55443", "wifi"]]) {
    d.data.device = {online: true, port};
    d.render();
    assert.equal(d.get("deviceBadge").textContent, "已连接 1 台设备");
    assert.equal(d.get("deviceBadge").querySelector("svg").dataset.connection, connection);
  }
  d.data.device.online = false;
  d.render();
  assert.equal(d.get("deviceBadge").querySelector("svg"), null);
  assert.equal(d.get("stop"), null);
  d.dom.window.close();
});

test("no lamp renders real completion and app delivery without an ACK", () => {
  const d = dashboard();
  d.render();
  assert.equal(d.get("scene").classList.contains("offline"), false);
  assert.match(d.get("orb").className, /done/);
  assert.match(d.get("deviceBadge").textContent, /无需硬件/);
  assert.match(d.get("sources").textContent, /同一任务/);
  assert.equal(d.get("verificationSource"), null);
  assert.equal(d.get("step4").className, "");
  d.dom.window.close();
});

test("simulated events do not qualify and are visibly marked", () => {
  const d = dashboard();
  d.data.display.simulated = true;
  d.data.events[0].simulated = true;
  d.data.verification.codex = {status: "waiting", complete_tasks: 0};
  d.render();
  assert.match(d.get("sources").textContent, /等待真实/);
  assert.match(d.get("sceneKind").textContent, /不计入真实验证/);
  assert.match(d.get("events").textContent, /自测/);
  d.dom.window.close();
});

test("activity receipts show a compact confirmation count and readable safe tooltip details", () => {
  const d = dashboard();
  d.data.events[0].status = "applied";
  d.data.events[0].delivery_summary = {
    reason: "设备已确认接收指令",
    devices: [
      {device_id: "lamp-one", name: "书桌灯", status: "applied", reason: "设备已确认接收指令"},
      {device_id: "lamp-two", name: "<img src=x onerror=alert(1)>", status: "offline", reason: "设备已离线，未收到回执"},
      {device_id: "lamp-three", name: "床头灯", status: "queued", reason: "排队中，等待当前提醒结束"},
    ],
  };
  d.render();
  const cell = d.get("events").querySelector("tr td:last-child");
  assert.equal(cell.querySelector(".pill").textContent, "设备已确认");
  const details = cell.querySelector("[data-delivery-details]");
  assert.equal(details.textContent, "已确认 1/3 台设备");
  assert.doesNotMatch(cell.textContent, /书桌灯|lamp-one|未收到回执|排队中|<img/);
  assert.equal(cell.querySelector("img"), null);
  assert.equal(cell.title, "处理结果：设备已确认接收指令\n\n设备回执\n书桌灯（lamp-one）\n设备已确认接收指令\n\n<img src=x onerror=alert(1)>（lamp-two）\n设备已离线，未收到回执\n\n床头灯（lamp-three）\n排队中，等待当前提醒结束");
  d.data.events[0].delivery_summary.devices.forEach(device => device.status = "applied");
  d.render();
  assert.equal(d.get("events").querySelector("[data-delivery-details]").textContent, "已确认 3/3 台设备");
  d.data.events[0].delivery_summary.devices.forEach(device => device.status = "offline");
  d.render();
  assert.equal(d.get("events").querySelector("[data-delivery-details]").textContent, "已确认 0/3 台设备");
  d.dom.window.close();
});

test("activity records explain missing device receipts and retain legacy status fallback", () => {
  const d = dashboard();
  for (const [status, label, reason] of [
    ["queued", "等待发送", "排队中，当前完成提醒等待键鼠活动结束"],
    ["screen", "APP 已呈现", "APP 已呈现，未发送到实体设备"],
    ["superseded", "已被后续状态替代", "后续任务状态已替代此事件，不再发送"],
    ["interrupted", "进程重启，未重播", "进程重启，未重播"],
  ]) {
    Object.assign(d.data.events[0], {status, delivery_summary: {reason, devices: []}});
    d.render();
    const cell = d.get("events").querySelector("tr td:last-child");
    assert.equal(cell.querySelector(".pill").textContent, label);
    assert.equal(cell.querySelector("[data-delivery-details]"), null);
    assert.equal(cell.textContent, label);
    assert.equal(cell.title, "处理结果：" + reason);
  }
  delete d.data.events[0].delivery_summary;
  d.data.events[0].status = "screen";
  d.render();
  const cell = d.get("events").querySelector("tr td:last-child");
  assert.equal(cell.textContent, "APP 已呈现");
  assert.equal(cell.querySelector("[data-delivery-details]"), null);
  d.dom.window.close();
});

test("paired offline keeps screen task and connected lamp exposes actual receipt", () => {
  const d = dashboard();
  d.data.hardware_paired = true;
  d.data.settings.mode = "hardware";
  d.render();
  assert.match(d.get("deviceBadge").textContent, /硬件未连接/);
  assert.match(d.get("welcome").textContent, /自动沿用/);
  d.data.device = {online: true, device_id: "fixture", firmware_version: "test"};
  d.data.last_ack = {state: "done"};
  d.data.display.device_confirmed = true;
  d.data.events[0].status = "applied";
  d.render();
  assert.match(d.get("delivery").textContent, /接受调度/);
  assert.equal(d.get("step4").className, "ok");
  d.dom.window.close();
});

test("all source types share a flat list with local brand icons and category dividers", () => {
  const d = dashboard();
  const {SITES} = require("../aiflame/integrations/chatgpt_web/extension/providers.js");
  const brands = JSON.parse(fs.readFileSync("aiflame/assets/web/brands/sources.json", "utf8"));
  const cliSources = [...d.dom.window.document.querySelectorAll("[data-cli]")].map(card => card.dataset.cli);
  const desktopSources = [...d.dom.window.document.querySelectorAll('[data-trigger$="-desktop"]')].map(button => button.dataset.trigger);
  const extraSources = [...new Set([...cliSources, ...desktopSources, ...SITES.map(site => site.id), "manual", "demo", "local-script"])].filter(id => id !== "codex");
  d.data.sources.push(...extraSources.map(id => ({id, name: id, enabled: true, description: "fixture"})));
  d.render();
  const list = d.get("sources");
  assert.equal(list.querySelectorAll(":scope > .source").length, d.data.sources.length);
  assert.equal(list.querySelectorAll(".source-divider").length, 3);
  const images = [...list.querySelectorAll("img")];
  assert.equal(images.length, d.data.sources.length - 3);
  assert.deepEqual(new Set(images.map(image => image.getAttribute("src"))), new Set(Object.keys(brands).map(brand => `/brands/${brand}.png`)));
  for (const image of images) {
    assert.match(image.getAttribute("src"), /^\/brands\/[a-z]+\.png$/);
    assert.ok(fs.existsSync("aiflame/assets/web" + image.getAttribute("src")));
  }
  assert.equal(d.get("beginVerification"), null);
  d.dom.window.close();
});

test("disabled source still shows its current status and a working source switch", async () => {
  const d = dashboard();
  d.data.sources[0].enabled = false;
  d.data.settings.source_enabled.codex = false;
  d.data.verification.codex.status = "disabled";
  d.render();
  assert.match(d.get("sources").textContent, /来源已关闭/);
  const check = d.get("sources").querySelector("input");
  assert.equal(check.checked, false);
  vm.runInContext("globalThis.calls = []; api = async (path, body) => { calls.push({path, body}); return path === '/api/status' ? fixture : {}; };", d.context);
  check.checked = true;
  await check.onchange();
  assert.equal(d.context.calls[0].path, "/api/settings");
  assert.equal(d.context.calls[0].body.source_enabled.codex, true);
  d.dom.window.close();
});

test("service disconnection clearly marks stale state", async () => {
  const d = dashboard();
  d.render();
  vm.runInContext("api = async () => { throw new Error('offline'); };", d.context);
  await vm.runInContext("refresh()", d.context);
  assert.match(d.get("displayTitle").textContent, /未连接/);
  assert.match(d.get("message").textContent, /未连接/);
  d.dom.window.close();
});

test("hardware warning persists across panes and muted states until connected", () => {
  const d = dashboard();
  d.data.settings.mode = "hardware";
  d.data.settings.paused = true;
  d.render();
  d.get("pane-home").hidden = true;
  d.get("pane-testing").hidden = false;
  assert.equal(d.get("hardwareWarning").hidden, false);
  assert.equal(d.get("hardwareWarning").closest(".pane"), null);
  d.data.device.online = true;
  d.render();
  assert.equal(d.get("hardwareWarning").hidden, true);
  d.data.device.online = false;
  d.data.settings.mode = "virtual";
  d.render();
  assert.equal(d.get("hardwareWarning").hidden, true);
  assert.equal(d.get("hardwareControls").hidden, true);
  d.dom.window.close();
});

test("mode selection saves through the API and ignores older status responses", async () => {
  const d = dashboard();
  d.data.settings.mode = "hardware";
  d.render();
  vm.runInContext(`
    globalThis.oldStatus = JSON.parse(JSON.stringify(fixture));
    globalThis.calls = [];
    let resolveOld;
    api = (path, body) => body ? Promise.resolve({}) : new Promise(resolve => { resolveOld = resolve; });
    globalThis.oldRefresh = refresh();
    api = async (path, body) => {
      calls.push({path, body});
      if (body) fixture.settings.mode = body.mode;
      return fixture;
    };
  `, d.context);
  await d.dom.window.document.querySelector('[data-mode="virtual"]').onclick();
  assert.equal(d.context.calls[0].body.mode, "virtual");
  await vm.runInContext("resolveOld(oldStatus); oldRefresh", d.context);
  assert.equal(d.dom.window.document.body.dataset.mode, "virtual");
  assert.equal(d.get("hardwareControls").hidden, true);
  d.dom.window.close();
});

test("virtual device page never scans and late hardware job cannot disable diagnostics", async () => {
  const d = dashboard();
  vm.runInContext(fs.readFileSync("aiflame/assets/web/devices.js", "utf8").replace(/^import .*?;\n/, ""), d.context);
  d.render();
  vm.runInContext(`globalThis.calls = []; api = async (path) => { calls.push(path); return {job: null}; };`, d.context);
  d.dom.window.document.querySelector('[data-tab="device"]').click();
  await vm.runInContext("refreshMaintenance()", d.context);
  assert.equal(d.context.calls.includes("/api/ports"), false);
  assert.equal(d.context.calls.includes("/api/device"), false);
  d.get("pane-device").hidden = true;
  d.data.settings.mode = "hardware";
  d.render();
  vm.runInContext(`
    let resolveDevice;
    api = (path) => path.startsWith('/api/device?') ? new Promise(resolve => { resolveDevice = resolve; }) : Promise.resolve({job: null});
    $("pane-device").hidden = false; showDeviceDetails("yeelight-yltd003"); globalThis.oldMaintenance = refreshMaintenance();
  `, d.context);
  d.data.settings.mode = "virtual";
  d.render();
  await vm.runInContext("resolveDevice({job: {state: 'running', progress: 5, message: 'old'}}); oldMaintenance", d.context);
  assert.equal(d.dom.window.document.querySelector("[data-trigger]").disabled, false);
  d.dom.window.close();
});

test("CLI sources share programming category and expose lifecycle controls", () => {
  const d = dashboard();
  for (const id of ["claude-code", "deepseek-harness", "qoder"])
    d.data.sources.push({id, name: id, enabled: false, description: "CLI"});
  d.render();
  assert.equal(d.get("sources").querySelector("h3").textContent, "编程工具");
  assert.equal(d.get("sources").querySelectorAll("h3").length, 1);
  assert.ok(d.get("sources").querySelector('img[src="/brands/deepseek.png"]'));
  for (const card of d.dom.window.document.querySelectorAll("[data-cli]")) {
    assert.ok(card.querySelector('[data-cli-action="install"]'));
    assert.ok(card.querySelector('[data-cli-action="uninstall"]'));
    assert.ok(card.querySelector('[data-cli-check]'));
    assert.equal(card.querySelector('[data-trigger]').dataset.trigger, card.dataset.cli);
  }
  d.dom.window.close();
});

test("DeepSeek requires an existing profile and sends only the selected profile", async () => {
  const d = dashboard();
  d.render();
  vm.runInContext(`globalThis.calls = []; api = async (path, body) => {
    calls.push({path, body});
    if (path === '/api/status') return fixture;
    if (path === '/api/cli-integrations') return {integrations: [{source_id: 'deepseek-harness', configuration_ready: false, issues: [], signals: {}, profiles: ['dev', 'work']}]};
    if (path.startsWith('/api/integration-check?')) return {source_id: 'deepseek-harness', configuration_ready: false, issues: [], signals: {}, profiles: ['dev', 'work']};
    return {message: 'saved'};
  };`, d.context);
  const card = d.dom.window.document.querySelector('[data-cli="deepseek-harness"]');
  const button = card.querySelector('[data-cli-action="install"]');
  await button.onclick();
  assert.equal(d.context.calls.length, 0);
  assert.match(card.querySelector('[data-cli-status]').textContent, /请选择.*已有 Profile/);
  await card.querySelector('[data-cli-check]').onclick();
  const select = card.querySelector('[data-cli-profile]');
  assert.equal(select.value, "");
  assert.equal(select.options.length, 3);
  select.value = "work";
  await button.onclick();
  const call = d.context.calls.find(c => c.path === '/api/integrations/install');
  assert.equal(call.body.source, 'deepseek-harness');
  assert.equal(call.body.profile, 'work');
  assert.equal(select.value, 'work');
  d.dom.window.close();
});

test("CLI custom directory is preserved in install and removal requests", async () => {
  const d = dashboard();
  d.render();
  vm.runInContext(`globalThis.calls = []; api = async (path, body) => {
    calls.push({path, body});
    if (path === '/api/status') return fixture;
    if (path === '/api/cli-integrations') return {integrations: []};
    return {message: 'saved'};
  };`, d.context);
  const card = d.dom.window.document.querySelector('[data-cli="claude-code"]');
  card.querySelector('[data-cli-config]').value = ' /tmp/CLI settings ';
  await card.querySelector('[data-cli-action="install"]').onclick();
  await card.querySelector('[data-cli-action="uninstall"]').onclick();
  for (const action of ['install', 'uninstall']) {
    const call = d.context.calls.find(c => c.path === '/api/integrations/' + action);
    assert.equal(call.body.source, 'claude-code');
    assert.equal(call.body.config_dir, '/tmp/CLI settings');
  }
  d.dom.window.close();
});


test("CLI diagnostics show verification limits and active configuration without replacing input", async () => {
  const d = dashboard();
  vm.runInContext(`api = async () => ({integrations: [{source_id: 'claude-code', configuration_ready: true, issues: [], signals: {}, trust_note: '仍需真实任务验证', limitations: ['取消检测有限'], config_dir: '/tmp/saved settings', installed_profiles: ['work']}]});`, d.context);
  const card = d.dom.window.document.querySelector('[data-cli="claude-code"]');
  card.querySelector('[data-cli-config]').value = '/tmp/new selection';
  await card.querySelector('[data-cli-check]').onclick();
  assert.equal(card.querySelector('[data-cli-status]').textContent, '配置已就绪，尚需真实任务验证。');
  const details = card.querySelector('[data-cli-details]');
  assert.equal(details.closest('details').open, false);
  const status = details.textContent;
  assert.match(status, /真实任务验证/);
  assert.match(status, /取消检测有限/);
  assert.match(status, /saved settings/);
  assert.match(status, /已连接 Profile：work/);
  assert.equal(card.querySelector('[data-cli-config]').value, '/tmp/new selection');
  d.dom.window.close();
});

test("connecting is neutral until handshake succeeds or fails", () => {
  const d = dashboard();
  d.data.settings.mode = "hardware";
  d.data.device = {online: false, connecting: true};
  d.render();
  assert.equal(d.get("hardwareWarning").hidden, true);
  assert.equal(d.get("deviceBadge").textContent, "正在连接设备");
  assert.equal(d.get("deviceBadge").classList.contains("on"), false);
  d.data.device = {online: false, connecting: false, last_error: "连接失败"};
  d.render();
  assert.equal(d.get("hardwareWarning").hidden, false);
  assert.match(d.get("deviceDetail").textContent, /连接失败/);
  d.data.device = {online: true};
  d.render();
  assert.equal(d.get("deviceBadge").textContent, "已连接 1 台设备");
  d.dom.window.close();
});








test("hardware model cards gate configuration and route model-specific operations", async () => {
  const d = dashboard();
  vm.runInContext(fs.readFileSync("aiflame/assets/web/devices.js", "utf8").replace(/^import .*?;\n/, ""), d.context);
  d.data.settings.mode = "hardware";
  d.render();
  vm.runInContext(`globalThis.calls = []; api = async (path, body) => {
    calls.push({path, body});
    if (path === '/api/device' && body) return {action: body.action, state: 'succeeded', progress: 100, message: 'selected'};
    if (path.startsWith('/api/device?')) return {hardware_model: selectedHardwareModel, selected_device_id: '', device: {online: false}, devices: [], paired_device: '', transport: 'yeelight', job: null};
    return {job: null};
  };`, d.context);
  const nav = d.dom.window.document.querySelector('[data-tab="device"]');
  nav.click();
  assert.equal(d.get('deviceSelector').hidden, false);
  assert.equal(d.get('deviceDetails').hidden, true);
  assert.equal(d.context.calls.some(c => c.path === '/api/ports'), false);
  const cards = d.dom.window.document.querySelectorAll('[data-device-model]');
  assert.equal(cards.length, 2);
  await d.dom.window.document.querySelector('[data-device-model="yeelight-yldp005"]').onclick();
  assert.equal(d.get('deviceSelector').hidden, true);
  assert.equal(d.get('deviceDetails').hidden, false);
  assert.equal(d.get('deviceModelTitle').textContent, 'Yeelight W3 彩色灯泡');
  await vm.runInContext("maintenance('discover')", d.context);
  assert.equal(d.context.calls.find(c => c.body?.action === 'discover').body.hardware_model, 'yeelight-yldp005');
  nav.click();
  assert.equal(d.get('deviceDetails').hidden, true);
  await d.dom.window.document.querySelector('[data-device-model="yeelight-yltd003"]').onclick();
  assert.equal(d.get('deviceModelTitle').textContent, 'Yeelight 屏幕挂灯 Pro');
  d.get('backToDeviceModels').click();
  assert.equal(d.get('deviceSelector').hidden, false);
  assert.equal(d.get('deviceDetails').hidden, true);
  d.dom.window.close();
});


test("model browsing sends no connection mutation and keeps navigation in the gallery", async () => {
  const d = dashboard();
  vm.runInContext(fs.readFileSync("aiflame/assets/web/devices.js", "utf8").replace(/^import .*?;\n/, ""), d.context);
  d.render();
  vm.runInContext(`globalThis.calls = []; api = async (path, body) => { calls.push({path, body}); return {job: null}; };`, d.context);
  const nav = d.dom.window.document.querySelector('[data-tab="device"]');
  nav.click();
  const pending = d.dom.window.document.querySelector('[data-device-model="yeelight-yldp005"]').onclick();
  nav.click();
  await pending;
  assert.equal(d.get('deviceSelector').hidden, false);
  assert.equal(d.get('deviceDetails').hidden, true);
  assert.equal(d.context.calls.some(call => call.body), false);
  d.dom.window.close();
});

test("same-model devices keep independent selection and disconnect targets", async () => {
  const d = dashboard();
  vm.runInContext(fs.readFileSync("aiflame/assets/web/devices.js", "utf8").replace(/^import .*?;\n/, ""), d.context);
  d.data.settings.mode = "hardware";
  d.data.devices = ["one", "two"].map(device_id => ({device_id, name: "Yeelight 屏幕挂灯 Pro", hardware_model: "yeelight-yltd003", online: true, port: device_id}));
  d.data.device = {...d.data.devices[0], connected_count: 2};
  d.context.lamps = d.data.devices;
  d.render();
  assert.match(d.get("deviceBadge").textContent, /已连接 2 台/);
  vm.runInContext(`globalThis.calls = []; api = async (path, body) => {
    calls.push({path, body});
    if (path.startsWith('/api/device?')) {
      const id = new URL(path, location.href).searchParams.get('device_id') ?? 'one';
      return {hardware_model: 'yeelight-yltd003', selected_device_id: id, devices: lamps,
        device: lamps.find(item => item.device_id === id), selected_port: id, paired_device: id, transport: 'yeelight', job: null};
    }
    return {job: null};
  };`, d.context);
  d.dom.window.document.querySelector('[data-tab="device"]').click();
  await d.dom.window.document.querySelector('[data-device-model="yeelight-yltd003"]').onclick();
  assert.equal(d.get("deviceInstances").options.length, 3);
  d.get("deviceInstances").value = "two";
  const loading = d.get("deviceInstances").onchange();
  assert.equal(d.get("disconnectDevice").disabled, true);
  await loading;
  assert.equal(d.get("disconnectDevice").disabled, false);
  assert.equal(d.context.calls.some(call => call.body), false);
  await d.get("testCurrentDevice").onclick();
  assert.equal(d.get("deviceTestCountdown").textContent, "试亮剩余 10 秒");
  assert.equal(d.get("deviceTestCountdown").hidden, false);
  assert.equal(d.context.calls.find(call => call.path === "/api/device/test-light").body.device_id, "two");
  assert.equal(d.context.calls.some(call => call.path === "/api/test"), false);
  await vm.runInContext("maintenance('disconnect')", d.context);
  assert.equal(d.context.calls.find(call => call.body?.action === "disconnect").body.device_id, "two");
  assert.match(d.get("deviceBadge").textContent, /已连接 2 台/);
  d.dom.window.close();
});



test("overview demo lasts ten seconds with two phases and a countdown", async () => {
  const d = dashboard();
  d.data.display = {state: "idle", key: "idle"};
  vm.runInContext("globalThis.demoNow = 0; performance.now = () => demoNow;", d.context);
  d.render();
  await d.get("demo").onclick();
  assert.match(d.get("orb").className, /working/);
  assert.equal(d.get("overviewCountdown").textContent, "演示剩余 10 秒");
  d.context.demoNow = 5000;
  d.render();
  assert.match(d.get("orb").className, /done/);
  assert.equal(d.get("overviewCountdown").textContent, "演示剩余 5 秒");
  d.context.demoNow = 10000;
  d.render();
  assert.match(d.get("orb").className, /idle/);
  assert.equal(d.get("overviewCountdown").hidden, true);
  d.dom.window.close();
});

for (const modelId of ["yeelight-yltd003", "yeelight-yldp005"]) test(`${modelId} uses LAN setup, keeps discovery selection across polls and sends manual IP`, async () => {
  const d = dashboard();
  vm.runInContext(fs.readFileSync("aiflame/assets/web/devices.js", "utf8").replace(/^import .*?;\n/, ""), d.context);
  d.data.settings.mode = 'hardware';
  d.render();
  vm.runInContext(`
    globalThis.calls = [];
    api = async (path, body) => {
      calls.push({path, body});
      if (path.startsWith('/api/device?')) return {hardware_model: '${modelId}', selected_device_id: '', device: {online: false}, devices: [], paired_device: '', transport: 'yeelight', job: null};
      if (path === '/api/trigger-test') return {job: null};
      return {id: 'connect', state: 'running', progress: 5, message: '正在连接'};
    };
  `, d.context);
  d.dom.window.document.querySelector('[data-tab="device"]').click();
  await d.dom.window.document.querySelector(`[data-device-model="${modelId}"]`).onclick();
  assert.equal(d.get('yeelightControls').hidden, false);
  assert.equal(d.get('deviceModelDimensions').textContent,
    d.dom.window.document.querySelector(`[data-device-model="${modelId}"] .device-model-detail`).textContent);
  assert.match(d.get('discoverYeelight').textContent, modelId.endsWith('yldp005') ? /W3 彩色灯泡/ : /屏幕挂灯 Pro/);
  assert.match(d.get('yeelightLightHint').textContent, modelId.endsWith('yldp005') ? /整颗灯泡/ : /前方照明/);
  for (const id of ['matrixConnection', 'matrixWifi', 'matrixMaintenance']) assert.equal(d.get(id), null);
  assert.equal(d.context.calls.some(call => call.path === '/api/ports'), false);
  const completed = {id: 'search', action: 'discover', state: 'succeeded', progress: 100, message: '已发现', result: {devices: [{name: 'YLTD003', host: '192.168.1.44'}]}};
  d.context.searchJob = completed;
  vm.runInContext('renderJob(searchJob)', d.context);
  d.get('yeelightDiscovered').value = '192.168.1.44';
  d.get('yeelightDiscovered').onchange();
  vm.runInContext('renderJob(searchJob)', d.context);
  assert.equal(d.get('yeelightDiscovered').value, '192.168.1.44');
  assert.equal(d.get('yeelightHost').value, '192.168.1.44');
  d.get('yeelightHost').value = '192.168.1.45';
  d.get('yeelightHost').oninput();
  assert.equal(d.get('connectYeelight').disabled, false);
  await d.get('connectYeelight').onclick();
  const sent = d.context.calls.find(call => call.body?.action === 'connect');
  assert.equal(sent.body.hardware_model, modelId);
  assert.equal(sent.body.transport, 'yeelight');
  assert.equal(sent.body.host, '192.168.1.45');
  d.dom.window.close();
});

test("compact model cards keep product details while live counts update", () => {
  const d = dashboard();
  vm.runInContext(fs.readFileSync("aiflame/assets/web/devices.js", "utf8").replace(/^import .*?;\n/, ""), d.context);
  const card = d.dom.window.document.querySelector('[data-device-model="yeelight-yldp005"]');
  const title = card.querySelector('strong').textContent;
  const detail = card.querySelector('.device-model-detail').textContent;
  d.dom.window.document.dispatchEvent(new d.dom.window.CustomEvent('flame-mode', {detail: {mode: 'hardware', devices: [
    {hardware_model: 'yeelight-yldp005', online: true},
    {hardware_model: 'yeelight-yldp005', online: true},
    {hardware_model: 'yeelight-yltd003', online: false},
  ]}}));
  const count = card.querySelector('[data-model-count]');
  assert.equal(count.textContent, '在线 2 · 离线 0');
  assert.equal(count.dataset.online, 'true');
  assert.equal(d.dom.window.document.querySelector('[data-model-count="yeelight-yltd003"]').textContent, '在线 0 · 离线 1');
  d.dom.window.document.dispatchEvent(new d.dom.window.CustomEvent('flame-mode', {detail: {mode: 'hardware', devices: [
    {hardware_model: 'yeelight-yldp005', online: true},
    {hardware_model: 'yeelight-yldp005', online: false},
  ]}}));
  assert.equal(count.textContent, '在线 1 · 离线 1');
  assert.equal(card.querySelector('strong').textContent, title);
  assert.equal(card.querySelector('.device-model-detail').textContent, detail);
  d.dom.window.close();
});

test('integration checks show exact failures and distinguish ready from real connected', async () => {
  const d = dashboard();
  const card = d.dom.window.document.querySelector('[data-cli="claude-code"]');
  card.querySelector('[data-cli-config]').value = '/tmp/selected path';
  vm.runInContext(`globalThis.report = {source_id:'claude-code', configuration_ready:false, status:'configuration_error', summary:'接入配置有问题：/tmp/selected path/settings.json 缺少 Stop', next_action:'自动连接 / 修复', issues:['缺少 Stop'], checks:[{status:'error', message:'Stop：缺少 SeeEnd Hook', action:'增量添加'}], signals:{}};
    globalThis.requested = ''; api = async path => {requested=path; return report;};`, d.context);
  await card.querySelector('[data-cli-check]').onclick();
  assert.match(d.context.requested, /^\/api\/integration-check\?/);
  assert.match(decodeURIComponent(d.context.requested.replaceAll('+', ' ')), /config_dir=\/tmp\/selected path/);
  assert.match(card.querySelector('[data-cli-status]').textContent, /settings.json 缺少 Stop/);
  assert.match(card.querySelector('[data-cli-details]').textContent, /增量添加/);
  vm.runInContext(`report = {...report, configuration_ready:true, status:'ready_unverified', summary:'配置已就绪，但尚未验证真实任务接入。', checks:[]};`, d.context);
  await card.querySelector('[data-cli-check]').onclick();
  assert.match(card.querySelector('[data-cli-status]').textContent, /尚未验证/);
  vm.runInContext(`report = {...report, status:'connected', summary:'接入正常：已收到同一真实任务的开始与完成。'};`, d.context);
  await card.querySelector('[data-cli-check]').onclick();
  assert.match(card.querySelector('[data-cli-status]').textContent, /接入正常/);
  assert.equal(card.querySelector('[data-cli-status]').dataset.status, 'connected');
  d.dom.window.close();
});

test('setup guide displays actual files and incremental fragment as text and never writes config', async () => {
  const d = dashboard();
  const card = d.dom.window.document.querySelector('[data-cli="codex"]');
  card.querySelector('[data-cli-config]').value = '/tmp/custom-codex';
  vm.runInContext(`globalThis.guideCalls = []; api = async (path,body) => {
    guideCalls.push({path,body});
    return {automatic:'自动增量配置', preservation:'不覆盖原文件', files:['/tmp/custom-codex/hooks.json'], changes:['追加事件组'], preparation:['核对路径'], next_steps:['信任 Hook 后完成真实任务'], manual:'不要替换整个文件', snippet:'<script>PRIVATE</script>', docs_url:'https://learn.chatgpt.com/docs/hooks', copy_label:'复制增量 Hooks 片段'};
  };`, d.context);
  const detail = card.querySelector('[data-integration-guide]');
  detail.open = true;
  await vm.runInContext('loadIntegrationGuide(document.querySelector(\'[data-cli="codex"]\'))', d.context);
  assert.match(detail.querySelector('p').textContent, /custom-codex\/hooks.json/);
  assert.match(detail.querySelector('p').textContent, /不替换整个配置文件/);
  assert.match(detail.querySelector('[data-manual-guide]').textContent, /不要替换/);
  assert.equal(detail.querySelector('pre').textContent, '<script>PRIVATE</script>');
  assert.equal(detail.querySelector('pre').querySelector('script'), null);
  assert.equal(detail.querySelector('[data-copy-integration]').disabled, false);
  assert.ok(d.context.guideCalls.every(call => call.path.startsWith('/api/integration-guide?') && !call.body));
  d.dom.window.close();
});

test('one existing DeepSeek profile is selected without installing or replacing a selection', async () => {
  const d = dashboard();
  const card = d.dom.window.document.querySelector('[data-cli="deepseek-harness"]');
  vm.runInContext(`globalThis.profileCalls = []; api = async (path,body) => {
    profileCalls.push({path,body});
    return {source_id:'deepseek-harness', configuration_ready:false, feedback:'请选择 Profile 后连接。', profiles:['default'], checks:[], signals:{}};
  };`, d.context);
  await card.querySelector('[data-cli-check]').onclick();
  assert.equal(card.querySelector('[data-cli-profile]').value, 'default');
  assert.ok(d.context.profileCalls.every(call => !call.body));
  assert.equal(card.querySelector('[data-cli-status]').textContent, '请选择 Profile 后连接。');
  assert.equal(card.querySelector('[data-cli-action="install"]').textContent, '连接');
  assert.equal(card.querySelector('[data-integration-guide]').open, false);
  d.dom.window.close();
});

test('check request failures are explicit rather than claiming configuration success', async () => {
  const d = dashboard();
  const card = d.dom.window.document.querySelector('[data-cli="qoder"]');
  vm.runInContext(`api = async () => {throw Error('Hub 未连接');};`, d.context);
  await card.querySelector('[data-cli-check]').onclick();
  assert.equal(card.querySelector('[data-cli-status]').textContent, '检查未完成：Hub 未连接');
  d.dom.window.close();
});

test("web source icons and titles open matching apps without changing source switches", () => {
  const d = dashboard();
  const urls = JSON.parse(fs.readFileSync('aiflame/integrations/chatgpt_web/extension/manifest.json')).content_scripts.flatMap(script => script.matches);
  const ids = ['claude-web', 'gemini-web', 'chatgpt-web', 'deepseek-web', 'qianwen-web', 'doubao-web', 'yuanbao-web', 'kimi-web', 'zhipu-web'];
  d.data.sources = ids.map(id => ({id, name: id, description: '', enabled: false}));
  d.render();
  assert.deepEqual([...d.get('sources').querySelectorAll('.source-info b')].map(node => node.textContent),
    ['chatgpt-web', 'gemini-web', 'claude-web', 'deepseek-web', 'doubao-web', 'qianwen-web', 'kimi-web', 'yuanbao-web', 'zhipu-web']);
  for (const row of d.get('sources').querySelectorAll('.source')) {
    const links = row.querySelectorAll('a');
    assert.equal(links.length, 2);
    assert.equal(links[0].href, links[1].href);
    assert.equal(links[0].target, '_blank');
    assert.equal(links[0].rel, 'noopener noreferrer');
    assert.ok(urls.some(pattern => links[0].href.startsWith(pattern.replace('*', ''))));
    assert.equal(row.querySelector('input').checked, false);
  }
  d.dom.window.close();
});

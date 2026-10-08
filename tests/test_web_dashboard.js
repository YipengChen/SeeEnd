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

test("initialization uses bundled firmware and requires board and erase confirmations", async () => {
  const d = dashboard();
  vm.runInContext(fs.readFileSync("aiflame/assets/web/devices.js", "utf8").replace(/^import .*?;\n/, ""), d.context);
  d.data.settings.mode = "hardware";
  d.render();
  d.get("usbPort").innerHTML = '<option value="fixture">fixture</option>';
  d.get("usbPort").onchange();
  assert.equal(d.get("flashBase").disabled, true);
  d.get("confirmBoard").checked = true;
  d.get("confirmBoard").onchange();
  assert.equal(d.get("flashBase").disabled, true);
  d.get("confirmFlash").checked = true;
  d.get("confirmFlash").onchange();
  assert.equal(d.get("flashBase").disabled, false);
  vm.runInContext(`
    globalThis.calls = [];
    api = async (path, body) => {
      calls.push({path, body});
      return {state: 'running', progress: 10, message: '初始化'};
    };
  `, d.context);
  await d.get("flashBase").onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(d.context.calls)), [{
    path: "/api/device", body: {action: "flash_base", hardware_model: "ai-flame-v01", device_id: "", port: "fixture", confirm_board: true, confirm_flash: true},
  }]);
  assert.equal(d.get("flashBase").disabled, true);
  assert.equal(d.dom.window.document.querySelector('input[type="file"]'), null);
  d.dom.window.close();
});

test("connected status uses wifi or USB cable icons and removes them offline", () => {
  const d = dashboard();
  d.data.settings.mode = "hardware";
  for (const [port, connection] of [["wifi://192.0.2.1:8766", "wifi"], ["/dev/cu.fixture", "usb"]]) {
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
  const extraSources = ["manual", "demo", "local-script", "chatgpt-desktop", "deepseek-web", "qianwen-web", "doubao-web", "yuanbao-web", "kimi-web", "zhipu-web", "chatgpt-web"];
  d.data.sources.push(...extraSources.map(id => ({id, name: id, enabled: true, description: "fixture"})));
  d.render();
  const list = d.get("sources");
  assert.equal(list.querySelectorAll(":scope > .source").length, d.data.sources.length);
  assert.equal(list.querySelectorAll(".source-divider").length, 3);
  const images = [...list.querySelectorAll("img")];
  assert.equal(images.length, 9);
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
  await vm.runInContext("scanPorts(); refreshMaintenance()", d.context);
  assert.equal(d.context.calls.includes("/api/ports"), false);
  assert.equal(d.context.calls.includes("/api/device"), false);
  d.get("pane-device").hidden = true;
  d.data.settings.mode = "hardware";
  d.render();
  vm.runInContext(`
    let resolveDevice;
    api = (path) => path.startsWith('/api/device?') ? new Promise(resolve => { resolveDevice = resolve; }) : Promise.resolve({job: null});
    $("pane-device").hidden = false; showDeviceDetails("ai-flame-v01"); globalThis.oldMaintenance = refreshMaintenance();
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

test("device connection buttons prevent duplicate connections and permit switching", () => {
  const d = dashboard();
  vm.runInContext(fs.readFileSync("aiflame/assets/web/devices.js", "utf8").replace(/^import .*?;\n/, ""), d.context);
  d.data.settings.mode = "hardware";
  d.data.hardware_paired = true;
  d.get("usbPort").innerHTML = '<option value="COM1">USB A</option><option value="COM2">USB B</option>';
  vm.runInContext('wifiAvailable = true;', d.context);
  d.data.device = {online: true, port: "COM1"};
  d.render();
  assert.equal(d.get("useUsb").disabled, true);
  assert.equal(d.get("useWifi").disabled, false);
  d.get("usbPort").value = "COM2";
  d.get("usbPort").onchange();
  assert.equal(d.get("useUsb").disabled, false);
  d.data.device = {online: true, port: "wifi://192.0.2.1:8766"};
  d.render();
  assert.equal(d.get("useWifi").disabled, true);
  assert.equal(d.get("useUsb").disabled, false);
  assert.equal(d.get("disconnectDevice").disabled, false);
  d.dom.window.close();
});

test("Wi-Fi scan populates choices without replacing manually entered SSID", () => {
  const d = dashboard();
  vm.runInContext(fs.readFileSync("aiflame/assets/web/devices.js", "utf8").replace(/^import .*?;\n/, ""), d.context);
  d.get("wifiSsid").value = "Hidden network";
  vm.runInContext('renderWifiScan({state: "succeeded", networks: [{ssid: "Studio", signal: -40}], message: "找到网络"})', d.context);
  assert.equal(d.get("wifiSsid").value, "Hidden network");
  assert.equal(d.get("wifiNetworks").children[0].value, "Studio");
  assert.equal(d.get("wifiSsid").getAttribute("aria-controls"), "wifiNetworks");
  d.dom.window.close();
});

test("clicking Wi-Fi name opens the list and selecting a network fills only its name", () => {
  const d = dashboard();
  vm.runInContext(fs.readFileSync("aiflame/assets/web/devices.js", "utf8").replace(/^import .*?;\n/, ""), d.context);
  d.get("wifiSsid").click();
  assert.equal(d.get("wifiNetworks").hidden, false);
  assert.equal(d.get("wifiSsid").getAttribute("aria-expanded"), "true");
  vm.runInContext('renderWifiScan({state: "succeeded", networks: [{ssid: "ESP-visible", signal: -45}], message: "ESP32 找到网络"})', d.context);
  d.get("wifiNetworks").querySelector("button").click();
  assert.equal(d.get("wifiSsid").value, "ESP-visible");
  assert.equal(d.get("wifiNetworks").hidden, true);
  d.dom.window.close();
});


test("hardware model cards gate configuration and route model-specific operations", async () => {
  const d = dashboard();
  vm.runInContext(fs.readFileSync("aiflame/assets/web/devices.js", "utf8").replace(/^import .*?;\n/, ""), d.context);
  d.data.settings.mode = "hardware";
  d.render();
  vm.runInContext(`globalThis.calls = []; api = async (path, body) => {
    calls.push({path, body});
    if (path === '/api/ports') return {ports: [{description: 'ESP32', port: 'fixture'}]};
    if (path === '/api/device' && body) return {action: body.action, state: 'succeeded', progress: 100, message: 'selected'};
    if (path.startsWith('/api/device?')) return {hardware_model: selectedHardwareModel, selected_device_id: '', device: {online: false}, devices: [], paired_device: '', transport: 'usb', job: null};
    return {job: null};
  };`, d.context);
  const nav = d.dom.window.document.querySelector('[data-tab="device"]');
  nav.click();
  assert.equal(d.get('deviceSelector').hidden, false);
  assert.equal(d.get('deviceDetails').hidden, true);
  assert.equal(d.context.calls.some(c => c.path === '/api/ports'), false);
  const cards = d.dom.window.document.querySelectorAll('[data-device-model]');
  assert.equal(cards.length, 4);
  await cards[1].onclick();
  assert.equal(d.get('deviceSelector').hidden, true);
  assert.equal(d.get('deviceDetails').hidden, false);
  assert.equal(d.get('deviceModelTitle').textContent, 'AI Flame 0.2');
  assert.match(d.get('firmwareDownload').href, /model=ai-flame-v02$/);
  await vm.runInContext("maintenance('install')", d.context);
  assert.equal(d.context.calls.find(c => c.body?.action === 'install').body.hardware_model, 'ai-flame-v02');
  nav.click();
  assert.equal(d.get('deviceDetails').hidden, true);
  await cards[0].onclick();
  assert.equal(d.get('deviceModelTitle').textContent, 'AI Flame 0.1');
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
  const pending = d.dom.window.document.querySelector('[data-device-model="ai-flame-v02"]').onclick();
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
  d.data.devices = ["one", "two"].map(device_id => ({device_id, name: "AI Flame 0.1", hardware_model: "ai-flame-v01", online: true, port: device_id}));
  d.data.device = {...d.data.devices[0], connected_count: 2};
  d.context.lamps = d.data.devices;
  d.render();
  assert.match(d.get("deviceBadge").textContent, /已连接 2 台/);
  vm.runInContext(`globalThis.calls = []; api = async (path, body) => {
    calls.push({path, body});
    if (path === '/api/ports') return {ports: lamps.map(item => ({description: item.device_id, port: item.port}))};
    if (path.startsWith('/api/device?')) {
      const id = new URL(path, location.href).searchParams.get('device_id') ?? 'one';
      return {hardware_model: 'ai-flame-v01', selected_device_id: id, devices: lamps,
        device: lamps.find(item => item.device_id === id), selected_port: id, paired_device: id, transport: 'usb', job: null};
    }
    return {job: null};
  };`, d.context);
  d.dom.window.document.querySelector('[data-tab="device"]').click();
  await d.dom.window.document.querySelector('[data-device-model="ai-flame-v01"]').onclick();
  assert.equal(d.get("deviceInstances").options.length, 3);
  d.get("deviceInstances").value = "two";
  const loading = d.get("deviceInstances").onchange();
  assert.equal(d.get("disconnectDevice").disabled, true);
  await loading;
  assert.equal(d.get("disconnectDevice").disabled, false);
  assert.equal(d.context.calls.some(call => call.body), false);
  assert.equal(d.get("useUsb").className, d.get("useWifi").className);
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

test("device brightness and motion save to physical identity and ignore old selection responses", async () => {
  const d = dashboard();
  vm.runInContext(fs.readFileSync("aiflame/assets/web/devices.js", "utf8").replace(/^import .*?;\n/, ""), d.context);
  d.data.settings.mode = "hardware";
  d.render();
  assert.equal(d.get("brightness"), null);
  assert.equal(d.get("reduced"), null);
  assert.ok(d.get("deviceDetails").contains(d.get("deviceBrightness")));
  assert.equal(d.get("deviceBrightness").disabled, true);
  vm.runInContext(`
    globalThis.calls = [];
    globalThis.lamps = ['one', 'two'].map(device_id => ({device_id, name: 'AI Flame 0.1', hardware_model: 'ai-flame-v01', online: false,
      light_settings: {brightness: device_id === 'one' ? 8 : 20, reduced: false}}));
    globalThis.delayLightSave = false;
    api = async (path, body) => {
      calls.push({path, body});
      if (path === '/api/ports') return {ports: []};
      if (path.startsWith('/api/device?')) {
        const id = new URL(path, location.href).searchParams.get('device_id') ?? 'one';
        const lamp = lamps.find(item => item.device_id === id);
        return {hardware_model: 'ai-flame-v01', selected_device_id: id, devices: lamps, device: lamp,
          light_settings: {...lamp?.light_settings}, transport: 'usb', job: null};
      }
      if (path === '/api/device/light-settings') {
        if (delayLightSave) await new Promise(resolve => {globalThis.releaseLightSave = resolve;});
        const lamp = lamps.find(item => item.device_id === body.device_id);
        Object.assign(lamp.light_settings, body.settings);
        return {device_id: body.device_id, light_settings: {...lamp.light_settings}};
      }
      return {job: null};
    };`, d.context);
  d.dom.window.document.querySelector('[data-tab="device"]').click();
  await d.dom.window.document.querySelector('[data-device-model="ai-flame-v01"]').onclick();
  assert.equal(d.get("deviceBrightness").disabled, false); // Offline settings remain editable.
  assert.equal(d.get("deviceBrightness").value, '8');
  d.get("deviceReduced").checked = true;
  await d.get("deviceReduced").onchange();
  assert.equal(d.context.lamps[0].light_settings.reduced, true);
  assert.equal(d.context.lamps[1].light_settings.reduced, false);
  d.context.delayLightSave = true;
  d.get("deviceBrightness").value = '7';
  d.get("deviceBrightness").oninput();
  const pending = d.get("deviceBrightness").onchange();
  d.get("deviceInstances").value = 'two';
  await d.get("deviceInstances").onchange();
  d.context.releaseLightSave();
  await pending;
  assert.equal(d.get("deviceInstances").value, 'two');
  assert.equal(d.get("deviceBrightness").value, '20');
  assert.equal(d.get("deviceReduced").checked, false);
  assert.equal(d.context.lamps[0].light_settings.brightness, 7);
  assert.equal(d.context.lamps[1].light_settings.brightness, 20);
  assert.equal(d.context.calls.some(call => call.path === '/api/settings'), false);
  vm.runInContext(`
    delayLightSave = false;
    const baseLightApi = api;
    globalThis.delayNextRead = true;
    api = async (path, body) => {
      if (path.startsWith('/api/device?') && delayNextRead) {
        delayNextRead = false;
        const stale = await baseLightApi(path, body);
        await new Promise(resolve => {globalThis.releaseStaleRead = resolve;});
        return stale;
      }
      return baseLightApi(path, body);
    };`, d.context);
  const staleRead = vm.runInContext("refreshMaintenance()", d.context);
  await Promise.resolve();
  d.get("deviceBrightness").value = '9';
  d.get("deviceBrightness").oninput();
  await d.get("deviceBrightness").onchange();
  d.context.releaseStaleRead();
  await staleRead;
  assert.equal(d.get("deviceBrightness").value, '9');
  assert.equal(d.context.lamps[1].light_settings.brightness, 9);
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
  assert.match(d.get('discoverYeelight').textContent, modelId.endsWith('yldp005') ? /YLDP005/ : /YLTD003/);
  assert.match(d.get('yeelightLightHint').textContent, modelId.endsWith('yldp005') ? /整颗灯泡/ : /前方照明/);
  for (const id of ['matrixConnection', 'matrixWifi', 'matrixMaintenance']) assert.equal(d.get(id).hidden, true);
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
  assert.equal(count.textContent, '2 台在线');
  assert.equal(count.dataset.online, 'true');
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

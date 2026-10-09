import { $, api, el, tell, names, results } from "./shared.js";

let lightRevision = 0;
let lightWriting = false;
let lightEditing = false;
let liveDevice = {}, pairedDevice = false, wifiAvailable = false, connectionPaused = false;
let wifiScanRunning = false;
let deviceJobRunning = false;
let triggerJobRunning = false;
let selectedHardwareModel = "ai-flame-v01";
let selectedDeviceId = null;
const isYeelight = () => ["yeelight-yltd003", "yeelight-yldp005"].includes(selectedHardwareModel);
const testDeadlines = new Map();
function renderTestCountdown() {
  const seconds = Math.max(0, Math.ceil(((testDeadlines.get(selectedDeviceId) || 0) - performance.now()) / 1000));
  $("deviceTestCountdown").hidden = !seconds || selectionLoading;
  $("deviceTestCountdown").textContent = seconds ? `试亮剩余 ${seconds} 秒` : "";
}
setInterval(renderTestCountdown, 250);
let deviceOperationId = null;
let renderedDiscovery = null;
let restoreSelectedPort = false;
let selectionLoading = false;
let selectingModel = false;
let deviceViewEpoch = 0;
let modelRevision = 0;
let modelSelectionRunning = false;
function showDeviceModels() {
  deviceViewEpoch += 1;
  $("deviceSelector").hidden = false;
  $("deviceDetails").hidden = true;
}
function showDeviceDetails(modelId) {
  const button = document.querySelector(`[data-device-model="${modelId}"]`);
  if (!button) return;
  selectedHardwareModel = modelId;
  $("deviceModelTitle").textContent = button.dataset.name;
  $("deviceModelDimensions").textContent = (isYeelight() ? "" : "暖黄色灯板 · ") + button.dataset.size;
  if (isYeelight()) $("deviceModelDimensions").textContent = button.querySelector(".device-model-detail").textContent;
  $("yeelightControls").hidden = !isYeelight();
  $("deviceReducedRow").hidden = isYeelight();
  $("deviceBrightnessRow").hidden = isYeelight();
  $("deviceLightTitle").hidden = isYeelight();
  $("yeelightStateBrightness").hidden = !isYeelight();
  $("deviceBrightness").max = isYeelight() ? "100" : "25";
  $("deviceBrightnessLabel").textContent = isYeelight() ? "灯光亮度 · 上限 100%" : "灯光亮度 · 上限 25%";
  if (isYeelight()) {
    $("discoverYeelight").textContent = "搜索 " + button.dataset.name.replace("Yeelight ", "");
    $("yeelightSetup").textContent = "在 Yeelight APP 中配置灯具，接入2.4 GHz WiFi，开启「局域网控制」。此电脑需与灯具在同一局域网内。";
    $("yeelightLightHint").textContent = button.dataset.lightHint;
  }
  for (const id of ["matrixConnection", "matrixWifi", "matrixMaintenance"]) $(id).hidden = isYeelight();
  $("firmwareDownload").href = "/api/firmware/download?model=" + modelId;
  $("deviceSelector").hidden = true;
  $("deviceDetails").hidden = false;
}
$("backToDeviceModels").onclick = showDeviceModels;
for (const button of document.querySelectorAll("[data-device-model]")) {
  button.onclick = async () => {
    modelRevision += 1;
    selectionLoading = true;
    deviceOperationId = null;
    wifiAvailable = pairedDevice = connectionPaused = wifiScanRunning = false;
    $("wifiNetworks").replaceChildren();
    $("usbPort").replaceChildren();
    updateBusy();
    selectedDeviceId = null;
    lightEditing = false;
    restoreSelectedPort = true;
    liveDevice = {};
    showDeviceDetails(button.dataset.deviceModel);
    $("yeelightHost").value = "";
    $("yeelightDiscovered").replaceChildren();
    $("wifiPassword").value = "";
    $("confirmBoard").checked = $("confirmFlash").checked = false;
    await scanPorts();
    await refreshMaintenance();
  };
}
$("deviceInstances").onchange = async () => {
  modelRevision += 1;
  selectionLoading = true;
  deviceOperationId = null;
  wifiAvailable = pairedDevice = connectionPaused = wifiScanRunning = false;
  $("wifiNetworks").replaceChildren();
  $("usbPort").replaceChildren();
  updateBusy();
  selectedDeviceId = $("deviceInstances").value;
  lightEditing = false;
  restoreSelectedPort = true;
  liveDevice = {};
  $("yeelightHost").value = "";
  $("wifiPassword").value = "";
  $("confirmBoard").checked = $("confirmFlash").checked = false;
  await scanPorts();
  await refreshMaintenance();
};
async function saveLightSetting(key, value) {
  if (selectionLoading || !selectedDeviceId || lightWriting) return;
  const deviceId = selectedDeviceId, modelId = selectedHardwareModel, revision = modelRevision;
  lightRevision += 1;
  lightWriting = true;
  lightEditing = false;
  updateBusy();
  try {
    await api("/api/device/light-settings", {device_id: deviceId, hardware_model: modelId, settings: {[key]: value}});
    if (revision === modelRevision) tell("此设备的灯光设置已保存");
  } catch (error) { tell(error.message, true); }
  finally {
    lightRevision += 1;
    lightWriting = false;
    updateBusy();
    await refreshMaintenance();
  }
}
$("deviceBrightness").oninput = () => {
  lightEditing = true;
  $("deviceBrightnessValue").textContent = $("deviceBrightness").value + "%";
  $("deviceBrightnessNote").hidden = Number($("deviceBrightness").value) !== 0;
};
$("deviceBrightness").onchange = () => saveLightSetting("brightness", Number($("deviceBrightness").value));
$("deviceReduced").onchange = () => saveLightSetting("reduced", $("deviceReduced").checked);
const stateBrightnessInputs = {working: "yeelightWorkingBrightness", done: "yeelightDoneBrightness", error: "yeelightErrorBrightness"};
for (const [state, id] of Object.entries(stateBrightnessInputs)) {
  $(id).oninput = () => {
    lightEditing = true;
    $(id + "Value").textContent = $(id).value + "%";
  };
  $(id).onchange = () => saveLightSetting("state_brightness", {[state]: Number($(id).value)});
}
function deviceQuery() {
  return "/api/device?model=" + encodeURIComponent(selectedHardwareModel) +
    (selectedDeviceId === null ? "" : "&device_id=" + encodeURIComponent(selectedDeviceId));
}
function scanQuery() { return "/api/wifi-networks?device_id=" + encodeURIComponent(selectedDeviceId || ""); }
const hardwareMode = () => document.body.dataset.mode === "hardware";
function updateBusy() {
  renderTestCountdown();
  for (const id of ["deviceBrightness", "deviceReduced", ...Object.values(stateBrightnessInputs)])
    $(id).disabled = selectionLoading || lightWriting || !selectedDeviceId;
  document.querySelectorAll("[data-device-model]").forEach(button => {
    button.disabled = false;
  });
  document
    .querySelectorAll("[data-maintenance]")
    .forEach((b) => (b.disabled = !hardwareMode() || selectionLoading || deviceJobRunning || triggerJobRunning));
  const blocked = !hardwareMode() || selectionLoading || deviceJobRunning || triggerJobRunning || liveDevice.connecting;
  $("connectYeelight").disabled = blocked || !$("yeelightHost").value.trim();
  $("discoverYeelight").disabled = !hardwareMode() || selectionLoading || deviceJobRunning || triggerJobRunning;
  $("testCurrentDevice").disabled = blocked || !selectedDeviceId || !liveDevice.online;
  const wifiOnline = liveDevice.online && liveDevice.port?.startsWith("wifi://");
  const usbOnline = liveDevice.online && !wifiOnline;
  const sameUsb = usbOnline && liveDevice.port === $("usbPort").value;
  $("useUsb").disabled = blocked || !$("usbPort").value || sameUsb;
  $("useUsb").textContent = sameUsb ? "USB 已连接" : usbOnline ? "切换到此串口" : wifiOnline ? "切换到 USB" : "连接 USB";
  $("useWifi").disabled = blocked || !wifiAvailable || wifiOnline;
  $("useWifi").textContent = wifiOnline ? "Wi-Fi 已连接" : usbOnline ? "切换到 Wi-Fi" : "连接 Wi-Fi";
  $("disconnectDevice").disabled = !hardwareMode() || selectionLoading || deviceJobRunning || triggerJobRunning || connectionPaused || !(liveDevice.online || liveDevice.connecting || pairedDevice);
  $("scanPorts").disabled = !hardwareMode() || selectionLoading || deviceJobRunning;
  $("flashBase").disabled = !hardwareMode() || selectionLoading || deviceJobRunning || triggerJobRunning
    || !$("usbPort").value || !$("confirmBoard").checked || !$("confirmFlash").checked;
  document
    .querySelectorAll("[data-trigger]")
    .forEach((b) => (b.disabled = deviceJobRunning || triggerJobRunning));
}
for (const button of document.querySelectorAll("[data-tab]")) {
  button.onclick = () => {
    deviceViewEpoch += 1;
    document
      .querySelectorAll("[data-tab]")
      .forEach((b) => b.classList.toggle("active", b === button));
    document
      .querySelectorAll(".pane")
      .forEach((p) => (p.hidden = p.id !== "pane-" + button.dataset.tab));
    if (button.dataset.tab === "device") showDeviceModels();
    if (button.dataset.tab === "testing") refreshDesktopClients();
  };
}
async function scanPorts() {
  if (!hardwareMode() || isYeelight()) return;
  const revision = modelRevision;
  try {
    const data = await api("/api/ports");
    if (!hardwareMode() || revision !== modelRevision) return;
    const selected = $("usbPort").value;
    $("usbPort").replaceChildren();
    for (const port of data.ports) {
      const option = el("option", port.description + " · " + port.port);
      option.value = port.port;
      $("usbPort").append(option);
    }
    if (!data.ports.length) {
      const option = el("option", "未找到 USB 设备");
      option.value = "";
      $("usbPort").append(option);
    }
    if (data.ports.some((p) => p.port === selected))
      $("usbPort").value = selected;
    updateBusy();
  } catch (error) {
    tell(error.message, true);
  }
}
async function maintenance(action, values = {}) {
  if (!hardwareMode() || selectionLoading) return false;
  try {
    const data = await api("/api/device", {
      action,
      hardware_model: selectedHardwareModel,
      device_id: selectedDeviceId || "",
      port: $("usbPort").value,
      ...values,
    });
    deviceOperationId = data.id;
    deviceJobRunning = true;
    updateBusy();
    renderJob(data);
    return true;
  } catch (error) {
    tell(error.message, true);
    $("deviceMessage").textContent = error.message;
    return false;
  }
}
function renderJob(job) {
  if (!job || (!hardwareMode() && job.action !== "select_model")) return;
  deviceJobRunning = job.state === "running";
  modelSelectionRunning = deviceJobRunning && job.action === "select_model";
  updateBusy();
  if (job.action === "discover" && job.state === "succeeded" && isYeelight() && (!job.hardware_model || job.hardware_model === selectedHardwareModel) && renderedDiscovery !== job.id + ":" + modelRevision) {
    renderedDiscovery = job.id + ":" + modelRevision;
    $("yeelightDiscovered").replaceChildren();
    const placeholder = el("option", job.result?.devices?.length ? "选择灯具…" : "未发现灯具，请检查局域网控制或填写 IP");
    placeholder.value = ""; $("yeelightDiscovered").append(placeholder);
    for (const item of job.result?.devices || []) {
      const option = el("option", item.name + " · " + item.host); option.value = item.host;
      $("yeelightDiscovered").append(option);
    }
  }
  $("deviceProgress").value = job.progress;
  $("jobState").textContent = {
    running: "进行中",
    succeeded: "已完成",
    failed: "需要处理",
  }[job.state];
  $("deviceMessage").textContent = job.message;
  $("deviceMessage").closest(".card").dataset.failed = job.state === "failed";
  $("deviceResult").textContent = job.result?.device_id
    ? "设备：" + job.result.device_id + " · " + job.result.board
    : "";
}
async function refreshMaintenance() {
  try {
    if (wifiScanRunning) {
      const revision = modelRevision;
      const scan = await api(scanQuery());
      if (revision === modelRevision) renderWifiScan(scan);
    }
    if ((!$("pane-device").hidden && !$("deviceDetails").hidden) || deviceJobRunning) {
      const revision = modelRevision;
      const appearanceRevision = lightRevision;
      const data = await api(deviceQuery());
      if (revision !== modelRevision) return;
      if (data.job?.id === deviceOperationId && data.job?.state === "succeeded" &&
          data.job?.hardware_model === selectedHardwareModel && !selectedDeviceId && data.job?.result?.device_id) {
        selectedDeviceId = data.job.result.device_id;
        data.selected_device_id = selectedDeviceId;
        deviceOperationId = null;
      }
      if (Object.hasOwn(data, "selected_device_id")) {
        selectionLoading = false;
        selectedDeviceId = data.selected_device_id;
        liveDevice = data.device || {};
        pairedDevice = Boolean(selectedDeviceId);
        if (appearanceRevision === lightRevision && !lightWriting && !lightEditing) {
          const appearance = data.light_settings || {brightness: isYeelight() ? 50 : 20, reduced: false};
          $("deviceBrightness").value = appearance.brightness;
          $("deviceBrightnessValue").textContent = appearance.brightness + "%";
          $("deviceReduced").checked = appearance.reduced;
          for (const [state, id] of Object.entries(stateBrightnessInputs)) {
            const value = appearance.state_brightness?.[state] ?? (state === "working" ? 20 : 50);
            $(id).value = value;
            $(id + "Value").textContent = value + "%";
          }
          $("deviceBrightnessNote").hidden = !selectedDeviceId || appearance.brightness !== 0;
        }
        $("deviceLightHint").hidden = Boolean(selectedDeviceId);
        $("deviceInstances").replaceChildren();
        const fresh = el("option", "添加此型号设备…"); fresh.value = "";
        $("deviceInstances").append(fresh);
        for (const item of data.devices || []) {
          const option = el("option", item.name + " · " + item.device_id + " · " + (item.online ? "已连接" : "离线"));
          option.dataset.userContent = ""; option.value = item.device_id; $("deviceInstances").append(option);
        }
        $("deviceInstances").value = selectedDeviceId;
        $("selectedDeviceState").textContent = liveDevice.online ? "此设备已连接，正在同步 AI 状态。" : "";
        $("selectedDeviceState").hidden = !liveDevice.online;
        if (isYeelight() && restoreSelectedPort) { $("yeelightHost").value = data.yeelight_host || ""; restoreSelectedPort = false; }
        if (restoreSelectedPort && data.selected_port && Array.from($("usbPort").options).some(option => option.value === data.selected_port)) {
          $("usbPort").value = data.selected_port; restoreSelectedPort = false;
        }
      }
      if (data.hardware_model && data.job?.state !== "running" && !$("deviceDetails").hidden) showDeviceDetails(data.hardware_model);
      {
        wifiAvailable = Boolean(data.wifi_available);
        connectionPaused = Boolean(data.connection_paused);
        $("wifiRemembered").textContent = data.wifi_info_current
          ? data.wifi_ssid ? "设备已记忆：" + data.wifi_ssid + (data.wifi_connected ? " · 已连入网络" : " · 尚未连入网络") : "设备尚未配置 Wi-Fi"
          : data.wifi_info_known ? "上次记录：" + (data.wifi_ssid || "未配置 Wi-Fi") + " · 等待设备确认" : "网络名未知 · 连接并更新灯光程序后可读取";
        updateBusy();
        $("pairedDevice").textContent = data.paired_device
          ? "已配对：" + data.paired_device
          : "尚未配对，请先保存网络并安装灯光程序";
        $("transportMode").textContent =
          "当前方式：" + (["wifi", "yeelight"].includes(data.transport) ? "Wi-Fi 局域网" : "USB");
        renderJob(data.job);
      }
    }
    const test = (await api("/api/trigger-test")).job;
    if (test) {
      triggerJobRunning = test.state === "running";
      updateBusy();
      $("testState").textContent =
        test.state === "running"
          ? "测试中"
          : test.state === "succeeded"
            ? "事件已接收"
            : "测试失败";
      $("testMessage").textContent = test.message;
      $("testDelivery").replaceChildren();
      for (const record of test.delivery || []) {
        $("testDelivery").append(
          el(
            "div",
            names[record.state] +
              " · " +
              (results[record.status] || record.status),
          ),
        );
      }
    }
  } catch (error) {
    /* Main dashboard owns the connection warning. */
  }
}
function renderWifiScan(data) {
  wifiScanRunning = data.state === "running";
  $("wifiScanHint").textContent = data.message || "";
  if (data.state === "succeeded") {
    $("wifiNetworks").replaceChildren();
    for (const network of data.networks || []) {
      const option = el("button", network.ssid + " · " + network.signal + " dBm");
      option.type = "button";
      option.dataset.userContent = ""; option.value = network.ssid;
      option.setAttribute("role", "option");
      option.onclick = () => { $("wifiSsid").value = network.ssid; closeWifiChoices(); };
      $("wifiNetworks").append(option);
    }
    if (!data.networks?.length) $("wifiNetworks").textContent = data.message || "未发现网络，可手动输入";
  } else {
    $("wifiNetworks").textContent = data.message || "ESP32 正在搜索…";
  }
  updateBusy();
}
let lastWifiScan = 0;
async function scanWifiChoices() {
  if (!hardwareMode() || !selectedDeviceId || selectionLoading || deviceJobRunning || wifiScanRunning || Date.now() - lastWifiScan < 15000) return;
  lastWifiScan = Date.now();
  wifiScanRunning = true;
  updateBusy();
  const revision = modelRevision;
  try { const scan = await api("/api/wifi-networks", {device_id: selectedDeviceId || ""}); if (revision === modelRevision) renderWifiScan(scan); }
  catch { if (revision === modelRevision) renderWifiScan({state: "failed", message: "搜索失败，请检查 ESP32 连接；也可手动输入网络名。"}); }
}
function closeWifiChoices() {
  $("wifiNetworks").hidden = true;
  $("wifiSsid").setAttribute("aria-expanded", "false");
}
function openWifiChoices() {
  $("wifiNetworks").hidden = false;
  $("wifiSsid").setAttribute("aria-expanded", "true");
  if (!$("wifiNetworks").childNodes.length) $("wifiNetworks").textContent = "正在读取 ESP32 网络列表…";
  scanWifiChoices();
}
$("wifiSsid").onfocus = openWifiChoices;
$("wifiSsid").onclick = openWifiChoices;
$("wifiSsid").onkeydown = event => {
  if (event.key === "Escape") closeWifiChoices();
  if (event.key === "ArrowDown") { openWifiChoices(); $("wifiNetworks").querySelector("button")?.focus(); event.preventDefault(); }
};
document.addEventListener("mousedown", event => {
  if (!event.target.closest(".wifi-picker")) closeWifiChoices();
});
$("yeelightHost").oninput = updateBusy;
$("yeelightDiscovered").onchange = () => { $("yeelightHost").value = $("yeelightDiscovered").value; updateBusy(); };
$("discoverYeelight").onclick = () => maintenance("discover");
$("connectYeelight").onclick = () => maintenance("connect", {transport: "yeelight", host: $("yeelightHost").value.trim()});
$("disconnectDevice").onclick = () => maintenance("disconnect");
$("scanPorts").onclick = scanPorts;
$("usbPort").onchange = updateBusy;
$("probeDevice").onclick = () => maintenance("probe");
$("useWifi").onclick = () => maintenance("connect", { transport: "wifi" });
$("useUsb").onclick = () => maintenance("connect", { transport: "usb" });
$("testCurrentDevice").onclick = async () => {
  if ($("testCurrentDevice").disabled) return;
  $("testCurrentDevice").disabled = true;
  try {
    const target = selectedDeviceId;
    const response = await api("/api/device/test-light", {device_id: target});
    testDeadlines.set(target, performance.now() + (response.remaining_ms ?? 10000));
    renderTestCountdown();
  } catch (error) { tell(error.message, true); }
  finally { updateBusy(); }
};
$("installFirmware").onclick = () => maintenance("install");
$("wifiForm").onsubmit = async (event) => {
  event.preventDefault();
  const ok = await maintenance("provision", {
    ssid: $("wifiSsid").value,
    password: $("wifiPassword").value,
    open_network: $("openNetwork").checked,
  });
  if (ok) $("wifiPassword").value = "";
};
$("flashBase").onclick = async () => {
  if (!$("confirmBoard").checked || !$("confirmFlash").checked) {
    tell("请确认开发板型号，并确认清除板上的程序和配置。", true);
    return;
  }
  $("flashBase").disabled = true;
  try {
    await maintenance("flash_base", {
      confirm_board: true,
      confirm_flash: true,
    });
  } finally {
    updateBusy();
  }
};
$("confirmBoard").onchange = updateBusy;
$("confirmFlash").onchange = updateBusy;
for (const button of document.querySelectorAll("[data-trigger]")) {
  button.onclick = async () => {
    try {
      await api("/api/trigger-test", { source: button.dataset.trigger });
      triggerJobRunning = true;
      updateBusy();
      await refreshMaintenance();
    } catch (error) {
      $("testMessage").textContent = error.message;
      tell(error.message, true);
    }
  };
}
let previousHardware = false;
document.addEventListener("flame-mode", (event) => {
  for (const item of event.detail.devices || []) testDeadlines.set(item.device_id, performance.now() + (item.test_remaining_ms || 0));
  renderTestCountdown();
  if (event.detail.devices) {
    liveDevice = event.detail.devices.find(item => item.device_id === selectedDeviceId) || {};
    pairedDevice = Boolean(selectedDeviceId);
    for (const count of document.querySelectorAll("[data-model-count]")) {
      const devices = event.detail.devices.filter(item => item.hardware_model === count.dataset.modelCount);
      const online = devices.filter(item => item.online).length;
      count.textContent = devices.length ? `在线 ${online} · 离线 ${devices.length - online}` : "未添加设备";
      count.dataset.online = String(Boolean(online));
    }
  } else {
    liveDevice = event.detail.device || {};
    pairedDevice = Boolean(event.detail.paired);
  }
  if (!hardwareMode() && !modelSelectionRunning) deviceJobRunning = false;

  updateBusy();
  if (hardwareMode() && !previousHardware && !$("pane-device").hidden && !$("deviceDetails").hidden) scanPorts();
  previousHardware = hardwareMode();
});
updateBusy();
refreshMaintenance();
setInterval(refreshMaintenance, 1200);

async function refreshDesktopClients() {
  try {
    const data = await api("/api/desktop-clients");
    $("desktopPermissions").disabled = !data.supported;
    $("desktopPermissionsStatus").textContent = data.supported
      ? "辅助功能：" + (data.permissions.accessibility ? "已授权" : "未授权") +
        " · 输入监控：" + (data.permissions.input_monitoring ? "已授权" : "未授权")
      : "桌面客户端接入目前仅支持 macOS。";
    const statuses = {
      checking: "正在检查", unsupported: "系统暂不支持", not_installed: "未发现安装",
      disabled: "请先开启来源", paused: "触发已暂停", permission_required: "需要授权",
      input_unavailable: "中断监测不可用，请重启 SeeEnd 并检查权限", ambiguous: "多个客户端进程，暂不跟踪",
      waiting: "请打开客户端", generating: "检测到生成中", ready: "控件已识别，等待新任务",
      controls_unavailable: "未识别到完整对话控件，请打开对话窗口；此版本可能需要适配",
    };
    $("desktopClientStatus").replaceChildren();
    for (const client of data.clients) {
      $("desktopClientStatus").append(el("div", client.name +
        (client.versions?.length ? " " + client.versions.join(" / ") : "") + " · " +
        (statuses[client.status] || "等待检查") + (client.tracking ? " · 正在跟踪本次任务" : "")));
    }
  } catch (error) {
    $("desktopPermissionsStatus").textContent = error.message;
  }
}
$("checkDesktopClients").onclick = refreshDesktopClients;
$("desktopPermissions").onclick = async () => {
  const button = $("desktopPermissions");
  button.disabled = true;
  try {
    const result = await api("/api/desktop-clients/permissions", {});
    tell(result.message);
  } catch (error) {
    tell(error.message, true);
  } finally {
    await refreshDesktopClients();
  }
};
refreshDesktopClients();
setInterval(() => {
  if (!$("pane-testing").hidden) refreshDesktopClients();
}, 3000);

for (const button of document.querySelectorAll("[data-install]")) {
  button.onclick = async () => {
    button.disabled = true;
    try {
      const result = await api("/api/integrations/install", {
        source: button.dataset.install,
      });
      $("testMessage").textContent = result.message;
      tell(result.message);
    } catch (error) {
      $("testMessage").textContent = error.message;
      tell(error.message, true);
    } finally {
      button.disabled = false;
    }
  };
}

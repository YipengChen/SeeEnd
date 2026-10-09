// Shared dashboard UI helpers; imported explicitly by each page controller.
export const $ = (id) => document.getElementById(id);
export const names = {
  idle: "当前无任务",
  working: "任务进行中",
  needs_input: "等待输入",
  timeout: "已超时",
  done: "任务已完成",
  error: "失败",
  cancelled: "已取消",
  unknown: "状态未知",
};
export const results = {
  screen: "APP 已呈现",
  queued: "等待发送",
  applied: "设备已确认",
  suppressed: "已静默",
  unmatched: "来源已停用",
  expired: "事件已过期",
  lease_expired: "工作状态超时",
  dropped: "队列已淘汰",
  ended_on_reconnect: "重连后不补播",
  superseded: "已被后续状态替代",
  interrupted: "进程重启，未重播",
};
export function appendDeliverySummary(cell, summary) {
  if (!summary) return;
  const devices = summary.devices || [];
  const name = (device) => device.name && device.name !== device.device_id
    ? `${device.name}（${device.device_id}）` : device.device_id;
  const receiptDetails = devices.map((device) => `${name(device)}\n${device.reason}`).join("\n\n");
  cell.title = `处理结果：${summary.reason || ""}` + (devices.length ? `\n\n设备回执\n${receiptDetails}` : "");
  if (!devices.length) return;
  const confirmed = devices.filter((device) => device.status === "applied").length;
  const details = el("div", `已确认 ${confirmed}/${devices.length} 台设备`, "muted");
  details.dataset.deliveryDetails = "";
  cell.append(details);
}
export function tell(text, error = false) {
  $("message").textContent = text;
  $("message").classList.toggle("error", error);
}
export async function api(path, body) {
  const controller = new AbortController();
  const installAction = body && /\/api\/(?:integrations|codex)\/(?:install|uninstall)$/.test(path);
  const timer = setTimeout(() => controller.abort(), installAction ? 35000 : 7000);
  try {
    const r = await fetch(path, {
      method: body ? "POST" : "GET",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    const data = await r.json();
    if (!r.ok) throw Error(data.error || "请求失败");
    return data;
  } finally {
    clearTimeout(timer);
  }
}
export function el(tag, text, cls) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

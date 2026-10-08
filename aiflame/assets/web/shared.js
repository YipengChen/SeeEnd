// Shared dashboard UI helpers; imported explicitly by each page controller.
export const $ = (id) => document.getElementById(id);
export const names = {
  idle: "待机",
  working: "工作中",
  needs_input: "等待输入",
  timeout: "已超时",
  done: "已完成",
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
};
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

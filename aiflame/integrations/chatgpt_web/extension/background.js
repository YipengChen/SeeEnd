importScripts('providers.js');
const HOST = 'com.aiflame.chatgpt';
const ALLOWED = ['schema_version','event_id','source_id','task_id','seq','state','occurred_at','ttl_ms','reason'];
let queue = Promise.resolve(), wakeTimer;
function scheduleWake() {
  if (wakeTimer === undefined) wakeTimer = setTimeout(wakeTabs, 5000);
}
async function wakeTabs() {
  wakeTimer = undefined;
  try {
    const saved = await chrome.storage.session.get(null);
    const records = Object.entries(saved).filter(([key, record]) => /^tab_\d+$/.test(key) && record.event?.state === 'working');
    await Promise.all(records.map(async ([key, record]) => {
      try {
        const page = await chrome.tabs.sendMessage(Number(key.slice(4)), {kind:'tick'},
          {frameId:0, ...(record.documentId ? {documentId:record.documentId} : {})});
        if (page?.source_id === record.event.source_id && page.active === false) await forgetRecord(key, record);
      } catch {
        // An unreachable document never becomes success. Stop polling stale
        // leases; actual tab close/navigation cancellation is handled below.
        if (Date.now() - Date.parse(record.event.occurred_at) > 120000) await forgetRecord(key, record);
      }
    }));
    if (records.length) scheduleWake();
  } catch {}
}
async function forgetRecord(key, record) {
  const current = (await chrome.storage.session.get(key))[key];
  if (current?.event?.task_id === record.event.task_id && current?.event?.seq === record.event.seq)
    await chrome.storage.session.remove(key);
}
// Resume active documents after a service-worker restart. No polling while idle.
wakeTabs();
function valid(event) {
  return event && typeof event === 'object' && Object.keys(event).every(k => ALLOWED.includes(k))
    && event.schema_version === '1.0' && FlameProviders.SITES.some(site => site.id === event.source_id)
    && /^[0-9a-f-]{36}$/i.test(event.event_id) && /^[0-9a-f-]{36}$/i.test(event.task_id)
    && Number.isInteger(event.seq) && event.seq > 0 && event.seq <= 2147483647
    && ['working','done','idle','error'].includes(event.state)
    && event.ttl_ms === 60000 && typeof event.occurred_at === 'string'
    && [undefined,'completed','cancelled','timeout','failed'].includes(event.reason);
}
function native(event) {
  return new Promise(resolve => chrome.runtime.sendNativeMessage(HOST, event, result => {
    const error = chrome.runtime.lastError;
    resolve(error ? {ok: false, error: 'native_host_unavailable', detail: error.message} : result);
  }));
}
async function forward(event, tabId, documentId, cancelOrigin) {
  const result = await native(event);
  const delivery = {...result, at:Date.now(), state:event.state, source_id:event.source_id,
    ...(cancelOrigin ? {cancel_origin:cancelOrigin} : {})};
  await chrome.storage.local.set({last_result:delivery, ['result_' + event.source_id]:delivery});
  if (result?.ok) {
    if (event.state === 'working') {
      await chrome.storage.session.set({['tab_' + tabId]: {event, documentId}});
      scheduleWake();
    }
    else await chrome.storage.session.remove('tab_' + tabId);
  }
  return result;
}
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id === chrome.runtime.id && sender.url === chrome.runtime.getURL('popup.html')
      && message.kind === 'check') {
    if (!FlameProviders.SITES.some(site => site.id === message.source_id)) return;
    native({kind:'ping', source_id:message.source_id}).then(reply); return true;
  }
  if (sender.id !== chrome.runtime.id || sender.frameId !== 0 || !sender.tab) return;
  let url; try { url = new URL(sender.url); } catch { return; }
  const site = FlameProviders.siteFor(url.href);
  if (!site || message.kind !== 'event' || !valid(message.event) || message.event.source_id !== site.id) return;
  queue = queue.catch(() => {}).then(() => forward(message.event, sender.tab.id, sender.documentId));
  queue.then(reply, () => reply({ok: false, error: 'bridge_failed'}));
  return true;
});
async function cancelTab(tabId, loading = false) {
  const key = 'tab_' + tabId, saved = await chrome.storage.session.get(key);
  const record = saved[key]; if (!record) return;
  const event = record.event || record;
  if (loading) {
    // Chrome can report loading during SPA conversation creation. A live content
    // document owns its turn; only cancel here when that document has disappeared.
    try {
      const page = await chrome.tabs.sendMessage(tabId, {kind:'probe'},
        {frameId:0, ...(record.documentId ? {documentId:record.documentId} : {})});
      if (page?.source_id === event.source_id && (record.documentId || page.active)) return;
    } catch {}
  }
  await forward({...event, event_id: crypto.randomUUID(), seq: event.seq + 1,
    state: 'idle', reason: 'cancelled', occurred_at: new Date().toISOString()}, tabId, undefined,
    loading ? 'document_unloaded' : 'tab_closed');
  await chrome.storage.session.remove(key);
}
chrome.tabs.onRemoved.addListener(tabId => {
  queue = queue.then(() => cancelTab(tabId)).catch(() => {});
});
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (change.status === 'loading') queue = queue.then(() => cancelTab(tabId, true)).catch(() => {});
});

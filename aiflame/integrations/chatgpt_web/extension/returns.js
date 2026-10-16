/* Profile-local task destinations and a native return channel. No chat content. */
(() => {
  'use strict';
  const KEY = 'seeend_task_returns', CLIENT = 'seeend_return_client', EPOCH = 'seeend_return_epoch';
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  let port, ready = false, reconnect, retryDelay = 2000, identity, writes = Promise.resolve(), commands = Promise.resolve();

  async function ids() {
    if (!identity) identity = (async () => {
      const local = await chrome.storage.local.get(CLIENT), session = await chrome.storage.session.get(EPOCH);
      const client = UUID.test(local[CLIENT] || '') ? local[CLIENT] : crypto.randomUUID();
      const epoch = UUID.test(session[EPOCH] || '') ? session[EPOCH] : crypto.randomUUID();
      await chrome.storage.local.set({[CLIENT]:client});
      await chrome.storage.session.set({[EPOCH]:epoch});
      return {client, epoch};
    })().catch(error => { identity = undefined; throw error; });
    return identity;
  }

  function safeURL(value, source) {
    try {
      const url = new URL(value), site = FlameProviders.siteFor(url.href);
      if (!site || site.id !== source || url.protocol !== 'https:' || url.username || url.password || url.href.length > 2048) return null;
      return new URL(FlameProviders.routeFor(url), url.origin).href;
    } catch { return null; }
  }

  function stableURL(value, source) {
    const clean = safeURL(value, source);
    if (!clean) return null;
    const url = new URL(clean), site = FlameProviders.siteFor(clean), route = FlameProviders.routeFor(url);
    return site.conversationRoute.test(route) && !site.newRoute.test(route) && !site.temporaryRoute?.test(route) ? clean : null;
  }

  function retained(saved) {
    return Object.fromEntries(Object.entries(saved).filter(([,item]) => item.at > Date.now() - 7*86400000)
      .sort((a,b) => b[1].at-a[1].at).slice(0,1000));
  }

  async function focusWindow(windowId) {
    const window = chrome.windows.get ? await chrome.windows.get(windowId) : null;
    if (window?.state === 'minimized') await chrome.windows.update(windowId, {state:'normal'});
    await chrome.windows.update(windowId, {focused:true});
  }

  async function probe(tabId) {
    let timer;
    try {
      return await Promise.race([chrome.tabs.sendMessage(tabId, {kind:'return-probe'}, {frameId:0}),
        new Promise((_,reject) => { timer = setTimeout(() => reject(Error('page_unavailable')), 1500); })]);
    } finally { clearTimeout(timer); }
  }

  async function remember(event, sender, returnURL) {
    if (!sender?.tab || sender.tab.incognito || !UUID.test(event.task_id || '')) return event;
    const clean = safeURL(returnURL || sender.url, event.source_id);
    if (!clean) return event;
    const {client, epoch} = await ids();
    const locator = event.task_id;
    writes = writes.catch(() => {}).then(async () => {
      const saved = (await chrome.storage.local.get(KEY))[KEY] || {};
      const old = saved[locator];
      // Cancellation may follow navigation. Preserve the originating conversation.
      const url = event.reason === 'cancelled' && old ? old.url : clean;
      saved[locator] = {source:event.source_id, epoch, tab:sender.tab.id,
        document:sender.documentId || '', url, at:Date.now()};
      await chrome.storage.local.set({[KEY]:retained(saved)});
    });
    await writes;
    ensure();
    return {...event, return_target:{kind:'web', client_id:client, locator_id:locator}};
  }

  async function open(command) {
    if (!command || !UUID.test(command.request_id || '') || !UUID.test(command.locator_id || '')
        || !FlameProviders.SITES.some(site => site.id === command.source_id)) return 'failed';
    const timeout = command.timeout_ms === undefined ? 15000 : command.timeout_ms;
    if (!Number.isInteger(timeout) || timeout <= 0 || timeout > 15000) return 'failed';
    const until = Date.now() + timeout;
    let records;
    writes = writes.catch(() => {}).then(async () => {
      records = retained((await chrome.storage.local.get(KEY))[KEY] || {});
      await chrome.storage.local.set({[KEY]:records});
    });
    await writes;
    const record = records[command.locator_id];
    if (!record || record.source !== command.source_id || record.at <= Date.now() - 7*86400000) return 'missing';
    const url = safeURL(record.url, record.source);
    if (!url) return 'missing';
    const {epoch} = await ids();
    if (record.epoch === epoch && Number.isInteger(record.tab)) {
      try {
        const tab = await chrome.tabs.get(record.tab);
        if (!tab.incognito) {
          const page = await probe(record.tab);
          if (Date.now() >= until) return 'failed';
          if (page?.source_id === record.source && safeURL(page.url, record.source) === url) {
            await chrome.tabs.update(record.tab, {active:true});
            await focusWindow(tab.windowId);
            return 'opened';
          }
        }
      } catch { /* Closed, unloaded or changed page: use only a stable saved conversation. */ }
    }
    const reopen = stableURL(url, record.source);
    if (!reopen) return 'missing';
    if (Date.now() >= until) return 'failed';
    const tab = await chrome.tabs.create({url:reopen, active:true});
    await focusWindow(tab.windowId);
    // The new tab becomes the next return target. A browser epoch never reuses old IDs.
    writes = writes.catch(() => {}).then(async () => {
      const saved = (await chrome.storage.local.get(KEY))[KEY] || {};
      if (saved[command.locator_id]) {
        saved[command.locator_id] = {...saved[command.locator_id], tab:tab.id, epoch, document:''};
        await chrome.storage.local.set({[KEY]:saved});
      }
    });
    await writes;
    return 'opened';
  }

  function ensure(force = false) {
    if (force && reconnect) { clearTimeout(reconnect); reconnect = undefined; }
    if (port || reconnect || !chrome.runtime.connectNative) return;
    ids().then(({client}) => {
      if (port) return;
      const current = chrome.runtime.connectNative('com.aiflame.chatgpt');
      port = current;
      current.onMessage.addListener(message => {
        if (message.kind === 'return-ready') {
          ready = !!message.ok;
          if (ready) retryDelay = 2000;
        }
        else if (message.kind === 'return-task' && ready) {
          commands = commands.catch(() => {}).then(async () => {
            let status;
            try { status = await open(message); } catch { status = 'failed'; }
            if (port === current) current.postMessage({kind:'return-result', request_id:message.request_id, status});
          });
        } else if (message.kind === 'return-disconnected' || message.ok === false) current.disconnect();
      });
      current.onDisconnect.addListener(() => {
        void chrome.runtime.lastError;
        if (port !== current) return;
        port = undefined; ready = false;
        // When SeeEnd is closed, progressively stop waking a native process.
        // A successful lifecycle/popup request can reconnect immediately.
        const delay = retryDelay;
        retryDelay = Math.min(60000, retryDelay * 2);
        reconnect = setTimeout(() => { reconnect = undefined; ensure(); }, delay);
      });
      current.postMessage({kind:'return-hello', client_id:client});
    }).catch(() => {});
  }

  globalThis.FlameReturns = {remember, open, ensure, safeURL, stableURL};
})();

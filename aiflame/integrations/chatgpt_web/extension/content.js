(() => {
  'use strict';
  const site = FlameProviders.siteFor(location.href);
  if (!site) return;
  const adapter = FlameProviders.createAdapter(site, document, location, () => navigator.onLine);
  let queue = Promise.resolve(), disabled = false, submission = 0, failedTask;
  function deliver(event) {
    const owner = submission;
    const return_url = location.origin + (machine.active?.route || FlameProviders.routeFor(location));
    function failed() {
      // Drop all queued events for this failed task, including a queued success.
      // A late failure must not cancel a newer user submission.
      failedTask = event.task_id;
      if (owner !== submission) return;
      disabled = true; cancel('bridge_failed');
    }
    queue = queue.then(async () => {
      if (event.task_id === failedTask) return;
      const result = await chrome.runtime.sendMessage({kind: 'event', event, return_url});
      if (!result?.ok) failed();
    }).catch(failed);
  }
  const machine = new FlameTurn(deliver, () => crypto.randomUUID(), () => Date.now(), FlameProviders.routePolicy(site));
  let renderStatus = null;
  if (site.backgroundFrames) document.addEventListener('aiflame:render-status', event => {
    const value = event.detail;
    if (!value || typeof value.enabled !== 'boolean') return;
    const counts = ['pending_frames','short_intervals','frame_runs','interval_runs','render_callbacks','render_resumes','short_timeouts','timeout_runs'];
    if (!counts.every(key => Number.isSafeInteger(value[key]) && value[key] >= 0)) return;
    renderStatus = {enabled:value.enabled, ...Object.fromEntries(counts.map(key => [key,value[key]]))};
  });
  function renderState() {
    if (site.backgroundFrames) document.dispatchEvent(new CustomEvent('aiflame:render-active',
      {detail:!disabled && !!(machine.pending || machine.active)}));
  }
  function cancel(cause) { machine.cancel(cause); renderState(); }
  function arm() {
    const snapshot = adapter.snapshot();
    if (machine.active && snapshot.busy) return;
    disabled = false;
    machine.arm(snapshot); submission++;
    renderState(); schedule();
  }
  // A bounded local trace of control types, never message IDs or chat contents.
  let lastTurnKey, replySamples = [];
  let scheduled = false;
  function check() {
    scheduled = false;
    if (!disabled && (machine.pending || machine.active)) {
      const s = adapter.snapshot();
      if (s.turnKey !== lastTurnKey) {
        lastTurnKey = s.turnKey;
        replySamples.push({origin:s.turnOrigin, busy:s.busy, reply_count:s.turnKeys.length});
        if (replySamples.length > 12) replySamples.shift();
      }
      machine.update(s);
      renderState();
    }
  }
  function schedule() {
    if (!disabled && (machine.pending || machine.active) && !scheduled) { scheduled = true; setTimeout(check, 100); }
  }
  document.addEventListener('click', e => {
    if (!e.isTrusted || !(e.target instanceof Element)) return;
    if (adapter.isStop(e.target)) cancel();
    else if (adapter.isSubmit(e.target)) { arm(); }
    else {
      const link = e.target.closest('a[href]');
      if (link && FlameProviders.routeFor(new URL(link.href, location.href)) !== FlameProviders.routeFor(location)) cancel('navigation');
    }
  }, true);
  document.addEventListener('keydown', e => {
    if (e.isTrusted && e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey && !e.isComposing
        && e.target instanceof Element && adapter.isComposer(e.target)) { arm(); }
  }, true);
  document.addEventListener('submit', e => {
    if (e.isTrusted && e.target instanceof Element
        && e.target.querySelector(site.composer)) { arm(); }
  }, true);
  window.addEventListener('offline', () => cancel('offline'));
  window.addEventListener('pagehide', () => cancel('pagehide'));
  window.addEventListener('popstate', schedule);
  window.addEventListener('hashchange', schedule);
  document.addEventListener('visibilitychange', schedule);
  window.addEventListener('pageshow', schedule);
  new MutationObserver(schedule).observe(document.documentElement,
    {childList: true, subtree: true, attributes: true,
      attributeFilter: ['class','disabled','aria-disabled','aria-label','data-testid','data-status','hidden','style',
        'data-message-id','data-turn-id','data-message-author-role','data-chat-answers-wrap','data-virtual-list-item-key',...(site.identityAttrs || [])]});
  setInterval(check, 1000);
  chrome.runtime.onMessage.addListener((message, sender, reply) => {
    if (message.kind === 'return-probe') {
      reply({source_id:site.id, url:location.origin + FlameProviders.routeFor(location)});
      return;
    }
    // Navigation liveness must not depend on optional DOM diagnostics. A layout
    // inspection failure does not mean the originating document has unloaded.
    if (message.kind === 'tick') {
      renderState();
      if (site.backgroundFrames && (machine.pending || machine.active))
        document.dispatchEvent(new CustomEvent('aiflame:render-pulse'));
      check();
      reply({source_id:site.id, active:!!machine.active});
      return;
    }
    if (message.kind === 'probe') {
      reply({source_id:site.id, active:!!machine.active});
      return;
    }
    if (message.kind === 'inspect') {
      if (site.backgroundFrames) document.dispatchEvent(new CustomEvent('aiflame:render-inspect'));
      const s = adapter.snapshot();
      reply({source_id:site.id, site:site.name, recognized:s.recognized, busy:s.busy, active:!!machine.active,
        disabled, pending:!!machine.pending, final_controls:!!s.final, response_settled:s.settled, composer_ready:s.ready,
        reply_matches_active:machine.active?.generationTurn ? s.turnKey === machine.active.generationTurn : null,
        new_final_controls:!!s.final && s.final !== machine.active?.final,
        reply_samples:replySamples, render_state:renderStatus,
        busy_controls:adapter.inspectBusyControls?.(),
        reply_structure:adapter.inspectReplyControls?.(),
        reply_layout:adapter.inspectReplyLayout?.(),
        reply_ancestors:adapter.inspectReplyAncestors?.(),
        page_controls:adapter.inspectPageControls?.(),
        composer_controls:adapter.inspectComposerControls?.(),
        last_transition:machine.lastTransition, version:FlameProviders.VERSION});
    }
  });
})();

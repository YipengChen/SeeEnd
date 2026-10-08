/* Shared control detection for every provider. Never reads prompt or response text. */
(function (root) {
  'use strict';
  const join = (...values) => values.filter(Boolean).join(',');
  function createAdapter(site, doc, location, online, isVisible = el => !!el && el.isConnected && el.getClientRects().length > 0, routeFor) {
    const ids = new WeakMap(), nodeKeys = new WeakMap(), aliases = new Map(), revisions = new Map(); let next = 0;
    const id = el => { if (!ids.has(el)) ids.set(el, String(++next)); return ids.get(el); };
    const find = (selector, scope = doc) => selector && [...scope.querySelectorAll(selector)].find(isVisible);
    function turnId(turn, anchor = turn) {
      if (!turn) return null;
      // Server-provided opaque IDs survive toolbar and response-node replacement.
      // They stay inside this document and are never included in emitted events.
      const markers = [];
      for (const attr of ['data-message-id','data-turn-id','data-chat-answers-wrap','data-virtual-list-item-key',...(site.identityAttrs || [])]) {
        const owner = turn.hasAttribute(attr) ? turn : anchor?.closest('[' + attr + ']');
        const value = owner?.getAttribute(attr);
        if (value) markers.push(attr + ':' + value);
      }
      const testId = turn.getAttribute('data-testid');
      // Generic names such as conversation-turn-message repeat across replies.
      if (/^conversation-turn-(?:\d+|[0-9a-f-]{36})$/i.test(testId || '')) markers.push('testid:' + testId);
      // Streaming placeholders can receive or change server IDs after tracking
      // starts. Pin the existing node identity and attach later IDs as aliases.
      const key = nodeKeys.get(turn) || (anchor && nodeKeys.get(anchor))
        || markers.map(marker => aliases.get(marker)).find(Boolean) || markers[0] || 'dom:' + id(turn);
      nodeKeys.set(turn, key);
      if (anchor) nodeKeys.set(anchor, key);
      for (const marker of markers) {
        aliases.delete(marker); aliases.set(marker, key);
        if (aliases.size > 1024) aliases.delete(aliases.keys().next().value);
      }
      return key;
    }
    const usable = el => {
      if (!el) return false;
      for (let node = el; node && node !== doc.documentElement; node = node.parentElement) {
        if (node.disabled || node.getAttribute('disabled') !== null || node.getAttribute('aria-disabled') === 'true'
            || [...node.classList].some(cls => /(?:^|[-_])disabled(?:$|[-_])/i.test(cls))
            || node.classList.contains('shimGrey')) return false;
      }
      return true;
    };
    const findUsable = (selector,scope) => selector && [...scope.querySelectorAll(selector)].find(el=>isVisible(el) && usable(el));
    function snapshot() {
      const composer = find(site.composer);
      // Keep the latest mounted reply even if layout is temporarily unavailable.
      // Falling back to a visible history reply would risk reporting old controls.
      const turns = [...doc.querySelectorAll(site.turn)].filter(el => !el.parentElement?.closest(site.turn));
      let turn = turns.at(-1), turnOrigin = turn ? 'reply' : 'none';
      if (!turn && site.fallbackTurn) { turn = [...doc.querySelectorAll(site.fallbackTurn)].at(-1); if (turn) turnOrigin = 'fallback'; }
      const anchor = turn;
      if (turn && site.turnContainer) turn = turn.closest(site.turnContainer) || turn;
      if (turn && site.fallbackScopeControls && turn.matches(site.fallbackTurn)) {
        // Some Doubao builds rename the message wrapper. Bind to the smallest
        // action row containing this copy and its own regenerate control.
        let scope = turn.parentElement, resolved = null;
        for (let depth = 0; scope && depth < 6 && !['BODY','HTML'].includes(scope.tagName); depth++, scope = scope.parentElement) {
          if (scope.querySelectorAll(site.fallbackTurn).length !== 1) break;
          if (find(site.final, scope)) { resolved = scope; break; }
        }
        turn = resolved;
      }
      // The fallback may itself be a copy control; include it without traversing chat text.
      const rendered = turn && isVisible(turn);
      const copyControl = rendered && (turn.matches(site.copy || ':not(*)') ? turn : findUsable(site.copy, turn));
      const finalControl = rendered && findUsable(site.final, turn);
      const row = rendered && find(site.actionRow, turn);
      const controls = row && [...row.querySelectorAll(site.actionControls)].filter(el => isVisible(el) && usable(el));
      const completedControls = rendered && findUsable(site.completedControls,turn);
      const finalAvailable = (usable(copyControl) && usable(finalControl)) || (controls?.length >= site.minimumControls)
        || usable(completedControls);
      const turnKey = turnId(turn, anchor);
      let finalKey = null;
      if (turn) {
        const previous = revisions.get(turnKey) || {available:false, seq:0};
        if (finalAvailable && !previous.available) previous.seq++;
        previous.available = !!finalAvailable;
        revisions.delete(turnKey);
        revisions.set(turnKey, previous);
        if (revisions.size > 256) revisions.delete(revisions.keys().next().value);
        if (finalAvailable) finalKey = turnKey + ':' + previous.seq;
      }
      const turnKeys = turns.map(el => turnId(site.turnContainer ? el.closest(site.turnContainer) || el : el, el));
      return {turnKey, turnKeys, turnOrigin, route:routeFor(location), online:online(), busy:!!find(site.stop) || !!find(site.busy)
          || !!(rendered && find(site.turnBusy,turn)),
        ready:!!composer && !composer.disabled && composer.getAttribute('aria-disabled') !== 'true',
        final:finalKey, error:!!(turn && find(site.error, turn)), recognized:!!composer};
    }
    function inspectBusyControls() {
      return [...doc.querySelectorAll(join(site.stop,site.busy))].slice(0,8).map(el => {
        const style = doc.defaultView.getComputedStyle(el);
        return {display:style.display, visibility:style.visibility, opacity:style.opacity,
          aria_hidden:el.getAttribute('aria-hidden'), rects:el.getClientRects().length};
      });
    }
    function inspectReplyControls() {
      const replies = [...doc.querySelectorAll(site.turn)].filter(el => !el.parentElement?.closest(site.turn));
      const last = replies.at(-1);
      if (!last) return null;
      const scope = site.turnContainer ? last.closest(site.turnContainer) || last : last;
      // Structural attributes only; no labels, IDs, URLs, or message text.
      return {tag:scope.tagName, classes:[...scope.classList].slice(0,8), rendered:isVisible(scope),
        copy_count:site.copy ? scope.querySelectorAll(site.copy).length : 0,
        final_count:site.final ? scope.querySelectorAll(site.final).length : 0,
        controls:[...scope.querySelectorAll('i,button,[role="button"],[data-testid]')].slice(-24).map(el => ({
          tag:el.tagName, testid:/^[\w-]{1,100}$/.test(el.getAttribute('data-testid') || '') ? el.getAttribute('data-testid') : null,
          classes:[...el.classList].slice(0,5)}))};
    }
    function inspectReplyLayout() {
      const last=[...doc.querySelectorAll(site.turn)].filter(el=>!el.parentElement?.closest(site.turn)).at(-1);
      if(!last) return [];
      const scope=site.turnContainer ? last.closest(site.turnContainer) || last : last;
      const selector=join(site.copy,site.final);
      return [scope,...(selector ? scope.querySelectorAll(selector) : [])].slice(0,12).map(el=>{
        const style=doc.defaultView.getComputedStyle(el);
        return {tag:el.tagName,classes:[...el.classList].slice(0,8),visible:isVisible(el),usable:usable(el),
          display:style.display,visibility:style.visibility,opacity:style.opacity,rects:el.getClientRects().length};
      });
    }
    function inspectReplyAncestors() {
      let node=[...doc.querySelectorAll(site.turn)].filter(el=>!el.parentElement?.closest(site.turn)).at(-1);
      const result=[];
      for(let depth=0; node && depth<6 && !['BODY','HTML'].includes(node.tagName); depth++,node=node.parentElement) {
        result.push({tag:node.tagName,classes:[...node.classList].slice(0,5),
          attributes:[...node.attributes].map(attr=>attr.name).slice(0,16)});
      }
      return result;
    }
    function inspectPageControls() {
      const known = /(?:conversation|message|answer|interact|operate|toolbar|copy|like|thumb|stop|send|shim)/i;
      return [...doc.querySelectorAll('[class],button,svg,[role="button"]')].filter(el => el.matches('button,svg,[role="button"]') || [...el.classList].some(cls => known.test(cls)))
        .slice(-120).map(el => ({tag:el.tagName, classes:[...el.classList].slice(0,6),
          parent_classes:[...(el.parentElement?.classList || [])].slice(0,6),
          path:el.tagName.toLowerCase() === 'svg' ? el.querySelector('path')?.getAttribute('d')?.slice(0,80) : undefined,
          testid:/^[\w-]{1,100}$/.test(el.getAttribute('data-testid') || '') ? el.getAttribute('data-testid') : null}));
    }
    function inspectComposerControls() {
      let scope = find(site.composer);
      const nodes = new Set();
      for (let level=0; scope && level<7 && !['BODY','HTML'].includes(scope.tagName); level++, scope=scope.parentElement) {
        for (const el of scope.querySelectorAll('button,svg,[role="button"],[class]')) {
          if (el.matches('button,svg,[role="button"]') || doc.defaultView.getComputedStyle(el).cursor === 'pointer') nodes.add(el);
        }
      }
      return [...nodes].slice(0,40).map(el => ({tag:el.tagName,classes:[...el.classList].slice(0,8),
        parent_classes:[...(el.parentElement?.classList || [])].slice(0,8),
        children:el.tagName.toLowerCase()==='svg' ? [...el.children].map(child=>child.tagName) : undefined,
        path:el.tagName.toLowerCase()==='svg' ? el.querySelector('path')?.getAttribute('d')?.slice(0,80) : undefined}));
    }
    return {snapshot, inspectBusyControls, inspectReplyControls, inspectReplyLayout, inspectReplyAncestors, inspectPageControls, inspectComposerControls, isStop:target => !!target.closest(site.stop),
      isSubmit:target => !!target.closest(join(site.send,site.retry)),
      isComposer:target => !!target.closest(site.composer)};
  }
  const api = {createAdapter};
  root.FlameDetector = api;
  if (typeof module !== 'undefined') module.exports = api;
})(globalThis);

/* Pure state machine: only an observed start followed by explicit final controls can finish. */
(function (root) {
  class FlameTurn {
    constructor(emit, uuid, now = () => Date.now(), options = {}) {
      this.emit = emit; this.uuid = uuid; this.now = now;
      this.sourceId = options.sourceId || 'chatgpt-web';
      this.isNewRoute = options.isNewRoute || (route => route === '/');
      this.isTemporaryRoute = options.isTemporaryRoute || (route => /^\/c\/local-chatgpt(?::|%3a)/i.test(route));
      this.canTransition = options.canTransition || ((from,to) => (from === '/' || this.isTemporaryRoute(from)) && /^\/c\//.test(to));
      this.pending = null; this.active = null; this.lastTransition = null;
    }
    arm(snapshot) {
      // A single send can produce keydown, click and submit notifications.
      // An extra notification during generation must not cancel that same turn.
      if (this.active && snapshot.busy) return;
      if (this.active) this.end('idle', 'cancelled', 'new_submission');
      this.pending = {route: snapshot.route, final: snapshot.final, turnKey:snapshot.turnKey,
        history:new Set([...(snapshot.turnKeys || []), ...(snapshot.turnKey ? [snapshot.turnKey] : [])]), at: this.now()};
    }
    send(state, reason) {
      const turn = this.active;
      if (!turn) return;
      turn.seq++;
      this.emit({schema_version: '1.0', event_id: this.uuid(), source_id: this.sourceId,
        task_id: turn.id, seq: turn.seq, state, occurred_at: new Date(this.now()).toISOString(),
        ttl_ms: 60000, ...(reason ? {reason} : {})});
    }
    end(state, reason, cause = reason) {
      this.lastTransition = {state, cause};
      this.send(state, reason); this.active = null; this.pending = null;
    }
    cancel(cause = 'stop') { if (this.active) this.end('idle', 'cancelled', cause); this.pending = null; }
    update(s) {
      const now = this.now();
      if (this.pending && now - this.pending.at > 15000) this.pending = null;
      if (this.pending && s.route !== this.pending.route) {
        // Only new-conversation URL commits can carry an unstarted submission.
        // History navigation must not adopt an unrelated generation.
        if (this.canTransition(this.pending.route, s.route)) this.pending.route = s.route;
        else {
          this.pending = null;
          this.lastTransition = {state:'idle', cause:'navigation'};
        }
      }
      if (!this.active && this.pending && s.busy && s.online) {
        this.active = {id: this.uuid(), seq: 0, route: s.route, final: this.pending.final,
          baselineTurn:this.pending.turnKey, observedIncomplete:false, observedUnsettled:false,
          history:this.pending.history,
          generationTurn:s.turnKey && !this.pending.history.has(s.turnKey) ? s.turnKey : null,
          creating: this.isNewRoute(this.pending.route) || this.isTemporaryRoute(this.pending.route),
          started: now, renewed: now, lastSeen: now, candidate: null};
        this.pending = null; this.send('working');
      }
      const a = this.active;
      if (!a) return;
      if (now - a.lastSeen > 120000) { this.end('idle', 'timeout'); return; }
      a.lastSeen = now;
      if (!s.online) { this.end('idle', 'cancelled', 'offline'); return; }
      if (s.route !== a.route) {
        // The current web UI creates a temporary local-chatgpt URL before its permanent URL.
        if (a.creating && this.canTransition(a.route, s.route)) {
          if (!s.busy && !(a.generationTurn && s.turnKey === a.generationTurn)) {
            // SPA navigation can briefly remove the stop control while creating
            // the conversation URL. Wait for continuity, without inferring success.
            if (a.routeCandidate?.route !== s.route) a.routeCandidate = {route:s.route, at:now};
            if (now - a.routeCandidate.at < 2000) return;
            this.end('idle', 'cancelled', 'navigation'); return;
          }
          a.route = s.route;
          a.routeCandidate = null;
          if (!this.isTemporaryRoute(s.route)) a.creating = false;
        }
        else { this.end('idle', 'cancelled', 'navigation'); return; }
      }
      if (s.error) { this.end('unknown', 'failed'); return; }
      if (now - a.started > 30 * 60 * 1000) { this.end('idle', 'timeout'); return; }
      // A provider may remove its initial loading marker while streaming the
      // same reply. Keep its lease while that verified reply has no final controls.
      if (now - a.renewed >= 30000 && (s.busy || (a.generationTurn && s.turnKey === a.generationTurn && s.ready && !s.final && !s.settled))) {
        a.renewed = now; this.send('working');
      }
      if (s.busy) {
        if (!a.generationTurn && s.turnKey && !a.history.has(s.turnKey)) a.generationTurn = s.turnKey;
        if (s.turnKey && s.turnKey === a.baselineTurn && !s.final) a.observedIncomplete = true;
        if (s.turnKey && s.turnKey === a.baselineTurn && !s.settled) a.observedUnsettled = true;
        a.candidate = null; a.unconfirmed = null;
        return;
      }
      if (!s.ready || (a.generationTurn && s.turnKey !== a.generationTurn)
          || (a.history.has(s.turnKey) && s.turnKey !== a.baselineTurn)
          || (s.turnKey && s.turnKey === a.baselineTurn && !a.observedIncomplete)) {
        a.candidate = null; a.unconfirmed = null; return;
      }
      // Allow actions to mount after the provider's explicit end marker. If they
      // never arrive, release the working state as unknown, never as success or
      // a claimed provider failure. A missing stop button alone cannot do this.
      if (!s.final && s.settled && s.turnKey && (s.turnKey !== a.baselineTurn || a.observedUnsettled)) {
        a.candidate = null;
        if (!a.unconfirmed || a.unconfirmed.key !== s.turnKey) a.unconfirmed = {key:s.turnKey, at:now};
        else if (now - a.unconfirmed.at >= 5000) this.end('unknown', undefined, 'completion_unconfirmed');
        return;
      }
      a.unconfirmed = null;
      // No silence/timer-based success: require new final controls and a live composer.
      if (!s.final || s.final === a.final) { a.candidate = null; return; }
      if (!a.candidate || a.candidate.key !== s.final) a.candidate = {key: s.final, at: now};
      else if (now - a.candidate.at >= 800) this.end('done', 'completed');
    }
  }
  root.FlameTurn = FlameTurn;
  if (typeof module !== 'undefined') module.exports = {FlameTurn};
})(globalThis);

/* Keep tracked replies rendering when hidden tabs pause animation frames or
 * throttle short rendering intervals. No message/input/network inspection. */
(() => {
  'use strict';
  const request = window.requestAnimationFrame.bind(window);
  const cancel = window.cancelAnimationFrame.bind(window);
  const frames = new Map(), intervals = new Map(), timeouts = new Map(), renderCallbacks = new Set();
  let renderResumes = 0, rendererResumed = false, activeSignal = false, lastRendererResume = -Infinity;
  const setRepeating = window.setInterval.bind(window);
  const clearRepeating = window.clearInterval.bind(window);
  const clearOneShot = window.clearTimeout.bind(window);
  const setOneShot = window.setTimeout.bind(window);
  let next = 0, enabledUntil = 0, timer, frameRuns = 0, intervalRuns = 0, timeoutRuns = 0, pulsing = false;
  let lastPulse = performance.now(), timerClock;
  const enabled = () => document.hidden && Date.now() < enabledUntil;
  // Zhipu explicitly pauses its response renderer on visibilitychange, even
  // when animation frames are available. Capture only that published renderer
  // callback, identified by static code markers; never inspect closure data.
  if (window.location?.hostname === 'chatglm.cn') {
    const add = document.addEventListener, remove = document.removeEventListener;
    document.addEventListener = function(kind, callback, options) {
      if (kind === 'visibilitychange' && typeof callback === 'function' && !options?.once) {
        const code = Function.prototype.toString.call(callback);
        if (code.includes('document.visibilityState') &&
            ((code.includes('answerListDisplay') && code.includes('answerListOutput')) ||
             (code.includes('pageHiden') && code.includes('showPrintData')))) renderCallbacks.add(callback);
      }
      return add.call(this, kind, callback, options);
    };
    document.removeEventListener = function(kind, callback, options) {
      if (kind === 'visibilitychange') renderCallbacks.delete(callback);
      return remove.call(this, kind, callback, options);
    };
  }
  function resumeRenderer() {
    if (!enabled() || !renderCallbacks.size || (rendererResumed && performance.now() - lastRendererResume < 5000)) return;
    const previous = Object.getOwnPropertyDescriptor(document, 'visibilityState');
    if (previous && !previous.configurable) return;
    try {
      // Only the known synchronous render callback sees this value. No global
      // visibility event is dispatched, and it is restored before microtasks.
      Object.defineProperty(document, 'visibilityState', {configurable:true, get:() => 'visible'});
      for (const callback of renderCallbacks) { callback.call(document); renderResumes++; }
      // Give the typewriter time to run its final steps before another flush.
      // Repeating this callback every pulse would continually rewind its index.
      rendererResumed = true; lastRendererResume = performance.now();
    } finally {
      if (previous) Object.defineProperty(document, 'visibilityState', previous);
      else delete document.visibilityState;
    }
  }
  function schedule() {
    if (enabled() && (frames.size || intervals.size || timeouts.size) && timer === undefined) timer = setTimeout(pulse, 1000);
  }
  function run(id, timestamp) {
    const frame = frames.get(id);
    if (!frame) return;
    frames.delete(id);
    if (enabled()) frameRuns++;
    try { frame.callback.call(window, timestamp); }
    catch (error) { setOneShot(() => { throw error; }, 0); }
  }
  async function pulse() {
    if (pulsing) return;
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
    if (!enabled()) return;
    pulsing = true;
    try {
      resumeRenderer();
      const started = performance.now();
      wakeShortTimers();
      const count = Math.min(300, Math.max(1, Math.floor((started - lastPulse) / (1000 / 60))));
      lastPulse = started;
      // A one-frame-per-second fallback also stalls frame-driven typewriters.
      // Advance a bounded burst of distinct frame batches. Microtasks between
      // batches allow the site's awaited frames and framework rendering to run.
      for (let step = 0; step < count && enabled(); step++) {
        const timestamp = performance.now();
        for (const id of [...frames.keys()]) {
          const frame = frames.get(id);
          if (!frame) continue;
          cancel(frame.native);
          run(id, timestamp);
        }
        await Promise.resolve();
        await Promise.resolve();
        if (!frames.size || performance.now() - started >= 50) break;
      }
      wakeShortTimers();
      for (const id of [...intervals.keys()]) {
        try { runInterval(id, performance.now()); }
        catch (error) { setOneShot(() => { throw error; }, 0); }
      }
    } finally {
      pulsing = false;
      schedule();
    }
  }
  function runTimeout(id) {
    const timeout = timeouts.get(id);
    if (!timeout) return;
    timeouts.delete(id);
    clearOneShot(id);
    const previous = timerClock;
    timerClock = enabled() ? timeout.due : undefined;
    if (enabled()) timeoutRuns++;
    try { timeout.callback.apply(window, timeout.args); }
    finally { timerClock = previous; }
  }
  function wakeShortTimers() {
    const started = performance.now();
    for (let count = 0; count < 250 && enabled() && performance.now() - started < 25; count++) {
      const due = [...timeouts].find(([,timeout]) => timeout.due <= performance.now());
      if (!due) break;
      try { runTimeout(due[0]); }
      catch (error) { setOneShot(() => { throw error; }, 0); }
    }
  }
  window.setTimeout = (callback, delay, ...args) => {
    if (typeof callback !== 'function' || (delay !== undefined &&
        (typeof delay !== 'number' || delay < 0 || delay > 100))) return setOneShot(callback, delay, ...args);
    const id = setOneShot(() => runTimeout(id), delay);
    timeouts.set(id, {callback, args, due:(enabled() && timerClock !== undefined ? timerClock : performance.now()) + (delay || 0)});
    schedule();
    return id;
  };
  function runInterval(id, timestamp) {
    const interval = intervals.get(id);
    if (!interval) return;
    if (!enabled()) {
      interval.last = timestamp;
      interval.callback.apply(window, interval.args);
      return;
    }
    // Only short rendering intervals catch up, at their original frequency.
    // Bound each batch and keep cancellation from within callbacks effective.
    const count = Math.min(250, Math.floor((timestamp - interval.last) / interval.delay));
    for (let i = 0; i < count && intervals.get(id) === interval && enabled(); i++) {
      interval.last += interval.delay;
      intervalRuns++;
      interval.callback.apply(window, interval.args);
    }
  }
  window.setInterval = (callback, delay, ...args) => {
    if (typeof callback !== 'function' || typeof delay !== 'number' || delay <= 0 || delay > 100)
      return setRepeating(callback, delay, ...args);
    const id = setRepeating(() => runInterval(id, performance.now()), delay);
    intervals.set(id, {callback, args, delay, last:performance.now()});
    schedule();
    return id;
  };
  window.clearInterval = id => { intervals.delete(id); timeouts.delete(id); clearRepeating(id); };
  // Browser timeout and interval handles share one pool and are interchangeable.
  window.clearTimeout = id => { intervals.delete(id); timeouts.delete(id); clearOneShot(id); };
  function resumeNative() {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
    lastPulse = performance.now();
    for (const interval of intervals.values()) interval.last = performance.now();
    // Pending native requests stay registered and resume normally on visibility.
  }
  window.requestAnimationFrame = callback => {
    if (typeof callback !== 'function') throw new TypeError('Animation frame callback must be a function');
    const id = ++next;
    frames.set(id, {callback, native:request(timestamp => run(id, timestamp))});
    schedule();
    return id;
  };
  window.cancelAnimationFrame = id => {
    const frame = frames.get(id);
    if (frame) { cancel(frame.native); frames.delete(id); }
  };
  document.addEventListener('aiflame:render-active', event => {
    if (event.detail !== true && event.detail !== false) return;
    if (event.detail && !activeSignal) rendererResumed = false;
    activeSignal = event.detail;
    enabledUntil = event.detail ? Date.now() + 45000 : 0;
    if (enabled()) schedule(); else resumeNative();
  });
  document.addEventListener('aiflame:render-pulse', pulse);
  document.addEventListener('aiflame:render-inspect', () => {
    document.dispatchEvent(new CustomEvent('aiflame:render-status', {detail:{enabled:enabled(),
      pending_frames:frames.size, short_intervals:intervals.size,
      frame_runs:frameRuns, interval_runs:intervalRuns, short_timeouts:timeouts.size, timeout_runs:timeoutRuns, render_callbacks:renderCallbacks.size, render_resumes:renderResumes}}));
  });
  document.addEventListener('visibilitychange', () => {
    rendererResumed = false;
    if (enabled()) schedule(); else resumeNative();
  });
})();

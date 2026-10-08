import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';

export const name = 'aiflame-deepseek-harness';
export const inject = ['agents', 'sessions'];

function readHelper(config) {
  if (config.helperArgv !== undefined) return config.helperArgv;
  if (config.helper_command !== undefined) return config.helper_command;
  try {
    return JSON.parse(readFileSync(new URL('./bridge.json', import.meta.url), 'utf8')).helperArgv;
  } catch {
    return [];
  }
}

// No shell is involved, and neither model text nor tool arguments cross this bridge.
export function sendLifecycle(argv, payload) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(argv[0], argv.slice(1), { stdio: ['pipe', 'ignore', 'ignore'] });
    } catch {
      resolve();
      return;
    }
    const timeout = setTimeout(() => child.kill('SIGKILL'), 3000);
    const finish = () => { clearTimeout(timeout); resolve(); };
    child.once('error', finish);
    child.once('close', finish);
    child.stdin.on('error', () => {});
    child.stdin.end(JSON.stringify(payload));
  });
}

export function createBridge(ctx, config = {}, dependencies = {}) {
  const argv = dependencies.helperArgv ?? readHelper(config);
  if (!Array.isArray(argv) || !argv.length || argv.some(arg => typeof arg !== 'string' || !arg)) return;
  const send = dependencies.send ?? sendLifecycle;
  const clock = dependencies.now ?? Date.now;
  const active = new Map();
  let pending = Promise.resolve();
  let disposed = false;
  const emit = (sessionId, turn, hook, reason) => {
    const payload = { hook_event_name: hook, session_id: String(sessionId), turn_id: String(turn) };
    if (reason !== undefined) payload.reason = reason;
    // Serialize subprocesses so a fast ending cannot overtake its start.
    pending = pending.then(() => send(argv, payload)).catch(() => {});
  };
  const rootSession = session => ctx.agents.roots().some(agent => agent.session === session);
  const activity = (sessionId, now = clock()) => {
    const task = active.get(sessionId);
    if (!task || now - task.lastActivity < 1000) return;
    task.lastActivity = now;
    emit(sessionId, task.turn, 'TurnActivity');
  };
  ctx.on('session/event', (session, event) => {
    if (disposed || !rootSession(session)) return;
    if (event.type === 'turn/start') {
      active.set(session.id, { turn: event.data.turn, lastActivity: clock() });
      emit(session.id, event.data.turn, 'TurnStart');
    } else if (event.type === 'turn/end') {
      const task = active.get(session.id);
      if (!task || task.turn !== event.data.turn) return;
      active.delete(session.id);
      const reason = event.data.reason.kind;
      if (reason !== 'interrupted' && reason !== 'forked') emit(session.id, event.data.turn, 'TurnEnd', reason);
    } else if (['step/start', 'tool/call', 'tool/result', 'assistant/message'].includes(event.type)) {
      activity(session.id);
    }
  });
  ctx.on('agent/assistant-stream', ({ agent, frame }) => {
    if (!disposed && frame.type === 'chunk') activity(agent.session.id);
  });
  ctx.on('session/disposed', session => active.delete(session.id));
  // Keep a long streaming request alive without needing its text or tool output.
  const heartbeat = (dependencies.setInterval ?? setInterval)(() => {
    if (disposed) return;
    const running = new Set(ctx.agents.roots()
      .filter(agent => agent.status === 'running')
      .map(agent => agent.session.id));
    for (const sessionId of active.keys()) {
      if (running.has(sessionId)) activity(sessionId);
    }
  }, 5000);
  heartbeat.unref();
  ctx.on('dispose', async () => {
    disposed = true;
    (dependencies.clearInterval ?? clearInterval)(heartbeat);
    active.clear();
    await pending;
  });
  return { drain: () => pending };
}

export function apply(ctx, config = {}) {
  createBridge(ctx, config);
}

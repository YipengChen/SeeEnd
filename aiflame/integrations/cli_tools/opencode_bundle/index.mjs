// SeeEnd OpenCode lifecycle bridge. Never read message parts, text or tool arguments.
import {spawn} from 'node:child_process';
import {readFileSync} from 'node:fs';

function sendLifecycle(argv, payload) {
  return new Promise(resolve => {
    let child;
    try { child = spawn(argv[0], argv.slice(1), {stdio:['pipe','ignore','ignore']}); }
    catch { resolve(); return; }
    const timer = setTimeout(() => child.kill('SIGKILL'), 3000);
    const finish = () => { clearTimeout(timer); resolve(); };
    child.once('error', finish); child.once('close', finish);
    child.stdin.on('error', () => {});
    child.stdin.end(JSON.stringify(payload));
  });
}

export default async function SeeEndPlugin({client}, dependencies = {}) {
  let argv = dependencies.helperArgv;
  if (!argv) {
    try { argv = JSON.parse(readFileSync(new URL('./bridge.json', import.meta.url), 'utf8')).helperArgv; }
    catch { return {}; }
  }
  if (!Array.isArray(argv) || !argv.length || argv.some(x => typeof x !== 'string' || !x)) return {};
  const send = dependencies.send || sendLifecycle;
  const active = new Map(), sessions = new Map(), queues = new Map();
  const serialize = (id, operation) => {
    const pending = (queues.get(id) || Promise.resolve()).then(operation).catch(() => {});
    queues.set(id, pending);
    pending.finally(() => { if (queues.get(id) === pending) queues.delete(id); });
    return pending;
  };
  const emit = (id, task, hook, reason) => send(argv, {hook_event_name:hook, session_id:id,
    turn_id:task.turn, ...(reason ? {reason} : {})});
  const finish = async (id, hook, reason) => {
    const task = active.get(id);
    if (!task) return;
    active.delete(id);
    await emit(id, task, hook, reason);
  };
  const activity = async id => {
    const task = active.get(id);
    if (task && !task.waiting.size) await emit(id, task, 'TurnActivity');
  };
  const rootSession = async id => {
    if (!sessions.has(id)) {
      try {
        const result = await Promise.race([client.session.get({path:{id}}), new Promise(resolve => { const timer = setTimeout(() => resolve(null), 500); timer.unref?.(); })]);
        if (!result?.data) return false;
        sessions.set(id, !result.data.parentID);
      } catch { return false; }
    }
    return sessions.get(id);
  };
  return {
    'chat.message': (input, output) => serialize(input.sessionID, async () => {
      const id = input.sessionID, turn = input.messageID || output?.message?.id;
      if (!id || !turn || !(await rootSession(id))) return;
      if (active.get(id)?.turn === turn) return;
      await finish(id, 'Interrupt', 'cancelled');
      const task = {turn, outcome:null, waiting:new Set()};
      active.set(id, task);
      await emit(id, task, 'TurnStart');
    }),
    'tool.execute.before': input => serialize(input.sessionID, () => activity(input.sessionID)),
    'tool.execute.after': input => serialize(input.sessionID, () => activity(input.sessionID)),
    event: ({event}) => {
      const p = event.properties || {}, info = p.info;
      const id = p.sessionID || info?.sessionID || info?.id;
      if (!id) return;
      return serialize(id, async () => {
        if (event.type === 'session.created' || event.type === 'session.updated') {
          sessions.set(id, !info.parentID); return;
        }
        const task = active.get(id);
        if (!task) return;
        if (event.type === 'message.updated' && info?.role === 'assistant' && info.parentID === task.turn) {
          if (info.error) await finish(id, info.error.name === 'MessageAbortedError' ? 'Interrupt' : 'Error', info.error.name === 'MessageAbortedError' ? 'cancelled' : 'failed');
          else if (info.time?.completed && info.finish !== 'tool-calls') task.outcome = info.finish === 'stop' ? 'completed' : ['length','content-filter','error'].includes(info.finish) ? 'failed' : 'unknown';
        } else if (event.type === 'session.idle' || (event.type === 'session.status' && p.status?.type === 'idle')) {
          // An idle signal has no turn ID. Require a matching finished assistant first.
          if (task.outcome) await finish(id, task.outcome === 'unknown' ? 'Unknown' : task.outcome === 'failed' ? 'Error' : 'TurnEnd', task.outcome === 'unknown' ? undefined : task.outcome);
        } else if (event.type === 'session.error') {
          await finish(id, p.error?.name === 'MessageAbortedError' ? 'Interrupt' : 'Error', p.error?.name === 'MessageAbortedError' ? 'cancelled' : 'failed');
        } else if (event.type === 'session.deleted') {
          await finish(id, 'Interrupt', 'cancelled'); sessions.delete(id);
        } else if (['permission.asked','permission.updated','question.asked'].includes(event.type)) {
          const key = p.id || p.requestID || p.permissionID || event.type;
          const wasWaiting = task.waiting.size > 0; task.waiting.add(key);
          if (!wasWaiting) await emit(id, task, 'NeedsInput');
        } else if (['permission.replied','question.replied','question.rejected'].includes(event.type)) {
          const key = p.id || p.requestID || p.permissionID;
          if (key) task.waiting.delete(key);
          else if (task.waiting.size === 1) task.waiting.clear();
          await activity(id);
        } else if (event.type === 'session.status' && ['busy','retry'].includes(p.status?.type)) {
          if (!task.waiting.size) await activity(id);
        }
      });
    },
  };
}

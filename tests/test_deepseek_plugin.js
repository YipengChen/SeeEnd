const assert = require('node:assert/strict');
const { mkdtempSync, cpSync, writeFileSync, readFileSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join, resolve } = require('node:path');
const { pathToFileURL } = require('node:url');

(async () => {
const { createBridge, sendLifecycle } = await import('../aiflame/integrations/cli_tools/deepseek_bundle/index.js');

function harness(options = {}) {
  const callbacks = new Map();
  const root = { id: 'root', header: { parentSession: 'fork-parent' } };
  const child = { id: 'child', header: { origin: 'subagent' } };
  const rootAgent = { session: root, status: 'running' };
  const sent = [];
  const ctx = {
    agents: { roots: () => [rootAgent] },
    on(event, fn) { callbacks.set(event, fn); },
  };
  const bridge = createBridge(ctx, {}, {
    helperArgv: [process.execPath, '--cli-hook', 'deepseek-harness'],
    send: async (argv, payload) => { sent.push({ argv, payload }); },
    ...options,
  });
  const event = (session, type, data) => callbacks.get('session/event')(session, { type, data });
  return { root, rootAgent, child, sent, bridge, event, callbacks };
}

const h = harness();
h.event(h.child, 'turn/start', { turn: 1 });
h.event(h.child, 'turn/end', { turn: 1, reason: { kind: 'completed' } });
h.event(h.root, 'turn/start', { turn: 2 });
h.event(h.root, 'assistant/message', { turn: 2, message: { content: 'SECRET CONTENT' } });
h.event(h.root, 'tool/call', { turn: 2, arguments: 'SECRET ARGUMENTS' });
h.event(h.root, 'turn/end', { turn: 2, reason: { kind: 'completed' } });
await h.bridge.drain();
assert.deepEqual(h.sent.map(x => x.payload), [
  { hook_event_name: 'TurnStart', session_id: 'root', turn_id: '2' },
  { hook_event_name: 'TurnEnd', session_id: 'root', turn_id: '2', reason: 'completed' },
]);
assert(!JSON.stringify(h.sent).includes('SECRET'));
assert(!JSON.stringify(h.sent).includes('fork-parent'));
for (const [index, reason] of ['error', 'aborted', 'blocked', 'max-tokens', 'interrupted', 'forked'].entries()) {
  h.event(h.root, 'turn/start', { turn: 10 + index });
  h.event(h.root, 'turn/end', { turn: 10 + index, reason: { kind: reason, error: 'SECRET ERROR' } });
}
await h.bridge.drain();
assert.deepEqual(h.sent.filter(x => x.payload.hook_event_name === 'TurnEnd').map(x => x.payload.reason),
  ['completed', 'error', 'aborted', 'blocked', 'max-tokens']);
assert.deepEqual(h.sent.filter(x => x.payload.hook_event_name === 'Interrupt').map(x => x.payload.turn_id), ['14', '15']);
await h.callbacks.get('dispose')();

let now = 10000;
const streaming = harness({ now: () => now });
streaming.event(streaming.root, 'turn/start', { turn: 3 });
now += 1001;
streaming.callbacks.get('agent/assistant-stream')({ agent: { session: streaming.root }, frame: { type: 'chunk', chunk: { text: 'SECRET' } } });
streaming.event(streaming.root, 'tool/call', { turn: 3, arguments: 'SECRET' });
streaming.event(streaming.root, 'turn/end', { turn: 99, reason: { kind: 'completed' } });
streaming.event(streaming.root, 'turn/end', { turn: 3, reason: { kind: 'completed' } });
streaming.event(streaming.root, 'turn/end', { turn: 3, reason: { kind: 'completed' } });
await streaming.bridge.drain();
assert.deepEqual(streaming.sent.map(x => x.payload.hook_event_name), ['TurnStart', 'TurnActivity', 'TurnEnd']);
assert(!JSON.stringify(streaming.sent).includes('SECRET'));
await streaming.callbacks.get('dispose')();

let heartbeat;
const live = harness({ now: () => now,
  setInterval: fn => { heartbeat = fn; return { unref() {} }; }, clearInterval() {} });
live.event(live.root, 'turn/start', { turn: 4 });
now += 5000;
heartbeat();
live.rootAgent.status = 'idle';
now += 5000;
heartbeat();
await live.bridge.drain();
assert.deepEqual(live.sent.map(x => x.payload.hook_event_name), ['TurnStart', 'TurnActivity']);
live.callbacks.get('session/disposed')(live.root);
live.rootAgent.status = 'running';
now += 5000;
heartbeat();
await live.bridge.drain();
assert.deepEqual(live.sent.map(x => x.payload.hook_event_name), ['TurnStart', 'TurnActivity', 'Interrupt']);
await live.callbacks.get('dispose')();

const replaced = harness();
replaced.event(replaced.root, 'turn/start', { turn: 1 });
replaced.event(replaced.root, 'turn/start', { turn: 1 });
replaced.event(replaced.root, 'turn/start', { turn: 2 });
replaced.event(replaced.root, 'turn/end', { turn: 1, reason: { kind: 'interrupted' } });
await replaced.callbacks.get('dispose')();
assert.deepEqual(replaced.sent.map(x => [x.payload.hook_event_name, x.payload.turn_id]),
  [['TurnStart', '1'], ['Interrupt', '1'], ['TurnStart', '2'], ['Interrupt', '2']]);

const failing = harness({ send: async () => { throw new Error('helper unavailable'); } });
failing.event(failing.root, 'turn/start', { turn: 1 });
failing.event(failing.root, 'turn/end', { turn: 1, reason: { kind: 'completed' } });
await failing.bridge.drain();
await failing.callbacks.get('dispose')();
await sendLifecycle(['/nonexistent/aiflame-helper'], { hook_event_name: 'TurnStart' });
const started = Date.now();
await sendLifecycle([process.execPath, '-e', "process.on('SIGTERM',()=>{});setInterval(()=>{},1000)"], {});
assert(Date.now() - started < 5000, 'timeout forcibly terminates an unresponsive helper');

// A profile-installed copy locates bridge.json beside itself, independent of cwd.
const temp = mkdtempSync(join(tmpdir(), 'aiflame-deepseek-'));
try {
  const bundle = join(temp, 'bundle');
  cpSync(resolve(__dirname, '../aiflame/integrations/cli_tools/deepseek_bundle'), bundle, { recursive: true });
  const output = join(temp, 'events.jsonl');
  const helper = join(temp, 'helper.cjs');
  writeFileSync(helper, "let data='';process.stdin.on('data',x=>data+=x);process.stdin.on('end',()=>require('node:fs').appendFileSync(process.argv[2],data+'\\n'));");
  writeFileSync(join(bundle, 'bridge.json'), JSON.stringify({ helperArgv: [process.execPath, helper, output] }));
  const installed = await import(pathToFileURL(join(bundle, 'index.js')));
  const callbacks = new Map();
  const session = { id: 'installed' };
  installed.apply({ agents: { roots: () => [{ session }] }, on: (name, fn) => callbacks.set(name, fn) });
  callbacks.get('session/event')(session, { type: 'turn/start', data: { turn: 0 } });
  callbacks.get('session/event')(session, { type: 'turn/end', data: { turn: 0, reason: { kind: 'completed' } } });
  await callbacks.get('dispose')();
  const events = readFileSync(output, 'utf8').trim().split('\n').map(JSON.parse);
  assert.deepEqual(events.map(x => x.hook_event_name), ['TurnStart', 'TurnEnd']);
  assert.equal(events[1].turn_id, '0');
} finally {
  rmSync(temp, { recursive: true, force: true });
}
console.log('DeepSeek plugin tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });

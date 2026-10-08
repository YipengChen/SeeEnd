const {test} = require('node:test');
const assert = require('node:assert/strict');
const {FlameTurn} = require('../aiflame/integrations/chatgpt_web/extension/state.js');
function fixture() {
  let now = 100000, id = 0;
  const events = [], state = new FlameTurn(e => events.push(e), () => String(++id), () => now);
  const view = patch => ({route: '/c/a', online: true, busy: false, ready: true, final: 'old', error: false, ...patch});
  return {state, events, view, advance: ms => now += ms,
    start() { state.arm(view()); state.update(view({busy: true})); }};
}
test('requires user submission; old history and already-running page do not trigger', () => {
  const f = fixture(); f.state.update(f.view({busy: true})); f.state.update(f.view({final: 'new'}));
  assert.equal(f.events.length, 0);
});
test('observed generation and new final controls complete exactly once', () => {
  const f = fixture(); f.start(); f.state.update(f.view({final: 'new'})); f.advance(801);
  f.state.update(f.view({final: 'new'})); f.state.update(f.view({final: 'new'}));
  assert.deepEqual(f.events.map(e => e.state), ['working', 'done']);
  assert.equal(f.events[0].task_id, f.events[1].task_id);
  assert.equal(f.events[1].seq, 2);
});
test('missing final controls or stalled text never infer success', () => {
  const f = fixture(); f.start(); f.advance(60000); f.state.update(f.view({final: null}));
  f.state.update(f.view()); assert.deepEqual(f.events.map(e => e.state), ['working']);
});
test('explicit stop blocks late final controls', () => {
  const f = fixture(); f.start(); f.state.cancel(); f.advance(1000); f.state.update(f.view({final: 'new'}));
  assert.deepEqual(f.events.map(e => e.state), ['working', 'idle']);
  assert.equal(f.events[1].reason, 'cancelled');
});
test('offline and conversation switch cancel instead of completing', () => {
  for (const patch of [{online: false, final: 'new'}, {route: '/c/b', final: 'new'}]) {
    const f = fixture(); f.start(); f.state.update(f.view(patch));
    assert.equal(f.events[1].reason, 'cancelled');
  }
});
test('new-chat URL transition remains the same task', () => {
  const f = fixture(); f.state.arm(f.view({route: '/'}));
  f.state.update(f.view({route: '/', busy: true}));
  f.state.update(f.view({route: '/c/new', busy: true}));
  assert.equal(f.events.length, 1); assert.ok(f.state.active);
});
test('temporary new-chat URL becomes permanent without cancellation', () => {
  const f = fixture(); f.state.arm(f.view({route: '/', final: null}));
  f.state.update(f.view({route: '/c/local-chatgpt%3Atest', busy: true}));
  f.state.update(f.view({route: '/c/permanent', busy: true}));
  f.state.update(f.view({route: '/c/permanent', final: 'new'})); f.advance(801);
  f.state.update(f.view({route: '/c/permanent', final: 'new'}));
  assert.deepEqual(f.events.map(e => e.state), ['working', 'done']);
  const g = fixture(); g.state.arm(g.view({route: '/'}));
  g.state.update(g.view({route: '/', busy: true}));
  g.state.update(g.view({route: '/c/local-chatgpt:test', busy: true}));
  g.state.update(g.view({route: '/c/permanent', busy: true}));
  g.state.update(g.view({route: '/c/other', busy: true}));
  assert.equal(g.events.at(-1).reason, 'cancelled');
});
test('an unbound reply requires busy controls to renew working', () => {
  const f = fixture(); f.start(); f.advance(30001); f.state.update(f.view({busy: true}));
  f.advance(30001); f.state.update(f.view({final: null}));
  assert.deepEqual(f.events.map(e => e.state), ['working','working']);
});
test('loading gaps never renew from missing, historical, or different replies', () => {
  for (const patch of [{turnKey:null}, {turnKey:'history'}, {turnKey:'different'}, {turnKey:'current',ready:false}, {turnKey:'current',final:'finished'}]) {
    const f=fixture();
    f.state.arm(f.view({turnKey:'history',turnKeys:['history']}));
    f.state.update(f.view({busy:true,turnKey:'current',turnKeys:['history','current'],final:null}));
    f.advance(30001); f.state.update(f.view({final:null,...patch}));
    assert.deepEqual(f.events.map(e=>e.state),['working']);
  }
});
test('expired submission does not adopt unrelated generation', () => {
  const f = fixture(); f.state.arm(f.view()); f.advance(15001); f.state.update(f.view({busy: true}));
  assert.equal(f.events.length, 0);
});
test('timeout and explicit errors are not success', () => {
  const f = fixture(); f.start(); f.advance(1800001); f.state.update(f.view({busy: true}));
  assert.equal(f.events.at(-1).reason, 'timeout');
  const g = fixture(); g.start(); g.state.update(g.view({error: true}));
  assert.equal(g.events.at(-1).state, 'error');
});
test('parallel tabs use independent tasks', () => {
  const a = fixture(), b = fixture(); a.start(); b.start(); a.state.cancel();
  assert.ok(b.state.active); assert.equal(b.events.length, 1);
});
test('sleep or discarded observation does not replay old completion', () => {
  const f = fixture(); f.start(); f.advance(120001); f.state.update(f.view({final: 'new'}));
  f.advance(1000); f.state.update(f.view({final: 'new'}));
  assert.deepEqual(f.events.map(e => e.state), ['working','idle']);
  assert.equal(f.events[1].reason, 'timeout');
});

test('duplicate submission notification during generation does not cancel the turn', () => {
  const f = fixture(); f.start();
  f.state.arm(f.view({busy:true}));
  f.state.update(f.view({final:'new'})); f.advance(801); f.state.update(f.view({final:'new'}));
  assert.deepEqual(f.events.map(e=>e.state),['working','done']);
});
test('new conversation navigation tolerates a stop-control repaint', () => {
  const f = fixture(); f.state.arm(f.view({route:'/',final:null}));
  f.state.update(f.view({route:'/',busy:true,final:null}));
  f.state.update(f.view({route:'/c/new',busy:false,final:null}));
  assert.ok(f.state.active);
  f.advance(100); f.state.update(f.view({route:'/c/new',busy:true,final:null}));
  f.state.update(f.view({route:'/c/new',final:'new'})); f.advance(801);
  f.state.update(f.view({route:'/c/new',final:'new'}));
  assert.deepEqual(f.events.map(e=>e.state),['working','done']);
});
test('new conversation can commit its URL just after the tracked response finishes', () => {
  const f = fixture(); f.state.arm(f.view({route:'/',final:null,turnKey:null}));
  f.state.update(f.view({route:'/',busy:true,final:null,turnKey:'reply'}));
  f.state.update(f.view({route:'/c/new',final:'new',turnKey:'reply'})); f.advance(801);
  f.state.update(f.view({route:'/c/new',final:'new',turnKey:'reply'}));
  assert.deepEqual(f.events.map(e=>e.state),['working','done']);
});
test('navigation without generation continuity still cancels and never infers success', () => {
  const f = fixture(); f.state.arm(f.view({route:'/',final:null,turnKey:null}));
  f.state.update(f.view({route:'/',busy:true,final:null,turnKey:null}));
  f.state.update(f.view({route:'/c/old',final:'history',turnKey:'history'})); f.advance(2001);
  f.state.update(f.view({route:'/c/old',final:'history',turnKey:'history'}));
  assert.deepEqual(f.events.map(e=>e.state),['working','idle']);
  assert.equal(f.state.lastTransition.cause,'navigation');
});
test('submission captured on a provisional URL still follows its permanent conversation', () => {
  const f = fixture();
  f.state.arm(f.view({route:'/c/local-chatgpt:test',final:null}));
  f.state.update(f.view({route:'/c/local-chatgpt:test',busy:true,final:null}));
  f.state.update(f.view({route:'/c/permanent',busy:true,final:null}));
  assert.deepEqual(f.events.map(e=>e.state),['working']); assert.ok(f.state.active);
});

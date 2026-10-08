const {test} = require('node:test');
const assert = require('node:assert/strict');

async function fixture() {
  const {default: plugin} = await import('../aiflame/integrations/cli_tools/opencode_bundle/index.mjs');
  const sent = [];
  const hooks = await plugin({client:{session:{get:async ({path}) => ({data:{id:path.id, ...(path.id === 'child' ? {parentID:'root'} : {})}})}}},
    {helperArgv:['helper'], send:async (_, payload) => { sent.push(payload); }});
  const start = (id='root', turn='turn') => hooks['chat.message']({sessionID:id,messageID:turn}, {message:{id:turn},parts:['PRIVATE']});
  const event = (type, properties) => hooks.event({event:{type,properties}});
  const complete = async (id='root', turn='turn') => {
    await event('message.updated', {info:{sessionID:id,role:'assistant',parentID:turn,time:{completed:10},finish:'stop'}});
    await event('session.idle', {sessionID:id});
  };
  return {hooks,sent,start,event,complete};
}

test('OpenCode start, permission, resume, completion and duplicate idle', async () => {
  const f = await fixture(); await f.start(); await f.start();
  await f.event('permission.asked', {sessionID:'root',title:'PRIVATE'});
  await f.event('permission.replied', {sessionID:'root'});
  await f.complete(); await f.complete();
  assert.deepEqual(f.sent.map(x=>x.hook_event_name), ['TurnStart','NeedsInput','TurnActivity','TurnEnd']);
  assert.ok(f.sent.every(x=>x.turn_id==='turn'));
  assert.ok(!JSON.stringify(f.sent).includes('PRIVATE'));
});

test('OpenCode ignores subagents and untracked or stale idle', async () => {
  const f = await fixture(); await f.start('child');
  await f.complete('missing'); assert.equal(f.sent.length,0);
  await f.start(); await f.event('session.idle',{sessionID:'root'});
  await f.complete('root','old-turn');
  assert.deepEqual(f.sent.map(x=>x.hook_event_name), ['TurnStart']);
  await f.complete(); assert.equal(f.sent.at(-1).reason,'completed');
});

test('OpenCode cancellation and failure cannot become completion', async () => {
  for (const name of ['MessageAbortedError','APIError']) {
    const f = await fixture(); await f.start();
    await f.event('session.error', {sessionID:'root',error:{name,message:'PRIVATE'}});
    await f.complete();
    assert.deepEqual(f.sent.map(x=>x.hook_event_name), ['TurnStart',name==='MessageAbortedError'?'Interrupt':'Error']);
  }
});

test('OpenCode independent sessions and new prompt replace unfinished task', async () => {
  const f = await fixture();
  await Promise.all([f.start('a','a1'),f.start('b','b1')]);
  await f.start('a','a2'); await f.complete('b','b1'); await f.complete('a','a2');
  assert.deepEqual(f.sent.filter(x=>x.session_id==='a').map(x=>x.hook_event_name), ['TurnStart','Interrupt','TurnStart','TurnEnd']);
  assert.deepEqual(f.sent.filter(x=>x.session_id==='b').map(x=>x.hook_event_name), ['TurnStart','TurnEnd']);
});

test('OpenCode finish reasons preserve failures and unknown', async () => {
  for (const reason of ['length','content-filter','error','unknown']) {
    const f = await fixture(); await f.start();
    await f.event('message.updated', {info:{sessionID:'root',role:'assistant',parentID:'turn',time:{completed:10},finish:reason}});
    await f.event('session.idle', {sessionID:'root'});
    assert.equal(f.sent.at(-1).hook_event_name, reason === 'unknown' ? 'Unknown' : 'Error');
    assert.notEqual(f.sent.at(-1).reason,'completed');
  }
});

test('OpenCode parallel permission requests remain waiting through other tool activity', async () => {
  const f = await fixture(); await f.start();
  await f.event('permission.asked', {sessionID:'root',id:'p1'});
  await f.event('permission.asked', {sessionID:'root',id:'p2'});
  await f.hooks['tool.execute.after']({sessionID:'root'});
  await f.event('permission.replied', {sessionID:'root',requestID:'p1'});
  assert.deepEqual(f.sent.map(x=>x.hook_event_name), ['TurnStart','NeedsInput']);
  await f.event('permission.replied', {sessionID:'root',requestID:'p2'});
  assert.equal(f.sent.at(-1).hook_event_name,'TurnActivity');
});

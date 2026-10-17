"""Exercise real plugin termination events through the Hub's source policy."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from aiflame.core.engine import Engine
from aiflame.integrations.cli_tools.lifecycle import from_hooks
from aiflame.integrations.registry import SOURCES


@unittest.skipUnless(shutil.which('node'), 'Node is required for the real plugin fixture')
class DeepseekLifecycleTests(unittest.TestCase):
    def test_abandoned_turns_end_without_completion_or_permanent_work(self):
        script = r'''
import {createBridge} from './aiflame/integrations/cli_tools/deepseek_bundle/index.js';
const cases = {};
for (const termination of ['interrupted', 'forked', 'session/disposed', 'dispose', 'replaced']) {
  const sent = [], callbacks = new Map(), session = {id: 'root'};
  let roots = [{session, status: 'running'}];
  const bridge = createBridge({agents: {roots: () => roots}, on: (k, v) => callbacks.set(k, v)}, {}, {
    helperArgv: ['helper'], send: async (_, payload) => sent.push(payload),
    setInterval: () => ({unref(){}}), clearInterval(){}});
  const event = (type, data) => callbacks.get('session/event')(session, {type, data});
  event('turn/start', {turn: 1});
  event('turn/start', {turn: 1});
  if (termination === 'session/disposed') {
    roots = [];
    callbacks.get('session/disposed')(session);
    roots = [{session, status: 'running'}];
  } else if (termination === 'dispose') await callbacks.get('dispose')();
  else if (termination !== 'replaced') event('turn/end', {turn: 1, reason: {kind: termination}});
  if (termination !== 'dispose') {
    event('turn/start', {turn: 2});
    event('turn/end', {turn: 1, reason: {kind: 'completed'}});
    event('turn/end', {turn: 2, reason: {kind: 'completed'}});
    await callbacks.get('dispose')();
  }
  await bridge.drain();
  cases[termination] = sent;
}
console.log(JSON.stringify(cases));
'''
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=root,
                                capture_output=True, text=True, check=True, timeout=10)
        for termination, payloads in json.loads(result.stdout).items():
            with self.subTest(termination=termination), tempfile.TemporaryDirectory() as directory:
                now = [100.0]
                engine = Engine(':memory:', clock=lambda: now[0], sources=SOURCES)
                try:
                    for payload in payloads:
                        for event in from_hooks('deepseek-harness', payload, directory):
                            engine.accept(event, 'deepseek-harness')
                    now[0] += 86400
                    snapshot = engine.snapshot()
                    states = [row['state'] for row in engine.db.execute('SELECT state FROM tasks ORDER BY rowid')]
                    self.assertEqual(states, ['cancelled'] if termination == 'dispose' else ['cancelled', 'done'])
                    self.assertEqual(snapshot['active_count'], 0)
                    self.assertEqual(snapshot['unfinished_count'], 0)
                    self.assertEqual([e['state'] for e in snapshot['events']].count('done'),
                                     0 if termination == 'dispose' else 1)
                finally:
                    engine.db.close()

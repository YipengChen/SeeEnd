"""Opt-in real DeepSeek requests through native CLI lifecycles, in a private lab.

Run: .venv/bin/python tests/verify_cli_live.py [--qoder-model MODEL_ID]
Reads ~/.config/ai-flame/deepseek-api-key; never prints or logs credentials.
"""
import json
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiflame.core.paths import load_config, write_private
from aiflame.core.process import command
from aiflame.hub.server import Hub
from aiflame.integrations.cli_tools.install import install
from aiflame.integrations.codex.registration import quote_command


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--qoder-model', help='Authenticated Qoder custom model ID to include')
    args = parser.parse_args()
    key = (Path.home() / '.config/ai-flame/deepseek-api-key').read_text().strip()
    root = Path(tempfile.mkdtemp(prefix='aiflame-cli-live-'))
    os.chmod(root, 0o700)
    os.environ.update({
        'AIFLAME_DATA_DIR': str(root / 'runtime'),
        'CLAUDE_CONFIG_DIR': str(root / 'claude'),
        'DSH_HOME': str(root / 'dsh'),
        'DEEPSEEK_API_KEY': key,
        'ANTHROPIC_AUTH_TOKEN': key,
        'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic',
        'ANTHROPIC_MODEL': 'deepseek-flash',
        'ANTHROPIC_DEFAULT_OPUS_MODEL': 'deepseek-flash',
        'ANTHROPIC_DEFAULT_SONNET_MODEL': 'deepseek-flash',
        'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'deepseek-flash',
        'CLAUDE_CODE_SUBAGENT_MODEL': 'deepseek-flash',
        'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
        'DSH_TELEMETRY_MODE': 'DISABLED',
    })
    workspace = root / 'workspace'
    workspace.mkdir()
    (workspace / 'fixture.txt').write_text('AI_FLAME_REAL_CLI_OK\n')
    hub = Hub(port=0, config=load_config())
    hub.start(hardware=False)
    enabled = dict(hub.engine.settings['source_enabled'])
    enabled.update({'claude-code': True, 'deepseek-harness': True})
    if args.qoder_model:
        enabled['qoder'] = True
    hub.update_settings({'source_enabled': enabled})
    reports = []
    try:
        print('Preparing native CLI wiring in isolated user directories', flush=True)
        path = install('claude-code')
        config = json.loads(path.read_text())
        for groups in config['hooks'].values():
            for group in groups:
                for handler in group['hooks']:
                    handler['command'] = quote_command(command('--cli-hook', 'claude-code', '--port', str(hub.port)))
        write_private(path, config)
        result = subprocess.run(['dsh', 'headless', '--dump-default-config'], cwd=workspace,
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError('DeepSeek profile initialization failed')
        bundle = install('deepseek-harness', profile='headless')
        bridge = {'helperArgv': command('--cli-hook', 'deepseek-harness', '--port', str(hub.port))}
        write_private(bundle / 'bridge.json', bridge)
        # file: installs can copy rather than symlink the bundle; set this private lab copy too.
        installed = root / 'dsh/profiles/headless/node_modules/aiflame-deepseek-harness'
        write_private(installed / 'bridge.json', bridge)
        prompt = 'Reply with exactly AI_FLAME_REAL_CLI_OK. Do not use tools.'
        invocations = {
            'claude-code': ['claude', '-p', '--model', 'deepseek-flash', '--tools', '',
                            '--max-turns', '2', '--output-format', 'json', '--no-session-persistence', prompt],
            'deepseek-harness': ['dsh', 'headless', '--json', prompt],
        }
        if args.qoder_model:
            qoder_settings = install('qoder', config_dir=root / 'qoder-hooks')
            config = json.loads(qoder_settings.read_text())
            for groups in config['hooks'].values():
                for group in groups:
                    for handler in group['hooks']:
                        handler['command'] = quote_command(command('--cli-hook', 'qoder', '--port', str(hub.port)))
            write_private(qoder_settings, config)
            invocations['qoder'] = ['qoder', '--print', '--model', args.qoder_model,
                '--tools', '', '--settings', str(qoder_settings),
                '--no-session-persistence', '--max-model-request-retries', '0', prompt]
        for source, argv in invocations.items():
            print('Running real DeepSeek session: ' + source, flush=True)
            result = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, timeout=180)
            time.sleep(0.5)
            with hub.lock:
                events = [dict(row) for row in hub.engine.db.execute(
                    'SELECT task,state,reason,status FROM events WHERE source=? ORDER BY created,rowid', (source,))]
            tasks = {event['task'] for event in events if event['state'] == 'working'}
            paired = any(event['state'] == 'done' and event['task'] in tasks for event in events)
            report = {'source': source, 'model': args.qoder_model if source == 'qoder' else 'deepseek-flash', 'exit_code': result.returncode,
                      'response_ok': 'AI_FLAME_REAL_CLI_OK' in result.stdout,
                      'lifecycle_paired': paired, 'events': events}
            if not paired or result.returncode:
                # Only known non-secret exception categories; do not expose CLI diagnostic bodies.
                report['failure_categories'] = [word for word in ('DUPLICATE', 'TypeError', 'Authentication',
                    'hook', 'API Error', 'plugin', 'ECONNREFUSED') if word in result.stderr + result.stdout]
            print(json.dumps(report, ensure_ascii=False), flush=True)
            reports.append(report)
        write_private(root / 'report.json', reports)
        print('Report directory: ' + str(root), flush=True)
        return 0 if all(r['exit_code'] == 0 and r['response_ok'] and r['lifecycle_paired'] for r in reports) else 1
    finally:
        hub.stop()


if __name__ == '__main__':
    raise SystemExit(main())

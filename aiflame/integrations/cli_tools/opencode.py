"""Install an owned OpenCode plugin without changing unrelated configuration."""
import hashlib
import json
from pathlib import Path

from aiflame.core.paths import write_private
from aiflame.core.process import command
from aiflame.integrations.codex.install import backup_file
from aiflame.integrations.codex.registration import owns_command, quote_command

MARKER = '// SeeEnd owned OpenCode plugin v1\n'
LEGACY_MARKER = '// AI Flame owned OpenCode plugin v1\n'
OWNER = 'seeend-opencode-v1'


def wrapper():
    return MARKER + 'export {default} from "../aiflame/index.mjs";\n'


def _digest(content):
    return hashlib.sha256(content).hexdigest()


def _files():
    return {
        'plugins/aiflame.js': wrapper().encode(),
        'aiflame/index.mjs': (Path(__file__).with_name('opencode_bundle') / 'index.mjs').read_bytes(),
        'aiflame/bridge.json': json.dumps({'helperArgv': command('--cli-hook', 'opencode')}, ensure_ascii=False, indent=2).encode(),
    }


def _ownership(directory, files):
    manifest = directory / 'aiflame/.seeend-owned.json'
    if manifest.is_symlink():
        raise ValueError('OpenCode 插件归属文件是符号链接，未修改')
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or data.get('owner') != OWNER
                or not isinstance(data.get('files'), dict)
                or set(data['files']) != set(files)
                or any(not isinstance(value, str) for value in data['files'].values())):
            raise ValueError('无法确认 OpenCode 插件归属，未修改')
        return manifest, data['files']
    # Recognize the previous installer only when every existing component is ours.
    path = directory / 'plugins/aiflame.js'
    owned = {}
    if path.exists() and path.read_text(encoding='utf-8') in {wrapper(), wrapper().replace(MARKER, LEGACY_MARKER)}:
        for relative in files:
            target = directory / relative
            if not target.exists():
                continue
            content = target.read_bytes()
            valid = relative == 'plugins/aiflame.js' or (relative == 'aiflame/index.mjs' and content == files[relative])
            if relative == 'aiflame/bridge.json':
                try:
                    value = json.loads(content)
                    argv = value.get('helperArgv') if isinstance(value, dict) else None
                    valid = (isinstance(argv, list) and bool(argv) and all(isinstance(item, str) for item in argv)
                             and owns_command(quote_command(argv), quote_command(command('--cli-hook', 'opencode')), ('--cli-hook', 'opencode')))
                except (ValueError, TypeError):
                    valid = False
            if valid:
                owned[relative] = _digest(content)
    return manifest, owned


def install_plugin(directory, *, uninstall=False):
    directory = Path(directory)
    path = directory / 'plugins/aiflame.js'
    files = _files()
    for relative in ('plugins', 'aiflame'):
        target = directory / relative
        if target.exists() and not target.is_dir():
            raise ValueError(f'OpenCode 插件目录被同名文件占用，未修改：{target}')
    for relative in ('plugins', 'aiflame', *files):
        if (directory / relative).is_symlink():
            raise ValueError('OpenCode 插件路径是符号链接，未修改')
    manifest, owned = _ownership(directory, files)
    if uninstall and not owned and any((directory / relative).exists() for relative in files):
        raise ValueError('未能确认 OpenCode 同名文件属于 SeeEnd，未修改')
    # Preflight the complete bundle before creating directories or backup files.
    if not uninstall:
        for relative in files:
            target = directory / relative
            if target.exists() and (not target.is_file() or owned.get(relative) != _digest(target.read_bytes())):
                raise ValueError(f'同名 OpenCode 文件不属于 SeeEnd 或已被修改，未修改：{target}')
        for relative, content in files.items():
            target = directory / relative
            if target.exists() and target.read_bytes() == content:
                continue
            backup_file(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative.endswith('.json'):
                write_private(target, json.loads(content))
            else:
                target.write_bytes(content)
        desired = {'owner': OWNER, 'files': {relative: _digest(content) for relative, content in files.items()}}
        if not manifest.exists() or json.loads(manifest.read_text(encoding='utf-8')) != desired:
            write_private(manifest, desired)
    else:
        preserved = False
        for relative in files:
            target = directory / relative
            if not target.exists():
                continue
            if target.is_file() and owned.get(relative) == _digest(target.read_bytes()):
                target.unlink()
            else:
                preserved = True
        if manifest.exists() and not preserved:
            manifest.unlink()
    return path


def plugin_ready(directory):
    directory = Path(directory)
    try:
        files = _files()
        if any((directory / relative).is_symlink() for relative in ('plugins', 'aiflame', *files)):
            return False
        _, owned = _ownership(directory, files)
        return all((directory / relative).read_bytes() == content and owned.get(relative) == _digest(content)
                   for relative, content in files.items())
    except (OSError, ValueError, TypeError):
        return False

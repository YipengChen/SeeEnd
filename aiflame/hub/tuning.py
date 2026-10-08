"""Ephemeral, acknowledged per-device flame tuning sessions."""
import uuid
from aiflame.core.events import EventError
from aiflame.devices.firmware.candle import TUNING_FIELDS, validate_tuning


def display(output):
    session = getattr(output, 'tuning_session', None)
    if not session:
        return None
    flame = output.fleet.engine
    if (flame.clock() >= session['expires'] or not output.device.get('online')
            or output.token != session['connection'] or flame.settings['mode'] != 'hardware'
            or flame.emergency):
        output.tuning_session = None
        return None
    return {'key': session['key'], 'state': 'done', 'duration_ms': 5000,
            'tuning': session['params']}


def status(hub):
    devices = []
    for key, output in hub.transport.outputs.items():
        if key not in hub.transport.records:
            continue
        current = display(output)
        devices.append({'id': key, 'name': output.record.get('name', key),
                        'online': bool(output.device.get('online')),
                        'supported': 'flame-tuning-v1' in output.device.get('capabilities', []),
                        'active': bool(current),
                        'confirmed': bool(current and output.last_display_key == current['key']),
                        'key': current['key'] if current else None,
                        'error': output.device.get('last_error')})
    return {'fields': TUNING_FIELDS, 'devices': devices}


def update(hub, body):
    if (not isinstance(body.get('device_id'), str)
            or body.get('action') not in ('apply', 'stop')
            or (body.get('session') is not None and not isinstance(body['session'], str))):
        raise EventError('调参请求格式错误')
    output = hub.transport.outputs.get(body.get('device_id'))
    if not output:
        raise EventError('请先在「我的设备」连接硬件', 409)
    action = body.get('action')
    session = getattr(output, 'tuning_session', None)
    if action == 'stop':
        if session and body.get('session') == session['id']:
            output.tuning_session = None
        return {'stopped': True}
    if action != 'apply':
        raise EventError('未知调参操作')
    if hub.engine.settings['mode'] != 'hardware' or not output.device.get('online'):
        raise EventError('请切换到硬件模式并连接设备', 409)
    if 'flame-tuning-v1' not in output.device.get('capabilities', []):
        raise EventError('请先通过 USB 更新灯光程序至 0.4.8 或更新版本', 409)
    if hub.engine.flame.emergency:
        raise EventError('请先在控制台恢复灯光', 409)
    try:
        params = validate_tuning(body.get('params'))
    except ValueError as error:
        raise EventError('参数无效：' + str(error))
    display(output)  # Expire stale sessions before checking ownership.
    session = getattr(output, 'tuning_session', None)
    if session and body.get('session') != session['id']:
        raise EventError('另一页面正在调试此设备，请先在该页面停止', 409)
    identifier = session['id'] if session else str(uuid.uuid4())
    key = 'tuning:' + str(uuid.uuid4())
    output.tuning_session = {'id': identifier, 'key': key, 'params': params,
                             'connection': output.token, 'expires': hub.engine.clock() + 6}
    return {'session': identifier, 'key': key}

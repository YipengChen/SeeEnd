# AI Flame: ESP32-S3 / warm monochrome IS31FL3731 model-specific candle matrix.
import sys, time, json, select, machine, neopixel, os, binascii, network, socket
from candle import Renderer, validate_tuning
from matrix import Matrix
from matrix_map import CALIBRATED, CHANNEL_MAP, DISABLED_CHANNELS
import matrix_map as board_map
WIDTH = getattr(board_map, "WIDTH", 9)
HEIGHT = getattr(board_map, "HEIGHT", 16)
MODEL = getattr(board_map, "MODEL", "ai-flame-v01")
from wifi_protocol import PORT, DISCOVERY_PORT, proof, equal
network.WLAN(network.AP_IF).active(False)
wlan = network.WLAN(network.STA_IF)
wlan.active(False)
try:
    with open('wifi.json') as f:
        wifi_config = json.load(f)
    with open('device_auth.json') as f:
        device_secret = json.load(f)['secret']
except (OSError, ValueError, KeyError):
    wifi_config = None
    device_secret = None
led = neopixel.NeoPixel(machine.Pin(48), 1)
led[0] = (0, 0, 0)
led.write()
DEVICE = binascii.hexlify(machine.unique_id()).decode()
SESSION = binascii.hexlify(os.urandom(12)).decode()
from version import FIRMWARE_VERSION as VERSION
OWNER = None
last_seq = -1
cache = {}
cache_order = []
state = 'idle'
brightness = 20
reduced = False
began = time.ticks_ms()
duration = 0
lease = 30000
contact = began
last_panel_retry = began
scan_id = None
scan_results = []
scan_started = began
renderer = Renderer(WIDTH, HEIGHT)
panel = None
hardware_error = None
animation_ms = 0
clock_tick = began
frame = bytearray()
frame_started = began
dropping = False
poll = select.poll()
poll.register(sys.stdin, select.POLLIN)


def output(value, target=None):
    if target:
        target.sendall((json.dumps(value) + '\n').encode())
    else:
        print(json.dumps(value))


def render_panel(display_state, display_brightness, display_reduced, fade=1):
    global panel, hardware_error
    try:
        if not CALIBRATED:
            raise ValueError('mapping-not-calibrated')
        if panel is None:
            bus = machine.I2C(0, sda=machine.Pin(21), scl=machine.Pin(18), freq=100000, timeout=100000)
            panel = Matrix(bus, CHANNEL_MAP, DISABLED_CHANNELS, width=WIDTH, height=HEIGHT)
            panel.initialize()
        panel.show(renderer.render(animation_ms, display_state, display_brightness, display_reduced, fade))
        hardware_error = None
    except (OSError, ValueError, RuntimeError) as error:
        if panel is not None:
            try:
                panel.shutdown()
            except OSError:
                pass
        panel = None
        hardware_error = 'mapping-not-calibrated' if not CALIBRATED else 'matrix-i2c'
        raise OSError(hardware_error)


def command(c, target=None):
    global OWNER, last_seq, state, brightness, reduced, began, duration, lease, contact
    global scan_id, scan_results, scan_started
    kind = c.get('type')
    now = time.ticks_ms()
    if kind == 'hello':
        owner = c.get('hub_id')
        if not isinstance(owner, str) or not 1 <= len(owner) <= 80:
            return
        if OWNER and OWNER != owner and time.ticks_diff(now, contact) < 30000:
            output({'type':'rejected','error':'busy'}, target)
            return
        OWNER = owner
        contact = now
        output({'type':'hello','device_id':DEVICE,'session_id':SESSION,
                'firmware_version':VERSION,'protocol_version':'1.0',
                'wifi_ssid':wifi_config.get('ssid') if wifi_config else None,
                'wifi_connected':bool(wifi_config and wlan.isconnected()),
                'capabilities':['led-matrix-%dx%d' % (WIDTH, HEIGHT),'mono-pwm','states-4','ack','lease','brightness-cap-25','hold-until-activity','wifi-scan','flame-tuning-v1'],
                'hardware_model':MODEL,'display_width':WIDTH,'display_height':HEIGHT,'display_ready':panel is not None,
                'hardware_error':hardware_error,'max_brightness':25,'seq':last_seq}, target)
        return
    if c.get('session_id') != SESSION or c.get('hub_id') != OWNER or not OWNER:
        output({'type':'rejected','error':'session'}, target)
        return
    if kind == 'wifi_scan':
        request_id = c.get('request_id')
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 80:
            return
        packet = {'type':'wifi_scan_result','request_id':request_id,'session_id':SESSION}
        offset = c.get('offset', 0)
        if type(offset) is not int or offset < 0:
            return
        contact = now
        try:
            if offset == 0:
                wlan.active(True)
                strongest = {}
                for row in wlan.scan():
                    try:
                        ssid = row[0].decode('utf-8')
                        if not ssid or not 1 <= row[2] <= 14:
                            continue
                        if ssid not in strongest or row[3] > strongest[ssid]:
                            strongest[ssid] = row[3]
                    except (UnicodeError, ValueError):
                        continue
                scan_results = [{'ssid':ssid,'signal':rssi} for ssid, rssi in strongest.items()]
                scan_results.sort(key=lambda item: -item['signal'])
                scan_id, scan_started = request_id, time.ticks_ms()
            if request_id != scan_id or time.ticks_diff(time.ticks_ms(), scan_started) > 20000 or offset > len(scan_results):
                raise OSError('expired-scan')
            # Pull one bounded page at a time, avoiding both oversized JSON and
            # flooding the nonblocking TCP socket on crowded wireless networks.
            end = offset + 6
            output(dict(packet, networks=scan_results[offset:end], done=end >= len(scan_results), next=end), target)
        except OSError:
            output(dict(packet, networks=[], done=True, error='scan-failed'), target)
        finally:
            contact = time.ticks_ms()
        return
    if kind == 'ping':
        contact = now
        output({'type':'pong','session_id':SESSION,'state':state,'seq':last_seq,
                'hardware_error':hardware_error,
                'wifi_ssid':wifi_config.get('ssid') if wifi_config else None,
                'wifi_connected':bool(wifi_config and wlan.isconnected())}, target)
        return
    cid = c.get('command_id')
    if not isinstance(cid, str) or not 1 <= len(cid) <= 80:
        return
    if cid in cache:
        output(cache[cid], target)
        return
    try:
        seq = c['seq']
        if type(seq) is not int or seq <= last_seq:
            raise ValueError('sequence')
        if c.get('type') != 'display' or c.get('state') not in ('idle','working','done','error'):
            raise ValueError('state')
        if c.get('effect') not in ('standard','reduced'):
            raise ValueError('effect')
        for key, low, high in (('brightness',0,100),('duration_ms',0,60000),('lease_ms',1000,30000)):
            if type(c.get(key)) is not int or not low <= c[key] <= high:
                raise ValueError(key)
        tuning = validate_tuning(c.get('tuning', {}))
        renderer.tuning = tuning
        # Acknowledge only after the complete first frame was written successfully.
        render_panel(c['state'], min(c['brightness'],25), c['effect'] == 'reduced')
        last_seq = seq
        state = c['state']
        brightness = min(c['brightness'],25)
        reduced = c['effect'] == 'reduced'
        duration = c['duration_ms']
        lease = c['lease_ms']
        began = contact = now
        ack = {'type':'ack','command_id':cid,'result':'applied','state':state,
               'brightness':brightness,'session_id':SESSION,'seq':seq}
    except OSError as error:
        ack = {'type':'ack','command_id':cid,'result':'error','error':str(error)}
    except (ValueError, KeyError) as error:
        ack = {'type':'ack','command_id':cid,'result':'rejected','error':str(error)}
    cache[cid] = ack
    cache_order.append(cid)
    if len(cache_order) > 64:
        del cache[cache_order.pop(0)]
    output(ack, target)


tcp_listener = None
udp_listener = None
client = None
net_frame = bytearray()
net_nonce = None
net_authenticated = False
last_wifi_attempt = time.ticks_add(began, -20000)


def close_client():
    global client, net_frame, net_authenticated
    if client:
        try:
            client.close()
        except OSError:
            pass
    client = None
    net_frame = bytearray()
    net_authenticated = False


def close_listeners():
    global tcp_listener, udp_listener
    close_client()
    for listener in (tcp_listener, udp_listener):
        if listener:
            listener.close()
    tcp_listener = None
    udp_listener = None


def wifi_tick(now):
    global last_wifi_attempt, tcp_listener, udp_listener
    if not wifi_config or not device_secret:
        return
    if not wlan.isconnected():
        if tcp_listener:
            close_listeners()
        if time.ticks_diff(now, last_wifi_attempt) >= 20000:
            last_wifi_attempt = now
            try:
                wlan.active(True)
                wlan.disconnect()
                wlan.connect(wifi_config['ssid'], wifi_config['password'])
            except (OSError, ValueError):
                pass
        return
    if tcp_listener:
        return
    try:
        tcp_listener = socket.socket()
        tcp_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        tcp_listener.bind(('0.0.0.0', PORT))
        tcp_listener.listen(1)
        tcp_listener.setblocking(False)
        udp_listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_listener.bind(('0.0.0.0', DISCOVERY_PORT))
        udp_listener.setblocking(False)
        print('FLAME_WIFI_READY ' + wlan.ifconfig()[0])
    except OSError:
        close_listeners()


def network_tick():
    global client, net_nonce, net_authenticated, net_frame
    if not tcp_listener:
        return
    try:
        ready, _, _ = select.select([tcp_listener, udp_listener] + ([client] if client else []), [], [], 0)
        for sock in ready:
            if sock is tcp_listener:
                incoming, _ = tcp_listener.accept()
                close_client()
                client = incoming
                client.setblocking(False)
                net_nonce = binascii.hexlify(os.urandom(12)).decode()
                output({'type':'challenge','nonce':net_nonce,'device_id':DEVICE,
                        'protocol_version':'1.0'}, client)
            elif sock is udp_listener:
                data, address = udp_listener.recvfrom(256)
                try:
                    query = json.loads(data)
                    if query.get('type') == 'discover' and query.get('device_id') == DEVICE:
                        udp_listener.sendto(json.dumps({'device_id':DEVICE,'port':PORT}).encode(), address)
                except (ValueError, AttributeError):
                    pass
            elif sock is client:
                block = client.recv(512)
                if not block:
                    close_client()
                    continue
                net_frame.extend(block)
                if len(net_frame) > 4096:
                    close_client()
                    continue
                while client and b'\n' in net_frame:
                    end = net_frame.index(10)
                    data = bytes(net_frame[:end])
                    net_frame = bytearray(net_frame[end + 1:])
                    try:
                        message = json.loads(data)
                        if not isinstance(message, dict):
                            continue
                        if not net_authenticated:
                            hub_id = message.get('hub_id')
                            expected = proof(device_secret, net_nonce, hub_id)
                            if message.get('type') != 'hello' or not equal(message.get('proof'), expected):
                                close_client()
                                break
                            net_authenticated = True
                        command(message, client)
                    except (ValueError, TypeError, UnicodeError, OSError):
                        close_client()
                        break
    except OSError:
        close_client()


try:
    render_panel('idle', 0, False)
except OSError:
    pass
output({'type':'boot','device_id':DEVICE,'firmware_version':VERSION,'hardware_error':hardware_error})
last_tick = time.ticks_ms()
try:
    while True:
        now = time.ticks_ms()
        animation_ms += max(0, time.ticks_diff(now, clock_tick))
        clock_tick = now
        wifi_tick(now)
        network_tick()
        # Bound serial work so animations and fail-safe remain responsive.
        for _ in range(256):
            if not poll.poll(0):
                break
            ch = sys.stdin.read(1)
            if not ch:
                break
            if ch == '\n':
                if frame and not dropping:
                    try:
                        value = json.loads(frame.decode())
                        if isinstance(value, dict):
                            command(value)
                    except (ValueError, UnicodeError):
                        output({'type':'rejected','error':'invalid_json'})
                frame = bytearray()
                dropping = False
            elif not dropping:
                if not frame:
                    frame_started = now
                frame.extend(ch.encode())
                if len(frame) > 4096:
                    frame = bytearray()
                    dropping = True
        if frame and time.ticks_diff(now, frame_started) > 2000:
            frame = bytearray()
            dropping = True
        now = time.ticks_ms()
        elapsed = time.ticks_diff(now, began)
        silent = time.ticks_diff(now, contact)
        if (state in ('done','error') and duration > 0 and elapsed >= duration) or silent >= lease:
            state = 'idle'
        if time.ticks_diff(now, last_tick) >= 40:
            last_tick = now
            fade = 1
            if silent > lease - 2000:
                fade = max(0, (lease - silent) / 2000)
            if panel is not None or (CALIBRATED and time.ticks_diff(now, last_panel_retry) >= 1000):
                last_panel_retry = now
                try:
                    render_panel(state, brightness, reduced, fade)
                except OSError:
                    output({'type':'hardware_error','error':hardware_error})
        time.sleep_ms(2)
finally:
    if panel is not None:
        try:
            panel.shutdown()
        except OSError:
            pass
    led[0] = (0,0,0)
    led.write()

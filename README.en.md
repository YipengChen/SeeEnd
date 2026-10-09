# SeeEnd

[中文](README.md) | **English**

The software is named **SeeEnd**. Supported lamps are the Yeelight YLTD003 Screen Light Bar Pro and YLDP005 W3 color bulb.

Turn AI task status into a candle flame.

SeeEnd is a local AI task notification app. It receives status events from command-line tools, web conversations, and local APIs, then uses an on-screen candle, desktop notifications, or physical lamps to signal work in progress, requests for input, completion, and errors. **No hardware is required**: the first launch defaults to virtual mode.

Current version: **0.8.4 desktop preview** · Python **3.11+** · **Proprietary license**

## Features

- **Unified task status**: track task lifecycles by source, distinguishing real tasks, simulated diagnostics, cancellations, and timeouts.
- **Multiple notification channels**: on-screen candle, menu bar / system tray status, desktop notifications, and Yeelight lamps over the LAN. Desktop notifications work independently of light output.
- **Adjustable alerts**: set each lamp’s color and brightness independently for each task state; configure alert duration and quiet hours globally; export activity to CSV.
- **Local processing**: the Hub listens only on `127.0.0.1:8765` and authenticates requests with credentials. Integrations collect task status metadata, without saving conversation content. No model API key is required by SeeEnd.
- **Chinese and English**: choose **中文** or **English** under **Alert preferences → Language**. The selection is saved locally and applied immediately, including the tray and notifications. The local web dashboard also provides a language selector.

## Integrations and platform scope

| Integration | Current implementation | Verification limits |
| --- | --- | --- |
| Command line | Codex, Claude Code, Qoder CLI, DeepSeek Harness, Gemini CLI, OpenCode | Hooks or local plugins; restart the target session after connecting and verify each tool separately |
| Browser extension | ChatGPT, DeepSeek, Qianwen, Doubao, Yuanbao, Kimi, Zhipu, Claude, Gemini | Experimental; page structure changes may affect detection. Automated tests do not replace acceptance with real accounts |
| macOS desktop clients | Doubao, Qianwen, Yuanbao, ordinary ChatGPT conversations | Experimental and disabled by default; requires system permissions and depends on client version and control structure |
| Local HTTP API | Report task events from your scripts and tools | Uses separate source credentials; see the architecture documentation |

macOS has records of testing on real machines. Windows and Linux share the implementation and have native build workflows, but still require testing on real machines. Desktop client detection currently supports only macOS. Signing, installers, and release acceptance are not complete on all platforms.

## Run from source

Requires Python 3.11 or newer; CI uses 3.11 / 3.13. Enter the repository directory.

```sh
python -m venv .venv
```

Activate the environment:

```sh
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Install and start the desktop app:

```sh
python -m pip install -e ".[desktop]"
python -m aiflame
```

1. Keep the default **Virtual mode**. You do not need to buy or connect a lamp.
2. Connect the sources you use under **App integrations**. Web integration also requires loading the Chrome extension and connecting the local bridge.
3. Submit a new task in the corresponding AI app and observe the candle and activity records. Simulated diagnostics only verify the event path.
4. For desktop notifications, enable them under **Alert preferences** and grant system permission.
5. Choose **中文 / English** under **Alert preferences → Language** to switch the interface.

To run only the headless Hub, install the base dependencies and use this entry point. The desktop app already includes the Hub; a separate instance is usually unnecessary.

```sh
python -m pip install -e .
python -m aiflame --hub
```

After installation, `seeend` and `seeend-hub` launch the desktop app and Hub respectively. The legacy commands `ai-flame` and `ai-flame-hub` remain as compatibility aliases. The Python module name `aiflame`, original user data directory, `AIFLAME_DATA_DIR` environment variable, and internal integration identifiers remain compatible. Existing pairing, preferences, and task history are retained.

Detailed setup steps are in the [user guide (Chinese)](docs/quick-start.md) and [browser extension documentation (Chinese)](aiflame/integrations/chatgpt_web/extension/README.md). On Windows, browser bridging requires the Bridge executable supplied with the packaged app.

## Incremental setup for coding tools

Find a tool in **App integrations**, expand **Advanced settings**, confirm the actual files and directories, and click **Connect**. All six tools provide automatic configuration guidance, before-and-after steps, and copyable incremental snippets or plugin commands. See the [user guide (Chinese)](docs/quick-start.md#编程工具配置文件与增量接入).

Integration checks distinguish specific configuration errors, disabled sources, configuration that is ready but unverified, tasks in progress, and receipt of both start and completion for the same real task. Failures identify the file or event and the next step. Simulated tests and historical installation results do not replace a new real verification.

## Optional hardware

Supports the RGB backlight of the **Yeelight YLTD003 Screen Light Bar Pro** and the **Yeelight YLDP005 W3 color bulb** through the original LAN protocol. Set up Wi-Fi and enable LAN control in the Yeelight App, then select a model in **My devices** and discover it or enter its IP. Multiple lamps can connect simultaneously, with independent connection details, colors, and per-state brightness. Browsing devices keeps connections active; virtual mode pauses all physical connections.

The complete AI Flame 0.1 / 0.2 implementation is preserved on [`archive/ai-flame-2026-10-09`](https://github.com/YipengChen/SeeEnd/tree/archive/ai-flame-2026-10-09). The main branch no longer supports these boards, firmware maintenance, or flame tuning. Upgrading ignores old matrix connection records while retaining Yeelight devices, task history, and preferences.

## Development and tests

JavaScript tests require Node.js (build CI uses 22) and npm. Running the app itself does not require Node.js.

```sh
python -m unittest discover -s tests -v
npm ci --ignore-scripts
npm test
```

For headless environments, set `QT_QPA_PLATFORM=offscreen`. Python integration tests create loopback ports and local IPC; the environment must permit those operations. Some Qt tests are skipped if desktop dependencies are not installed.

`tests/verify_*_live.py` and `tests/verify_live.py` are manual acceptance tools, separate from the automated tests above. Check their arguments and target environment before running them; some invoke real AI tools or devices.

## Build the desktop app

Build on the target operating system:

```sh
python -m pip install -e ".[desktop,build]"
python packaging/build.py
python packaging/archive.py
```

Output defaults to `dist/`; the macOS app is `dist/SeeEnd.app`. To use a separate output directory:

```sh
python packaging/build.py --dist-dir dist/staging
python packaging/archive.py --dist-dir dist/staging
```

GitHub Actions runs tests for pushes and PRs. The desktop build workflow supports manual runs and `v*` tag triggers. A successful cross-platform build does not imply acceptance on real machines.

The brand icon source is `aiflame/assets/icon.svg`. After installing desktop dependencies and Pillow, run `python packaging/render_icon.py` to regenerate PNG, Windows ICO, and macOS ICNS assets, then rebuild the app. The menu bar flame indicates light status and is separate from the brand icon.

## Project structure

All directories below are inside this repository; documentation lives in `docs/`.

```text
aiflame/
  core/           Events, task state machine, storage, user data paths, translations
  hub/            Local HTTP service and orchestration
  integrations/   CLI, web, desktop client, and local API integrations
  outputs/        Light and desktop notification delivery
  desktop/        Native PySide6 desktop interface
  devices/        Yeelight discovery, connection, and configuration
  transports/     Yeelight LAN communication
  assets/         UI assets, translation catalog, and web dashboard
tests/            Automated tests and manual acceptance tools
packaging/        PyInstaller build and archiving
setup.py          Source distribution entry point
docs/             User guide, architecture, historical validation, product materials
```

- [Architecture and platform boundaries (Chinese)](docs/architecture.md)
- [Historical validation records (Chinese)](docs/validation.md): results for specific versions and devices; they do not imply that every current environment is verified.
- [Contributing (Chinese)](CONTRIBUTING.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## License

Starting with 0.8.4, original project code uses the [SeeEnd proprietary software license](LICENSE). The source repository remains private; previously granted rights are not revoked. Third-party dependencies and brand assets retain their respective licenses and ownership. See [third-party notices](THIRD_PARTY_NOTICES.md).

### Yeelight YLTD003 (Screen Light Bar Pro)

Select **Yeelight YLTD003** in **My devices**. First use the Yeelight App to connect the lamp to 2.4 GHz Wi-Fi and enable **LAN control**. The computer and lamp must be on the same LAN. In hardware mode, discover and select the lamp, or enter its IPv4 address manually. After connecting, use **Test this device** to check the backlight. This model retains its original firmware and uses the Yeelight App for network setup; USB and firmware flashing are unnecessary.

This implementation controls only the rear RGB ambient light. The front white light keeps its existing settings. The default colors are listed below; each state's color and brightness can be adjusted independently:

| AI status | Backlight |
| --- | --- |
| Working | Steady blue `#4A90E2`, default brightness 20% |
| Completed | Steady green `#52C788`, default brightness 50%, for the alert duration; optionally held until user activity |
| Error / Waiting for input | Steady orange `#FF9F43`, default brightness 50%, for the alert duration |
| Idle, paused, quiet hours, current state's brightness at 0% | Backlight off |

Under **My devices → Selected device**, set the color and 0–100% brightness independently for Working, Completed, and Error / Waiting for input. These settings affect only the selected device. At 100%, the app sends the lamp's protocol brightness parameter `100` directly, without additional scaling. Setting a state to 0% disables only that state's light alerts; other states retain their own settings. Both Yeelight models hold a steady color at the selected brightness and do not use breathing, pulses, or a **Reduce motion** setting. Each lamp has independent connection, disconnection, test, and receipt records, and can receive task status alongside other Yeelight lamps. Reconnecting does not replay old completion alerts.

Implementation references: [Yeelight official LAN protocol](https://www.yeelight.com/download/Yeelight_Inter-Operation_Spec.pdf), [official Chroma Connector capability detection](https://github.com/Yeelight/Yeelight-Chroma-Connector/blob/main/ChromaBroadcastSampleApplication/ChromaBroadcastSampleApplicationDlg.cpp), and [official product brochure](https://en.yeelight.com/wp-content/uploads/sites/4/2023/08/Yeelight-Fun.pdf). Discovery responses identify compatible lamps through `lamp15`, physical `id`, and the `support` method list. Discovery uses SSDP-style UDP at `239.255.255.250:1982` (manual IPs are queried directly first); control uses CRLF-terminated JSON over TCP `55443`. Automatic reconnection discovers the physical ID again to avoid controlling another lamp after an IP change. A LAN search takes about 4 seconds and sends three queries, 1 second apart, collecting delayed replies on the same UDP socket and deduplicating by physical ID. Targeted IP discovery returns as soon as a valid reply arrives.

The native finite color flow uses `bg_set_scene`, falling back to `bg_set_power` + `bg_start_cf` if the firmware does not declare the former. No front-light commands are sent. The official `action=2` turns off the backlight when a flow ends. Working and held alerts use native flows of at most 30 seconds, renewed every 20 seconds. After an app crash or network interruption, the last successfully submitted flow ends automatically. Commands are separated by at least 1.1 seconds, below the official per-connection quota of 60 commands per minute. The device-wide quota is 144 commands per minute, shared with other software. This version does not perform real-time per-frame UDP synchronization.

Simulated discovery responses, TCP receipts, light behavior, and UI regressions have been tested. Testing on a physical YLTD003 is still pending. If both discovery and manual connection fail, check LAN control availability, router isolation, VPNs, and reachability of UDP 1982 / TCP 55443. A manual IP still requires a discovery response to confirm physical identity and capabilities.

### Yeelight YLDP005 (W3 color bulb)

Select **Yeelight YLDP005** in **My devices**. As with YLTD003, connect it to 2.4 GHz Wi-Fi and enable **LAN control** in the Yeelight App, then discover it or enter its IP manually. Independent tests, disconnection, automatic reconnection, and synchronization with multiple devices are supported. The lamps retain their original firmware.

YLDP005 uses the entire bulb to show task status, with the same default colors and brightness as above: steady blue while working (20%), steady green on completion (50%), and steady orange for errors / input requests (50%). It turns off when idle, paused, during quiet hours, or when the current state's brightness is 0%. Each state's color and 0–100% brightness are set independently; 100% is sent directly without additional scaling. Alert duration and hold-until-activity settings are supported. While connected, SeeEnd uses the bulb for task alerts.

The [official Yeelight product specifications](https://store.yeelight.com/sv/products/yeelight-smart-bulb) identify YLDP005 as the W3 color bulb; the [official Home Assistant device table](https://www.home-assistant.io/integrations/yeelight/) maps its LAN model to `colorb`. It uses the same SSDP discovery and TCP 55443 JSON protocol, but the bulb channel uses `set_scene`, `set_power`, `start_cf`, and `power` / `bright` / `rgb` properties. YLTD003 backlight control continues to use `bg_*` commands. Discovery filters by the selected model and advertised capabilities to avoid connecting the wrong light channel.

Native flow renewal, rate limiting, automatic shutoff, and disconnection handling follow the YLTD003 implementation above. Simulated protocol and UI regression tests have passed. Discovery and TCP status queries have also been verified on a physical W3 reporting `colorb` and discovery firmware version `7`. This unit sometimes gives no reply when the first query is sent immediately after TCP connection; extending the reply timeout alone does not help. The client now waits 0.5 seconds after each connection before sending commands, and five consecutive fresh connections returned valid status replies. This interruptible wait covers both adding a device and automatic reconnection. Physical flow display, renewal, and shutoff still need separate verification.

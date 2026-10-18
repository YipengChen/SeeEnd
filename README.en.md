# SeeEnd

[中文](README.md) | **English**

Turn AI task status into a candle flame.

SeeEnd is a local AI task notification app. It receives lifecycle events from coding tools, web conversations, and local APIs, then signals work, requests for input, and completion through a desktop flame, system notifications, sounds, WeChat, or physical devices. No hardware or model API key for SeeEnd is required.

Source baseline: **App 0.8.69** · browser extension **0.2.14** · Python **3.11+** · [GPL-3.0-only](LICENSE). Source versions and local builds do not imply public availability. See [repositories and releases](docs/REPOSITORIES.md) and [extension submission status](docs/chrome-web-store/APPLICATION_STATUS.md).

## Product and support scope

- Five independently controlled notification channels can run together. Only the desktop flame starts on after first launch or reset. Sounds default to off and follow system volume; system notifications need OS permission; WeChat needs account linking and a prepared connection; devices need a separate connection.
- Return to a task from Overview, activity details, or its macOS system notification. Double-click the overview or desktop flame to return to the currently indicated task. See [task return](docs/quick-start.md#返回对应任务) for supported terminals, permissions, and limits.
- Save each device's state colors, brightness, white-light settings, or power rules independently. Export activity as CSV. The interface supports Chinese and English.
- The Hub defaults to `127.0.0.1:8765` with credential authentication. It stores lifecycle metadata without saving prompts or answers. Enabled WeChat alerts send source, status, and time to Tencent's service.

| Integration | Implementation |
| --- | --- |
| Coding tools | Codex, Claude Code, Qoder CLI, DeepSeek Harness, Gemini CLI, OpenCode; installed and maintained through [SeeEnd Skill](https://github.com/YipengChen/SeeEnd/tree/main/skill/seeend) |
| Browser extension | ChatGPT, DeepSeek, Qianwen, Doubao, Tencent Yuanbao, Kimi, Zhipu, Claude, Gemini; experimental and subject to website changes |
| Local HTTP API | Authenticated lifecycle reporting from your own scripts and tools |

| Optional device | Alert capability |
| --- | --- |
| Yeelight YLTD003 Screen Light Bar Pro | Color backlight or front white light |
| Yeelight YLDP005 W3 color bulb | Color or white light |
| Yeelight YLFWD-0021 Beam light bars | Both bars, color or RGB-simulated white |
| Yeelight Cube Lite | Pixel status board, comet, snake, text status; full-screen color and white modes |
| Mijia Smart Plug 3 (ZNCZ301KK), 4 (ZNCZ401KK) | Switch the connected lamp's power |

Devices use their original LAN protocols. Setup, default values, and connection limits are documented in the [user guide](docs/quick-start.md#连接设备). Legacy AI Flame boards and firmware are outside the current product; their implementation is in the [archive branch](https://github.com/YipengChen/SeeEnd/tree/archive/ai-flame-2026-10-09).

macOS has historical real-machine evidence. Windows and Linux share the implementation and native build workflows, but require separate acceptance. Automated tests, device acknowledgements, and successful submissions do not prove a visible notification or physical light. See [validation](docs/validation.md).

## Launch and connect

For a packaged app, put it in a stable location, then open App integrations. Copy the Skill installation instruction for coding tools, or follow Install browser extension for websites. Submit a new task and confirm its start and completion. Detailed instructions are in the [user guide](docs/quick-start.md).

Run from source:

```sh
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[desktop]"
python -m aiflame
```

For a headless Hub, install the base package with `python -m pip install -e .` and run `python -m aiflame --hub`. The desktop app already includes a Hub.

Installed commands `seeend` / `seeend-hub` launch the desktop app / Hub. `ai-flame` / `ai-flame-hub` remain compatibility aliases. The `aiflame` package name, existing data directory, `AIFLAME_DATA_DIR`, and internal integration identifiers preserve existing configuration.

## Develop, test, and build

Python CI uses 3.11 / 3.13. Browser tests also require Node.js (22 in build CI) and npm; the running app does not need Node.js.

```sh
python -m unittest discover -s tests -v
npm ci --ignore-scripts
npm test
python -m unittest discover -s skill/tests -v
python tools/check_workspace.py
```

For a headless display, set `QT_QPA_PLATFORM=offscreen`. Some tests require loopback sockets, IPC, desktop dependencies, or a frozen Helper artifact. `tests/verify_*_live.py` scripts are manual acceptance tools outside routine regression; check their targets and arguments before running them.

Build on the target operating system, then verify and archive in order:

```sh
python -m pip install -e ".[desktop,build]"
python packaging/build.py --download-sources
python packaging/verify_frozen.py
python packaging/archive.py
```

The default output is `dist/SeeEnd.app` plus the platform distribution. Use `--dist-dir dist/staging` consistently on all three commands for a separate output directory. The first build downloads and verifies third-party materials; omit `--download-sources` for subsequent offline builds once those materials are ready. A local C compiler is required. Core modules are compiled with Cython without Python fallback. For core changes, run `python packaging/verify_native.py` first or build with `--verify-native`.

`SeeEnd-build.json` records versions, source identity, Git state, and third-party material identity. Rebuild after source changes; verification and archiving reject mismatched artifacts. The app ZIP (`.tar.gz` on Linux) is the installation download. Third-party source ZIPs are separate and optional for app users. Signing and release acceptance must be checked against the actual artifact.

`python packaging/extension_release.py` generates versioned development packages, a store-submission ZIP, and checksums. Generated packages do not imply submission or publication. The brand icon source is `aiflame/assets/icon.svg`; regenerate derivatives with `python packaging/render_icon.py`, then rebuild.

## Repository and CI releases

Application sources live at this repository root. `skill/seeend/` is independently installable; `website/` contains policy pages and historical release records. `packaging/` and `.github/` build and verify distributions. Personal configuration, raw acceptance evidence and research media stay outside the repository.

Manually run `Build desktop and draft release` in Actions with `target_os=all` to build macOS arm64/Intel, Windows x64 and Linux x64. Pushing a matching `v<app_version>` tag creates a draft Release only after every platform, extension and source job succeeds. Assets include the exact application source, standalone Skill, matching third-party sources, build receipts and SHA256SUMS. The maintainer reviews and publishes the draft manually; repository visibility also stays private until manually changed. Local binary uploads are unnecessary. See [release workflow](docs/REPOSITORIES.md).

## Documentation and license

- [User guide](docs/quick-start.md): channels, integrations, task return, and devices
- [Current product baseline](docs/product/CURRENT_PRODUCT.md): implemented scope and compatibility
- [Architecture](docs/architecture.md): modules, data, and protocol
- [Validation](docs/validation.md): checks, evidence levels, and historical records
- [Repositories and releases](docs/REPOSITORIES.md) · [Contributing](CONTRIBUTING.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md) · [Library replacement](LIBRARY_REPLACEMENT.md)

The current original implementation is licensed under [GNU GPL v3.0](LICENSE) (GPL-3.0-only). Earlier releases retain their release-time terms and previously granted rights. Third-party dependencies and brand assets retain their respective licenses and ownership.

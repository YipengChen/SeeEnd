# Develop and build

[Home](../README.en.md) · [中文](development.md) · [Contributing](../CONTRIBUTING.md)

## Run from source

Requires Python 3.11+. The desktop app includes a local Hub.

```sh
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[desktop]"
python -m aiflame
```

For a headless Hub, install the base package with `python -m pip install -e .` and run `python -m aiflame --hub`.

Installed commands `seeend` / `seeend-hub` launch the desktop app / Hub. `ai-flame` / `ai-flame-hub` remain compatibility aliases. The `aiflame` package name, existing data directory, `AIFLAME_DATA_DIR`, and internal integration identifiers preserve existing configuration.

## Test

Python CI uses 3.11 / 3.13. Browser tests also need Node.js (22 in CI) and npm. The running app does not need Node.js.

```sh
python -m unittest discover -s tests -v
npm ci --ignore-scripts
npm test
python -m unittest discover -s skill/tests -v
python -m unittest discover -s tools -p 'test_*.py' -v
python .github/scripts/test_release_assets.py
python tools/check_workspace.py
```

For a headless display, set `QT_QPA_PLATFORM=offscreen`. Some tests require loopback sockets, IPC, desktop dependencies, or frozen Helper artifacts. `tests/verify_*_live.py` scripts are manual acceptance tools; check their targets and arguments before running them.

## Build locally

Run these commands in order on the target operating system. A local C compiler is required. Core modules compile with Cython without a Python fallback.

```sh
python -m pip install -e ".[desktop,build]"
python packaging/build.py --download-sources
python packaging/verify_frozen.py
python packaging/archive.py
```

Default app outputs: `dist/SeeEnd.app` on macOS and `dist/SeeEnd/SeeEnd.exe` on Windows. Archiving creates the platform distribution (`.tar.gz` on Linux). Use `--dist-dir dist/staging` consistently on all three commands for an isolated output directory. The first build downloads and verifies third-party materials; omit `--download-sources` for later offline rebuilds once prepared.

`SeeEnd-build.json` records versions, source identity, Git state, and third-party material identity. Rebuild after source changes; verification and archiving reject mismatched artifacts. For core changes, run `python packaging/verify_native.py` first or build with `--verify-native`.

Regular distributions build on GitHub and become a draft Release after all platforms succeed. Downloads contain three app installers, a developer materials ZIP, and checksums. See [repository and release workflow](REPOSITORIES.md), and [validation](validation.md) for signing, notarization, and physical-device acceptance.

## Extensions and media

`python packaging/extension_release.py` generates development packages, a store-submission ZIP, and checksums. Generated packages do not imply submission or publication.

The app icon source is `aiflame/assets/icon.svg`; regenerate derivatives with `python packaging/render_icon.py`, then rebuild.

README animations are exported from the current local preview widgets. See [light-effect media](images/light-effects/README.md) for sources and regeneration.

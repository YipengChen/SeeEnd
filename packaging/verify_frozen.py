"""Exercise the built application's native CLI helper on the target OS."""

import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import zipfile

from native import NATIVE_MODULES, native_artifacts
from release import validate_build
from verify_native import probe_socket_restrictions


def verify_native_payload(helper, modules):
    protected = set(NATIVE_MODULES)
    leaked = protected.intersection(modules)
    if leaked:
        raise RuntimeError("Native core bytecode was bundled in PYZ: " + str(sorted(leaked)))
    contents = helper.parent.parent if sys.platform == "darwin" else helper.parent / "_internal"
    libraries = contents / "Frameworks" if sys.platform == "darwin" else contents
    artifacts = native_artifacts(libraries)
    stems = {module.replace(".", "/") for module in protected}

    def check_source(name):
        name = name.replace("\\", "/")
        path = Path(name)
        if path.suffix not in {".py", ".pyc", ".pyo", ".pyx", ".c", ".cpp", ".h", ".pdb"}:
            return
        parts = list(path.parts)
        if "aiflame" not in parts:
            return
        parts = parts[parts.index("aiflame"):]
        if "__pycache__" in parts:
            parts.remove("__pycache__")
        parts[-1] = parts[-1].split(".", 1)[0]
        if "/".join(parts) in stems:
            raise RuntimeError("Native core source or debug material was bundled: " + name)

    for path in contents.rglob("*"):
        if path.is_file():
            check_source(str(path.relative_to(contents)))
            if path.name == "base_library.zip":
                with zipfile.ZipFile(path) as archive:
                    for name in archive.namelist():
                        check_source(name)
    print(f"{len(artifacts)} native core libraries present; no protected Python bytecode or source bundled.")


def verify_helper_smoke(helper, root):
    """Execute silent helper paths with ignored/private-invalid input, without HTTP."""
    sys.path.insert(0, str(root))
    from aiflame.integrations.cli_tools.providers import CLI_SOURCES
    from aiflame.integrations.chatgpt_web.host import extension_origins

    private = b"SEEEND_PRIVATE_PAYLOAD_MUST_NOT_APPEAR"
    payloads = (
        ("empty", b""),
        ("malformed", b'{"prompt":"' + private),
        ("ignored", json.dumps({"prompt":private.decode(), "tool_input":private.decode()}).encode()),
    )
    checks = 0
    with tempfile.TemporaryDirectory(prefix="seeend-frozen-smoke-") as work:
        runtime = Path(work) / "unused-runtime"
        environment = {**os.environ, "AIFLAME_DATA_DIR": str(runtime), "QT_QPA_PLATFORM": "offscreen"}

        def check(label, executable, arguments, payload, expected_code, expected_stdout=b"", framed=False):
            nonlocal checks
            result = subprocess.run([str(executable), *arguments], input=payload,
                                    capture_output=True, timeout=15, env=environment)
            if private in result.stdout or private in result.stderr:
                raise RuntimeError(label + ": private payload appeared in helper output")
            if result.returncode != expected_code or result.stderr:
                raise RuntimeError(f"{label}: unexpected exit/stderr (exit={result.returncode})")
            if framed:
                output = result.stdout
                if len(output) < 4 or len(output) != 4 + struct.unpack("=I", output[:4])[0]:
                    raise RuntimeError(label + ": invalid native-message response frame")
                if json.loads(output[4:]) != {"ok":False, "error":"invalid_message"}:
                    raise RuntimeError(label + ": unexpected native-message response")
            elif result.stdout.replace(b"\r\n", b"\n") != expected_stdout:
                raise RuntimeError(label + ": unexpected stdout")
            if runtime.exists():
                raise RuntimeError(label + ": ignored input created runtime data")
            checks += 1
            print("PASS frozen helper:", label, flush=True)

        for source in CLI_SOURCES:
            for kind, payload in payloads:
                check(source + "/" + kind, helper, ["--cli-hook", source], payload, 0,
                      b"{}\n" if source == "gemini-cli" else b"")
        for kind, payload in payloads:
            check("codex/" + kind, helper, ["--codex-hook"], payload, 0)

        bridge = helper.parent / "SeeEnd Bridge.exe" if sys.platform == "win32" else helper
        if not bridge.is_file():
            raise FileNotFoundError(bridge)
        origins = extension_origins(Path(root) / "aiflame/integrations/chatgpt_web")
        for index, origin in enumerate(origins):
            arguments = [origin] if sys.platform == "win32" else ["--browser-host", origin]
            check(f"browser/origin-{index}/empty", bridge, arguments, b"", 0)
            for kind, payload in payloads[1:]:
                frame = struct.pack("=I", len(payload)) + payload
                check(f"browser/origin-{index}/" + kind, bridge, arguments, frame, 1, framed=True)
        unknown = "chrome-extension://" + "a" * 32 + "/"
        arguments = [unknown] if sys.platform == "win32" else ["--browser-host", unknown]
        check("browser/unknown-origin", bridge, arguments, private, 1)
    print(f"Frozen helper smoke: {len(CLI_SOURCES) + 1} hook paths (CLI + Codex), "
          f"{len(origins)} allowed browser origins, {checks} subprocess checks passed.", flush=True)
    return checks


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Verify the frozen SeeEnd build")
    parser.add_argument("--dist-dir", type=Path, default=root / "dist")
    dist = parser.parse_args().dist_dir.resolve()
    identity = validate_build(root, dist)
    print("Verified build identity:", identity["app_version"], identity["source_sha256"])
    if sys.platform == "darwin":
        helper = dist / "SeeEnd.app/Contents/MacOS/SeeEnd"
    elif sys.platform == "win32":
        helper = dist / "SeeEnd/SeeEnd Helper.exe"
    else:
        helper = dist / "SeeEnd/SeeEnd"
    if not helper.is_file():
        raise FileNotFoundError(helper)
    # Inspect the frozen module archive for retired hardware dependencies.
    from PyInstaller.archive.readers import CArchiveReader
    app_archive = CArchiveReader(str(helper))
    pyz = next(name for name in app_archive.toc if name.endswith(".pyz"))
    modules = app_archive.open_embedded_archive(pyz).toc
    if "aiflame.integrations.codex.return_location" not in modules:
        raise RuntimeError("Codex on-demand return resolver was not bundled")
    verify_native_payload(helper, modules)
    retired = ("esptool", "mpremote", "serial", "aiflame.devices.firmware",
               "aiflame.transports.usb", "aiflame.transports.wifi", "aiflame.hub.tuning",
               "aiflame.integrations.desktop_clients")
    if any(name == prefix or name.startswith(prefix + ".") for name in modules for prefix in retired):
        raise RuntimeError("Retired modules were bundled")
    if sys.platform == "darwin" and (helper.parent.parent / "Resources/third_party/esptool").exists():
        raise RuntimeError("Removed esptool executable was bundled")
    resources = helper.parent.parent / "Resources" if sys.platform == "darwin" else helper.parent / "_internal"
    assets = resources / "aiflame/assets"
    if (assets / "web").exists():
        raise RuntimeError("Removed web console assets were bundled")
    for filename in ("brands/codex.png", "devices/yeelight-yldp005.svg", "devices/yeelight-ylfwd0021.svg", "devices/mijia-zncz301kk.svg", "locales/en.json"):
        if not (assets / filename).is_file():
            raise RuntimeError("Missing shared desktop resource: " + filename)
    print("Web console is absent; shared desktop icons and translations are bundled.")
    print("Desktop archive contains no AI Flame hardware modules or tools.")
    verify_helper_smoke(helper, root)
    restricted = probe_socket_restrictions()
    if restricted.get("tcp"):
        print("SKIP frozen HTTP lifecycle: " + restricted["tcp"] +
              "; real helper transport/delivery remains unverified in this environment.", flush=True)
        return 0
    environment = {**os.environ, "AIFLAME_TEST_FROZEN_HELPER": str(helper)}
    result = subprocess.call(
        [sys.executable, "-m", "unittest",
         "test_cli_http.CliHttpTests.test_frozen_helpers_deliver_real_lifecycle",
         "test_task_return.ReturnHttpTests.test_codex_helper_metadata_reaches_authenticated_hub_and_explicit_return",
         "test_task_return.ReturnHttpTests.test_actual_native_helper_delivers_return_commands_and_receipts", "-v"],
        cwd=root / "tests",
        env={**environment, "PYTHONPATH": str(root)},
    )
    if result == 0:
        print("PASS frozen HTTP lifecycle: real helper events and screen delivery verified.", flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())

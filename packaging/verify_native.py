"""Run the regression suite against native core libraries, with no Python fallback."""

import argparse
import ast
import errno
import inspect
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

from native import NATIVE_MODULES, build_native, native_artifacts


def probe_socket_restrictions():
    """Report permission-denied capabilities; unexpected socket errors fail."""
    restricted = {}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            probe.listen(1)
    except OSError as error:
        if error.errno not in (errno.EPERM, errno.EACCES):
            raise
        restricted["tcp"] = f"loopback TCP bind denied: {error}"
    if os.name != "nt" and hasattr(socket, "AF_UNIX"):
        try:
            with tempfile.TemporaryDirectory(prefix="seeend-socket-", dir="/tmp") as work:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                    probe.bind(str(Path(work) / "probe"))
                    probe.listen(1)
        except OSError as error:
            if error.errno not in (errno.EPERM, errno.EACCES):
                raise
            restricted["unix"] = f"local Unix socket bind denied: {error}"
    if restricted and os.environ.get("CI", "").lower() not in ("", "0", "false"):
        raise RuntimeError("CI requires socket integration checks: " + "; ".join(restricted.values()))
    for reason in restricted.values():
        print("Environment restriction:", reason, flush=True)
    return restricted


def test_starts_hub(case):
    """Recognize actual Hub assignments and start calls in setup/test source."""
    functions = [getattr(case, case._testMethodName)]
    for cls in type(case).__mro__:
        for name in ("setUp", "setUpClass"):
            if name in cls.__dict__:
                functions.append(getattr(cls, name))
    trees = []
    for function in functions:
        try:
            source = inspect.getsource(function)
            # Dedenting also changes multiline strings, whose contents can start
            # at column zero. Parse methods in a suite without changing bytes.
            trees.append(ast.parse("if True:\n" + source if source[:1].isspace() else source))
        except (OSError, TypeError):
            continue

    def target(node):
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return target(node.value) + "." + node.attr
        return ""

    hubs = set()
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Call):
                if target(node.value.func).split(".")[-1] == "Hub":
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    hubs.update(target(item) for item in targets)
    return any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == "start" and target(node.func.value) in hubs
               and any(keyword.arg == "hardware" for keyword in node.keywords)
               for tree in trees for node in ast.walk(tree))


def skip_restricted_tests(suite, restricted):
    """Skip only identified integration cases before their setup runs."""
    skipped = []
    ipc = {
        "test_concurrent_start_has_one_primary_and_acknowledged_activation",
        "test_dead_owner_and_stale_socket_are_recovered",
        "test_live_unresponsive_owner_is_not_replaced",
        "test_secondary_requires_acknowledgment",
        "test_close_is_idempotent_and_allows_restart",
    }
    for case in suite:
        if isinstance(case, unittest.TestSuite):
            skipped.extend(skip_restricted_tests(case, restricted))
            continue
        method = getattr(case, case._testMethodName)
        if getattr(type(case), "__unittest_skip__", False) or getattr(method, "__unittest_skip__", False):
            continue
        reason = None
        if restricted.get("tcp") and test_starts_hub(case):
            reason = restricted["tcp"]
        elif (restricted.get("unix") and type(case).__module__ == "test_desktop_instance"
              and type(case).__name__ == "DesktopInstanceTests" and case._testMethodName in ipc):
            reason = restricted["unix"]
        if reason:
            setattr(case, case._testMethodName, unittest.skip(reason)(method))
            skipped.append(case.id())
            print(f"SKIP {case.id()}: {reason}", flush=True)
    return skipped


def verify_native(root, directory, work, *, regression=True):
    stage = Path(work) / "application"
    stage.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "*.so", "*.pyd")
    for name in ("aiflame", "tests"):
        shutil.copytree(root / name, stage / name, ignore=ignore)
    shutil.copy2(root / "app.py", stage / "app.py")
    for module, library in native_artifacts(directory).items():
        source = stage.joinpath(*module.split(".")).with_suffix(".py")
        source.unlink()
        shutil.copy2(library, source.parent / library.name)
    # Preload every protected module, asserting its loader and lack of Python
    # code. Discovery and child processes both run from this isolated checkout.
    support = "\n".join(inspect.getsource(function) for function in
                        (probe_socket_restrictions, test_starts_hub, skip_restricted_tests))
    script = """
import ast, errno, inspect, os, socket, tempfile, textwrap
import importlib, importlib.machinery, sys, unittest
from pathlib import Path
""" + support + """
for name in sys.argv[2:]:
    module = importlib.import_module(name)
    if not isinstance(module.__loader__, importlib.machinery.ExtensionFileLoader):
        raise RuntimeError('Python fallback loaded: ' + name)
    if module.__loader__.get_code(name) is not None:
        raise RuntimeError('Python bytecode available: ' + name)
    if not Path(module.__file__).resolve().is_relative_to(Path.cwd()):
        raise RuntimeError('Module loaded outside isolated checkout: ' + name)
print(f'{len(sys.argv[2:])} native core imports passed without Python fallback.', flush=True)
if sys.argv[1] == 'regression':
    restricted = probe_socket_restrictions()
    suite = unittest.defaultTestLoader.discover('tests')
    environment_skips = skip_restricted_tests(suite, restricted)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    passed = result.testsRun - len(result.skipped) - len(result.failures) - len(result.errors)
    print(f'Native regression: {passed} passed, {len(result.skipped)} skipped '
          f'({len(environment_skips)} for environment restrictions), '
          f'{len(result.failures)} failures, {len(result.errors)} errors.', flush=True)
    raise SystemExit(not result.wasSuccessful())
"""
    environment = {**os.environ, "PYTHONPATH": str(stage), "QT_QPA_PLATFORM": "offscreen",
                   "PYTHONDONTWRITEBYTECODE": "1", "AIFLAME_DATA_DIR": str(Path(work) / "runtime-tests")}
    environment.pop("AIFLAME_TEST_FROZEN_HELPER", None)
    mode = "regression" if regression else "imports"
    subprocess.run([sys.executable, "-c", script, mode, *NATIVE_MODULES],
                   cwd=stage, env=environment, check=True)
    print(f"Native core {mode} passed without Python fallback.", flush=True)


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="seeend-native-tests-") as work:
        directory = args.native_dir or build_native(root, Path(work) / "native")
        verify_native(root, directory, work)


if __name__ == "__main__":
    main()

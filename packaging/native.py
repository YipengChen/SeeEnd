"""Compile release-only native modules without changing the source checkout."""

from contextlib import chdir
import importlib.machinery
import os
from pathlib import Path
import shutil
import subprocess
import sys


NATIVE_MODULES = (
    "aiflame.core.activity",
    "aiflame.core.delivery",
    "aiflame.core.engine",
    "aiflame.core.events",
    "aiflame.core.i18n",
    "aiflame.core.management_auth",
    "aiflame.core.paths",
    "aiflame.core.process",
    "aiflame.core.task_origins",
    "aiflame.core.verification",
    "aiflame.outputs.desktop",
    "aiflame.outputs.sound",
    "aiflame.outputs.wechat",
    "aiflame.outputs.flame",
    "aiflame.outputs.router",
    "aiflame.hub.reset",
    "aiflame.hub.server",
    "aiflame.hub.task_returns",
    "aiflame.devices.fleet",
    "aiflame.devices.light_settings",
    "aiflame.devices.cube_effects",
    "aiflame.devices.models",
    "aiflame.devices.registry",
    "aiflame.devices.service",
    "aiflame.devices.yeelight",
    "aiflame.devices.mijia",
    "aiflame.devices.mijia_cloud",
    "aiflame.devices.mijia_crypto",
    "aiflame.devices.token_store",
    "aiflame.integrations.wechat.api",
    "aiflame.integrations.wechat.credentials",
    "aiflame.integrations.wechat.service",
    "aiflame.transports.fleet",
    "aiflame.transports.manager",
    "aiflame.transports.yeelight",
    "aiflame.transports.mijia",
)


def native_artifacts(directory):
    """Require one current-interpreter extension for every protected module."""
    artifacts = {}
    for module in NATIVE_MODULES:
        stem = Path(directory).joinpath(*module.split("."))
        candidates = [Path(str(stem) + suffix)
                      for suffix in importlib.machinery.EXTENSION_SUFFIXES]
        matches = [path for path in candidates if path.is_file()]
        if len(matches) != 1:
            raise RuntimeError(f"Expected one native library for {module}, got {matches}")
        artifacts[module] = matches[0]
    return artifacts


def build_native(root, work):
    from Cython.Build import cythonize
    from Cython.Compiler import Options
    from setuptools import Distribution, Extension

    root, work = Path(root).resolve(), Path(work).resolve()
    source = work / "source"
    source.mkdir(parents=True)
    for module in NATIVE_MODULES:
        relative = Path(*module.split(".")).with_suffix(".py")
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target)
        for parent in relative.parents:
            initializer = root / parent / "__init__.py"
            if initializer.is_file():
                shutil.copy2(initializer, source / parent / "__init__.py")

    # Preserve Python's dynamic types and class behavior; omit source comments,
    # docstrings and compiler debugging information from release libraries.
    Options.docstrings = False
    flags = ["/O2"] if sys.platform == "win32" else ["-O2", "-g0"]
    extensions = [Extension(module, [str(Path(*module.split(".")).with_suffix(".py"))],
                            extra_compile_args=flags) for module in NATIVE_MODULES]
    with chdir(source):
        extensions = cythonize(
            extensions,
            build_dir=str(work / "generated"),
            compiler_directives={"language_level": 3, "annotation_typing": False,
                                 "infer_types": False, "emit_code_comments": False},
            annotate=False,
        )
        distribution = Distribution({"name": "seeend-native", "ext_modules": extensions})
        command = distribution.get_command_obj("build_ext")
        command.build_lib = str(work / "lib")
        command.build_temp = str(work / "objects")
        command.parallel = min(4, os.cpu_count() or 1)
        command.ensure_finalized()
        command.run()

    artifacts = native_artifacts(work / "lib")
    if sys.platform != "win32":
        strip = shutil.which("strip")
        if strip is None:
            raise RuntimeError("A native strip tool is required for release builds")
        option = "-x" if sys.platform == "darwin" else "--strip-unneeded"
        for path in artifacts.values():
            subprocess.run([strip, option, str(path)], check=True)
    print(f"Compiled {len(artifacts)} core modules to native libraries.", flush=True)
    return work / "lib"

# SPDX-License-Identifier: GPL-2.0-or-later
# Reproducible build recipe for the separate, unmodified esptool utility.
from pathlib import Path
import esptool
from PyInstaller.utils.hooks import collect_submodules, collect_data_files
entry = Path(esptool.__file__).parent / "__main__.py"
a = Analysis([str(entry)], hiddenimports=collect_submodules("esptool"),
             datas=collect_data_files("esptool"),
             excludes=["aiflame", "PySide6", "IPython", "readline", "zmq", "numpy", "matplotlib", "tkinter"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="esptool", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="esptool", upx=False)

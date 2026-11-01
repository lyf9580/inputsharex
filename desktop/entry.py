"""PyInstaller 打包入口：把桌面 GUI 当作独立程序启动。

打包后源码被解压到 ``sys._MEIPASS``，而引擎里的 ``REPO_ROOT`` /
``default_jar_path()`` 是按源码目录层级（``src/inputsharex/..``）推算的，
在 onefile 里会算到临时目录的上一级。因此这里在导入 GUI 之前把 vendor
路径修正到 ``_MEIPASS``；源码运行时完全不动这些全局变量。
"""
from __future__ import annotations

import sys
from pathlib import Path


def _patch_frozen_paths() -> None:
    if not getattr(sys, "frozen", False):
        return
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    from inputsharex import adb, server

    adb.REPO_ROOT = base
    adb.CACHE_DIR = base / ".cache"
    adb_candidate = str(base / "vendor" / "platform-tools" / "adb.exe")
    if adb_candidate not in adb.DEFAULT_CANDIDATES:
        adb.DEFAULT_CANDIDATES.insert(0, adb_candidate)
    jar = base / "vendor" / "scrcpy-server.jar"
    server.default_jar_path = lambda: str(jar)


def main() -> int:
    _patch_frozen_paths()
    from inputsharex.gui import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())

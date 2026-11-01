"""安卓端配套 App 的检测与自动安装。

配对/连接成功后，桌面端需要确认安卓端已经装了 ``com.inputsharex.app``：
辅助通道、共享状态和「下次连接自动开启共享」都依赖它。缺了就从打包的
``vendor/inputsharex-android.apk`` 自动装一份，避免用户重新找 APK 手动安装。

这个模块不依赖 Qt，便于单元测试。
"""
from __future__ import annotations

import os
import re
import subprocess

from . import adb as adb_module
from .adb import Adb, AdbError

PACKAGE = "com.inputsharex.app"
APK_NAME = "inputsharex-android.apk"
MAIN_ACTIVITY = f"{PACKAGE}/.MainActivity"
# 与 Android 端 MainActivity.EXTRA_CONNECT 保持一致：电脑拉起 App 时带上这个标记，
# App 才会主动连接一次（平时不自动重连）。
EXTRA_CONNECT = "inputsharex_connect"

_VERSION_RE = re.compile(r"versionName=([^\s]+)")
_VERSION_CODE_RE = re.compile(r"versionCode=(\d+)")


def default_apk_path() -> str:
    """返回随程序分发的 APK 路径（源码运行与 PyInstaller onefile 都适用）。"""
    root = os.path.join(str(adb_module.REPO_ROOT), "vendor", APK_NAME)
    if os.path.isfile(root):
        return root
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in (
        ("..", "..", "vendor", APK_NAME),
        ("..", "..", "..", "vendor", APK_NAME),
    ):
        candidate = os.path.abspath(os.path.join(here, *rel))
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(
        f"vendor/{APK_NAME} 不存在：构建时需要通过 build-desktop.ps1 或手动放入"
    )


def parse_dumpsys(text: str) -> tuple[bool, str]:
    """从 ``dumpsys package com.inputsharex.app`` 判断是否安装，并取出版本名。"""
    if not text:
        return False, ""
    if "Unable to find package" in text or "Package [" not in text:
        return False, ""
    match = _VERSION_RE.search(text)
    return True, (match.group(1) if match else "")


def installed_version(adb: Adb, serial: str) -> tuple[bool, str]:
    """查询安卓端是否装了配套 App；返回 (已安装, versionName)。"""
    try:
        proc = adb.run_for(serial, "shell", "dumpsys", "package", PACKAGE,
                           check=False, timeout=12)
    except (AdbError, OSError, subprocess.SubprocessError):
        return False, ""
    output = proc.stdout.decode("utf-8", "replace") if proc.stdout else ""
    if proc.returncode != 0:
        return False, ""
    return parse_dumpsys(output)


def _describe(installed: bool, version: str) -> str:
    if installed:
        return f"设备已安装安卓端 App（{version}）" if version else "设备已安装安卓端 App"
    return ""


def launch(adb: Adb, serial: str, *, log=None) -> bool:
    """拉起设备上的配套 App，并让它连接电脑（连接只由电脑端发起）。

    电脑端在辅助通道（adb reverse）就绪后调用；App 收到 ``inputsharex_connect``
    标记才会做一次连接尝试，没有电脑时不会自己反复重连。
    """
    def say(message: str) -> None:
        if log is not None:
            log(message)

    try:
        proc = adb.run_for(
            serial, "shell", "am", "start", "--activity-clear-task",
            "-n", MAIN_ACTIVITY,
            "--ez", EXTRA_CONNECT, "true", check=False, timeout=20,
        )
    except (AdbError, OSError, subprocess.SubprocessError) as exc:
        say(f"自动打开设备 App 失败：{exc}")
        return False
    output = ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", "replace").strip()
    if proc.returncode != 0 or "Error" in output:
        detail = output.splitlines()[-1] if output else f"adb 退出码 {proc.returncode}"
        say(f"自动打开设备 App 失败：{detail}")
        return False
    say("已自动打开设备上的输入流转X，安卓端将连接电脑")
    return True


def ensure_installed(adb: Adb, serial: str, *, apk_path: str | None = None,
                     log=None, open_app: bool = True) -> bool:
    """确保安卓端装有配套 App；缺了就从打包 APK 自动安装。

    Returns:
        True 表示已安装（原本就有或刚装好）；False 表示检测/安装失败。
    """
    def say(message: str) -> None:
        if log is not None:
            log(message)

    installed, version = installed_version(adb, serial)
    if installed:
        say(_describe(installed, version))
        return True

    try:
        path = apk_path or default_apk_path()
    except FileNotFoundError as exc:
        say(f"未检测到安卓端 App，且自动安装包缺失：{exc}")
        return False
    if not os.path.isfile(path):
        say(f"未检测到安卓端 App，自动安装包不存在：{path}")
        return False

    say(f"未检测到安卓端 App，正在自动安装（{os.path.basename(path)}）…")
    try:
        proc = adb.run_for(serial, "install", "-r", path, check=False, timeout=240)
    except (AdbError, OSError, subprocess.SubprocessError) as exc:
        say(f"自动安装安卓端 App 失败：{exc}")
        return False

    output = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip()
    if proc.returncode != 0 or "Success" not in output:
        detail = output.splitlines()[-1] if output else f"adb 退出码 {proc.returncode}"
        say(f"自动安装安卓端 App 失败：{detail}")
        return False

    installed, version = installed_version(adb, serial)
    if not installed:
        say("已执行安装，但未能确认安卓端 App 安装成功；请在设备上核对")
        return False
    say(f"已自动安装安卓端 App（{version}）" if version else "已自动安装安卓端 App")

    if open_app:
        launch(adb, serial, log=log)
    return True

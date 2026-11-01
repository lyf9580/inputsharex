"""开机自启：写当前用户注册表的 Run 项（不需要管理员权限）。

两种运行形态：
- 打包后（``sys.frozen``）：直接注册 ``InputShareX.exe --minimized``；
- 源码运行：在 ``%LOCALAPPDATA%\\inputsharex\\launch-gui.pyw`` 生成一个
  ``pythonw`` 启动器（无控制台窗口），再把它写进 Run 项。

不写 Startup 快捷方式，避免额外拉起 PowerShell；注册表方案可随 exe 路径自动更新。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

VALUE_NAME = "InputShareX"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_DIRNAME = "inputsharex"


def state_dir() -> Path:
    """与 AppController 的 config.json 同目录。"""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / _APP_DIRNAME


def launcher_path() -> Path:
    return state_dir() / "launch-gui.pyw"


def _source_root() -> Path:
    # 本文件: <repo>/src/inputsharex/ui/autostart.py
    return Path(__file__).resolve().parents[2]


def _write_launcher() -> Path:
    launcher = launcher_path()
    launcher.parent.mkdir(parents=True, exist_ok=True)
    src = str(_source_root())
    content = (
        "# -*- coding: utf-8 -*-\n"
        '"""输入流转X 开机启动器（自动生成，请勿手工修改）。"""\n'
        "import sys\n\n"
        f"sys.path.insert(0, {src!r})\n"
        "from inputsharex.gui import main\n\n"
        'if __name__ == "__main__":\n'
        '    raise SystemExit(main(["--minimized"]))\n'
    )
    # 启动器是 Python 源码，普通 UTF-8 即可；不需要 BOM。
    launcher.write_text(content, encoding="utf-8")
    return launcher


def command() -> str:
    """返回要写入 Run 项的完整命令行。"""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}" --minimized'
    launcher = _write_launcher()
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            exe = candidate
    return f'"{exe}" "{launcher}"'


def is_supported() -> bool:
    return sys.platform == "win32"


def is_enabled() -> bool:
    if not is_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
        return bool(value)
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    """开启/关闭开机自启；失败时抛 RuntimeError 供界面提示。"""
    if not is_supported():
        raise RuntimeError("开机自启只支持 Windows")
    import winreg

    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        raise RuntimeError(f"无法写入注册表：{exc}") from exc

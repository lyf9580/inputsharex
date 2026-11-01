"""导入冒烟测试：确保打包入口与界面模块可以在无显示器环境下导入。"""
from __future__ import annotations


def test_gui_and_window_modules_import():
    import inputsharex.gui as gui
    import inputsharex.ui.main_window as main_window

    assert callable(gui.main)
    assert main_window.MainWindow is not None


def test_update_module_public_api():
    from inputsharex import update

    assert update.REPO_URL.startswith("https://github.com/")
    assert update.download_dir().name == "download"

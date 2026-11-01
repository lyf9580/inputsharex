# 桌面程序构建说明

「输入流转X」的 GUI 用 PySide6 编写，入口是 `src/inputsharex/gui.py`，界面代码在
`src/inputsharex/ui/`。本目录放构建脚本、图标生成器和源码启动器。

## 一、直接跑源码（无需打包）

在仓库根目录：

```powershell
$env:PYTHONPATH = "src"
python -m inputsharex.gui
```

或双击 `desktop\start-gui.cmd`（用 `pythonw` 无控制台启动，适合做桌面快捷方式）。

如果已经 `pip install -e .[gui]` 安装了本项目，也可以直接运行：

```powershell
inputsharex-gui
```

## 二、打包单文件 exe

1. 安装 PyInstaller（本机 Python 在 `%LOCALAPPDATA%\Python\bin\python.exe`）：

   ```powershell
   python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pyinstaller
   ```

2. 在仓库根目录执行：

   ```powershell
   .\build-desktop.ps1 -Shortcut
   ```

   产出：`dist\InputShareX.exe`（单文件、无控制台窗口），并把
   `vendor\platform-tools` 和 `vendor\scrcpy-server.jar` 一并打进包里。
   `-Shortcut` 会在桌面创建「输入流转X」快捷方式。

3. 首次运行如被 Windows SmartScreen 拦截：点「更多信息 → 仍要运行」。
   单文件模式每次启动会解压到临时目录，冷启动约 1～3 秒，属正常现象。

已在本机验证：Python 3.14.7 + PyInstaller 6.22.3，产出
`dist\InputShareX.exe`（约 43.6 MB），`--version` 与正常启动均通过。
`desktop\entry.py` 会在打包态把 vendor 路径修正到 PyInstaller 的
`_MEIPASS`（引擎里的 `REPO_ROOT` 是按源码目录层级推算的），源码运行不受影响。

## 三、PyInstaller 装不上时

用源码快捷方式替代（不生成 exe）：

```powershell
.\build-desktop.ps1 -SourceShortcut
```

它会创建一个指向 `desktop\start-gui.cmd` 的桌面快捷方式，内部等价于：

```text
"%LOCALAPPDATA%\Python\bin\pythonw.exe" -m inputsharex.gui
```

注意：这种方式要求本机保留仓库和 `src` 目录，不能像 exe 一样单独拷走。

## 四、脚本参数

| 参数 | 作用 |
| --- | --- |
| `-Shortcut` | 构建成功后在桌面创建 exe 快捷方式 |
| `-SourceShortcut` | 跳过打包，直接创建源码启动快捷方式 |
| `-SkipIcon` | 不重新生成 `desktop\icon.ico` |
| `-PythonExe <path>` | 指定 Python 解释器 |

## 五、图标

`desktop\icon.ico` 与 `desktop\icon-256.png` 由 `desktop\make_icon.py` 生成
（Qt 绘制，不依赖 Pillow / 图片素材），重新生成：

```powershell
python desktop\make_icon.py
```

## 六、开机自启

设置页的「开机自启」会写入当前用户注册表
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，值名为 `InputShareX`：

- 打包后：指向 `InputShareX.exe --minimized`；
- 源码模式：生成 `%LOCALAPPDATA%\inputsharex\launch-gui.pyw` 并用 `pythonw` 启动。

移动 exe 或删除仓库后，需要回到设置页重新切换一次开机自启。

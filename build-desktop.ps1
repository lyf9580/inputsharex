#requires -Version 5.1
<#
.SYNOPSIS
    构建「输入流转X」Windows 桌面程序（PyInstaller 单文件）。

.DESCRIPTION
    默认生成 dist\InputShareX.exe（--onefile --noconsole），并把
    vendor\platform-tools、vendor\scrcpy-server.jar 与
    vendor\inputsharex-android.apk（平板端配套 App）一起打包进去。

    首次构建前需要安装 PyInstaller（二选一）：
        python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pyinstaller
        python -m pip install pyinstaller

    如果 PyInstaller 装不上（网络/编译环境问题），可以退化为源码快捷方式：
        .\build-desktop.ps1 -SourceShortcut
    它会创建指向 desktop\start-gui.cmd 的桌面快捷方式，用 pythonw 无窗口启动。

.PARAMETER Shortcut
    构建成功后，在桌面创建指向 dist\InputShareX.exe 的快捷方式。

.PARAMETER SourceShortcut
    不构建 exe，直接在桌面创建源码启动快捷方式（pythonw -m inputsharex.gui）。

.PARAMETER SkipIcon
    跳过图标生成（复用已有的 desktop\icon.ico）。

.PARAMETER PythonExe
    指定 Python 解释器路径；默认查找 %LOCALAPPDATA%\Python\bin\python.exe。

.EXAMPLE
    .\build-desktop.ps1 -Shortcut
#>
[CmdletBinding()]
param(
    [switch]$Shortcut,
    [switch]$SourceShortcut,
    [switch]$SkipIcon,
    [string]$PythonExe = "",
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
# 本脚本位于仓库根目录；-File/双击/从任意目录调用都以脚本位置为准。
$Root = $PSScriptRoot
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$Desktop = [Environment]::GetFolderPath("Desktop")
$IconPath = Join-Path $Root "desktop\icon.ico"

function Resolve-Python {
    if ($PythonExe) {
        if (-not (Test-Path $PythonExe)) { throw "找不到指定的 Python：$PythonExe" }
        return (Resolve-Path $PythonExe).Path
    }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Python\bin\python.exe"),
        (Join-Path $env:USERPROFILE "AppData\Local\Python\bin\python.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    throw "找不到 Python。请用 -PythonExe 指定，例如 -PythonExe C:\Python312\python.exe"
}

function New-AppShortcut([string]$Name, [string]$TargetPath, [string]$Arguments,
                         [string]$WorkingDirectory, [string]$IconLocation, [int]$WindowStyle = 1) {
    $shell = New-Object -ComObject WScript.Shell
    $linkPath = Join-Path $Desktop "$Name.lnk"
    $link = $shell.CreateShortcut($linkPath)
    $link.TargetPath = $TargetPath
    if ($Arguments) { $link.Arguments = $Arguments }
    $link.WorkingDirectory = $WorkingDirectory
    if ($IconLocation) { $link.IconLocation = "$IconLocation,0" }
    $link.WindowStyle = $WindowStyle
    $link.Description = "输入流转X：把 Windows 键鼠共享给安卓平板"
    $link.Save()
    Write-Host "[快捷方式] $linkPath"
}

function New-SourceShortcut {
    $launcher = Join-Path $Root "desktop\start-gui.cmd"
    if (-not (Test-Path $launcher)) { throw "缺少 $launcher" }
    if (-not (Test-Path $IconPath)) {
        Write-Host "[提示] 缺少 $IconPath，快捷方式将使用默认图标"
        $IconPath = ""
    }
    New-AppShortcut -Name "输入流转X" -TargetPath $launcher -Arguments "" `
        -WorkingDirectory $Root -IconLocation $IconPath -WindowStyle 7
}

$Py = Resolve-Python
Write-Host "仓库根目录：$Root"
Write-Host "Python：$Py"

if ($SourceShortcut) {
    New-SourceShortcut
    Write-Host "已创建源码启动快捷方式（pythonw -m inputsharex.gui），无需 PyInstaller。"
    exit 0
}

# ---- 图标 ----------------------------------------------------------
if (-not $SkipIcon) {
    Write-Host "[1/3] 生成图标 ..."
    & $Py (Join-Path $Root "desktop\make_icon.py")
    if ($LASTEXITCODE -ne 0) { throw "生成图标失败（exit $LASTEXITCODE）" }
}
if (-not (Test-Path $IconPath)) { throw "缺少图标文件：$IconPath" }

# ---- 依赖检查 ------------------------------------------------------
Write-Host "[2/3] 检查 PyInstaller ..."
# 用 importlib 检查，避免 PowerShell 5.1 把原生命令的 stderr 当成终止错误。
& $Py -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "[!] 未安装 PyInstaller，无法生成单文件 exe。"
    Write-Host "    安装命令（清华镜像）："
    Write-Host "      & `"$Py`" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pyinstaller"
    Write-Host "    安装后重新运行： .\build-desktop.ps1 -Shortcut"
    Write-Host ""
    Write-Host "    不想安装也可以直接用源码快捷方式："
    Write-Host "      .\build-desktop.ps1 -SourceShortcut"
    exit 2
}
$pyInstallerVersion = & $Py -c "import PyInstaller; print(PyInstaller.__version__)"
Write-Host "    PyInstaller $pyInstallerVersion"

foreach ($required in @(
    (Join-Path $Root "vendor\platform-tools\adb.exe"),
    (Join-Path $Root "vendor\scrcpy-server.jar"),
    (Join-Path $Root "vendor\inputsharex-android.apk")
)) {
    if (-not (Test-Path $required)) { throw "缺少运行依赖：$required" }
}

# ---- 构建 ----------------------------------------------------------
Write-Host "[3/3] PyInstaller 构建中（第一次会慢一些）..."
$distPath = if ($OutputDirectory) { $OutputDirectory } else { Join-Path $Root "dist" }
$workPath = Join-Path $Root "build\pyinstaller"
$specPath = Join-Path $Root "build"
New-Item -ItemType Directory -Force -Path $workPath, $specPath | Out-Null

$pyArgs = @(
    "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onefile", "--noconsole",
    "--name", "InputShareX",
    "--icon", $IconPath,
    "--paths", (Join-Path $Root "src"),
    "--add-data", "$Root/vendor\platform-tools;vendor\platform-tools",
    "--add-data", "$Root/vendor\scrcpy-server.jar;vendor",
    "--add-data", "$Root/vendor\inputsharex-relay.jar;vendor",
    "--add-data", "$Root/vendor\inputsharex-android.apk;vendor",
    "--distpath", $distPath,
    "--workpath", $workPath,
    "--specpath", $specPath,
    (Join-Path $Root "desktop\entry.py")
)
# Isolate DLL discovery from unrelated native toolchains inherited through PATH.
# Otherwise foreign ICU/UCRT DLLs can be bundled and QtCore fails at runtime.
$runtimeDirs = @(& $Py -c "import os,sys,PySide6,shiboken6; print(sys.prefix); print(os.path.dirname(PySide6.__file__)); print(os.path.dirname(shiboken6.__file__))")
$originalBuildPath = $env:PATH
try {
    $env:PATH = @(
        (Join-Path $env:SystemRoot "System32"), $env:SystemRoot,
        $runtimeDirs[0], (Join-Path $runtimeDirs[0] "DLLs"),
        $runtimeDirs[1], $runtimeDirs[2], (Split-Path $Py -Parent)
    ) -join ";"
    & $Py @pyArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败（exit $LASTEXITCODE）" }
} finally {
    $env:PATH = $originalBuildPath
}

$exe = Join-Path $distPath "InputShareX.exe"
if (-not (Test-Path $exe)) { throw "构建结束但没有找到 $exe" }
$sizeMB = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "[完成] $exe（约 $sizeMB MB）"
Write-Host "直接双击即可运行；首次启动若被 SmartScreen 拦截，选「更多信息 → 仍要运行」。"
Write-Host "开机自启会注册这个 exe 的路径，移动 exe 后需要在设置页重新勾选。"

if ($Shortcut) {
    New-AppShortcut -Name "输入流转X" -TargetPath $exe -Arguments "" `
        -WorkingDirectory $distPath -IconLocation $exe
}

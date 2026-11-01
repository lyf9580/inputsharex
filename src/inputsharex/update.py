"""启动时检查 GitHub Release 更新，并把新版安装包下载到本地。

只依赖标准库（urllib / json），不引入额外 GUI 或网络依赖。
下载目录固定为程序所在目录下的 ``download/``（源码运行时为仓库根目录下的 ``download/``）。
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __repo__, __version__

REPO_URL = __repo__
_API_URL = "https://api.github.com/repos/{slug}/releases/latest"
_TIMEOUT = 10.0
_DOWNLOAD_TIMEOUT = 30.0
_USER_AGENT = f"InputShareX/{__version__} (+{__repo__})"
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)*)")


class DownloadCancelled(Exception):
    """用户取消了下载。"""


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    page_url: str
    notes: str
    asset_name: str
    asset_url: str
    asset_size: int

    @property
    def label(self) -> str:
        return f"v{self.version}"


def repo_slug() -> str:
    """把 https://github.com/<owner>/<repo> 转成 <owner>/<repo>。"""
    text = REPO_URL.rstrip("/")
    marker = "github.com/"
    if marker in text:
        text = text.split(marker, 1)[1]
    parts = [part for part in text.split("/") if part]
    if len(parts) < 2:
        raise ValueError(f"无法解析仓库地址：{REPO_URL}")
    return f"{parts[0]}/{parts[1]}"


def parse_version(text: str) -> tuple[int, ...] | None:
    """从 tag / 版本字符串中提取数字版本，如 v0.4.3 → (0, 4, 3)。"""
    if not text:
        return None
    match = _VERSION_RE.search(text.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(remote: str, current: str) -> bool:
    remote_version = parse_version(remote)
    current_version = parse_version(current)
    if remote_version is None or current_version is None:
        return False
    width = max(len(remote_version), len(current_version))
    remote_version = remote_version + (0,) * (width - len(remote_version))
    current_version = current_version + (0,) * (width - len(current_version))
    return remote_version > current_version


def app_dir() -> Path:
    """程序所在目录：冻结态为 EXE 目录，源码运行时为仓库根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def download_dir() -> Path:
    """更新包固定下载到程序目录下的 download/。"""
    return app_dir() / "download"


def check_for_update(
    current: str = __version__,
    *,
    timeout: float = _TIMEOUT,
    opener: Callable[..., object] | None = None,
) -> ReleaseInfo | None:
    """查询 GitHub 最新 Release；有新版本返回 ReleaseInfo，否则返回 None。"""
    url = _API_URL.format(slug=repo_slug())
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": _USER_AGENT,
        },
    )
    open_func = opener or urllib.request.urlopen
    with open_func(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    tag = str(payload.get("tag_name") or "").strip()
    version = parse_version(tag) or parse_version(str(payload.get("name") or ""))
    if version is None:
        return None
    version_text = ".".join(str(part) for part in version)
    if not is_newer(version_text, current):
        return None

    assets = payload.get("assets") or []
    asset_name = ""
    asset_url = ""
    asset_size = 0
    for asset in assets:
        name = str(asset.get("name") or "")
        if name.lower().endswith(".exe"):
            asset_name = name
            asset_url = str(asset.get("browser_download_url") or "")
            asset_size = int(asset.get("size") or 0)
            break

    return ReleaseInfo(
        version=version_text,
        tag=tag,
        page_url=str(payload.get("html_url") or f"{REPO_URL}/releases"),
        notes=str(payload.get("body") or ""),
        asset_name=asset_name or f"InputShareX-{version_text}.exe",
        asset_url=asset_url,
        asset_size=asset_size,
    )


def format_size(num_bytes: int) -> str:
    size = float(max(0, num_bytes))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{int(num_bytes)} B"


def download_update(
    info: ReleaseInfo,
    *,
    dest_dir: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
    timeout: float = _DOWNLOAD_TIMEOUT,
) -> Path:
    """下载新版 EXE 到 download/；返回保存路径。"""
    if not info.asset_url:
        raise RuntimeError("该 Release 没有可下载的 Windows EXE")
    target_dir = dest_dir or download_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = info.asset_name or f"InputShareX-{info.version}.exe"
    target = target_dir / filename
    temp = target.with_name(target.name + ".part")

    request = urllib.request.Request(
        info.asset_url,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/octet-stream"},
    )
    downloaded = 0
    with urllib.request.urlopen(request, timeout=timeout) as response:
        total = int(response.headers.get("Content-Length") or info.asset_size or 0)
        with temp.open("wb") as sink:
            while True:
                if cancel is not None and cancel():
                    raise DownloadCancelled()
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                sink.write(chunk)
                downloaded += len(chunk)
                if progress is not None:
                    progress(downloaded, total)
    temp.replace(target)
    return target

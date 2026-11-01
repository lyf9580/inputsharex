"""update.py 的单元测试：版本解析/比较、Release 解析、下载流程。"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from inputsharex import update


class _FakeResponse:
    def __init__(self, payload: bytes, headers: dict[str, str] | None = None):
        self._payload = payload
        self.headers = headers or {}

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            data, self._payload = self._payload, b""
            return data
        data, self._payload = self._payload[:size], self._payload[size:]
        return data

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _payload(version: str, assets: list[dict] | None = None, *, notes: str = "changes"):
    return {
        "tag_name": f"v{version}",
        "name": f"InputShareX v{version}",
        "html_url": f"https://github.com/lyf9580/inputsharex/releases/tag/v{version}",
        "body": notes,
        "assets": assets
        or [
            {
                "name": "InputShareX.exe",
                "browser_download_url": f"https://example.com/InputShareX-{version}.exe",
                "size": 1234,
            }
        ],
    }


def test_parse_version_extracts_numbers():
    assert update.parse_version("v0.4.3") == (0, 4, 3)
    assert update.parse_version("InputShareX v1.2") == (1, 2)
    assert update.parse_version("no version") is None
    assert update.parse_version("2026-11-01") == (2026,)


def test_is_newer_compares_numerically():
    assert update.is_newer("0.4.3", "0.4.2") is True
    assert update.is_newer("0.4.2", "0.4.3") is False
    assert update.is_newer("0.4.2", "0.4.2") is False
    assert update.is_newer("1.0", "0.9.9") is True
    assert update.is_newer("0.5", "0.5.0") is False
    assert update.is_newer("bad", "0.4.2") is False


def test_repo_slug_matches_project_repo():
    assert update.repo_slug() == "lyf9580/inputsharex"


def test_download_dir_is_program_dir_download():
    original = update.app_dir
    with tempfile.TemporaryDirectory() as tmp:
        update.app_dir = lambda: Path(tmp)
        try:
            assert update.download_dir() == Path(tmp) / "download"
        finally:
            update.app_dir = original


def test_check_for_update_returns_release_info():
    payload = _payload("0.4.9")
    calls: list[str] = []

    def opener(request, *, timeout):
        calls.append(request.full_url)
        assert "api.github.com/repos/lyf9580/inputsharex/releases/latest" in request.full_url
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    info = update.check_for_update("0.4.3", opener=opener)
    assert info is not None
    assert info.version == "0.4.9"
    assert info.label == "v0.4.9"
    assert info.asset_name == "InputShareX.exe"
    assert info.asset_url.endswith("InputShareX-0.4.9.exe")
    assert info.asset_size == 1234
    assert calls


def test_check_for_update_returns_none_when_up_to_date():
    payload = _payload("0.4.3")
    info = update.check_for_update(
        "0.4.3", opener=lambda request, *, timeout: _FakeResponse(json.dumps(payload).encode())
    )
    assert info is None


def test_check_for_update_handles_payload_without_version():
    payload = {"tag_name": "nightly", "assets": []}
    info = update.check_for_update(
        "0.4.3", opener=lambda request, *, timeout: _FakeResponse(json.dumps(payload).encode())
    )
    assert info is None


def test_check_for_update_falls_back_when_asset_has_no_exe():
    payload = _payload("0.5.0", assets=[{"name": "notes.txt", "size": 1}])
    info = update.check_for_update(
        "0.4.3", opener=lambda request, *, timeout: _FakeResponse(json.dumps(payload).encode())
    )
    assert info is not None
    assert info.asset_name == "InputShareX-0.5.0.exe"
    assert info.asset_url == ""


def test_download_update_writes_file_and_reports_progress():
    chunks = [b"abc", b"def", b""]

    class _Stream:
        headers = {"Content-Length": "6"}

        def read(self, size=-1):
            return chunks.pop(0)

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    original = update.urllib.request.urlopen
    update.urllib.request.urlopen = lambda *a, **k: _Stream()
    info = update.ReleaseInfo(
        version="0.4.9",
        tag="v0.4.9",
        page_url="https://example.com",
        notes="",
        asset_name="InputShareX.exe",
        asset_url="https://example.com/InputShareX.exe",
        asset_size=6,
    )
    seen: list[tuple[int, int]] = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = update.download_update(
                info, dest_dir=Path(tmp), progress=lambda done, total: seen.append((done, total))
            )
            assert target == Path(tmp) / "InputShareX.exe"
            assert target.read_bytes() == b"abcdef"
            assert not (Path(tmp) / "InputShareX.exe.part").exists()
    finally:
        update.urllib.request.urlopen = original
    assert seen[-1] == (6, 6)


def test_download_update_cancel_removes_partial_file():
    class _Stream:
        headers = {"Content-Length": "6"}

        def read(self, size=-1):
            return b"abc"

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    original = update.urllib.request.urlopen
    update.urllib.request.urlopen = lambda *a, **k: _Stream()
    info = update.ReleaseInfo(
        version="0.4.9",
        tag="v0.4.9",
        page_url="https://example.com",
        notes="",
        asset_name="InputShareX.exe",
        asset_url="https://example.com/InputShareX.exe",
        asset_size=6,
    )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                update.download_update(info, dest_dir=Path(tmp), cancel=lambda: True)
            except update.DownloadCancelled:
                pass
            else:  # pragma: no cover - 失败时明确指出
                raise AssertionError("cancel=True 时应抛 DownloadCancelled")
            assert not (Path(tmp) / "InputShareX.exe").exists()
    finally:
        update.urllib.request.urlopen = original


def test_download_update_without_asset_url_raises():
    info = update.ReleaseInfo(
        version="0.4.9",
        tag="v0.4.9",
        page_url="https://example.com",
        notes="",
        asset_name="InputShareX.exe",
        asset_url="",
        asset_size=0,
    )
    try:
        update.download_update(info)
    except RuntimeError as exc:
        assert "Windows EXE" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("缺少 asset_url 时应抛 RuntimeError")


def test_format_size_readable():
    assert update.format_size(0) == "0 B"
    assert update.format_size(1024) == "1.0 KB"
    assert update.format_size(1024 * 1024) == "1.0 MB"

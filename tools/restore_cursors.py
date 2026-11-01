"""把 Windows 鼠标指针恢复成注册表里记录的方案（aero 等），并校验结果。

用法：python tools/restore_cursors.py [--dry]
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import hashlib
import sys

user32 = ctypes.WinDLL("user32", use_last_error=True)

SPI_SETCURSORS = 0x0057
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02
IMAGE_CURSOR = 2
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040

OCR_IDS = {
    "Arrow": 32512, "IBeam": 32513, "Wait": 32514, "Crosshair": 32515,
    "UpArrow": 32516, "SizeNWSE": 32642, "SizeNESW": 32643, "SizeWE": 32644,
    "SizeNS": 32645, "SizeAll": 32646, "No": 32648, "Hand": 32649,
    "AppStarting": 32650, "Help": 32651, "Pin": 32671, "Person": 32672,
}


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
                ("hbmColor", wintypes.HBITMAP)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG),
                ("bmHeight", wintypes.LONG), ("bmWidthBytes", wintypes.LONG),
                ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", wintypes.LPVOID)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


def cursor_bitmap_hash(hcursor) -> tuple[int, int, str]:
    info = ICONINFO()
    if not user32.GetIconInfo(wintypes.HANDLE(hcursor), ctypes.byref(info)):
        return (0, 0, "")
    bmp = BITMAP()
    gdi32.GetObjectW(info.hbmColor or info.hbmMask, ctypes.sizeof(BITMAP), ctypes.byref(bmp))
    w, h = int(bmp.bmWidth), int(bmp.bmHeight)
    hdc = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(hdc)
    old = gdi32.SelectObject(mem, info.hbmColor or info.hbmMask)
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth, bi.biHeight = w, -h
    bi.biPlanes, bi.biBitCount = 1, 32
    buf = (ctypes.c_ubyte * (w * h * 4))()
    gdi32.GetDIBits(mem, info.hbmColor or info.hbmMask, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, hdc)
    digest = hashlib.sha256(bytes(buf)).hexdigest()[:16]
    if info.hbmColor:
        gdi32.DeleteObject(info.hbmColor)
    if info.hbmMask:
        gdi32.DeleteObject(info.hbmMask)
    return (w, h, digest)


def current_cursor():
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(ci)):
        return None
    return ci.hCursor


def main() -> int:
    dry = "--dry" in sys.argv
    before = current_cursor()
    print("before: handle=%s size/hash=%s" % (before, cursor_bitmap_hash(before) if before else None))

    # SPI_SETCURSORS 的文档模式：uiParam=0、pvParam=NULL；实测 fWinIni=0 返回成功，
    # 传 SPIF_UPDATEINIFILE|SPIF_SENDCHANGE(3) 反而返回失败。
    ok = user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)
    err = ctypes.get_last_error()
    print(f"SystemParametersInfoW(SPI_SETCURSORS) -> {bool(ok)} (GetLastError={err})")

    if not ok:
        print("回退：按注册表逐项 SetSystemCursor…")
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Cursors") as key:
            restored = 0
            for name, ocr in OCR_IDS.items():
                try:
                    path, _ = winreg.QueryValueEx(key, name)
                except OSError:
                    continue
                if not path:
                    continue
                h = user32.LoadImageW(None, path, IMAGE_CURSOR, 0, 0,
                                      LR_LOADFROMFILE | LR_DEFAULTSIZE)
                if not h:
                    continue
                if user32.SetSystemCursor(h, ocr):
                    restored += 1
            print(f"fallback restored {restored} cursors")

    after = current_cursor()
    print("after : handle=%s size/hash=%s" % (after, cursor_bitmap_hash(after) if after else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

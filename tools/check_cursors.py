"""对比“当前系统箭头光标位图”与注册表 aero 方案文件里的箭头位图，判断是否已被篡改。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import hashlib
import winreg

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
gdi32.GetObjectW.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p)
gdi32.GetObjectW.restype = ctypes.c_int
gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HANDLE)
gdi32.SelectObject.restype = wintypes.HANDLE
gdi32.DeleteDC.argtypes = (wintypes.HDC,)
gdi32.DeleteDC.restype = wintypes.BOOL

user32.SystemParametersInfoW.argtypes = (wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT)
user32.SystemParametersInfoW.restype = wintypes.BOOL
user32.LoadImageW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT)
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadCursorW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR)
user32.LoadCursorW.restype = wintypes.HANDLE
user32.SetSystemCursor.argtypes = (wintypes.HANDLE, wintypes.DWORD)
user32.SetSystemCursor.restype = wintypes.BOOL
user32.GetDC.argtypes = (wintypes.HWND,)
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
user32.ReleaseDC.restype = ctypes.c_int

SPI_SETCURSORS = 0x57
SPIF_UPDATEINIFILE, SPIF_SENDCHANGE = 0x01, 0x02
IMAGE_CURSOR, LR_LOADFROMFILE, LR_DEFAULTSIZE = 2, 0x10, 0x40
OCR_ARROW = 32512


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG),
                ("bmWidthBytes", wintypes.LONG), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", wintypes.LPVOID)]


class BMIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


gdi32.GetDIBits.argtypes = (wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.POINTER(BMIH), wintypes.UINT)
gdi32.GetDIBits.restype = ctypes.c_int

user32.GetIconInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(ICONINFO))
user32.GetIconInfo.restype = wintypes.BOOL


def hash_cursor(h) -> tuple[int, int, str]:
    info = ICONINFO()
    if not user32.GetIconInfo(wintypes.HANDLE(h), ctypes.byref(info)):
        return (0, 0, "no-iconinfo")
    hbmp = info.hbmColor or info.hbmMask
    bmp = BITMAP()
    gdi32.GetObjectW(hbmp, ctypes.sizeof(BITMAP), ctypes.byref(bmp))
    w, hgt = int(bmp.bmWidth), int(bmp.bmHeight)
    hdc = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(hdc)
    old = gdi32.SelectObject(mem, hbmp)
    bi = BMIH(ctypes.sizeof(BMIH), w, -hgt, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = (ctypes.c_ubyte * (w * hgt * 4))()
    gdi32.GetDIBits(mem, hbmp, 0, hgt, buf, ctypes.byref(bi), 0)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, hdc)
    return (w, hgt, hashlib.sha256(bytes(buf)).hexdigest()[:16])


with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Cursors") as key:
    try:
        base = winreg.QueryValueEx(key, "CursorBaseSize")[0]
    except OSError:
        base = 0
    arrow_path = winreg.QueryValueEx(key, "Arrow")[0]
print("registry CursorBaseSize =", base, "| Arrow =", arrow_path)

sys_arrow = user32.LoadCursorW(None, ctypes.c_wchar_p(OCR_ARROW))
print("system arrow       :", hash_cursor(sys_arrow))
file_arrow = user32.LoadImageW(None, arrow_path, IMAGE_CURSOR, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
print("registry arrow file:", hash_cursor(file_arrow))

ok = user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)
print("SPI_SETCURSORS ->", bool(ok), "err=", ctypes.get_last_error())
sys_arrow2 = user32.LoadCursorW(None, ctypes.c_wchar_p(OCR_ARROW))
print("system arrow after :", hash_cursor(sys_arrow2))

if hash_cursor(sys_arrow2)[2] != hash_cursor(file_arrow)[2]:
    print("仍不一致 -> 用 SetSystemCursor 单独替换箭头")
    h = user32.LoadImageW(None, arrow_path, IMAGE_CURSOR, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
    r = user32.SetSystemCursor(h, OCR_ARROW)
    print("  SetSystemCursor ->", bool(r), "err=", ctypes.get_last_error())
    sys_arrow3 = user32.LoadCursorW(None, ctypes.c_wchar_p(OCR_ARROW))
    print("  system arrow now:", hash_cursor(sys_arrow3))

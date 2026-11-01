"""从界面用的矢量 logo 生成 Windows 图标（icon.ico + icon-256.png）。

不依赖 Pillow：直接用 Qt 渲染，再手写 ICO（BMP DIB 32bpp）格式。
运行：python desktop/make_icon.py
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from inputsharex.ui import icons  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)
OUT_DIR = Path(__file__).resolve().parent


def _image(size: int) -> QImage:
    pixmap = icons.logo_pixmap(size, 1.0)
    return pixmap.toImage().convertToFormat(QImage.Format_ARGB32)


def _bmp_dib(image: QImage) -> bytes:
    """32bpp BMP DIB（含 AND mask），供 ICO 使用。"""
    width, height = image.width(), image.height()
    header = struct.pack(
        "<IiiHHIIiiII",
        40,          # biSize
        width,
        height * 2,  # XOR + AND
        1,           # planes
        32,          # bit count
        0,           # BI_RGB
        width * height * 4 + ((width + 31) // 32) * 4 * height,
        0, 0, 0, 0,
    )
    pixels = bytearray()
    for y in range(height - 1, -1, -1):  # BMP 自下而上
        for x in range(width):
            argb = image.pixel(x, y)
            alpha = (argb >> 24) & 0xFF
            red = (argb >> 16) & 0xFF
            green = (argb >> 8) & 0xFF
            blue = argb & 0xFF
            pixels += bytes((blue, green, red, alpha))
    mask_row = ((width + 31) // 32) * 4
    pixels += b"\x00" * (mask_row * height)
    return header + bytes(pixels)


def build_ico(path: Path) -> None:
    images = [_bmp_dib(_image(size)) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(SIZES))
    offset = 6 + 16 * len(SIZES)
    entries = bytearray()
    for size, data in zip(SIZES, images):
        entries += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,
            size if size < 256 else 0,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        offset += len(data)
    path.write_bytes(header + bytes(entries) + b"".join(images))
    print(f"[icon] {path} ({path.stat().st_size} bytes, {len(SIZES)} sizes)")


def build_png(path: Path) -> None:
    image = _image(256)
    if not image.save(str(path), "PNG"):
        raise RuntimeError(f"无法写入 {path}")
    print(f"[icon] {path}")


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    if not isinstance(app, QGuiApplication):
        raise RuntimeError("无法创建 QGuiApplication")
    build_ico(OUT_DIR / "icon.ico")
    build_png(OUT_DIR / "icon-256.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

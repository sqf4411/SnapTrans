"""生成 assets/icon.ico（多尺寸）。打包前运行一次即可：python tools/make_icon.py"""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

SIZES = [16, 24, 32, 48, 64, 128, 256]


def render(size: int) -> bytes:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    m = size / 32
    p.setBrush(QColor(0, 120, 215))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(m, m, size - 2 * m, size - 2 * m, size * 0.22, size * 0.22)
    f = QFont("Microsoft YaHei")
    f.setBold(True)
    f.setPixelSize(round(size * 0.62))
    p.setFont(f)
    p.setPen(Qt.white)
    p.drawText(img.rect(), Qt.AlignCenter, "译")
    p.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def main() -> None:
    app = QGuiApplication(sys.argv)  # noqa: F841  字体渲染需要
    images = [render(s) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for s, png in zip(SIZES, images):
        dim = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    out = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"
    out.write_bytes(header + entries + blobs)
    print("wrote", out)


if __name__ == "__main__":
    main()

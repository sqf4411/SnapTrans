"""可靠的剪贴板写入。

Qt 的 QClipboard 在 Windows 上用 OLE 延迟渲染：真正的数据要等别的程序粘贴时再回调本程序生成，
而且 OleSetClipboard 遇到剪贴板被其他程序（剪贴板历史、输入法、远程桌面等）短暂占用时会直接失败。
表现就是“复制了截图，粘贴出来却是之前的文字”。

这里直接调用 Win32 API：打开剪贴板失败会重试，数据立即写入（CF_DIB + PNG / CF_UNICODETEXT），
不依赖本程序主线程是否空闲。

另外维护一个“复制序号”：用户每次主动复制都会 +1。后台任务（识字自动复制、翻译完成自动复制）
在开始时记下序号，完成时如果用户在此期间又主动复制过别的内容，就不再覆盖剪贴板。
"""

import ctypes
import time
from ctypes import wintypes

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.SetClipboardData.restype = wintypes.HANDLE
_user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
_user32.RegisterClipboardFormatW.restype = wintypes.UINT
_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalFree.restype = wintypes.HGLOBAL

CF_UNICODETEXT = 13
CF_DIB = 8
GMEM_MOVEABLE = 0x0002

_seq = 0  # 用户主动复制的次数


def token() -> int:
    """后台任务开始时调用，记下当前复制序号。"""
    return _seq


def _bump() -> None:
    global _seq
    _seq += 1


def _encode(image: QImage, fmt: str) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    image.save(buf, fmt)
    return bytes(data)


def _global(data: bytes):
    h = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        raise OSError("GlobalAlloc failed")
    p = _kernel32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    _kernel32.GlobalUnlock(h)
    return h


def _open(retries: int = 40, delay: float = 0.025) -> bool:
    for _ in range(retries):  # 最多等约 1 秒
        if _user32.OpenClipboard(None):
            return True
        time.sleep(delay)
    return False


def _write(items: list[tuple[int, bytes]]) -> bool:
    if not _open():
        return False
    try:
        if not _user32.EmptyClipboard():
            return False
        ok = False
        for fmt, data in items:
            h = _global(data)
            if _user32.SetClipboardData(fmt, h):
                ok = True  # 成功后内存归系统所有
            else:
                _kernel32.GlobalFree(h)
        return ok
    finally:
        _user32.CloseClipboard()


def set_image(image: QImage) -> bool:
    """用户主动复制图片。"""
    _bump()
    if image.isNull():
        return False
    img = image.convertToFormat(QImage.Format_RGB32)
    img.setDevicePixelRatio(1.0)
    try:
        dib = _encode(img, "BMP")[14:]  # 去掉 14 字节的 BITMAPFILEHEADER 就是 CF_DIB
        png = _encode(img, "PNG")
        if _write([(CF_DIB, dib), (_user32.RegisterClipboardFormatW("PNG"), png)]):
            return True
    except Exception:
        pass
    QApplication.clipboard().setImage(image)  # 兜底
    return False


def set_text(text: str, since: int | None = None) -> bool:
    """复制文字。since 不为空时表示后台自动复制：用户之后又主动复制过，就跳过。"""
    if not text:
        return False
    if since is not None:
        if since != _seq:
            return False
    else:
        _bump()
    try:
        if _write([(CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))]):
            return True
    except Exception:
        pass
    QApplication.clipboard().setText(text)
    return False

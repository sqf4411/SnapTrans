"""可靠的剪贴板写入。

Qt 的 QClipboard 在 Windows 上用 OLE 延迟渲染：真正的数据要等别的程序粘贴时再回调本程序生成，
而且 OleSetClipboard 遇到剪贴板被其他程序（剪贴板历史、输入法、远程桌面等）短暂占用时会直接失败。
表现就是“复制了截图，粘贴出来却是之前的文字”。

这里直接调用 Win32 API：打开剪贴板失败会重试，数据立即写入（CF_DIB + PNG / CF_UNICODETEXT），
不依赖本程序主线程是否空闲。

注意：OpenClipboard 必须传一个真实窗口作为剪贴板所有者。传 NULL 时 EmptyClipboard 会把所有者设为 NULL，
按微软文档“这会导致 SetClipboardData 失败”——结果就是剪贴板被清空但没写进去（粘贴不出东西），
再走 Qt 兜底时又可能失败（粘贴出来还是旧内容）。所以这里创建一个隐藏的 message-only 窗口专门做所有者。

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
_user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
_user32.CreateWindowExW.restype = wintypes.HWND
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL

CF_UNICODETEXT = 13
CF_DIB = 8
GMEM_MOVEABLE = 0x0002
HWND_MESSAGE = wintypes.HWND(-3)

_seq = 0  # 用户主动复制的次数
_owner_hwnd = None


def _owner():
    """剪贴板所有者窗口（在主线程首次复制时创建，之后复用）。创建失败时返回 None。"""
    global _owner_hwnd
    if not (_owner_hwnd and _user32.IsWindow(_owner_hwnd)):
        # 系统自带的 STATIC 窗口类，不需要注册窗口过程；消息由 Qt 的事件循环顺带分发
        _owner_hwnd = _user32.CreateWindowExW(0, "STATIC", "SnapTrans Clipboard", 0, 0, 0, 0, 0,
                                              HWND_MESSAGE, None, None, None) or None
    return _owner_hwnd


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
    hwnd = _owner()
    for _ in range(retries):  # 剪贴板被其他程序短暂占用时重试，最多等约 1 秒
        if _user32.OpenClipboard(hwnd):
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
    """用户主动复制图片。返回 False 表示图片为空，没有复制。"""
    if image.isNull():
        return False
    _bump()
    img = image.convertToFormat(QImage.Format_RGB32)
    img.setDevicePixelRatio(1.0)
    try:
        bmp = _encode(img, "BMP")
        png = _encode(img, "PNG")
        if len(bmp) > 14 and _write([(CF_DIB, bmp[14:]),  # 去掉 14 字节的 BITMAPFILEHEADER 就是 CF_DIB
                                     (_user32.RegisterClipboardFormatW("PNG"), png)]):
            return True
    except Exception:
        pass
    QApplication.clipboard().setImage(img)  # 兜底
    return True


def set_text(text: str, since: int | None = None) -> bool:
    """复制文字。since 不为空时表示后台自动复制：用户之后又主动复制过，就跳过。

    返回 True 表示已写入剪贴板，False 表示文字为空或被跳过。
    """
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
    QApplication.clipboard().setText(text)  # 兜底
    return True

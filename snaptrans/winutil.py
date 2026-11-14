"""Windows 辅助：枚举可见窗口（截图时自动识别窗口）、显示器物理坐标。"""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QRect

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_dwmapi = ctypes.WinDLL("dwmapi")

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                     ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

_user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, MONITORENUMPROC, wintypes.LPARAM]
_user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
_dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


def _qrect(r: wintypes.RECT) -> QRect:
    return QRect(r.left, r.top, r.right - r.left, r.bottom - r.top)


def monitor_rects() -> dict[str, QRect]:
    """显示器设备名（与 QScreen.name() 相同）→ 物理像素矩形。"""
    out: dict[str, QRect] = {}

    def cb(hmon, _hdc, _rect, _lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if _user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            out[info.szDevice] = _qrect(info.rcMonitor)
        return True

    _user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    return out


def window_rects() -> list[QRect]:
    """所有可见顶层窗口的物理像素矩形，按 Z 序从上到下。"""
    rects: list[QRect] = []

    def cb(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
            return True
        if _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TRANSPARENT:
            return True
        cloaked = wintypes.DWORD(0)
        _dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True
        r = wintypes.RECT()
        # 扩展边框不含 Win10/11 的透明阴影，比 GetWindowRect 更贴合可见窗口
        if _dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)) != 0:
            if not _user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return True
        q = _qrect(r)
        if q.width() >= 8 and q.height() >= 8:
            rects.append(q)
        return True

    _user32.EnumWindows(WNDENUMPROC(cb), 0)
    return rects

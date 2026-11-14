"""Windows 全局快捷键（RegisterHotKey + Qt 原生事件过滤器）。"""

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication

user32 = ctypes.windll.user32
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]

WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

_SPECIAL_KEYS = {
    "space": 0x20, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "return": 0x0D, "enter": 0x0D,
    "ins": 0x2D, "insert": 0x2D, "del": 0x2E, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "pgup": 0x21, "pgdown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "print": 0x2C, "pause": 0x13, "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
    "\\": 0xDC, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}


def parse_hotkey(text: str) -> tuple[int, int]:
    """把 "Ctrl+Alt+Q" 这类字符串解析为 (modifiers, virtual key)。"""
    mods, vk = 0, None
    for part in (p.strip() for p in text.split("+")):
        low = part.lower()
        if low in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif low == "alt":
            mods |= MOD_ALT
        elif low == "shift":
            mods |= MOD_SHIFT
        elif low in ("meta", "win"):
            mods |= MOD_WIN
        elif len(part) == 1 and part.isascii() and part.isalnum():
            vk = ord(part.upper())
        elif low.startswith("f") and low[1:].isdigit() and 1 <= int(low[1:]) <= 24:
            vk = 0x70 + int(low[1:]) - 1
        elif low in _SPECIAL_KEYS:
            vk = _SPECIAL_KEYS[low]
        else:
            raise ValueError(f"无法识别的按键: {part}")
    if vk is None:
        raise ValueError("快捷键缺少主键")
    return mods, vk


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, manager):
        super().__init__()
        self._manager = manager

    def nativeEventFilter(self, event_type, message):
        if event_type.data() == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                callback = self._manager._callbacks.get(msg.wParam)
                if callback:
                    callback()
                    return True, 0
        return False, 0


class HotkeyManager:
    def __init__(self):
        self._callbacks: dict[int, callable] = {}
        self._next_id = 1
        self._filter = _Filter(self)
        QCoreApplication.instance().installNativeEventFilter(self._filter)

    def register(self, text: str, callback) -> None:
        """注册快捷键；失败时抛出 ValueError（格式错误）或 OSError（被占用）。"""
        mods, vk = parse_hotkey(text)
        hid = self._next_id
        self._next_id += 1
        if not user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
            raise OSError(f"快捷键 {text} 已被其他程序占用")
        self._callbacks[hid] = callback

    def unregister_all(self) -> None:
        for hid in self._callbacks:
            user32.UnregisterHotKey(None, hid)
        self._callbacks.clear()

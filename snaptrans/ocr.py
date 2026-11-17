"""OCR 引擎：Windows 系统自带 OCR（离线、最快）和 OpenAI 兼容视觉模型（如 DeepSeek-OCR）。"""

import asyncio
import base64
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage, QPainter

from . import net

# 中日文字符之间的空格（Windows OCR 会把每个汉字当成一个单词，用空格隔开）
_CJK = r"[　-〿぀-ヿ㐀-䶿一-鿿豈-﫿＀-￯]"
_CJK_SPACE = re.compile(rf"(?<={_CJK})\s+(?={_CJK})")


def clean_text(text: str) -> str:
    return _CJK_SPACE.sub("", text).strip()


class OcrError(Exception):
    pass


# ---------------------------------------------------------------- Windows OCR

_engine_lock = threading.Lock()
_engine_cache: dict[str, object] = {}
_recognize_locks: dict[str, threading.Lock] = {}  # 同一个 OcrEngine 不允许并发识别，不同语言的引擎可以并行
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ocr")

# 可以一键安装的 Windows OCR 语言包（Windows 设置 → 语言 → 可选功能 中的“光学字符识别”）
INSTALLABLE = [
    ("ko-KR", "한국어 韩语"), ("ja-JP", "日本語 日语"), ("en-US", "English 英语"), ("zh-TW", "繁體中文"),
    ("zh-CN", "简体中文"), ("ru-RU", "Русский 俄语"), ("fr-FR", "Français 法语"), ("de-DE", "Deutsch 德语"),
    ("es-ES", "Español 西班牙语"), ("it-IT", "Italiano 意大利语"), ("pt-BR", "Português 葡萄牙语"),
]


def windows_languages() -> list[str]:
    try:
        from winrt.windows.media.ocr import OcrEngine
        return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]
    except Exception:
        return []


def language_name(tag: str) -> str:
    try:
        from winrt.windows.globalization import Language
        return f"{Language(tag).display_name} ({tag})"
    except Exception:
        return tag


def reset_engines() -> None:
    """安装了新的语言包后调用，重新创建引擎。"""
    with _engine_lock:
        _engine_cache.clear()


def _windows_engine(lang: str):
    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine

    with _engine_lock:
        if lang not in _engine_cache:
            if lang:
                engine = OcrEngine.try_create_from_language(Language(lang))
            else:
                engine = OcrEngine.try_create_from_user_profile_languages()
            if engine is None:
                raise OcrError(f"无法创建 Windows OCR 引擎（{lang or '系统语言'}），"
                               "请在 设置 → OCR 中点击“安装 OCR 语言包”")
            _engine_cache[lang] = engine
            _recognize_locks.setdefault(lang, threading.Lock())
        return _engine_cache[lang]


_TARGET_LINE_HEIGHT = 26  # 放大后的文字行高（像素）；实测 Windows OCR 在这个尺度附近最准
_PADDING = 8              # 选区通常紧贴文字，加一圈背景色边距能明显提高识别率


def estimate_line_height(image: QImage) -> float | None:
    """用水平投影估算文字行高：有高对比度像素的连续行构成一行文字。"""
    try:
        import numpy as np
    except ImportError:
        return None
    g = image.convertToFormat(QImage.Format_Grayscale8)
    a = np.frombuffer(g.constBits(), np.uint8).reshape(g.height(), g.bytesPerLine())[:, :g.width()]
    ink = (a.max(axis=1).astype(np.int16) - a.min(axis=1)) > 60
    # 计算连续 True 段的长度
    padded = np.concatenate(([False], ink, [False])).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    runs = edges[1::2] - edges[::2]
    runs = runs[runs >= 3]
    return float(np.median(runs)) if runs.size else None


def _prepare(image: QImage, max_dim: int) -> QImage:
    """按文字行高自适应缩放（小字放大、大字不动），并补边距。"""
    image = image.convertToFormat(QImage.Format_ARGB32)
    padded = QImage(image.width() + 2 * _PADDING, image.height() + 2 * _PADDING, QImage.Format_ARGB32)
    padded.fill(image.pixelColor(0, 0))
    p = QPainter(padded)
    p.drawImage(_PADDING, _PADDING, image)
    p.end()

    line_h = estimate_line_height(image)
    if line_h:
        scale = min(3.0, max(1.0, _TARGET_LINE_HEIGHT / line_h))
    else:
        scale = 2.0 if image.height() < 300 else 1.0
    w, h = padded.width(), padded.height()
    if max(w, h) * scale > max_dim:
        scale = max_dim / max(w, h)
    if scale != 1.0:
        padded = padded.scaled(round(w * scale), round(h * scale), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return padded.convertToFormat(QImage.Format_ARGB32)  # 小端内存布局即 BGRA8


def _windows_recognize(img: QImage, lang: str) -> str:
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    engine = _windows_engine(lang)
    writer = DataWriter()
    writer.write_bytes(bytes(img.constBits()))
    bitmap = SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.BGRA8, img.width(), img.height()
    )

    async def recognize():
        return await engine.recognize_async(bitmap)

    with _recognize_locks.setdefault(lang, threading.Lock()):
        result = asyncio.run(recognize())
    # 韩文、英文等单词之间要保留空格；中日文的多余空格由 clean_text 去掉
    lines = [" ".join(w.text for w in line.words) for line in result.lines]
    return clean_text("\n".join(lines))


def _mostly_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isascii() for c in letters) / len(letters) > 0.9


_PUNCT = set(",.!?;:'\"()[]-_/%&@#+=*，。！？；：、“”‘’（）【】《》…·~—")


def _char_score(c: str) -> float:
    """单个字符的“可信度”：常用字得分高，用错语言引擎识别出的生僻字 / 怪符号得分低甚至扣分。"""
    o = ord(c)
    if c.isspace():
        return 0.0
    if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:  # 汉字
        try:
            b = c.encode("gb2312")
            return 2.0 if 0xB0 <= b[0] <= 0xD7 else 0.8   # GB2312 一级常用字 / 二级次常用字
        except UnicodeEncodeError:
            pass
        for enc, score in (("big5", 1.5), ("shift_jis", 1.2)):  # 繁体 / 日文汉字
            try:
                c.encode(enc)
                return score
            except UnicodeEncodeError:
                pass
        return 0.3
    if 0xAC00 <= o <= 0xD7A3:  # 韩文音节
        try:
            c.encode("euc-kr")  # KS X 1001 常用 2350 字
            return 2.0
        except UnicodeEncodeError:
            return 0.6
    if 0x3040 <= o <= 0x30FF:  # 假名
        return 1.5
    if c.isalpha():
        return 0.6
    if c.isdigit():
        return 0.3
    if c in _PUNCT:
        return 0.1
    return -0.8


def text_score(text: str) -> float:
    return sum(_char_score(c) for c in text)


def _pick_best(results: dict[str, str]) -> str:
    """多个语言引擎的识别结果中选出最可信的一个。键 "" 是系统默认引擎。"""
    scores = {lang: text_score(t) * (1.1 if lang == "" else 1.0) for lang, t in results.items()}
    best = max(scores, key=scores.get)
    # 默认引擎（通常是中文）识别纯英文容易出错：有英文引擎时优先用它
    if best == "" and _mostly_latin(results[""]):
        en = next((lang for lang in results if lang.startswith("en") and results[lang]), None)
        if en:
            best = en
    return results[best]


def windows_ocr(image: QImage, lang: str = "") -> str:
    """lang 为空（自动）时，用所有已安装的 OCR 语言并行识别，挑最可信的结果。"""
    from winrt.windows.media.ocr import OcrEngine

    img = _prepare(image, OcrEngine.max_image_dimension)
    if lang:
        return _windows_recognize(img, lang)
    default = _windows_engine("").recognizer_language.language_tag
    others = [t for t in windows_languages() if t != default]
    if not others:
        return _windows_recognize(img, "")
    futures = {t: _pool.submit(_windows_recognize, img, t) for t in ["", *others]}
    results = {}
    for t, f in futures.items():
        try:
            results[t] = f.result()
        except Exception:
            if t == "":
                raise
    return _pick_best(results)


def warmup_windows(lang: str = "") -> None:
    try:
        _windows_engine(lang)
        if not lang:  # 自动模式会用到所有已安装的语言
            for t in windows_languages():
                _windows_engine(t)
    except Exception:
        pass


def install_languages(tags: list[str]) -> bool:
    """以管理员身份打开 PowerShell 安装 OCR 语言包，阻塞到安装窗口关闭。返回是否成功启动。

    需要联网；会弹出 Windows 的 UAC 确认框。请在后台线程调用。
    """
    import ctypes
    from ctypes import wintypes

    if not tags or not all(re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,4})*", t) for t in tags):
        return False
    quoted = ",".join(f"'{t}'" for t in tags)
    script = (
        "$ErrorActionPreference='Continue'; "
        f"foreach($t in @({quoted})){{ "
        "Write-Host \"Installing OCR language pack: $t ...\"; "
        "$caps = Get-WindowsCapability -Online | Where-Object { $_.Name -like \"Language.OCR~~~$t~*\" }; "
        "if(-not $caps){ Write-Host \"  not found: $t\" -ForegroundColor Yellow; continue }; "
        "foreach($c in $caps){ if($c.State -eq 'Installed'){ Write-Host '  already installed' } "
        "else { Add-WindowsCapability -Online -Name $c.Name | Out-Null; Write-Host '  done' -ForegroundColor Green } } }; "
        "Write-Host ''; Write-Host 'Finished. This window will close in 5 seconds.'; Start-Sleep 5"
    )

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
                    ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                    ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = "powershell.exe"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    info.lpParameters = f"-NoProfile -ExecutionPolicy Bypass -EncodedCommand {encoded}"
    info.nShow = 1
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    if not shell32.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
        return False  # 用户在 UAC 中点了“否”
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
    kernel32.CloseHandle(info.hProcess)
    reset_engines()
    return True


# ---------------------------------------------------------------- 在线视觉模型

def image_to_data_url(image: QImage) -> str:
    # 视觉模型对超大图很慢，限制长边
    if max(image.width(), image.height()) > 2000:
        image = image.scaled(2000, 2000, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(bytes(data)).decode()


def api_ocr(image: QImage, cfg: dict) -> str:
    if not cfg.get("api_key") and "localhost" not in cfg.get("base_url", ""):
        raise OcrError("未填写在线 OCR 的 API Key，请在设置中填写，或改用 Windows 本地 OCR")
    messages = [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": image_to_data_url(image)}},
            {"type": "text", "text": cfg.get("prompt") or "Free OCR."},
        ],
    }]
    try:
        text = net.chat(cfg["base_url"], cfg["api_key"], cfg["model"], messages, temperature=0)
    except net.ApiError as e:
        raise OcrError(f"在线 OCR 失败：{e}") from e
    # DeepSeek-OCR 可能输出带定位标记的内容，去掉
    text = re.sub(r"<\|ref\|>.*?<\|/ref\|><\|det\|>.*?<\|/det\|>", "", text, flags=re.S)
    return clean_text(text)


def recognize(image: QImage, ocr_cfg: dict) -> str:
    if ocr_cfg.get("engine") == "api":
        return api_ocr(image, ocr_cfg)
    try:
        return windows_ocr(image, ocr_cfg.get("windows_lang", ""))
    except OcrError:
        raise
    except Exception as e:
        raise OcrError(f"Windows OCR 失败：{e}") from e

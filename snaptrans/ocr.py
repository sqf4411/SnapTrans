"""OCR 引擎：Windows 系统自带 OCR（离线、最快）和 OpenAI 兼容视觉模型（如 DeepSeek-OCR）。"""

import asyncio
import base64
import re
import threading

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


def windows_languages() -> list[str]:
    try:
        from winrt.windows.media.ocr import OcrEngine
        return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]
    except Exception:
        return []


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
                raise OcrError("无法创建 Windows OCR 引擎，请在 Windows 设置 → 语言 中安装对应语言的「光学字符识别」组件")
            _engine_cache[lang] = engine
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

    result = asyncio.run(recognize())
    lines = [" ".join(w.text for w in line.words) for line in result.lines]
    return clean_text("\n".join(lines))


def _mostly_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(c.isascii() for c in letters) / len(letters) > 0.9


def windows_ocr(image: QImage, lang: str = "") -> str:
    from winrt.windows.media.ocr import OcrEngine

    img = _prepare(image, OcrEngine.max_image_dimension)
    text = _windows_recognize(img, lang)
    # 自动模式下，中文引擎识别纯英文时容易出错；如果装了英文 OCR 包就用它再识别一次
    if not lang and _mostly_latin(text):
        engine_lang = _windows_engine("").recognizer_language.language_tag
        if not engine_lang.startswith("en"):
            en = next((t for t in windows_languages() if t.startswith("en")), None)
            if en:
                text = _windows_recognize(img, en)
    return text


def warmup_windows(lang: str = "") -> None:
    try:
        _windows_engine(lang)
    except Exception:
        pass


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

"""翻译：调用 OpenAI 兼容的大模型接口，流式返回。"""

import re
import threading

from . import net

LANG_NAMES_EN = {
    "简体中文": "Simplified Chinese", "繁體中文": "Traditional Chinese", "English": "English",
    "日本語": "Japanese", "한국어": "Korean", "Français": "French", "Deutsch": "German",
    "Español": "Spanish", "Русский": "Russian",
}

_HAN = re.compile(r"[一-鿿]")
_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯]")
_LATIN = re.compile(r"[A-Za-z]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def guess_is_target(text: str, target: str) -> bool:
    """粗略判断原文是否已经是目标语言（用于中英互译的自动切换）。"""
    counts = {
        "han": len(_HAN.findall(text)), "kana": len(_KANA.findall(text)),
        "hangul": len(_HANGUL.findall(text)), "latin": len(_LATIN.findall(text)),
        "cyrillic": len(_CYRILLIC.findall(text)),
    }
    total = sum(counts.values()) or 1
    if target in ("简体中文", "繁體中文"):
        return counts["kana"] == 0 and counts["han"] / total > 0.5
    if target == "English":
        return counts["latin"] / total > 0.8
    if target == "日本語":
        return counts["kana"] > 0
    if target == "한국어":
        return counts["hangul"] / total > 0.5
    if target == "Русский":
        return counts["cyrillic"] / total > 0.5
    return False


def pick_target(text: str, cfg: dict) -> str:
    target = cfg["target_lang"]
    if cfg.get("alt_lang") and cfg["alt_lang"] != target and guess_is_target(text, target):
        return cfg["alt_lang"]
    return target


def build_messages(text: str, target: str) -> list:
    lang = LANG_NAMES_EN.get(target, target)
    system = (
        f"You are a professional translation engine. Translate the user's text into {lang}.\n"
        "The text comes from OCR of a screenshot: sentences may be broken across lines and may contain "
        "minor recognition errors. Merge broken lines into natural paragraphs, silently fix obvious OCR "
        "errors, keep code, URLs, numbers and proper nouns as appropriate.\n"
        "Output ONLY the translation, with no explanations, notes or quotes."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": text}]


def translate_stream(text: str, target: str, tcfg: dict, cancel: threading.Event | None = None):
    if not tcfg.get("api_key") and "localhost" not in tcfg.get("base_url", ""):
        raise net.ApiError("未填写翻译服务的 API Key，请在主窗口 → 设置 → 翻译 中填写")
    yield from net.chat_stream(
        tcfg["base_url"], tcfg["api_key"], tcfg["model"], build_messages(text, target),
        temperature=float(tcfg.get("temperature", 0.3)), cancel=cancel,
    )

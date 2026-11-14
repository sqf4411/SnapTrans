"""配置读写。配置文件保存在 %APPDATA%/SnapTrans/config.json。"""

import copy
import json
import os
from pathlib import Path

APP_NAME = "SnapTrans"
CONFIG_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"

# 翻译服务预设：都走 OpenAI 兼容的 /chat/completions 接口
TRANSLATE_PRESETS = {
    "DeepSeek": {"base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    "硅基流动 SiliconFlow": {"base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3"},
    "通义千问 DashScope": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-turbo"},
    "OpenAI": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "Ollama 本地": {"base_url": "http://localhost:11434/v1", "model": "qwen2.5:7b"},
    "自定义": None,
}

# 在线 OCR 预设：OpenAI 兼容的视觉模型接口
OCR_PRESETS = {
    "硅基流动 · DeepSeek-OCR": {
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-OCR",
        "prompt": "Free OCR.",
    },
    "通义千问 · qwen-vl-ocr": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-vl-ocr",
        "prompt": "Read all the text in the image. Output plain text only.",
    },
    "OpenAI · gpt-4o-mini": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "prompt": "Read all the text in the image and output it as plain text, keeping line breaks. Output nothing else.",
    },
    "自定义": None,
}

LANGUAGES = ["简体中文", "繁體中文", "English", "日本語", "한국어", "Français", "Deutsch", "Español", "Русский"]

DEFAULTS = {
    "hotkey": "Alt+Q",          # 截图翻译
    "hotkey_ocr": "Alt+W",      # 截图 OCR（只识别并复制文字）
    "target_lang": "简体中文",
    "alt_lang": "English",      # 原文已经是目标语言时，改为翻译成这个语言
    "autostart": False,
    "copy_translation": False,  # 翻译完成后自动复制译文
    "history_enabled": True,    # 保存截图记录
    "history_limit": 200,       # 最多保留条数
    "hide_on_snip": True,       # 截图前隐藏主窗口
    "close_to_tray": True,      # 关闭主窗口时最小化到托盘
    "ocr": {
        "engine": "windows",    # windows | api
        "windows_lang": "",     # 空 = 跟随系统
        "preset": "硅基流动 · DeepSeek-OCR",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key": "",
        "model": "deepseek-ai/DeepSeek-OCR",
        "prompt": "Free OCR.",
    },
    "translate": {
        "enabled": True,
        "preset": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "model": "deepseek-chat",
        "temperature": 0.3,
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


def load() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return _merge(DEFAULTS, json.load(f))
    except (OSError, ValueError):
        return copy.deepcopy(DEFAULTS)


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def is_first_run() -> bool:
    return not CONFIG_PATH.exists()

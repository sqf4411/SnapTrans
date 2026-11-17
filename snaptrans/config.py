"""配置读写。

数据目录默认是 %APPDATA%/SnapTrans（配置 config.json + 截图数据库 history.db）。
便携模式：程序目录（exe 所在文件夹 / 源码根目录）下存在 data 文件夹时，改为保存到这个 data 文件夹，
方便把整个文件夹拷到 U 盘或别的电脑上连同记录一起使用。
"""

import copy
import json
import os
import sys
from pathlib import Path

APP_NAME = "SnapTrans"


def app_dir() -> Path:
    """exe 所在目录（打包后）或项目根目录（源码运行）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    portable = app_dir() / "data"
    if portable.is_dir():
        return portable
    return Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME


CONFIG_DIR = _data_dir()
PORTABLE = CONFIG_DIR.parent == app_dir()
CONFIG_PATH = CONFIG_DIR / "config.json"
CONFIG_VERSION = 2

# 旧版默认快捷键 → 新版默认快捷键（用户没改过的才迁移）
_OLD_HOTKEYS = {"hotkey_capture": "Alt+S", "hotkey": "Alt+Q", "hotkey_ocr": "Alt+W"}

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
    "config_version": CONFIG_VERSION,
    "hotkey_capture": "Ctrl+`",  # 普通截图（标注 / 贴图）
    "hotkey": "Ctrl+1",          # 截图翻译
    "hotkey_ocr": "Ctrl+2",      # 截图 OCR（只识别并复制文字）
    "target_lang": "简体中文",
    "alt_lang": "English",      # 原文已经是目标语言时，改为翻译成这个语言
    "autostart": False,
    "copy_translation": False,  # 翻译完成后自动复制译文
    "translate_display": "bubble",  # bubble：贴在截图旁边；window：弹出窗口
    "bubble_font_size": 11,
    "save_dir": "",             # 上次保存截图的文件夹
    "history_enabled": True,    # 保存截图记录
    "history_limit": 200,       # 最多保留条数
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


def _migrate(raw: dict) -> dict:
    if int(raw.get("config_version", 1)) < 2:
        for key, old in _OLD_HOTKEYS.items():
            if raw.get(key, old) == old:  # 还是旧默认值 → 换成新默认值
                raw[key] = DEFAULTS[key]
        raw["config_version"] = 2
    return raw


def load() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return copy.deepcopy(DEFAULTS)
    if not isinstance(raw, dict):
        return copy.deepcopy(DEFAULTS)
    return _merge(DEFAULTS, _migrate(raw))


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def is_first_run() -> bool:
    return not CONFIG_PATH.exists()

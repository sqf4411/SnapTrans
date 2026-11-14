"""设置窗口：快捷键、OCR 引擎、翻译服务商（API Key / 地址 / 模型）。"""

import copy
import threading
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QPushButton, QRadioButton, QTabWidget,
    QVBoxLayout, QWidget,
)

from . import __version__, config, net, ocr, translator


class _Bridge(QObject):
    result = Signal(object, str)  # 目标标签, 文本


def _key_edit(text: str) -> QKeySequenceEdit:
    edit = QKeySequenceEdit(QKeySequence(text))
    if hasattr(edit, "setMaximumSequenceLength"):
        edit.setMaximumSequenceLength(1)
    if hasattr(edit, "setClearButtonEnabled"):
        edit.setClearButtonEnabled(True)
    return edit


def _secret_edit(text: str) -> QWidget:
    edit = QLineEdit(text)
    edit.setEchoMode(QLineEdit.Password)
    edit.setPlaceholderText("sk-...")
    toggle = QCheckBox("显示")
    toggle.toggled.connect(lambda on: edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(edit, 1)
    lay.addWidget(toggle)
    box.edit = edit
    return box


def _sample_image() -> QImage:
    img = QImage(520, 60, QImage.Format_ARGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    p.setFont(QFont("Segoe UI", 20))
    p.setPen(Qt.black)
    p.drawText(12, 42, "Hello world, OCR test 123")
    p.end()
    return img


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SnapTrans 首选项")
        self.setMinimumWidth(480)
        self.cfg = copy.deepcopy(cfg)
        self._bridge = _Bridge()
        self._bridge.result.connect(lambda label, text: label.setText(text))

        tabs = QTabWidget()
        tabs.addTab(self._general_tab(), "常规")
        tabs.addTab(self._ocr_tab(), "OCR")
        tabs.addTab(self._translate_tab(), "翻译")
        tabs.addTab(self._about_tab(), "关于")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults)
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.button(QDialogButtonBox.RestoreDefaults).setText("恢复默认")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._restore)

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(buttons)

    # ------------------------------------------------------------ 常规

    def _general_tab(self) -> QWidget:
        c = self.cfg
        self.hk_translate = _key_edit(c["hotkey"])
        self.hk_ocr = _key_edit(c["hotkey_ocr"])
        self.target_lang = QComboBox()
        self.target_lang.addItems(config.LANGUAGES)
        self.target_lang.setCurrentText(c["target_lang"])
        self.alt_lang = QComboBox()
        self.alt_lang.addItems(config.LANGUAGES)
        self.alt_lang.setCurrentText(c["alt_lang"])
        self.autostart = QCheckBox("开机启动")
        self.autostart.setChecked(c["autostart"])
        self.copy_translation = QCheckBox("翻译完成后自动复制译文")
        self.copy_translation.setChecked(c["copy_translation"])

        form = QFormLayout()
        form.addRow("截图翻译快捷键：", self.hk_translate)
        form.addRow("截图识字快捷键：", self.hk_ocr)
        form.addRow("目标语言：", self.target_lang)
        form.addRow("原文已是目标语言时译为：", self.alt_lang)
        tip = QLabel("提示：Snipaste 默认占用 F1 / F3，请避免冲突。快捷键只能是单个组合键。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray;")

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addLayout(form)
        lay.addWidget(self.autostart)
        lay.addWidget(self.copy_translation)
        lay.addWidget(tip)
        lay.addStretch()
        return w

    # ------------------------------------------------------------ OCR

    def _ocr_tab(self) -> QWidget:
        c = self.cfg["ocr"]
        self.ocr_windows = QRadioButton("Windows 系统 OCR（离线，约 50~150ms，推荐）")
        self.ocr_api = QRadioButton("在线视觉模型（DeepSeek-OCR 等，约 1~3 秒，复杂排版更准）")
        (self.ocr_api if c["engine"] == "api" else self.ocr_windows).setChecked(True)

        self.win_lang = QComboBox()
        self.win_lang.addItem("跟随系统语言", "")
        for tag in ocr.windows_languages():
            self.win_lang.addItem(tag, tag)
        idx = self.win_lang.findData(c["windows_lang"])
        self.win_lang.setCurrentIndex(max(idx, 0))
        win_box = QGroupBox("Windows OCR")
        win_form = QFormLayout(win_box)
        win_form.addRow("识别语言：", self.win_lang)
        hint = QLabel("中文语言包可同时识别中英文。识别日文/韩文等需要在 Windows 设置 → 时间和语言 → "
                      "语言 中添加对应语言（含“光学字符识别”）。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        win_form.addRow(hint)

        self.ocr_preset = QComboBox()
        self.ocr_preset.addItems(config.OCR_PRESETS.keys())
        self.ocr_preset.setCurrentText(c["preset"])
        self.ocr_url = QLineEdit(c["base_url"])
        self.ocr_key = _secret_edit(c["api_key"])
        self.ocr_model = QLineEdit(c["model"])
        self.ocr_prompt = QLineEdit(c["prompt"])
        self.ocr_preset.currentTextChanged.connect(self._apply_ocr_preset)
        self.ocr_test_result = QLabel()
        self.ocr_test_result.setWordWrap(True)
        test = QPushButton("测试 OCR")
        test.clicked.connect(self._test_ocr)

        api_box = QGroupBox("在线视觉模型（OpenAI 兼容接口）")
        api_form = QFormLayout(api_box)
        api_form.addRow("服务商：", self.ocr_preset)
        api_form.addRow("接口地址：", self.ocr_url)
        api_form.addRow("API Key：", self.ocr_key)
        api_form.addRow("模型：", self.ocr_model)
        api_form.addRow("提示词：", self.ocr_prompt)
        note = QLabel("DeepSeek 官方 API 目前没有提供 DeepSeek-OCR，可通过硅基流动等平台调用，"
                      "或用 vLLM 自行部署后填写本地地址。")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        api_form.addRow(note)

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self.ocr_windows)
        lay.addWidget(self.ocr_api)
        lay.addWidget(win_box)
        lay.addWidget(api_box)
        row = QHBoxLayout()
        row.addWidget(test)
        row.addWidget(self.ocr_test_result, 1)
        lay.addLayout(row)
        lay.addStretch()
        return w

    def _apply_ocr_preset(self, name: str) -> None:
        preset = config.OCR_PRESETS.get(name)
        if preset:
            self.ocr_url.setText(preset["base_url"])
            self.ocr_model.setText(preset["model"])
            self.ocr_prompt.setText(preset["prompt"])

    def _test_ocr(self) -> None:
        cfg = self._collect()["ocr"]
        self.ocr_test_result.setText("测试中…")
        image = _sample_image()

        def run():
            t0 = time.perf_counter()
            try:
                text = ocr.recognize(image, cfg)
                msg = f"✔ {round((time.perf_counter() - t0) * 1000)}ms：{text[:80]}"
            except Exception as e:
                msg = f"✘ {e}"
            self._bridge.result.emit(self.ocr_test_result, msg)

        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------ 翻译

    def _translate_tab(self) -> QWidget:
        c = self.cfg["translate"]
        self.tr_enabled = QCheckBox("启用翻译（关闭后截图翻译只做识别）")
        self.tr_enabled.setChecked(c["enabled"])
        self.tr_preset = QComboBox()
        self.tr_preset.addItems(config.TRANSLATE_PRESETS.keys())
        self.tr_preset.setCurrentText(c["preset"])
        self.tr_url = QLineEdit(c["base_url"])
        self.tr_key = _secret_edit(c["api_key"])
        self.tr_model = QLineEdit(c["model"])
        self.tr_temp = QDoubleSpinBox()
        self.tr_temp.setRange(0, 2)
        self.tr_temp.setSingleStep(0.1)
        self.tr_temp.setValue(float(c["temperature"]))
        self.tr_preset.currentTextChanged.connect(self._apply_tr_preset)
        self.tr_test_result = QLabel()
        self.tr_test_result.setWordWrap(True)
        test = QPushButton("测试翻译")
        test.clicked.connect(self._test_translate)

        box = QGroupBox("翻译服务（OpenAI 兼容接口）")
        form = QFormLayout(box)
        form.addRow("服务商：", self.tr_preset)
        form.addRow("接口地址：", self.tr_url)
        form.addRow("API Key：", self.tr_key)
        form.addRow("模型：", self.tr_model)
        form.addRow("温度：", self.tr_temp)
        note = QLabel("DeepSeek API Key 在 platform.deepseek.com 获取。追求速度可选非推理模型"
                      "（如 deepseek-chat、qwen-turbo、gpt-4o-mini），不要选 reasoner/R1 类模型。")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        form.addRow(note)

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self.tr_enabled)
        lay.addWidget(box)
        row = QHBoxLayout()
        row.addWidget(test)
        row.addWidget(self.tr_test_result, 1)
        lay.addLayout(row)
        lay.addStretch()
        return w

    def _apply_tr_preset(self, name: str) -> None:
        preset = config.TRANSLATE_PRESETS.get(name)
        if preset:
            self.tr_url.setText(preset["base_url"])
            self.tr_model.setText(preset["model"])

    def _test_translate(self) -> None:
        cfg = self._collect()
        self.tr_test_result.setText("测试中…")

        def run():
            t0 = time.perf_counter()
            first = None
            out = ""
            try:
                for piece in translator.translate_stream("Hello, world! This is a translation test.",
                                                         cfg["target_lang"], cfg["translate"]):
                    if first is None:
                        first = round((time.perf_counter() - t0) * 1000)
                    out += piece
                total = round((time.perf_counter() - t0) * 1000)
                msg = f"✔ 首字 {first}ms，总计 {total}ms：{out[:80]}"
            except Exception as e:
                msg = f"✘ {e}"
            self._bridge.result.emit(self.tr_test_result, msg)

        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------ 关于

    def _about_tab(self) -> QWidget:
        label = QLabel(
            f"<h3>SnapTrans {__version__}</h3>"
            "<p>截图 OCR 实时翻译工具，可与 Snipaste 搭配使用。</p>"
            "<p>按快捷键 → 框选区域 → 自动识别并翻译。<br>"
            "结果窗口：Esc 关闭，Ctrl+Enter 重新翻译，Ctrl+Shift+C 复制译文。</p>"
            f"<p>配置文件：{config.CONFIG_PATH}</p>"
        )
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(label)
        lay.addStretch()
        return w

    # ------------------------------------------------------------ 保存

    def _collect(self) -> dict:
        c = copy.deepcopy(self.cfg)
        c["hotkey"] = self.hk_translate.keySequence().toString(QKeySequence.PortableText)
        c["hotkey_ocr"] = self.hk_ocr.keySequence().toString(QKeySequence.PortableText)
        c["target_lang"] = self.target_lang.currentText()
        c["alt_lang"] = self.alt_lang.currentText()
        c["autostart"] = self.autostart.isChecked()
        c["copy_translation"] = self.copy_translation.isChecked()
        c["ocr"].update(
            engine="api" if self.ocr_api.isChecked() else "windows",
            windows_lang=self.win_lang.currentData(),
            preset=self.ocr_preset.currentText(),
            base_url=self.ocr_url.text().strip(),
            api_key=self.ocr_key.edit.text().strip(),
            model=self.ocr_model.text().strip(),
            prompt=self.ocr_prompt.text().strip(),
        )
        c["translate"].update(
            enabled=self.tr_enabled.isChecked(),
            preset=self.tr_preset.currentText(),
            base_url=self.tr_url.text().strip(),
            api_key=self.tr_key.edit.text().strip(),
            model=self.tr_model.text().strip(),
            temperature=self.tr_temp.value(),
        )
        return c

    def _accept(self) -> None:
        self.cfg = self._collect()
        self.accept()

    def _restore(self) -> None:
        d = config.DEFAULTS
        self.hk_translate.setKeySequence(QKeySequence(d["hotkey"]))
        self.hk_ocr.setKeySequence(QKeySequence(d["hotkey_ocr"]))
        self.target_lang.setCurrentText(d["target_lang"])
        self.alt_lang.setCurrentText(d["alt_lang"])
        self.copy_translation.setChecked(d["copy_translation"])
        self.ocr_windows.setChecked(True)
        self.win_lang.setCurrentIndex(0)
        self.tr_enabled.setChecked(True)
        self.tr_temp.setValue(d["translate"]["temperature"])
        # API Key 等服务商配置保留，避免误清空

"""设置面板（嵌在主窗口里）：快捷键、OCR 引擎、翻译服务商、截图记录。"""

import copy
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QKeySequenceEdit,
    QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QSpinBox, QTabWidget, QVBoxLayout,
    QWidget,
)

from . import __version__, config, ocr, translator
from .hotkey import parse_hotkey


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


class SettingsPanel(QWidget):
    saved = Signal(dict)
    open_history_dir = Signal()
    clear_history = Signal()

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = copy.deepcopy(cfg)
        self._bridge = _Bridge()
        self._bridge.result.connect(lambda label, text: label.setText(text))

        tabs = QTabWidget()
        tabs.addTab(self._general_tab(), "常规")
        tabs.addTab(self._ocr_tab(), "OCR")
        tabs.addTab(self._translate_tab(), "翻译")
        tabs.addTab(self._history_tab(), "记录")
        tabs.addTab(self._about_tab(), "关于")
        self.tabs = tabs

        self.save_hint = QLabel()
        self.save_hint.setStyleSheet("color: gray;")
        restore = QPushButton("恢复默认")
        restore.clicked.connect(self._restore)
        save = QPushButton("保存设置")
        save.setDefault(True)
        save.clicked.connect(self._save)
        bar = QHBoxLayout()
        bar.addWidget(self.save_hint, 1)
        bar.addWidget(restore)
        bar.addWidget(save)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(tabs)
        lay.addLayout(bar)

    # ------------------------------------------------------------ 常规

    def _general_tab(self) -> QWidget:
        c = self.cfg
        self.hk_capture = _key_edit(c["hotkey_capture"])
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
        self.close_to_tray = QCheckBox("关闭主窗口时最小化到托盘（不退出）")
        self.close_to_tray.setChecked(c["close_to_tray"])

        form = QFormLayout()
        self.display = QComboBox()
        self.display.addItem("贴在截图旁边（不遮挡原文）", "bubble")
        self.display.addItem("弹出翻译窗口", "window")
        self.display.setCurrentIndex(max(0, self.display.findData(c["translate_display"])))
        self.bubble_font = QSpinBox()
        self.bubble_font.setRange(8, 24)
        self.bubble_font.setSuffix(" pt")
        self.bubble_font.setValue(int(c["bubble_font_size"]))

        form.addRow("截图快捷键（标注 / 贴图）：", self.hk_capture)
        form.addRow("截图翻译快捷键：", self.hk_translate)
        form.addRow("截图识字快捷键：", self.hk_ocr)
        form.addRow("译文显示方式：", self.display)
        form.addRow("译文字号：", self.bubble_font)
        form.addRow("目标语言：", self.target_lang)
        form.addRow("原文已是目标语言时译为：", self.alt_lang)
        tip = QLabel("点击快捷键输入框后直接按下新的组合键即可修改，按 × 清空表示不使用。"
                     "Snipaste 默认占用 F1 / F3，请避免冲突。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray;")

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addLayout(form)
        lay.addWidget(self.autostart)
        lay.addWidget(self.copy_translation)
        lay.addWidget(self.close_to_tray)
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

    # ------------------------------------------------------------ 记录

    def _history_tab(self) -> QWidget:
        c = self.cfg
        self.history_enabled = QCheckBox("保存截图记录（截图图片 + 识别文字 + 译文）")
        self.history_enabled.setChecked(c["history_enabled"])
        self.history_limit = QSpinBox()
        self.history_limit.setRange(10, 10000)
        self.history_limit.setSingleStep(50)
        self.history_limit.setValue(int(c["history_limit"]))
        self.history_limit.setSuffix(" 条")
        self.history_usage = QLabel()

        open_dir = QPushButton("打开记录文件夹")
        open_dir.clicked.connect(self.open_history_dir.emit)
        clear = QPushButton("清空全部记录")
        clear.clicked.connect(self._confirm_clear)

        form = QFormLayout()
        form.addRow("最多保留：", self.history_limit)
        form.addRow("已占用：", self.history_usage)
        row = QHBoxLayout()
        row.addWidget(open_dir)
        row.addWidget(clear)
        row.addStretch()
        hint = QLabel("超过上限时自动删除最早的记录。记录保存在："
                      f"<br>{config.CONFIG_DIR / 'history'}")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        hint.setTextInteractionFlags(Qt.TextSelectableByMouse)

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self.history_enabled)
        lay.addLayout(form)
        lay.addLayout(row)
        lay.addWidget(hint)
        lay.addStretch()
        return w

    def set_history_usage(self, count: int, size: int) -> None:
        self.history_usage.setText(f"{count} 条，{size / 1024 / 1024:.1f} MB")

    def _confirm_clear(self) -> None:
        if QMessageBox.question(self, "SnapTrans", "确定清空全部截图记录？此操作不可恢复。") == QMessageBox.Yes:
            self.clear_history.emit()

    # ------------------------------------------------------------ 关于

    def _about_tab(self) -> QWidget:
        label = QLabel(
            f"<h3>SnapTrans {__version__}</h3>"
            "<p>截图 OCR 实时翻译工具。</p>"
            "<p><b>截图界面</b>：拖动框选，单击选中窗口；选好后可拖动 / 调整选区，方向键微调。<br>"
            "标注：R 矩形，E 椭圆，A 箭头，P 画笔，M 荧光笔，T 文字，X 马赛克，Ctrl+Z / Ctrl+Y 撤销 / 重做。<br>"
            "Enter 或双击：复制并完成；Ctrl+P 贴图；Ctrl+T 翻译；Ctrl+O 识字；Ctrl+S 保存；Esc / 右键 取消。</p>"
            "<p><b>贴图</b>：拖动移动，滚轮缩放，Ctrl+滚轮 透明度，<b>, / .</b> 上一张 / 下一张历史截图，"
            "Ctrl+T 翻译，Ctrl+C 复制，双击或 Esc 关闭，右键更多。</p>"
            "<p><b>译文气泡</b>：Esc 关闭，📌 固定，“展开”打开窗口修改原文 / 切换语言。</p>"
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
        c["hotkey_capture"] = self.hk_capture.keySequence().toString(QKeySequence.PortableText)
        c["hotkey"] = self.hk_translate.keySequence().toString(QKeySequence.PortableText)
        c["hotkey_ocr"] = self.hk_ocr.keySequence().toString(QKeySequence.PortableText)
        c["target_lang"] = self.target_lang.currentText()
        c["alt_lang"] = self.alt_lang.currentText()
        c["autostart"] = self.autostart.isChecked()
        c["copy_translation"] = self.copy_translation.isChecked()
        c["translate_display"] = self.display.currentData()
        c["bubble_font_size"] = self.bubble_font.value()
        c["close_to_tray"] = self.close_to_tray.isChecked()
        c["history_enabled"] = self.history_enabled.isChecked()
        c["history_limit"] = self.history_limit.value()
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

    def _save(self) -> None:
        cfg = self._collect()
        used = {}
        for name, key in (("截图", "hotkey_capture"), ("截图翻译", "hotkey"), ("截图识字", "hotkey_ocr")):
            if not cfg[key]:
                continue
            try:
                parse_hotkey(cfg[key])
            except ValueError as e:
                QMessageBox.warning(self, "SnapTrans", f"{name}快捷键无效：{e}")
                return
            if cfg[key] in used:
                QMessageBox.warning(self, "SnapTrans", f"“{name}”和“{used[cfg[key]]}”的快捷键相同：{cfg[key]}")
                return
            used[cfg[key]] = name
        self.cfg = cfg
        self.saved.emit(copy.deepcopy(cfg))
        self.save_hint.setText("✔ 已保存")
        QTimer.singleShot(3000, lambda: self.save_hint.setText(""))

    def _restore(self) -> None:
        d = config.DEFAULTS
        self.hk_capture.setKeySequence(QKeySequence(d["hotkey_capture"]))
        self.hk_translate.setKeySequence(QKeySequence(d["hotkey"]))
        self.hk_ocr.setKeySequence(QKeySequence(d["hotkey_ocr"]))
        self.target_lang.setCurrentText(d["target_lang"])
        self.alt_lang.setCurrentText(d["alt_lang"])
        self.copy_translation.setChecked(d["copy_translation"])
        self.display.setCurrentIndex(self.display.findData(d["translate_display"]))
        self.bubble_font.setValue(d["bubble_font_size"])
        self.close_to_tray.setChecked(d["close_to_tray"])
        self.history_enabled.setChecked(d["history_enabled"])
        self.history_limit.setValue(d["history_limit"])
        self.ocr_windows.setChecked(True)
        self.win_lang.setCurrentIndex(0)
        self.tr_enabled.setChecked(True)
        self.tr_temp.setValue(d["translate"]["temperature"])
        # API Key 等服务商配置保留，避免误清空

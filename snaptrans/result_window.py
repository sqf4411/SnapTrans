"""翻译结果浮窗（类似 Bob 的弹窗）：上方原文可编辑，下方流式显示译文。"""

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QCursor, QFont, QGuiApplication, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QToolButton, QVBoxLayout, QWidget,
)

from . import clipboard
from .config import LANGUAGES
from .worker import Job


class ResultWindow(QWidget):
    # 记录 id, 更新的字段（source / ocr_ms / translation / target / tr_ms）
    result = Signal(str, dict)

    def __init__(self, get_config):
        super().__init__(None, Qt.Tool | Qt.WindowStaysOnTopHint)
        self._get_config = get_config
        self._job: Job | None = None
        self._ocr_ms: int | None = None
        self._translation = ""
        self._record_id: str | None = None
        self.setWindowTitle("SnapTrans 截图翻译")
        self.resize(460, 360)

        font = QFont("Microsoft YaHei UI", 10)

        self.status = QLabel()
        self.status.setStyleSheet("color: gray;")
        self.lang_box = QComboBox()
        self.lang_box.addItems(LANGUAGES)
        self.lang_box.setToolTip("目标语言（本次）")
        self.lang_box.activated.connect(lambda _: self.retranslate(self.lang_box.currentText()))
        self.pin_btn = QToolButton()
        self.pin_btn.setText("📌")
        self.pin_btn.setCheckable(True)
        self.pin_btn.setToolTip("固定窗口（点击其他地方不自动隐藏）")

        top = QHBoxLayout()
        top.addWidget(self.status, 1)
        top.addWidget(self.lang_box)
        top.addWidget(self.pin_btn)

        self.source = QPlainTextEdit()
        self.source.setFont(font)
        self.source.setPlaceholderText("识别的原文（可修改后按 Ctrl+Enter 重新翻译）")
        self.target = QPlainTextEdit()
        self.target.setFont(font)
        self.target.setReadOnly(True)
        self.target.setPlaceholderText("译文")

        def btn(text, slot, tip=""):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        src_bar = QHBoxLayout()
        src_bar.addWidget(QLabel("原文"))
        src_bar.addStretch()
        src_bar.addWidget(btn("复制原文", lambda: self._copy(self.source.toPlainText())))
        src_bar.addWidget(btn("翻译", lambda: self.retranslate(), "Ctrl+Enter"))
        dst_bar = QHBoxLayout()
        dst_bar.addWidget(QLabel("译文"))
        dst_bar.addStretch()
        dst_bar.addWidget(btn("复制译文", lambda: self._copy(self._translation), "Ctrl+Shift+C"))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.addLayout(top)
        lay.addLayout(src_bar)
        lay.addWidget(self.source, 2)
        lay.addLayout(dst_bar)
        lay.addWidget(self.target, 3)

        QShortcut(QKeySequence("Esc"), self, self.hide)
        QShortcut(QKeySequence("Ctrl+Return"), self, lambda: self.retranslate())
        QShortcut(QKeySequence("Ctrl+Shift+C"), self, lambda: self._copy(self._translation))

    # ------------------------------------------------------------ 对外接口

    def start_image(self, image: QImage, anchor: QRect | None, translate: bool = True,
                    record_id: str | None = None) -> None:
        self._record_id = record_id
        self._ocr_ms = None
        self.source.setPlainText("")
        self._set_translation("")
        self.target.setVisible(translate)
        self._set_status("识别中…")
        self._show_near(anchor)
        self._run(Job(self._get_config(), image=image, translate=translate), copy_ocr=not translate)

    def show_existing(self, source: str, translation: str, target: str, status: str,
                      record_id: str | None, anchor: QRect | None) -> None:
        """把气泡里已有的结果放到窗口中继续编辑。"""
        if self._job:
            self._job.cancel()
            self._job = None
        self._record_id = record_id
        self.source.setPlainText(source)
        self._set_translation(translation)
        self.target.setVisible(True)
        if target:
            self.lang_box.setCurrentText(target)
        self._set_status(status)
        self._show_near(anchor)

    def start_text(self, text: str, record_id: str | None = None) -> None:
        self._record_id = record_id
        self.source.setPlainText(text)
        self._show_near(None)
        self.retranslate()

    def retranslate(self, target: str | None = None) -> None:
        text = self.source.toPlainText().strip()
        if not text:
            return
        self.target.setVisible(True)
        self._ocr_ms = None
        self._set_translation("")
        self._set_status("翻译中…")
        self._run(Job(self._get_config(), text=text, target=target))

    # ------------------------------------------------------------ 内部

    def _run(self, job: Job, copy_ocr: bool = False) -> None:
        if self._job:
            self._job.cancel()
        self._job = job
        self._clip_token = clipboard.token()
        job.ocr_done.connect(lambda text, ms: self._on_ocr(job, text, ms, copy_ocr))
        job.target_chosen.connect(lambda lang: job is self._job and self.lang_box.setCurrentText(lang))
        job.chunk.connect(lambda s: job is self._job and self._set_translation(self._translation + s))
        job.finished.connect(lambda ms: self._on_finished(job, ms))
        job.failed.connect(lambda msg: job is self._job and self._on_failed(msg))
        job.start()

    def _on_ocr(self, job: Job, text: str, ms: int, copy_ocr: bool) -> None:
        if job is not self._job:
            return
        self._ocr_ms = ms
        self.source.setPlainText(text)
        if self._record_id:
            self.result.emit(self._record_id, {"source": text, "ocr_ms": ms})
        if copy_ocr:
            if text:
                if self._clip_token == clipboard.token():
                    clipboard.set_text(text, since=self._clip_token)
                    self._set_status(f"识别 {ms}ms · 已复制到剪贴板")
                else:  # 识别期间用户又主动复制了别的内容，不覆盖
                    self._set_status(f"识别 {ms}ms · 未复制（剪贴板已有更新内容）")
            else:
                self._set_status(f"识别 {ms}ms · 没有识别到文字")
        elif text:
            self._set_status(f"识别 {ms}ms · 翻译中…")

    def _on_finished(self, job: Job, ms: int) -> None:
        if job is not self._job:
            return
        parts = [f"识别 {self._ocr_ms}ms"] if self._ocr_ms is not None else []
        parts.append(f"翻译 {ms}ms")
        self._set_status(" · ".join(parts))
        if self._record_id:
            self.result.emit(self._record_id, {
                "source": self.source.toPlainText(), "translation": self._translation,
                "target": self.lang_box.currentText(), "tr_ms": ms,
            })
        if self._get_config().get("copy_translation") and self._translation:
            clipboard.set_text(self._translation, since=self._clip_token)

    def _on_failed(self, msg: str) -> None:
        self._set_status("出错了")
        self.target.setVisible(True)
        self.target.setPlainText("⚠ " + msg)

    def _set_translation(self, text: str) -> None:
        self._translation = text
        self.target.setPlainText(text)

    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    def _copy(self, text: str) -> None:
        if text:
            clipboard.set_text(text)
            self._set_status(self.status.text().split(" · 已复制")[0] + " · 已复制")

    def _show_near(self, anchor: QRect | None) -> None:
        if anchor is None:
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            area = screen.availableGeometry()
            pos = area.center() - self.rect().center()
        else:
            screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
            area = screen.availableGeometry()
            w, h = self.width(), self.frameGeometry().height() or self.height()
            # 优先放在选区右侧，其次下方、左侧，最后贴边
            if anchor.right() + 12 + w <= area.right():
                x, y = anchor.right() + 12, anchor.top()
            elif anchor.bottom() + 12 + h <= area.bottom():
                x, y = anchor.left(), anchor.bottom() + 12
            elif anchor.left() - 12 - w >= area.left():
                x, y = anchor.left() - 12 - w, anchor.top()
            else:
                x, y = area.right() - w, area.top()
            x = max(area.left(), min(x, area.right() - w))
            y = max(area.top(), min(y, area.bottom() - h))
            pos = QPoint(x, y)
        self.move(pos)
        self.show()
        self.raise_()
        self.activateWindow()

    def event(self, e):
        # 失去焦点时自动隐藏（除非已固定）
        if e.type() == QEvent.WindowDeactivate and not self.pin_btn.isChecked():
            if QApplication.activeModalWidget() is None and QApplication.activePopupWidget() is None:
                self.hide()
        return super().event(e)

    def hideEvent(self, e):
        if self._job:
            self._job.cancel()
            self._job = None
        super().hideEvent(e)

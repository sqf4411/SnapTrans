"""译文气泡：贴在截图选区旁边显示识别 / 翻译结果，不遮挡原文。"""

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QCursor, QFont, QGuiApplication, QImage
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from .worker import Job

_STYLE = """
QFrame#bubble { background: rgba(28, 29, 33, 242); border: 1px solid rgba(255, 255, 255, 50); border-radius: 8px; }
QLabel { color: #f2f2f2; background: transparent; }
QLabel#status { color: #9aa0a6; font-size: 9pt; }
QLabel#src { color: #a9b1ba; border-bottom: 1px solid rgba(255, 255, 255, 35); padding-bottom: 6px; }
QToolButton { color: #d0d0d0; background: transparent; border: none; border-radius: 4px; padding: 1px 6px; }
QToolButton:hover { background: rgba(255, 255, 255, 35); }
QToolButton:checked { background: rgba(10, 132, 255, 150); color: white; }
QScrollArea, QWidget#content { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }
QScrollBar::handle:vertical { background: rgba(255, 255, 255, 70); border-radius: 3px; min-height: 20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

MARGIN = 10
GAP = 8


class Bubble(QWidget):
    result = Signal(str, dict)          # 记录 id, 更新字段（写入截图记录）
    expand_requested = Signal(object)   # 在完整窗口中打开

    def __init__(self, get_config, sticky: bool = False):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("SnapTrans 译文")
        self._get_config = get_config
        self._job: Job | None = None
        self._record_id: str | None = None
        self._translate = True
        self._anchor = QRect()
        self._side = "cursor"
        self._width = 420
        self._follow: QWidget | None = None
        self._manual = False                 # 用户拖动过则不再自动定位
        self._drag: QPoint | None = None
        self._ocr_ms: int | None = None
        self.source_text = ""
        self.translation = ""
        self.target_lang = ""

        frame = QFrame(self)
        frame.setObjectName("bubble")
        frame.setStyleSheet(_STYLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

        self.status = QLabel()
        self.status.setObjectName("status")

        def tbtn(text, tip, slot=None, checkable=False):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setCursor(Qt.PointingHandCursor)
            if slot:
                b.clicked.connect(lambda _=False: slot())
            return b

        self.btn_src = tbtn("原文", "显示 / 隐藏识别的原文", checkable=True)
        self.btn_src.toggled.connect(self._toggle_source)
        self.btn_copy = tbtn("复制", "复制译文", self._copy)
        btn_expand = tbtn("展开", "在窗口中查看：可修改原文、切换语言", lambda: self.expand_requested.emit(self))
        self.btn_pin = tbtn("📌", "固定：点击其他地方时不自动关闭", checkable=True)
        self.btn_pin.setChecked(sticky)
        btn_close = tbtn("✕", "关闭 (Esc)", self.hide)

        header = QHBoxLayout()
        header.setSpacing(2)
        header.addWidget(self.status, 1)
        for b in (self.btn_src, self.btn_copy, btn_expand, self.btn_pin, btn_close):
            header.addWidget(b)

        self.src_label = QLabel()
        self.src_label.setObjectName("src")
        self.text_label = QLabel()
        for lab in (self.src_label, self.text_label):
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lab.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.src_label.hide()

        self._content = QWidget()
        self._content.setObjectName("content")
        cl = QVBoxLayout(self._content)
        cl.setContentsMargins(0, 0, 4, 0)
        cl.setSpacing(6)
        cl.addWidget(self.src_label)
        cl.addWidget(self.text_label)
        cl.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setWidget(self._content)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().setStyleSheet("background: transparent;")

        lay = QVBoxLayout(frame)
        lay.setContentsMargins(MARGIN, 6, MARGIN, MARGIN)
        lay.setSpacing(6)
        lay.addLayout(header)
        lay.addWidget(self.scroll)

    # ------------------------------------------------------------ 对外接口

    def start_image(self, image: QImage, anchor: QRect | None, translate: bool = True,
                    record_id: str | None = None) -> None:
        self._record_id = record_id
        self._translate = translate
        self._ocr_ms = None
        self.source_text = self.translation = self.target_lang = ""
        self.src_label.setText("")
        self.btn_src.setVisible(translate)
        self.btn_copy.setToolTip("复制译文" if translate else "复制文字")
        self._apply_font()
        self._set_text("识别中…", muted=True)
        self.status.setText("")
        if self._follow is None:
            self._anchor = QRect(anchor) if anchor else QRect()
        self._manual = False
        self._choose_side()
        self._relayout()
        self.show()
        self.raise_()
        self.activateWindow()
        self._run(Job(self._get_config(), image=image, translate=translate))

    def follow(self, widget: QWidget, rect_getter) -> None:
        """跟随某个窗口（贴图）移动。"""
        self._follow = widget
        self._rect_getter = rect_getter
        self._anchor = rect_getter()
        widget.installEventFilter(self)

    def status_text(self) -> str:
        return self.status.text()

    # ------------------------------------------------------------ 任务

    def _run(self, job: Job) -> None:
        # 旧任务不取消：让它在后台完成并写入截图记录，只是不再刷新界面
        self._job = job
        rid = self._record_id
        acc: list[str] = []
        target = {"lang": ""}

        def current():
            return job is self._job

        def on_ocr(text, ms):
            if rid:
                self.result.emit(rid, {"source": text, "ocr_ms": ms})
            if current():
                self._on_ocr(text, ms)

        def on_target(lang):
            target["lang"] = lang
            if current():
                self.target_lang = lang

        def on_chunk(piece):
            acc.append(piece)
            if current():
                self.translation += piece
                self._set_text(self.translation)

        def on_finished(ms):
            if rid:
                self.result.emit(rid, {"translation": "".join(acc), "target": target["lang"], "tr_ms": ms})
            if current():
                self._on_finished(ms)

        job.ocr_done.connect(on_ocr)
        job.target_chosen.connect(on_target)
        job.chunk.connect(on_chunk)
        job.finished.connect(on_finished)
        job.failed.connect(lambda msg: current() and self._on_failed(msg))
        job.start()

    def _on_ocr(self, text: str, ms: int) -> None:
        self._ocr_ms = ms
        self.source_text = text
        self.src_label.setText(text)
        if self._translate:
            self.status.setText(f"识别 {ms}ms · 翻译中…")
            if text:
                self._set_text("…", muted=True)
            return
        if text:
            QApplication.clipboard().setText(text)
            self.status.setText(f"识别 {ms}ms · 已复制")
            self._set_text(text)
        else:
            self.status.setText(f"识别 {ms}ms")
            self._set_text("没有识别到文字", muted=True)

    def _on_finished(self, ms: int) -> None:
        parts = [f"识别 {self._ocr_ms}ms"] if self._ocr_ms is not None else []
        parts.append(f"翻译 {ms}ms")
        if self.target_lang:
            parts.append(f"→ {self.target_lang}")
        self.status.setText(" · ".join(parts))
        if self._get_config().get("copy_translation") and self.translation:
            QApplication.clipboard().setText(self.translation)

    def _on_failed(self, msg: str) -> None:
        self.status.setText("出错了")
        self._set_text("⚠ " + msg, error=True)

    # ------------------------------------------------------------ 显示

    def _apply_font(self) -> None:
        size = float(self._get_config().get("bubble_font_size", 11))
        f = QFont("Microsoft YaHei UI")
        f.setPointSizeF(size)
        self.text_label.setFont(f)
        f2 = QFont(f)
        f2.setPointSizeF(max(8.0, size - 1))
        self.src_label.setFont(f2)

    def _set_text(self, text: str, muted: bool = False, error: bool = False) -> None:
        color = "#ff8a80" if error else ("#9aa0a6" if muted else "#f2f2f2")
        self.text_label.setStyleSheet(f"color: {color};")
        self.text_label.setText(text)
        self._relayout()

    def _toggle_source(self, on: bool) -> None:
        self.src_label.setVisible(on and bool(self.src_label.text()))
        self._relayout()

    def _copy(self) -> None:
        text = self.translation if self._translate else self.source_text
        if text:
            QApplication.clipboard().setText(text)
            base = self.status.text().split(" · 已复制")[0]
            self.status.setText(base + " · 已复制")

    def _screen_area(self) -> QRect:
        center = self._anchor.center() if not self._anchor.isEmpty() else QCursor.pos()
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _choose_side(self) -> None:
        """确定气泡放在选区哪一侧（之后译文变长时只调整高度，不换边，避免跳动）。"""
        a, area = self._anchor, self._screen_area()
        if a.isEmpty():
            self._side, self._width = "cursor", 420
            return
        right = area.right() - a.right() - GAP
        left = a.left() - area.left() - GAP
        below = area.bottom() - a.bottom() - GAP
        above = a.top() - area.top() - GAP
        wide = min(max(a.width(), 320), 600, area.width())
        options = [("right", right >= 300, min(460, right)), ("below", below >= 140, wide),
                   ("left", left >= 300, min(460, left)), ("above", above >= 140, wide)]
        if a.width() >= 360:  # 宽选区（段落）优先放下方，和原文同宽，方便对照
            options.insert(0, options.pop(1))
        for side, ok, width in options:
            if ok:
                self._side, self._width = side, width
                return
        self._side, self._width = "inside", min(wide, 460)

    def _relayout(self) -> None:
        area = self._screen_area()
        inner = self._width - 2 * MARGIN - 2 - 10
        h = self.text_label.heightForWidth(inner)
        if self.src_label.isVisible():
            h += self.src_label.heightForWidth(inner) + 6
        self.scroll.setFixedHeight(max(24, min(h + 4, int(area.height() * 0.6))))
        self.setFixedWidth(self._width)
        self.adjustSize()
        if not self._manual:
            self._place(area)

    def _place(self, area: QRect) -> None:
        a, w, h = self._anchor, self.width(), self.height()
        if self._side == "right":
            x, y = a.right() + GAP, a.top()
        elif self._side == "left":
            x, y = a.left() - GAP - w, a.top()
        elif self._side == "below":
            x, y = a.left(), a.bottom() + GAP
        elif self._side == "above":
            x, y = a.left(), a.top() - GAP - h
        elif self._side == "inside":
            x, y = a.left(), a.bottom() - h
        else:
            c = QCursor.pos()
            x, y = c.x() + 16, c.y() + 16
        x = max(area.left(), min(x, area.right() - w))
        y = max(area.top(), min(y, area.bottom() - h))
        self.move(x, y)

    # ------------------------------------------------------------ 事件

    def eventFilter(self, obj, e):
        if obj is self._follow:
            if e.type() in (QEvent.Move, QEvent.Resize) and self.isVisible():
                self._anchor = self._rect_getter()
                self._manual = False
                self._place(self._screen_area())
            elif e.type() == QEvent.Hide:
                self.hide()
        return False

    def event(self, e):
        # 失去焦点时自动隐藏（除非已固定）
        if e.type() == QEvent.WindowDeactivate and not self.btn_pin.isChecked():
            if QApplication.activePopupWidget() is None:
                self.hide()
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self._manual = True
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

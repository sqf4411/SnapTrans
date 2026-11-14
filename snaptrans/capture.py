"""截图：冻结屏幕后框选（悬停自动识别窗口，单击选中）。

普通截图模式下，选区确定后可调整大小/位置并标注，然后复制 / 保存 / 贴图 / 识字 / 翻译；
截图翻译、截图识字模式下选完立即返回。
"""

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QImage, QPainter, QPen, QPixmap, QRegion,
)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QToolButton, QWidget

from . import winutil
from .annotate import COLORS, SIZES, TEXT_PX, TWO_POINT, Shape, make_mosaic, paint_shape

ACCENT = QColor(0, 150, 255)

TOOLS = [
    ("rect", "▭", "矩形 (R)", Qt.Key_R),
    ("ellipse", "◯", "椭圆 (E)", Qt.Key_E),
    ("arrow", "➜", "箭头 (A)", Qt.Key_A),
    ("pen", "✎", "画笔 (P)", Qt.Key_P),
    ("marker", "▌", "荧光笔 (M)", Qt.Key_M),
    ("text", "T", "文字 (T)", Qt.Key_T),
    ("mosaic", "▦", "马赛克 (X)", Qt.Key_X),
]

HANDLE_CURSORS = {
    "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
    "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor, "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
}

_BAR_STYLE = """
QFrame#bar { background: #fafafa; border: 1px solid #9a9a9a; border-radius: 4px; }
QToolButton { border: none; border-radius: 3px; padding: 2px 4px; min-width: 22px; min-height: 22px;
              font-family: "Segoe UI Symbol", "Microsoft YaHei"; font-size: 14px; color: #202020; }
QToolButton:hover { background: #e3eefa; }
QToolButton:checked { background: #c6dcf5; }
QFrame#sep { background: #d0d0d0; }
"""


def _sep(parent) -> QFrame:
    f = QFrame(parent)
    f.setObjectName("sep")
    f.setFixedSize(1, 20)
    return f


def _tool_button(parent, text, tip, slot=None, checkable=False) -> QToolButton:
    b = QToolButton(parent)
    b.setText(text)
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setFocusPolicy(Qt.NoFocus)
    if slot:
        b.clicked.connect(lambda _=False: slot())
    return b


class _Toolbar(QFrame):
    def __init__(self, overlay: "_Overlay"):
        super().__init__(overlay)
        self.setObjectName("bar")
        self.setStyleSheet(_BAR_STYLE)
        self.setCursor(Qt.ArrowCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 3, 4, 3)
        lay.setSpacing(2)
        self.tool_buttons: dict[str, QToolButton] = {}
        for key, glyph, tip, _ in TOOLS:
            b = _tool_button(self, glyph, tip, checkable=True)
            b.clicked.connect(lambda checked, k=key: overlay.set_tool(k if checked else None))
            self.tool_buttons[key] = b
            lay.addWidget(b)
        lay.addWidget(_sep(self))
        lay.addWidget(_tool_button(self, "↶", "撤销 (Ctrl+Z)", overlay.undo))
        lay.addWidget(_tool_button(self, "↷", "重做 (Ctrl+Y)", overlay.redo))
        lay.addWidget(_sep(self))
        for text, tip, action in (("📌", "贴到屏幕 (Ctrl+P)", "pin"), ("识字", "识别文字并复制 (Ctrl+O)", "ocr"),
                                  ("翻译", "识别并翻译 (Ctrl+T)", "translate"), ("💾", "保存为文件 (Ctrl+S)", "save")):
            lay.addWidget(_tool_button(self, text, tip, lambda a=action: overlay.do_action(a)))
        lay.addWidget(_sep(self))
        lay.addWidget(_tool_button(self, "✕", "取消 (Esc / 右键)", overlay.ctl.cancel))
        done = _tool_button(self, "✔", "复制到剪贴板并完成 (Enter / 双击选区)", lambda: overlay.do_action("copy"))
        done.setStyleSheet("color: #1a7f37; font-weight: bold;")
        lay.addWidget(done)

    def sync(self, tool: str | None) -> None:
        for key, b in self.tool_buttons.items():
            b.setChecked(key == tool)


class _PropsBar(QFrame):
    """线宽 + 颜色。"""

    def __init__(self, overlay: "_Overlay"):
        super().__init__(overlay)
        self.setObjectName("bar")
        self.setStyleSheet(_BAR_STYLE)
        self.setCursor(Qt.ArrowCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 3, 4, 3)
        lay.setSpacing(3)
        self.size_buttons: dict[int, QToolButton] = {}
        for i, size in enumerate(SIZES):
            b = _tool_button(self, "●", ["细", "中", "粗"][i], lambda s=size: overlay.set_width(s), checkable=True)
            b.setStyleSheet(f"font-size: {7 + i * 5}px;")
            self.size_buttons[size] = b
            lay.addWidget(b)
        lay.addWidget(_sep(self))
        self.color_buttons: dict[str, QToolButton] = {}
        for c in COLORS:
            b = _tool_button(self, "", c, lambda c=c: overlay.set_color(QColor(c)), checkable=True)
            b.setFixedSize(20, 20)
            b.setStyleSheet(
                f"QToolButton {{ background: {c}; border: 1px solid #888; border-radius: 2px; min-width: 0; min-height: 0; }}"
                "QToolButton:checked { border: 2px solid #0a64d6; }"
                f"QToolButton:hover {{ background: {c}; border: 1px solid #0a64d6; }}")
            self.color_buttons[c] = b
            lay.addWidget(b)

    def sync(self, color: QColor, width: int) -> None:
        for c, b in self.color_buttons.items():
            b.setChecked(QColor(c) == color)
        for s, b in self.size_buttons.items():
            b.setChecked(s == width)


class _Overlay(QWidget):
    def __init__(self, ctl: "SnipController", screen, pixmap: QPixmap, windows: list[QRect],
                 monitor: QRect | None, editable: bool):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.ctl = ctl
        self._pixmap = pixmap
        self._dpr = pixmap.devicePixelRatio()
        self._editable = editable
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setScreen(screen)
        geo = screen.geometry()
        self.setGeometry(geo)
        self._windows = self._map_windows(windows, geo, monitor)

        self._state = "select"          # select：框选中；edit：选区已确定，可调整/标注
        self._sel = QRect()             # 选区（本窗口逻辑坐标）
        self._hover = QRect()           # 鼠标下的窗口
        self._drag: tuple | None = None
        self._press = QPoint()
        self._tool: str | None = None
        self._shapes: list[Shape] = []
        self._redo: list[Shape] = []
        self._drawing: Shape | None = None
        self._mosaic: QPixmap | None = None
        self._text_edit: QLineEdit | None = None
        self.toolbar: _Toolbar | None = None
        self.props: _PropsBar | None = None

    # ------------------------------------------------------------ 坐标

    @staticmethod
    def _map_windows(windows: list[QRect], geo: QRect, mon: QRect | None) -> list[QRect]:
        """把窗口的物理像素坐标换算成本屏遮罩的逻辑坐标。"""
        if mon is None or mon.width() <= 0 or geo.width() <= 0:
            return []
        scale = mon.width() / geo.width()
        local = QRect(0, 0, geo.width(), geo.height())
        out = []
        for r in windows:
            if not r.intersects(mon):
                continue
            q = QRect(round((r.left() - mon.left()) / scale), round((r.top() - mon.top()) / scale),
                      round(r.width() / scale), round(r.height() / scale)).intersected(local)
            if q.width() > 4 and q.height() > 4:
                out.append(q)
        return out

    def init_hover(self, pos: QPoint) -> None:
        self._hover = next((r for r in self._windows if r.contains(pos)), QRect())
        self.update()

    def global_sel(self) -> QRect:
        return QRect(self.mapToGlobal(self._sel.topLeft()), self._sel.size())

    # ------------------------------------------------------------ 绘制

    def _mosaic_source(self) -> tuple[QRect, QPixmap] | None:
        if not any(s.kind == "mosaic" for s in self._shapes + ([self._drawing] if self._drawing else [])):
            return None
        if self._mosaic is None:
            self._mosaic = make_mosaic(self._pixmap, round(10 * self._dpr))
        return self.rect(), self._mosaic

    def _paint_shapes(self, p: QPainter) -> None:
        mosaic = self._mosaic_source()
        for s in self._shapes:
            paint_shape(p, s, mosaic)
        if self._drawing:
            paint_shape(p, self._drawing, mosaic)

    def _handles(self) -> dict[str, QPoint]:
        r = self._sel
        cx, cy = r.center().x(), r.center().y()
        return {"tl": r.topLeft(), "t": QPoint(cx, r.top()), "tr": r.topRight(), "r": QPoint(r.right(), cy),
                "br": r.bottomRight(), "b": QPoint(cx, r.bottom()), "bl": r.bottomLeft(), "l": QPoint(r.left(), cy)}

    def _hit_handle(self, pos: QPoint) -> str | None:
        for name, pt in self._handles().items():
            if abs(pos.x() - pt.x()) <= 6 and abs(pos.y() - pt.y()) <= 6:
                return name
        return None

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self._pixmap)
        sel = self._sel if not self._sel.isEmpty() else self._hover
        shade = QRegion(self.rect())
        if not sel.isEmpty():
            shade = shade.subtracted(QRegion(sel))
        p.setClipRegion(shade)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        p.setClipping(False)
        if sel.isEmpty():
            return

        if self._shapes or self._drawing:
            p.save()
            p.setRenderHint(QPainter.Antialiasing)
            p.setClipRect(self._sel)
            self._paint_shapes(p)
            p.restore()

        p.setPen(QPen(ACCENT, 2 if self._sel.isEmpty() else 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(sel.adjusted(0, 0, -1, -1))
        if self._state == "edit" and self._tool is None and self._drag is None:
            for pt in self._handles().values():
                p.fillRect(QRect(pt.x() - 3, pt.y() - 3, 7, 7), ACCENT)

        d = self._dpr
        label = f"{round(sel.width() * d)} × {round(sel.height() * d)}"
        y = sel.top() - 22 if sel.top() > 24 else sel.top() + 4
        box = QRect(sel.left(), y, p.fontMetrics().horizontalAdvance(label) + 12, 20)
        p.fillRect(box, QColor(0, 0, 0, 170))
        p.setPen(Qt.white)
        p.drawText(box, Qt.AlignCenter, label)

    def render_selection(self) -> QImage:
        """选区原图（物理像素）+ 标注。"""
        sel, d = self._sel, self._dpr
        crop = QRect(round(sel.x() * d), round(sel.y() * d), round(sel.width() * d), round(sel.height() * d))
        image = self._pixmap.copy(crop).toImage().convertToFormat(QImage.Format_ARGB32)
        # 截屏图带着屏幕缩放比（如 1.1），QPainter 会自动再缩放一次；输出图统一按纯物理像素处理
        image.setDevicePixelRatio(1.0)
        if self._shapes:
            p = QPainter(image)
            p.setRenderHint(QPainter.Antialiasing)
            p.scale(image.width() / sel.width(), image.height() / sel.height())
            p.translate(-sel.left(), -sel.top())
            p.setClipRect(sel)
            self._paint_shapes(p)
            p.end()
        return image

    # ------------------------------------------------------------ 工具栏

    def _show_bars(self) -> None:
        if self._state != "edit" or self._sel.isEmpty():
            return
        if self.toolbar is None:
            self.toolbar = _Toolbar(self)
            self.props = _PropsBar(self)
        tb, pr = self.toolbar, self.props
        tb.sync(self._tool)
        pr.sync(self.ctl.color, self.ctl.width)
        tb.adjustSize()
        pr.adjustSize()
        show_props = self._tool is not None
        need = tb.height() + (pr.height() + 4 if show_props else 0) + 6
        sel = self._sel
        x = max(0, min(sel.right() - tb.width() + 1, self.width() - tb.width()))
        if sel.bottom() + need < self.height():
            y = sel.bottom() + 6
            py = y + tb.height() + 4
        elif sel.top() - need > 0:
            y = sel.top() - 6 - tb.height()
            py = y - 4 - pr.height()
        else:  # 选区几乎占满屏幕：放在选区内部底部
            y = sel.bottom() - tb.height() - 6
            py = y - 4 - pr.height()
        tb.move(x, y)
        tb.show()
        tb.raise_()
        if show_props:
            pr.move(x, py)
            pr.show()
            pr.raise_()
        else:
            pr.hide()

    def _hide_bars(self) -> None:
        if self.toolbar:
            self.toolbar.hide()
            self.props.hide()

    def set_tool(self, tool: str | None) -> None:
        self._commit_text()
        self._tool = tool
        self.setCursor(Qt.CrossCursor if tool else Qt.ArrowCursor)
        self._show_bars()
        self.update()

    def set_color(self, color: QColor) -> None:
        self.ctl.color = color
        if self._text_edit:
            self._style_text_edit(self._text_edit)
        self._show_bars()

    def set_width(self, width: int) -> None:
        self.ctl.width = width
        if self._text_edit:
            self._style_text_edit(self._text_edit)
        self._show_bars()

    def undo(self) -> None:
        self._commit_text()
        if self._shapes:
            self._redo.append(self._shapes.pop())
            self.update()

    def redo(self) -> None:
        if self._redo:
            self._shapes.append(self._redo.pop())
            self.update()

    def do_action(self, action: str) -> None:
        self._commit_text()
        if self._sel.isEmpty():
            return
        self.ctl.act(action, self.render_selection(), self.global_sel())

    # ------------------------------------------------------------ 文字工具

    def _style_text_edit(self, e: QLineEdit) -> None:
        """颜色 / 字号变化时调用（不要在 textChanged 里调用 setStyleSheet）。"""
        f = QFont("Microsoft YaHei")
        f.setPixelSize(TEXT_PX.get(self.ctl.width, 22))
        e.setFont(f)
        c = self.ctl.color.name()
        e.setStyleSheet(f"QLineEdit {{ background: transparent; color: {c}; border: 1px dashed {c}; padding: 0px; }}")
        self._fit_text_edit(e)

    @staticmethod
    def _fit_text_edit(e: QLineEdit) -> None:
        fm = QFontMetrics(e.font())
        e.resize(max(60, fm.horizontalAdvance(e.text()) + 24), fm.height() + 6)

    def _start_text(self, pos: QPoint) -> None:
        e = QLineEdit(self)
        self._style_text_edit(e)
        e.move(pos.x(), pos.y() - e.height() // 2)
        e.textChanged.connect(lambda _: self._fit_text_edit(e))
        e.installEventFilter(self)
        e.show()
        e.setFocus()
        self._text_edit = e

    def eventFilter(self, obj, e):
        # QLineEdit 处理 Enter 后会把按键事件继续传给父窗口（会被当成“完成截图”），这里直接吃掉
        if obj is self._text_edit and e.type() == QEvent.KeyPress and e.key() in (
                Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape):
            self._commit_text()
            return True
        return super().eventFilter(obj, e)

    def _commit_text(self) -> None:
        e, self._text_edit = self._text_edit, None
        if e is None:
            return
        if e.text().strip():
            g = e.geometry()
            self._shapes.append(Shape("text", QColor(self.ctl.color), self.ctl.width,
                                      [QPointF(g.left() + 3, g.top()), QPointF(g.right() + 400, g.bottom() + 1)],
                                      text=e.text(), font_px=TEXT_PX.get(self.ctl.width, 22)))
            self._redo.clear()
        e.removeEventFilter(self)
        e.deleteLater()
        self.setFocus()
        self.update()

    # ------------------------------------------------------------ 鼠标

    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        if e.button() == Qt.RightButton:
            if self._text_edit:
                self._commit_text()
            elif self._state == "edit" and self._tool:
                self.set_tool(None)  # 右键先退出当前标注工具
            else:
                self.ctl.cancel()
            return
        if e.button() != Qt.LeftButton:
            return
        if self._text_edit:
            self._commit_text()
            return
        self._press = pos
        if self._state == "select":
            self._drag = ("new",)
            return
        if self._tool == "text":
            self._start_text(pos)
            return
        if self._tool:
            pts = [QPointF(pos), QPointF(pos)] if self._tool in TWO_POINT else [QPointF(pos)]
            self._drawing = Shape(self._tool, QColor(self.ctl.color), self.ctl.width, pts)
            self._drag = ("draw",)
            return
        handle = self._hit_handle(pos)
        if handle:
            self._drag = ("resize", handle, QRect(self._sel))
        elif self._sel.contains(pos):
            self._drag = ("move", QRect(self._sel))
        else:  # 在选区外按下：重新框选
            self._state = "select"
            self._sel = QRect()
            self._drag = ("new",)
        self._hide_bars()

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        d = self._drag
        if d is None:
            if self._state == "select":
                hover = next((r for r in self._windows if r.contains(pos)), QRect())
                if hover != self._hover:
                    self._hover = hover
                    self.update()
            elif self._tool is None:
                h = self._hit_handle(pos)
                self.setCursor(HANDLE_CURSORS[h] if h else
                               (Qt.SizeAllCursor if self._sel.contains(pos) else Qt.ArrowCursor))
            return
        kind = d[0]
        if kind == "new":
            if (pos - self._press).manhattanLength() > 3 or not self._sel.isEmpty():
                self._sel = QRect(self._press, pos).normalized()
                self._hover = QRect()
        elif kind == "move":
            r = d[1].translated(pos - self._press)
            r.moveLeft(max(0, min(r.left(), self.width() - r.width())))
            r.moveTop(max(0, min(r.top(), self.height() - r.height())))
            self._sel = r
        elif kind == "resize":
            r0, h = d[2], d[1]
            left, top, right, bottom = r0.left(), r0.top(), r0.right(), r0.bottom()
            if "l" in h:
                left = pos.x()
            if "r" in h:
                right = pos.x()
            if "t" in h:
                top = pos.y()
            if "b" in h:
                bottom = pos.y()
            self._sel = QRect(QPoint(left, top), QPoint(right, bottom)).normalized()
        elif kind == "draw" and self._drawing:
            if self._drawing.kind in TWO_POINT:
                self._drawing.points[1] = QPointF(pos)
            else:
                self._drawing.points.append(QPointF(pos))
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self._drag is None:
            return
        kind = self._drag[0]
        self._drag = None
        if kind == "new":
            if self._sel.width() < 4 or self._sel.height() < 4:
                self._sel = QRect(self._hover)  # 单击：选中鼠标下的窗口
            self._hover = QRect()
            if self._sel.isEmpty():
                self.update()
                return
            if not self._editable:
                self.ctl.finish(self.render_selection(), self.global_sel())
                return
            self._state = "edit"
            self.ctl.keep_only(self)
            self.setCursor(Qt.ArrowCursor)
        elif kind == "draw":
            if self._drawing and self._drawing.is_valid():
                self._shapes.append(self._drawing)
                self._redo.clear()
            self._drawing = None
        self._show_bars()
        self.update()

    def mouseDoubleClickEvent(self, e):
        if (e.button() == Qt.LeftButton and self._state == "edit" and self._tool is None
                and self._sel.contains(e.position().toPoint())):
            self.do_action("copy")

    # ------------------------------------------------------------ 键盘

    def keyPressEvent(self, e):
        k = e.key()
        ctrl = bool(e.modifiers() & Qt.ControlModifier)
        if self._text_edit and k == Qt.Key_Escape:
            self._commit_text()
            return
        if k == Qt.Key_Escape:
            self.ctl.cancel()
            return
        if self._state != "edit":
            return
        actions = {Qt.Key_C: "copy", Qt.Key_S: "save", Qt.Key_P: "pin", Qt.Key_T: "translate", Qt.Key_O: "ocr"}
        if k in (Qt.Key_Return, Qt.Key_Enter):
            self.do_action("copy")
        elif ctrl and k == Qt.Key_Z:
            self.undo()
        elif ctrl and k == Qt.Key_Y:
            self.redo()
        elif ctrl and k in actions:
            self.do_action(actions[k])
        elif not ctrl:
            for key, _, _, qk in TOOLS:
                if k == qk:
                    self.set_tool(None if self._tool == key else key)
                    return
            moves = {Qt.Key_Left: QPoint(-1, 0), Qt.Key_Right: QPoint(1, 0),
                     Qt.Key_Up: QPoint(0, -1), Qt.Key_Down: QPoint(0, 1)}
            if k in moves:  # 方向键微调选区
                self._sel.translate(moves[k])
                self._show_bars()
                self.update()


def _match_monitor(screen, monitors: dict[str, QRect]) -> QRect | None:
    """找到 Qt 屏幕对应的物理显示器矩形。Qt 在 Windows 上保持各屏左上角为物理坐标，按左上角匹配。"""
    if screen.name() in monitors:
        return monitors[screen.name()]
    tl = screen.geometry().topLeft()
    for rect in monitors.values():
        if rect.topLeft() == tl:
            return rect
    return None


class SnipController(QObject):
    captured = Signal(QImage, QRect)       # 截图翻译 / 识字模式：图片, 选区全局逻辑坐标
    action = Signal(str, QImage, QRect)    # 普通截图模式：copy / save / pin / translate / ocr
    canceled = Signal()

    def __init__(self):
        super().__init__()
        self._overlays: list[_Overlay] = []
        self.color = QColor(COLORS[0])
        self.width = SIZES[1]

    @property
    def active(self) -> bool:
        return bool(self._overlays)

    def start(self, editable: bool = False) -> None:
        if self._overlays:
            return
        try:
            windows, monitors = winutil.window_rects(), winutil.monitor_rects()
        except Exception:
            windows, monitors = [], {}
        # 先把所有屏幕抓下来，再显示遮罩，避免遮罩被截进去
        shots = [(s, s.grabWindow(0)) for s in QGuiApplication.screens()]
        self._overlays = [_Overlay(self, s, pm, windows, _match_monitor(s, monitors), editable) for s, pm in shots]
        cursor = QCursor.pos()
        cursor_screen = QGuiApplication.screenAt(cursor)
        for ov in self._overlays:
            ov.show()
        for ov in self._overlays:
            if ov.screen() is cursor_screen:
                ov.init_hover(ov.mapFromGlobal(cursor))
                ov.raise_()
                ov.activateWindow()
                ov.setFocus()

    def keep_only(self, keep: _Overlay) -> None:
        """选区确定后关闭其他屏幕的遮罩。"""
        for ov in self._overlays:
            if ov is not keep:
                ov.close()
        self._overlays = [keep]
        keep.activateWindow()
        keep.setFocus()

    def _close_all(self) -> None:
        overlays, self._overlays = self._overlays, []
        for ov in overlays:
            ov.close()

    def finish(self, image: QImage, rect: QRect) -> None:
        self._close_all()
        self.captured.emit(image, rect)

    def act(self, name: str, image: QImage, rect: QRect) -> None:
        self._close_all()
        self.action.emit(name, image, rect)

    def cancel(self) -> None:
        self._close_all()
        self.canceled.emit()

"""贴图：把截图钉在屏幕最上层。

拖动移动，滚轮缩放，Ctrl+滚轮调透明度，, / . 翻看上一张 / 下一张历史截图，双击或 Esc 关闭。
"""

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication, QMenu, QWidget

B = 1  # 边框宽度（像素），图片内容与原截图位置对齐


class PinWindow(QWidget):
    translate_requested = Signal(object, bool)  # 贴图, True=翻译 / False=只识字
    save_requested = Signal(QImage)
    close_all_requested = Signal()
    closed = Signal(object)
    image_changed = Signal(object)

    def __init__(self, image: QImage, top_left: QPoint, history=None, rid: str | None = None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("SnapTrans 贴图")
        self.history = history
        self.rid = rid
        self.image = image
        self.bubble = None
        self._zoom = 1.0
        self._drag_offset: QPoint | None = None
        self._toast = ""
        self._toast_timer = QTimer(self, singleShot=True, interval=1600)
        self._toast_timer.timeout.connect(self._clear_toast)
        screen = QGuiApplication.screenAt(top_left) or QGuiApplication.primaryScreen()
        self._dpr = screen.devicePixelRatio()
        self._apply_size()
        self.move(top_left - QPoint(B, B))

    # ------------------------------------------------------------ 尺寸 / 绘制

    def _apply_size(self) -> None:
        w = max(1, round(self.image.width() / self._dpr * self._zoom))
        h = max(1, round(self.image.height() / self._dpr * self._zoom))
        self.setFixedSize(QSize(w + 2 * B, h + 2 * B))

    def image_rect(self) -> QRect:
        """图片内容的全局逻辑坐标（翻译气泡以它为锚点）。"""
        g = self.frameGeometry()
        return g.adjusted(B, B, -B, -B)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, self._zoom != 1.0)
        p.drawImage(QRectF(B, B, self.width() - 2 * B, self.height() - 2 * B), self.image)
        p.setPen(QPen(QColor(10, 132, 255) if self.isActiveWindow() else QColor(130, 130, 130, 200), B))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(B / 2, B / 2, self.width() - B, self.height() - B))
        if self._toast:
            fm = p.fontMetrics()
            box = QRect(B + 4, self.height() - B - 26, fm.horizontalAdvance(self._toast) + 14, 22)
            p.fillRect(box, QColor(0, 0, 0, 180))
            p.setPen(Qt.white)
            p.drawText(box, Qt.AlignCenter, self._toast)

    def changeEvent(self, e):
        super().changeEvent(e)
        self.update()  # 激活状态变化时更新边框颜色

    def show_toast(self, text: str) -> None:
        self._toast = text
        self._toast_timer.start()
        self.update()

    def _clear_toast(self) -> None:
        self._toast = ""
        self.update()

    # ------------------------------------------------------------ 操作

    def set_zoom(self, zoom: float) -> None:
        self._zoom = max(0.1, min(8.0, zoom))
        self._apply_size()
        self.show_toast(f"{round(self._zoom * 100)}%")

    def copy(self) -> None:
        QApplication.clipboard().setImage(self.image)
        self.show_toast("已复制")

    def page(self, step: int) -> None:
        """step=+1 上一张（更早），-1 下一张（更新）。"""
        if self.history is None:
            self.show_toast("没有截图记录")
            return
        recs = [r for r in self.history.records() if self.history.image_path(r["id"]).exists()]
        if not recs:
            self.show_toast("没有截图记录")
            return
        ids = [r["id"] for r in recs]
        if self.rid in ids:
            i = ids.index(self.rid) + step
        else:
            i = 0 if step > 0 else -1
        if i < 0:
            self.show_toast("已经是最新一张")
            return
        if i >= len(ids):
            self.show_toast("已经是最早一张")
            return
        image = QImage(str(self.history.image_path(ids[i])))
        if image.isNull():
            return
        self.rid = ids[i]
        self.image = image
        self._apply_size()
        self.image_changed.emit(self)
        self.show_toast(f"{i + 1} / {len(ids)}   {recs[i]['time'][5:16]}")

    # ------------------------------------------------------------ 事件

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_offset is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, e):
        self._drag_offset = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.close()

    def wheelEvent(self, e):
        delta = e.angleDelta().y()
        if not delta:
            return
        if e.modifiers() & Qt.ControlModifier:
            op = max(0.2, min(1.0, self.windowOpacity() + (0.1 if delta > 0 else -0.1)))
            self.setWindowOpacity(op)
            self.show_toast(f"透明度 {round(op * 100)}%")
        else:
            self.set_zoom(self._zoom * (1.1 if delta > 0 else 1 / 1.1))

    def keyPressEvent(self, e):
        k = e.key()
        ctrl = bool(e.modifiers() & Qt.ControlModifier)
        if k == Qt.Key_Escape:
            self.close()
        elif k == Qt.Key_Comma:
            self.page(+1)
        elif k == Qt.Key_Period:
            self.page(-1)
        elif ctrl and k == Qt.Key_C:
            self.copy()
        elif ctrl and k == Qt.Key_S:
            self.save_requested.emit(self.image)
        elif ctrl and k == Qt.Key_T:
            self.translate_requested.emit(self, True)
        elif ctrl and k == Qt.Key_O:
            self.translate_requested.emit(self, False)
        elif ctrl and k == Qt.Key_0:
            self.set_zoom(1.0)
        else:
            super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction("复制\tCtrl+C", self.copy)
        m.addAction("另存为…\tCtrl+S", lambda: self.save_requested.emit(self.image))
        m.addSeparator()
        m.addAction("翻译\tCtrl+T", lambda: self.translate_requested.emit(self, True))
        m.addAction("识字（复制文字）\tCtrl+O", lambda: self.translate_requested.emit(self, False))
        m.addSeparator()
        m.addAction("上一张历史截图\t,", lambda: self.page(+1))
        m.addAction("下一张历史截图\t.", lambda: self.page(-1))
        m.addAction("原始大小\tCtrl+0", lambda: self.set_zoom(1.0))
        m.addSeparator()
        m.addAction("关闭\tEsc", self.close)
        m.addAction("关闭全部贴图", self.close_all_requested.emit)
        m.exec(e.globalPos())

    def closeEvent(self, e):
        self.closed.emit(self)
        super().closeEvent(e)

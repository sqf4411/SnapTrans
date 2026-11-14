"""截图选区：每个屏幕一个全屏遮罩，冻结当前画面后拖拽选择区域。"""

from PySide6.QtCore import QObject, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget


class _Overlay(QWidget):
    def __init__(self, controller, screen, pixmap: QPixmap):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._controller = controller
        self._pixmap = pixmap
        self._dpr = pixmap.devicePixelRatio()
        self._origin: QPoint | None = None
        self._current: QPoint | None = None
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())

    def _selection(self) -> QRect:
        if self._origin is None or self._current is None:
            return QRect()
        return QRect(self._origin, self._current).normalized()

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(self.rect(), self._pixmap)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        sel = self._selection()
        if sel.isEmpty():
            return
        d = self._dpr
        src = QRectF(sel.x() * d, sel.y() * d, sel.width() * d, sel.height() * d)
        p.drawPixmap(QRectF(sel), self._pixmap, src)
        p.setPen(QPen(QColor(0, 150, 255), 1))
        p.drawRect(sel.adjusted(0, 0, -1, -1))
        label = f"{round(sel.width() * d)} × {round(sel.height() * d)}"
        y = sel.top() - 22 if sel.top() > 24 else sel.top() + 4
        box = QRect(sel.left(), y, p.fontMetrics().horizontalAdvance(label) + 12, 20)
        p.fillRect(box, QColor(0, 0, 0, 170))
        p.setPen(Qt.white)
        p.drawText(box, Qt.AlignCenter, label)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._origin = self._current = e.position().toPoint()
            self.update()
        elif e.button() == Qt.RightButton:
            self._controller.cancel()

    def mouseMoveEvent(self, e):
        if self._origin is not None:
            self._current = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self._origin is None:
            return
        self._current = e.position().toPoint()
        sel = self._selection()
        if sel.width() < 4 or sel.height() < 4:
            self._origin = self._current = None
            self.update()
            return
        d = self._dpr
        crop = QRect(round(sel.x() * d), round(sel.y() * d), round(sel.width() * d), round(sel.height() * d))
        image = self._pixmap.copy(crop).toImage()
        global_rect = QRect(self.mapToGlobal(sel.topLeft()), sel.size())
        self._controller.finish(image, global_rect)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._controller.cancel()


class SnipController(QObject):
    captured = Signal(QImage, QRect)  # 截图, 选区的全局逻辑坐标
    canceled = Signal()

    def __init__(self):
        super().__init__()
        self._overlays: list[_Overlay] = []

    @property
    def active(self) -> bool:
        return bool(self._overlays)

    def start(self) -> None:
        if self._overlays:
            return
        # 先把所有屏幕抓下来，再显示遮罩，避免遮罩被截进去
        shots = [(s, s.grabWindow(0)) for s in QGuiApplication.screens()]
        self._overlays = [_Overlay(self, s, pm) for s, pm in shots]
        cursor_screen = QGuiApplication.screenAt(QCursor.pos())
        for ov in self._overlays:
            ov.show()
        for ov in self._overlays:
            if ov.screen() is cursor_screen:
                ov.raise_()
                ov.activateWindow()
                ov.setFocus()

    def _close_all(self) -> None:
        overlays, self._overlays = self._overlays, []
        for ov in overlays:
            ov.close()

    def finish(self, image: QImage, rect: QRect) -> None:
        self._close_all()
        self.captured.emit(image, rect)

    def cancel(self) -> None:
        self._close_all()
        self.canceled.emit()

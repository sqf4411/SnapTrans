"""截图标注图形：矩形、椭圆、箭头、画笔、荧光笔、文字、马赛克。"""

from dataclasses import dataclass, field

from PySide6.QtCore import QLineF, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap, QPolygonF,
)

COLORS = ["#ff3b30", "#ff9500", "#ffd60a", "#34c759", "#0a84ff", "#af52de", "#000000", "#ffffff"]
SIZES = [2, 4, 7]
TWO_POINT = {"rect", "ellipse", "arrow"}
TEXT_PX = {SIZES[0]: 16, SIZES[1]: 22, SIZES[2]: 30}


@dataclass
class Shape:
    kind: str
    color: QColor
    width: int
    points: list = field(default_factory=list)
    text: str = ""
    font_px: int = 22

    def is_valid(self) -> bool:
        if self.kind == "text":
            return bool(self.text.strip())
        if self.kind in TWO_POINT:
            if len(self.points) < 2:
                return False
            d = self.points[1] - self.points[0]
            return abs(d.x()) + abs(d.y()) >= 3
        return len(self.points) >= 1


def _path(points: list) -> QPainterPath:
    path = QPainterPath(points[0])
    if len(points) == 1:
        path.lineTo(points[0] + QPointF(0.01, 0))
    for pt in points[1:]:
        path.lineTo(pt)
    return path


def _arrow(p: QPainter, s: Shape) -> None:
    a, b = s.points[0], s.points[1]
    length = QLineF(a, b).length()
    if length < 1:
        return
    ux, uy = (b.x() - a.x()) / length, (b.y() - a.y()) / length
    head = min(length, max(12.0, s.width * 4.0))
    half = head * 0.45
    base = QPointF(b.x() - ux * head, b.y() - uy * head)
    left = QPointF(base.x() + uy * half, base.y() - ux * half)
    right = QPointF(base.x() - uy * half, base.y() + ux * half)
    p.setPen(QPen(s.color, s.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawLine(a, QPointF(b.x() - ux * head * 0.8, b.y() - uy * head * 0.8))
    p.setPen(Qt.NoPen)
    p.setBrush(s.color)
    p.drawPolygon(QPolygonF([b, left, right]))


def paint_shape(p: QPainter, s: Shape, mosaic: tuple[QRect, QPixmap] | None = None) -> None:
    p.save()
    pen = QPen(s.color, s.width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    k = s.kind
    if k in ("rect", "ellipse"):
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        r = QRectF(s.points[0], s.points[1]).normalized()
        p.drawRect(r) if k == "rect" else p.drawEllipse(r)
    elif k == "arrow":
        _arrow(p, s)
    elif k == "pen":
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(_path(s.points))
    elif k == "marker":
        c = QColor(s.color)
        c.setAlpha(90)
        p.setPen(QPen(c, max(12, s.width * 4), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(_path(s.points))
    elif k == "mosaic" and mosaic is not None:
        stroker = QPainterPathStroker()
        stroker.setWidth(max(16, s.width * 5))
        stroker.setCapStyle(Qt.RoundCap)
        stroker.setJoinStyle(Qt.RoundJoin)
        p.setClipPath(stroker.createStroke(_path(s.points)), Qt.IntersectClip)
        p.drawPixmap(mosaic[0], mosaic[1])
    elif k == "text":
        f = QFont("Microsoft YaHei")
        f.setPixelSize(s.font_px)
        p.setFont(f)
        p.setPen(s.color)
        p.drawText(QRectF(s.points[0], s.points[1]), Qt.AlignLeft | Qt.AlignVCenter | Qt.TextDontClip, s.text)
    p.restore()


def make_mosaic(pm: QPixmap, block: int) -> QPixmap:
    """整屏像素化版本，马赛克笔刷按笔迹裁剪绘制它。"""
    img = pm.toImage()
    w, h = img.width(), img.height()
    b = max(4, block)
    small = img.scaled(max(1, w // b), max(1, h // b), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    out = QPixmap.fromImage(small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.FastTransformation))
    out.setDevicePixelRatio(pm.devicePixelRatio())
    return out

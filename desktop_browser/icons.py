"""Line icons of the chat composer (microphone, speaker, stop), drawn with
QPainter in the colour of the theme: no image files and no Qt SVG module.

Shapes follow a 24×24 grid with 2 px rounded strokes, like the Lucide set.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

SIZE = 20  # logical pixels of the icon in the button
_SCALE = 3  # drawn at 3x: sharp on 150-300 % screens


def _canvas() -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(SIZE * _SCALE, SIZE * _SCALE)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(SIZE * _SCALE / 24, SIZE * _SCALE / 24)
    return pixmap, painter


def _pen(color: str) -> QPen:
    pen = QPen(QColor(color), 2.0)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _icon(pixmap: QPixmap, painter: QPainter) -> QIcon:
    painter.end()
    pixmap.setDevicePixelRatio(_SCALE)
    return QIcon(pixmap)


def microphone(color: str) -> QIcon:
    pixmap, p = _canvas()
    p.setPen(_pen(color))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(9, 2, 6, 12), 3, 3)  # capsule
    arc = QPainterPath(QPointF(19, 10))
    arc.lineTo(19, 12)
    arc.arcTo(QRectF(5, 5, 14, 14), 0, -180)  # the holder under the capsule
    arc.lineTo(5, 10)
    p.drawPath(arc)
    p.drawLine(QPointF(12, 19), QPointF(12, 22))
    return _icon(pixmap, p)


def stop(color: str) -> QIcon:
    """A filled rounded square: recording is on, press to stop."""
    pixmap, p = _canvas()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(6, 6, 12, 12), 2.5, 2.5)
    return _icon(pixmap, p)


def speaker(color: str, on: bool) -> QIcon:
    """Speaker with sound waves (*on*) or crossed out."""
    pixmap, p = _canvas()
    p.setPen(_pen(color))
    p.setBrush(Qt.NoBrush)
    p.drawPolygon(QPolygonF([QPointF(*xy) for xy in ((11, 5), (6, 9), (2, 9), (2, 15), (6, 15), (11, 19))]))
    if on:
        for rect in (QRectF(7, 7, 10, 10), QRectF(3, 3, 18, 18)):
            wave = QPainterPath()
            wave.arcMoveTo(rect, 45)
            wave.arcTo(rect, 45, -90)
            p.drawPath(wave)
    else:
        p.drawLine(QPointF(16, 9), QPointF(22, 15))
        p.drawLine(QPointF(22, 9), QPointF(16, 15))
    return _icon(pixmap, p)


def headphones(color: str) -> QIcon:
    """Hands-free voice mode."""
    pixmap, p = _canvas()
    p.setPen(_pen(color))
    p.setBrush(Qt.NoBrush)
    band = QPainterPath(QPointF(3, 18))
    band.lineTo(3, 12)
    band.arcTo(QRectF(3, 3, 18, 18), 180, -180)
    band.lineTo(21, 18)
    p.drawPath(band)
    p.drawRoundedRect(QRectF(3, 14, 5, 7), 2, 2)
    p.drawRoundedRect(QRectF(16, 14, 5, 7), 2, 2)
    return _icon(pixmap, p)

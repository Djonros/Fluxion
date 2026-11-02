"""Line icons of the window in the colour of the theme: no image files.

The composer's microphone, speaker, stop and headphones are drawn with QPainter;
the other outline icons are Lucide shapes (ISC licence) read by the SVG image
plugin of Qt (qsvg, shipped with PySide6), without the Qt SVG module.
Shapes follow a 24×24 grid with 2 px rounded strokes: grey is idle, the accent
colour is on. The «Думаю…» spinner is drawn here too.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

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


# ── outline icons (Lucide) ───────────────────────────────────────────────

_SHAPES = {
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "arrow-up": '<path d="M12 19V5M5 12l7-7 7 7"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.9 4.9 7 7M17 17l2.1 2.1M2 12h3M19 12h3'
                'M4.9 19.1 7 17M17 7l2.1-2.1"/>',
    "more": '<circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>',
    "paperclip": '<path d="m21.4 11.6-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8'
                 'l8.5-8.5"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/>'
             '<path d="m21 15-3-3a2 2 0 0 0-3 0l-9 9"/>',
    "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>',
    "pen": '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/>',
    "feather": '<path d="M20.2 12.2a6 6 0 0 0-8.5-8.5L5 10.5V19h8.5z"/><path d="M16 8 2 22M17.5 15H9"/>',
    "play": '<path d="m6 3 14 9-14 9z"/>',
    "slash": '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="m15 7-6 10"/>',
    "wrench": '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9'
              'a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9z"/>',
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "copy": '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9'
            'a2 2 0 0 1 2 2v1"/>',
    "paste": '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6'
             'a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>',
    "cut": '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.1 15.9M14.5 14.5 20 20'
           'M8.1 8.1 12 12"/>',
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "redo": '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    "select": '<path d="M5 3a2 2 0 0 0-2 2M19 3a2 2 0 0 1 2 2M21 19a2 2 0 0 1-2 2M5 21a2 2 0 0 1-2-2M9 3h1M9 21h1'
              'M14 3h1M14 21h1M3 9v1M21 9v1M3 14v1M21 14v1"/>',
    "chat": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "folder": '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><path d="M2 12h20M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20"/>',
    "bot": '<rect x="3" y="8" width="18" height="12" rx="2"/><path d="M12 8V4M8 14h.01M16 14h.01M9 18h6"/>',
    "pulse": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "box": '<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="m3 8 9 5 9-5M12 13v8"/>',
    "calculator": '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M8 6h8M8 10h.01M12 10h.01M16 10h.01'
                  'M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01M16 18h.01"/>',
    "layers": '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>',
    "ruler": '<path d="M21.3 15.3 8.7 2.7a1 1 0 0 0-1.4 0L2.7 7.3a1 1 0 0 0 0 1.4l12.6 12.6a1 1 0 0 0 1.4 0'
             'l4.6-4.6a1 1 0 0 0 0-1.4z"/><path d="m7.5 10.5 2-2M10.5 13.5l2-2M13.5 16.5l2-2"/>',
    "archive": '<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8'
               'M10 12h4"/>',
    "diff": '<path d="M8 3v12M5 6l3-3 3 3M16 21V9M13 18l3 3 3-3"/>',
    "cart": '<circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2 2h3l2.7 12.4a2 2 0 0 0 2 1.6'
            'h9.7a2 2 0 0 0 2-1.6L23 6H6"/>',
    "coin": '<circle cx="12" cy="12" r="9"/><path d="M15 9.5a3 3 0 0 0-3-1.5c-1.7 0-3 .9-3 2s1.3 2 3 2 3 .9 3 2'
            '-1.3 2-3 2a3 3 0 0 1-3-1.5M12 6v2M12 16v2"/>',
    "zap": '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>',
    "scan": '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/>'
            '<circle cx="12" cy="12" r="3"/>',
    "phone": '<rect x="6" y="2" width="12" height="20" rx="2"/><path d="M11 18h2"/>',
    "volume": '<path d="M11 5 6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
}


def line(name: str, color: str, size: int = SIZE) -> QIcon:
    """The outline icon *name* (a key of _SHAPES) in *color*."""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size * _SCALE}" height="{size * _SCALE}" '
           f'viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" '
           f'stroke-linejoin="round">{_SHAPES[name]}</svg>')
    pixmap = QPixmap.fromImage(QImage.fromData(svg.encode("utf-8"), "SVG"))
    pixmap.setDevicePixelRatio(_SCALE)
    return QIcon(pixmap)


def names() -> tuple[str, ...]:
    return tuple(_SHAPES)


# ── «Думаю…»: a light running along the infinity of the logo ───────────────

SPINNER_FRAMES = 48  # one loop; at 55 ms a frame — about 2,6 s
_PURPLE = (123, 47, 255)
_CYAN = (0, 229, 255)


def _lemniscate(t: float, cx: float, cy: float, a: float) -> QPointF:
    s, c = math.sin(t), math.cos(t)
    d = 1 + s * s
    return QPointF(cx + a * c / d, cy + a * s * c / d)


def spinner_frame(index: int, width: int = 34, height: int = 18) -> QImage:
    """Frame *index* of the spinner: a faint purple-to-cyan infinity and a comet on it."""
    image = QImage(width * _SCALE, height * _SCALE, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(_SCALE, _SCALE)
    cx, cy, a = width / 2, height / 2, width / 2 - 2.5
    track = QPainterPath(_lemniscate(0, cx, cy, a))
    for i in range(1, 121):
        track.lineTo(_lemniscate(2 * math.pi * i / 120, cx, cy, a))
    gradient = QLinearGradient(cx - a, 0, cx + a, 0)
    gradient.setColorAt(0, QColor(*_PURPLE, 70))
    gradient.setColorAt(1, QColor(*_CYAN, 70))
    p.setPen(QPen(gradient, 2.2, Qt.SolidLine, Qt.RoundCap))
    p.drawPath(track)
    p.setPen(Qt.NoPen)
    head = 2 * math.pi * index / SPINNER_FRAMES
    tail = 16
    for k in range(tail):
        t = head - k * 0.1
        mix = (math.cos(t) + 1) / 2  # purple on the left loop, cyan on the right
        color = QColor(*(round(lo * (1 - mix) + hi * mix) for lo, hi in zip(_PURPLE, _CYAN)))
        color.setAlphaF(1 - k / tail)
        radius = 2.2 * (1 - k / (tail + 4))
        p.setBrush(color)
        p.drawEllipse(_lemniscate(t, cx, cy, a), radius, radius)
    p.end()
    return image

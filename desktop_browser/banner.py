"""Fluxion wordmark banner drawn with QPainter.

Approximates the project logo: thin letter-spaced white caps spelling
"F-L-U-X-I-O-N" with the X replaced by a glossy, gradient infinity ribbon
(purple to cyan) on a dark background.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QWidget


class BrandBanner(QWidget):
    """Header banner: "F-L-U-" + gradient ribbon (the X) + "-I-O-N"."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("brandBanner")
        self.setMinimumHeight(48)
        self._dark = True

    def apply_colors(self, dark: bool) -> None:
        self._dark = dark
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        font = QFont("Segoe UI")
        font.setPixelSize(max(12, int(self.height() * 0.48)))
        font.setWeight(QFont.Light)
        painter.setFont(font)
        fm = QFontMetricsF(font)

        text_color = QColor("#f8fafc") if self._dark else QColor("#0f172a")
        symbol_gap = fm.horizontalAdvance("-") * 0.35
        dash_ratio = 0.55  # dash width relative to a letter

        left_symbols = ["F", "-", "L", "-", "U", "-"]
        right_symbols = ["-", "I", "-", "O", "-", "N"]

        def symbol_width(sym: str) -> float:
            if sym == "-":
                return fm.horizontalAdvance("I") * dash_ratio
            return fm.horizontalAdvance(sym)

        left_w = sum(symbol_width(s) for s in left_symbols) + symbol_gap * (len(left_symbols) - 1)
        right_w = sum(symbol_width(s) for s in right_symbols) + symbol_gap * (len(right_symbols) - 1)

        # ribbon occupies roughly the space of the original X
        ribbon_w = min(self.height() * 1.5, self.width() * 0.22)
        ribbon_h = ribbon_w * 0.70
        text_ribbon_gap = fm.horizontalAdvance("I") * 0.45

        total = left_w + text_ribbon_gap + ribbon_w + text_ribbon_gap + right_w
        x = (self.width() - total) / 2
        cy = self.height() / 2

        # draw left text
        painter.setPen(text_color)
        cur_x = x
        for sym in left_symbols:
            w = symbol_width(sym)
            if sym == "-":
                # draw a short horizontal dash, vertically centered
                dash_y = cy
                dash_half = w * 0.35
                pen = painter.pen()
                painter.setPen(QPen(text_color, max(1.5, self.height() * 0.035), Qt.SolidLine, Qt.RoundCap))
                painter.drawLine(QPointF(cur_x + w / 2 - dash_half, dash_y), QPointF(cur_x + w / 2 + dash_half, dash_y))
                painter.setPen(pen)
            else:
                painter.drawText(
                    QRectF(cur_x, cy - fm.height() / 2, w, fm.height()),
                    Qt.AlignCenter,
                    sym,
                )
            cur_x += w + symbol_gap

        # draw right text
        cur_x = x + left_w + text_ribbon_gap + ribbon_w + text_ribbon_gap
        for sym in right_symbols:
            w = symbol_width(sym)
            if sym == "-":
                dash_y = cy
                dash_half = w * 0.35
                pen = painter.pen()
                painter.setPen(QPen(text_color, max(1.5, self.height() * 0.035), Qt.SolidLine, Qt.RoundCap))
                painter.drawLine(QPointF(cur_x + w / 2 - dash_half, dash_y), QPointF(cur_x + w / 2 + dash_half, dash_y))
                painter.setPen(pen)
            else:
                painter.drawText(
                    QRectF(cur_x, cy - fm.height() / 2, w, fm.height()),
                    Qt.AlignCenter,
                    sym,
                )
            cur_x += w + symbol_gap

        # draw gradient ribbon X
        rx = x + left_w + text_ribbon_gap
        mid_x = rx + ribbon_w / 2

        # build two intertwined S-curves
        path1 = QPainterPath()
        path1.moveTo(QPointF(rx, cy + ribbon_h * 0.38))
        path1.cubicTo(
            QPointF(rx + ribbon_w * 0.05, cy + ribbon_h * 0.05),
            QPointF(rx + ribbon_w * 0.20, cy - ribbon_h * 0.55),
            QPointF(mid_x, cy - ribbon_h * 0.10),
        )
        path1.cubicTo(
            QPointF(rx + ribbon_w * 0.80, cy + ribbon_h * 0.35),
            QPointF(rx + ribbon_w * 0.95, cy - ribbon_h * 0.15),
            QPointF(rx + ribbon_w, cy - ribbon_h * 0.42),
        )

        path2 = QPainterPath()
        path2.moveTo(QPointF(rx, cy - ribbon_h * 0.38))
        path2.cubicTo(
            QPointF(rx + ribbon_w * 0.05, cy - ribbon_h * 0.05),
            QPointF(rx + ribbon_w * 0.20, cy + ribbon_h * 0.55),
            QPointF(mid_x, cy + ribbon_h * 0.10),
        )
        path2.cubicTo(
            QPointF(rx + ribbon_w * 0.80, cy - ribbon_h * 0.35),
            QPointF(rx + ribbon_w * 0.95, cy + ribbon_h * 0.15),
            QPointF(rx + ribbon_w, cy + ribbon_h * 0.42),
        )

        # subtle shadow/glow behind the ribbon
        shadow = QPen(QColor(0, 0, 0, 120))
        shadow.setWidthF(ribbon_w * 0.22)
        shadow.setCapStyle(Qt.RoundCap)
        painter.setPen(shadow)
        painter.translate(0, ribbon_h * 0.04)
        painter.drawPath(path1)
        painter.drawPath(path2)
        painter.translate(0, -ribbon_h * 0.04)

        # main gradient stroke along the ribbon
        gradient = QLinearGradient(rx, cy + ribbon_h / 2, rx + ribbon_w, cy - ribbon_h / 2)
        gradient.setColorAt(0.0, QColor("#a855f7"))
        gradient.setColorAt(0.5, QColor("#6366f1"))
        gradient.setColorAt(1.0, QColor("#22d3ee"))

        ribbon_pen = QPen()
        ribbon_pen.setBrush(QBrush(gradient))
        ribbon_pen.setWidthF(ribbon_w * 0.14)
        ribbon_pen.setCapStyle(Qt.RoundCap)
        ribbon_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(ribbon_pen)
        painter.drawPath(path1)
        painter.drawPath(path2)

        # glossy highlight stroke
        gloss = QPen(QColor(255, 255, 255, 170))
        gloss.setWidthF(ribbon_w * 0.045)
        gloss.setCapStyle(Qt.RoundCap)
        painter.setPen(gloss)
        painter.translate(0, -ribbon_h * 0.06)
        painter.drawPath(path1)
        painter.drawPath(path2)

# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

"""Small, reusable controls with a quiet green visual language."""

from PySide6.QtCore import Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from .theme import make_icon


def label(text, name='', wrap=False):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


class Toggle(QAbstractButton):
    """An animated switch that also supports keyboard and accessibility."""

    def __init__(self, title='', parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setAccessibleName(title)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(42, 24)
        self._position = 0.0
        self.animation = QPropertyAnimation(self, b'position', self)
        self.animation.setDuration(160)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.toggled.connect(self.animate)

    def animate(self, checked):
        self.animation.stop()
        self.animation.setStartValue(self._position)
        self.animation.setEndValue(1.0 if checked else 0.0)
        self.animation.start()
        self.update()

    def get_position(self):
        return self._position

    def set_position(self, value):
        self._position = value
        self.update()

    position = Property(float, get_position, set_position)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        color = '#2b825d' if self.isChecked() else '#d9e2dc'
        if not self.isEnabled():
            color = '#e5ebe6'
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(QRectF(1, 1, 40, 22), 11, 11)
        painter.setBrush(QColor('white'))
        painter.drawEllipse(QRectF(4 + self._position * 18, 4, 16, 16))
        if self.hasFocus():
            painter.setPen(QPen(QColor('#7cab8c'), 1, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(.5, .5, 41, 23), 11, 11)
        painter.end()


class Card(QFrame):
    def __init__(self, title='', description='', icon=None):
        super().__init__()
        self.setObjectName('card')
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(23, 21, 23, 22)
        self.layout.setSpacing(17)
        self.header = QHBoxLayout()
        self.header.setSpacing(11)
        if icon:
            image = QLabel()
            image.setPixmap(make_icon(icon, '#548468', 23))
            image.setFixedSize(25, 25)
            self.header.addWidget(image)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        if title:
            texts.addWidget(label(title, 'cardTitle'))
        if description:
            texts.addWidget(label(description, 'muted', True))
        self.header.addLayout(texts, 1)
        self.layout.addLayout(self.header)


class Hero(QWidget):
    """Painted vector illustration; no downloaded assets or image dependencies."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        return QSize(800, 190)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        gradient = QLinearGradient(bounds.topLeft(), bounds.bottomRight())
        gradient.setColorAt(0, QColor('#e5efe2'))
        gradient.setColorAt(.65, QColor('#edf3e5'))
        gradient.setColorAt(1, QColor('#e4eee7'))
        painter.setPen(QPen(QColor('#dce8d8')))
        painter.setBrush(gradient)
        painter.drawRoundedRect(bounds, 16, 16)
        painter.setPen(QColor('#5e8061'))
        painter.setFont(QFont('Segoe UI', 9, QFont.Weight.DemiBold))
        painter.drawText(28, 34, 'YOUR NEWS, YOUR RHYTHM')
        painter.setPen(QColor('#244b33'))
        painter.setFont(QFont('Microsoft YaHei UI', 22, QFont.Weight.Bold))
        painter.drawText(27, 80, '让值得关注的消息，找到你。')
        painter.setFont(QFont('Microsoft YaHei UI', 10))
        painter.setPen(QColor('#6a826b'))
        painter.drawText(29, 113, '从信息采集到 AI 筛选，再到每一次恰好的推送。')
        painter.setBrush(QColor('#f8fbf4'))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(28, 138, 135, 27), 13, 13)
        painter.setPen(QColor('#4a7750'))
        painter.setFont(QFont('Microsoft YaHei UI', 8))
        painter.drawText(QRectF(28, 138, 135, 27), Qt.AlignmentFlag.AlignCenter, '轻量运行  ·  由你掌控')
        if self.width() > 740:
            painter.save()
            painter.translate(self.width() - 220, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#d9e6d7'))
            painter.drawEllipse(QRectF(30, 8, 150, 150))
            painter.translate(40, 27)
            painter.rotate(-9)
            painter.setBrush(QColor('#b9cdb6'))
            painter.drawRoundedRect(QRectF(7, 7, 122, 110), 12, 12)
            painter.setBrush(QColor('#fcfef8'))
            painter.drawRoundedRect(QRectF(0, 0, 122, 110), 12, 12)
            painter.setBrush(QColor('#d8e7d3'))
            painter.drawRoundedRect(QRectF(15, 15, 92, 37), 6, 6)
            painter.setPen(QPen(QColor('#7a9b75'), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for y, width in [(66, 77), (78, 88), (90, 53)]:
                painter.drawLine(QPointF(17, y), QPointF(width, y))
            painter.restore()
            painter.setBrush(QColor('#659a73'))
            painter.setPen(Qt.PenStyle.NoPen)
            x = self.width() - 55
            path = QPainterPath(QPointF(x, 113))
            path.lineTo(x - 29, 123)
            path.lineTo(x - 13, 128)
            path.lineTo(x - 8, 145)
            path.closeSubpath()
            painter.drawPath(path)
        painter.end()


def note(text):
    frame = QFrame()
    frame.setObjectName('note')
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(16, 13, 16, 13)
    layout.addWidget(label(text, 'muted', True))
    return frame

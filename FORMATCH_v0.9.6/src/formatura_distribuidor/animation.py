from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class DistributionAnimation(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(265, 86)
        self.phase = 0.0
        self.running = False
        self.complete = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(35)

    def set_running(self, running: bool) -> None:
        self.running = running
        self.complete = False
        self.update()

    def set_complete(self) -> None:
        self.running = False
        self.complete = True
        self.update()

    def tick(self) -> None:
        if self.running:
            self.phase = (self.phase + 0.018) % 1.0
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        green = QColor("#22df55")
        muted = QColor("#304138")
        painter.setPen(QPen(green if self.running else muted, 2))
        painter.setBrush(QColor("#101713"))
        painter.drawRoundedRect(5, 25, 55, 40, 7, 7)
        painter.drawRoundedRect(205, 25, 55, 40, 7, 7)
        painter.setPen(QColor("#dce7df"))
        painter.drawText(12, 50, "EVENTOS")
        painter.drawText(216, 50, "ÁLBUM")
        painter.setPen(QPen(green if self.running else muted, 2))
        painter.drawLine(65, 45, 200, 45)
        if self.running:
            x = 70 + int(self.phase * 125)
            painter.setBrush(green)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(x, 34, 17, 22, 3, 3)
        elif self.complete:
            painter.setPen(QPen(green, 4))
            painter.drawLine(120, 44, 132, 56)
            painter.drawLine(132, 56, 154, 29)


from PyQt6 import QtGui as G, QtWidgets as W, QtCore as C


class AlarmLight(W.QWidget):
    def __init__(self, label_text: str, active_color: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 50)
        self.label_text = label_text
        self.active_color = G.QColor(active_color)
        self.is_active = False

    def set_active(self, active: bool):
        if self.is_active != active:
            self.is_active = active
            self.update()

    def paintEvent(self, event):
        p = G.QPainter(self)
        p.setRenderHint(G.QPainter.RenderHint.Antialiasing)

        pal = self.palette()
        tc  = pal.color(G.QPalette.ColorRole.WindowText)
        bc  = pal.color(G.QPalette.ColorRole.Mid)
        off = pal.color(G.QPalette.ColorRole.Dark)

        cx, cy, r = self.width() / 2, 20, 12
        rect = C.QRectF(cx - r, cy - r, r * 2, r * 2)

        col = self.active_color if self.is_active else off
        grad = G.QRadialGradient(cx - r / 3, cy - r / 3, r)
        grad.setColorAt(0.0, col.lighter(150 if self.is_active else 110))
        grad.setColorAt(1.0, col)

        p.setBrush(grad)
        p.setPen(G.QPen(bc, 1.5))
        p.drawEllipse(rect)

        p.setPen(tc)
        p.setFont(G.QFont("Segoe UI", 9, G.QFont.Weight.Bold))
        p.drawText(self.rect(),
                   C.Qt.AlignmentFlag.AlignBottom | C.Qt.AlignmentFlag.AlignHCenter,
                   self.label_text)
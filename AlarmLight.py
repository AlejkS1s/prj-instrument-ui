from PyQt6 import QtGui, QtWidgets, QtCore

class AlarmLight(QtWidgets.QWidget):
    def __init__(self, label_text: str, active_color: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(50, 70)
        self.label_text = label_text
        self.active_color = QtGui.QColor(active_color)
        self.is_active = False

    def set_active(self, active: bool):
        if self.is_active != active:
            self.is_active = active
            self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        
        pal = self.palette()
        text_color = pal.color(QtGui.QPalette.ColorRole.WindowText)
        border_color = pal.color(QtGui.QPalette.ColorRole.Mid)
        off_color = pal.color(QtGui.QPalette.ColorRole.Dark)

        # 1. Draw the "Light" (Circle with Radial Gradient)
        cx, cy, r = self.width() / 2, 25, 15
        light_rect = QtCore.QRectF(cx - r, cy - r, r * 2, r * 2)

        grad = QtGui.QRadialGradient(cx - r/3, cy - r/3, r)
        if self.is_active:
            grad.setColorAt(0.0, self.active_color.lighter(150))
            grad.setColorAt(1.0, self.active_color)
        else:
            grad.setColorAt(0.0, off_color.lighter(110))
            grad.setColorAt(1.0, off_color)

        # Border and Fill
        p.setBrush(grad)
        p.setPen(QtGui.QPen(border_color, 1.5))
        p.drawEllipse(light_rect)

        # 2. Draw the Text Description
        p.setPen(text_color)
        p.setFont(QtGui.QFont("Segoe UI", 9, QtGui.QFont.Weight.Bold))
        p.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignBottom | QtCore.Qt.AlignmentFlag.AlignHCenter, self.label_text)
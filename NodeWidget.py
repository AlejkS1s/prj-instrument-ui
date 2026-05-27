# ===========================================================================
# NodeWidget — view
# ===========================================================================
from PyQt6 import QtWidgets, QtGui, QtCore

from RadialGauge import RadialGauge
from constants import HIGH_TEMP_THRESH_F, LOW_TEMP_THRESH_F, TEMP_MAX_F, TEMP_MIN_F


class NodeWidget(QtWidgets.QFrame):

    def __init__(self, node_id: str, color: str, display_index: int):
        super().__init__()
        self.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        # self.setMinimumSize(250, 270)
        # self.setMaximumSize(280, 150)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Expanding)

        self.bg_no_blend = self.palette().color(QtGui.QPalette.ColorRole.Base).name()
        self.base_color = color
        qc = QtGui.QColor(color)
        self.bg_blend = f"rgba({qc.red()}, {qc.green()}, {qc.blue()}, 0.1)"
        text_color = self.palette().color(QtGui.QPalette.ColorRole.WindowText).name()
        sub_id_text_color = self.palette().color(QtGui.QPalette.ColorRole.WindowText).darker(120).name()

        self.setStyleSheet(
            f"NodeWidget {{ border: 2px solid {color}; border-radius: 8px;"
            f" background-color: {self.bg_blend}; }}"
        )

        # Primary Horizontal Separation: Left Content vs Right Gauge
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(10, 8, 10, 8)
        main_layout.setSpacing(6)

        # Left Column Layout (Header, Telemetry text, Alarms)
        identifier_vbox = QtWidgets.QVBoxLayout()
        identifier_vbox.setSpacing(2)

        # Title/ID Area — use a sequential display index rather than raw ID
        self.display_index = display_index
        self.lbl_title = QtWidgets.QLabel(f"Node {self.display_index}")
        
        title_font = QtGui.QFont(self.font())
        ps = title_font.pointSizeF()
        if ps <= 0:
            ps = QtGui.QFont().pointSizeF()
        title_font.setPointSizeF(ps * 1.15)
        title_font.setBold(True)

        self.lbl_title.setFont(title_font)
        self.lbl_title.setStyleSheet(f"color: {text_color}; background: transparent;")
        
        self.lbl_sub_id = QtWidgets.QLabel(f"ID: {node_id}")
        self.lbl_sub_id.setStyleSheet(f"color: {sub_id_text_color}; background: transparent;")

        identifier_vbox.addWidget(self.lbl_title)
        identifier_vbox.addWidget(self.lbl_sub_id)

        identifier_vbox.addStretch()

        #  ---------------------------------

        # Alarm Ribbon
        status_ribbon = QtWidgets.QHBoxLayout()
        status_ribbon.setSpacing(4)

        self.lbl_low = QtWidgets.QLabel("L")
        self.lbl_low.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self.lbl_high = QtWidgets.QLabel("H")
        self.lbl_high.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        status_ribbon.addWidget(self.lbl_low)
        status_ribbon.addWidget(self.lbl_high)

        # Label for optional state text (e.g. "DISCONNECTED")
        self.lbl_state = QtWidgets.QLabel("")
        self.lbl_state.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        lbl_state_font = QtGui.QFont(self.font())
        lbl_state_font.setPointSizeF(ps * 0.85)
        lbl_state_font.setBold(True)
        self.lbl_state.setFont(lbl_state_font)
        
        self.lbl_state.setStyleSheet(
             " background-color: #D32F2F; border-radius: 4px; padding: 2px 4px;"
        )
        self.lbl_state.hide()
        status_ribbon.addWidget(self.lbl_state)
        status_ribbon.addStretch(1)

        # -------------------------- Gauge Area --------------------------

        gauge_vbox = QtWidgets.QVBoxLayout()
        gauge_vbox.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.gauge = RadialGauge(TEMP_MIN_F, TEMP_MAX_F, LOW_TEMP_THRESH_F, HIGH_TEMP_THRESH_F, parent=self)
        gauge_vbox.addWidget(self.gauge, stretch=1)

        self._last_high_alarm = False
        self._last_low_alarm = False

        main_layout.addLayout(identifier_vbox)
        main_layout.addLayout(gauge_vbox, stretch=1)
        main_layout.addLayout(status_ribbon)

    def resizeEvent(self, event: QtGui.QResizeEvent):
        super().resizeEvent(event)

    def update_data(self, temp_f: float, high_alarm: bool, low_alarm: bool, state: str | None = None):
        """Update metrics UI states, forcing gauge colors red on disconnect or alarm states."""
        self.gauge.set_value(temp_f)

        is_bad_state = True if state else False

        self.gauge.set_fault_state(is_bad_state)
        self._last_high_alarm = high_alarm
        self._last_low_alarm = low_alarm

        if state and state.strip():
            s = state.strip().upper()
            self.lbl_state.setText(s)
            self.lbl_state.show()
        else:
            self.lbl_state.hide()
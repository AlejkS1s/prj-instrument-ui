from PyQt6 import QtWidgets as W, QtGui as G, QtCore as C

from gui.AlarmLight import AlarmLight
from gui.radial_gauge import RadialGauge
from config import HIGH_TEMP_THRESH_F, LOW_TEMP_THRESH_F, TEMP_MAX_F, TEMP_MIN_F


class NodeWidget(W.QFrame):

    def __init__(self, node_id: str, color: str, display_index: int):
        super().__init__()
        self.setFrameShape(W.QFrame.Shape.StyledPanel)
        self.setSizePolicy(W.QSizePolicy.Policy.Preferred, W.QSizePolicy.Policy.Expanding)

        self.setMinimumSize(300, 220)
        pal = self.palette()
        qc = G.QColor(color)
        self.base_color  = color
        self.bg_blend    = f"rgba({qc.red()}, {qc.green()}, {qc.blue()}, 0.1)"
        tc  = pal.color(G.QPalette.ColorRole.WindowText).name()
        stc = pal.color(G.QPalette.ColorRole.WindowText).darker(120).name()

        self.setStyleSheet(
            f"NodeWidget {{ border: 2px solid {color}; border-radius: 8px;"
            f" background-color: {self.bg_blend}; }}"
        )

        ml = W.QVBoxLayout(self)
        ml.setContentsMargins(8, 6, 8, 6)
        ml.setSpacing(4)

        # --- Header row ---
        hr = W.QHBoxLayout()
        hr.setSpacing(6)

        id_box = W.QVBoxLayout()
        id_box.setSpacing(1)

        self.display_index = display_index
        self.lbl_title = W.QLabel(f"Node {display_index}")
        tf = G.QFont(self.font())
        ps = tf.pointSizeF() or G.QFont().pointSizeF()
        tf.setPointSizeF(ps * 1.15)
        tf.setBold(True)
        self.lbl_title.setFont(tf)
        self.lbl_title.setStyleSheet(f"color: {tc}; background: transparent;")

        self.lbl_sub_id = W.QLabel(f"ID: {node_id}")
        self.lbl_sub_id.setStyleSheet(f"color: {stc}; background: transparent;")

        id_box.addWidget(self.lbl_title)
        id_box.addWidget(self.lbl_sub_id)

        ah = W.QHBoxLayout()
        ah.setSpacing(2)
        self.light_low  = AlarmLight("LOW",  "#2196F3", self)
        self.light_high = AlarmLight("HIGH", "#F44336", self)
        ah.addWidget(self.light_low,  alignment=C.Qt.AlignmentFlag.AlignRight)
        ah.addWidget(self.light_high, alignment=C.Qt.AlignmentFlag.AlignRight)

        hr.addLayout(id_box)
        hr.addStretch(1)
        hr.addLayout(ah)

        # --- Status ribbon ---
        srw = W.QWidget(self)
        srw.setSizePolicy(W.QSizePolicy.Policy.Preferred, W.QSizePolicy.Policy.Fixed)
        sr = W.QHBoxLayout(srw)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.setSpacing(4)

        self.lbl_state = W.QLabel("")
        self.lbl_state.setAlignment(C.Qt.AlignmentFlag.AlignCenter)
        sf = G.QFont(self.font())
        sf.setPointSizeF(ps * 0.75)
        sf.setBold(True)
        sf.setCapitalization(G.QFont.Capitalization.AllUppercase)
        self.lbl_state.setFont(sf)
        self.lbl_state.setStyleSheet(
            "color: #FFFFFF; background-color: #D32F2F; border-radius: 4px; padding: 2px 2px;"
        )
        srw.setMinimumHeight(G.QFontMetrics(sf).height() + 5)
        self.srw = srw
        self.lbl_state.setHidden(True)
        sr.addWidget(self.lbl_state)
        sr.addStretch(1)

        # --- Gauge ---
        gc = W.QHBoxLayout()
        gc.setContentsMargins(2, 0, 2, 0)
        self.gauge = RadialGauge(TEMP_MIN_F, TEMP_MAX_F, LOW_TEMP_THRESH_F, HIGH_TEMP_THRESH_F, parent=self)
        gc.addWidget(self.gauge, stretch=1)

        ml.addLayout(hr)
        ml.addLayout(gc, stretch=1)
        ml.addWidget(srw)

    def update_data(self, temp_f: float, high_alarm: bool, low_alarm: bool, state: str | None = None):
        """Update metrics UI states, forcing gauge colors red on disconnect or alarm states."""
        self.gauge.set_value(temp_f)
        self.light_low.set_active(low_alarm)
        self.light_high.set_active(high_alarm)
        self.gauge.set_fault_state(bool(state))

        if state and state.strip():
            self.lbl_state.setText(state.strip().upper())
            self.lbl_state.setHidden(False)
        else:
            self.lbl_state.setText("")
            self.lbl_state.setHidden(True)
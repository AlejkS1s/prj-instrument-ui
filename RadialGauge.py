
import math
from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib.pylab import angle
from numpy import angle

NEEDLE_COLOR_DEFAULT: str = "#455A64"

class GaugePalette:
    """
    All widget colours in one immutable value.
    Pass a custom instance to RadialGauge to theme the widget.
    """

    # Cold zone gradient  (min_val → low_threshold)
    cold_start:    str   = "#1A237E"
    cold_end:      str   = "#26C6DA"

    # Normal zone multi-stop  (low_threshold → high_threshold)
    # Each entry: (t ∈ [0,1],  (R, G, B))
    normal_stops:  tuple = (
        (0.0, (38,  198, 218)),
        (0.3, (102, 187, 106)),
        (0.7, (255, 167,  38)),
        (1.0, (239,  83,  80)),
    )

    # gauge track background (full sweep)
    track_bg:      str   = "#E0E0E0"

    # Alarm zone gradient  (high_threshold → max_val)
    alarm_start:   str   = "#EF5350"
    alarm_end:     str   = "#FF0000"

    # Fault / alarm state  (solid arc colour)
    fault_primary: str   = "#FF6F6F"

    # Inactive arc portion
    seg_inactive:  str   = "#E0E0E0"

    # Threshold tick marks
    mark_low:      str   = "#2196F3"
    mark_high:     str   = "#F44336"

    # Dim text / sub-labels
    text_dim:      str   = "#9E9E9E"

    # Halo behind needle pivot  — (R, G, B, A)
    halo_rgba:     tuple = (230, 235, 240, 160)

    # Default needle body colour
    needle:        str   = NEEDLE_COLOR_DEFAULT

    # Font family for all text
    font_family:   str   = "Segoe UI"

    # Widget background
    background:    str   = "#28101052"


class _R:
    """
    Single source of truth for every size ratio.
    Change a value here; every paint method that references it updates.
    """

    ARC_START: int = 220    # degrees — low-value end of the sweep
    ARC_SPAN:  int = -260   # negative → clockwise; high-value end = 320°

    # Arc ring band
    RING_OUTER       = 0.400
    RING_INNER       = 0.340

    # Threshold tick marks
    MARK_PEN_W       = 4.0     # px — intentionally not sz-scaled for crispness
    MARK_INNER_GAP   = 0.1   # gap between RING_OUTER and tick start
    MARK_LENGTH      = 0.080   # radial length of each tick

    # Scale ticks (outside)
    SCALE_TICK_OUTER_GAP = 0.020   # gap from RING_OUTER to start of outside ticks
    SCALE_TICK_LENGTH    = 0.035   # radial length of each scale tick
    SCALE_TICK_PEN_W     = 2.0     # px — thinner than threshold marks
    SCALE_TICK_COUNT     = 20       # number of ticks (including endpoints)

    # Scale labels
    LABEL_GAP        = 0.20   # added to RING_OUTER to get label radius
    LABEL_RECT_W     = 0.220   # text bounding rect width
    LABEL_RECT_H     = 0.090   # text bounding rect height
    LABEL_COUNT      = 7       # number of labels, evenly spaced (including endpoints)

    FONT_LABEL       = 0.048
    FONT_LABEL_MIN   = 7       # pt floor — prevents < 1 pt at tiny sizes


    # Needle
    # HUB_R            = 0.048   # pivot circle radius
    # HUB_HOLE_RATIO   = 0.400   # hollow-hole radius as fraction of HUB_R
    # NEEDLE_BASE_HALF = 0.036   # half-width at pivot end
    # NEEDLE_TIP_HALF  = 0.007   # half-width and tip-cap radius at tip end
    # NEEDLE_REACH     = 0.900   # fraction of r_in the tip reaches
    # HALO_R           = 0.065   # halo disc radius

    # Centre text block
    TEXT_Y_OFFSET    = 0.180   # cy + sz * Y_OFFSET = top of primary rect
    TEXT_MAIN_W      = 0.500
    TEXT_MAIN_H      = 0.140
    FONT_MAIN        = 0.060

    TEXT_SUB_GAP     = 0.125   # additional offset below TEXT_Y_OFFSET
    TEXT_SUB_W       = 0.420
    TEXT_SUB_H       = 0.100
    FONT_SUB         = 0.050

    MIN_W_SIZE         = 200
    MIN_H_SIZE         = 150


class RadialGauge(QtWidgets.QWidget):


    def __init__(
        self,
        min_val:        float,
        max_val:        float,
        low_threshold:  float,
        high_threshold: float,
        needle_color:   str           = NEEDLE_COLOR_DEFAULT,
        palette:        GaugePalette  = GaugePalette(),
        parent:         QtWidgets.QWidget | None = None,
        main_unit:      str           = "°F",
    ) -> None:
        super().__init__(parent)

        self.min_val        = min_val
        self.max_val        = max_val
        self.low_threshold  = low_threshold
        self.high_threshold = high_threshold
        self.main_unit      = main_unit

        self._palette      = palette
        self._needle_color = QtGui.QColor(needle_color)

        self.temp_f    = float(min_val)
        self.temp_c    = 0.0
        self._is_fault = False

        self.setMinimumSize(_R.MIN_W_SIZE, _R.MIN_H_SIZE)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )

        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(self._palette.background))
        self.setPalette(pal)

    def set_value(self, temp_f: float, temp_c: float | None = None) -> None:
        self.temp_f = max(self.min_val, min(self.max_val, temp_f))
        self.temp_c = temp_c if temp_c is not None else (self.temp_f - 32) * 5 / 9
        self.update()

    def set_fault_state(self, fault: bool) -> None:
        """Switches the active arc to a solid fault colour."""
        self._is_fault = fault
        self.update()

    def set_thresholds(self, low: float, high: float) -> None:
        self.low_threshold = low
        self.high_threshold = high
        self.update()

    def _pct(self, v: float) -> float:
        """Normalise v → [0, 1] within [min_val, max_val]."""
        if self.max_val == self.min_val:
            return 0.0
        return (v - self.min_val) / (self.max_val - self.min_val)

    def _val_to_angle(self, v: float) -> float:
        return _R.ARC_START + _R.ARC_SPAN * self._pct(v)

    def _pt(self, angle_deg: float, radius: float, cx: float, cy: float) -> QtCore.QPointF:
        rad = math.radians(angle_deg)
        return QtCore.QPointF(cx + radius * math.cos(rad), cy - radius * math.sin(rad))

    def _lerp(self, c1: QtGui.QColor, c2: QtGui.QColor, t: float) -> QtGui.QColor:
        t = max(0.0, min(1.0, t))
        return QtGui.QColor(
            int(c1.red()   + (c2.red()   - c1.red())   * t),
            int(c1.green() + (c2.green() - c1.green()) * t),
            int(c1.blue()  + (c2.blue()  - c1.blue())  * t),
        )

    def _multi_lerp(self, stops: tuple, t: float) -> QtGui.QColor:
        for i in range(len(stops) - 1):
            if t <= stops[i + 1][0]:
                st = (t - stops[i][0]) / (stops[i + 1][0] - stops[i][0])
                return self._lerp(
                    QtGui.QColor(*stops[i][1]),
                    QtGui.QColor(*stops[i + 1][1]),
                    st,
                )
        return QtGui.QColor(*stops[-1][1])

    def _fault_grad(self) -> QtGui.QConicalGradient:
        """
        Returns a smooth alternating conical gradient for the fault state.
        Aligns the stripes to the start of the gauge arc.
        """
        cx, cy = self.width() / 2.0, self.height() / 2.0
        # Start the gradient at the same angle as the gauge
        grad = QtGui.QConicalGradient(QtCore.QPointF(cx, cy), _R.ARC_START)
        
        c1 = QtGui.QColor(self._palette.fault_primary)
        c2 = c1.darker(200)  # Distinct dark red for the stripe
        
        # Evenly spaced alternating stops create soft transitions instead of
        # hard block edges.
        num_segments = 20
        for i in range(num_segments + 1):
            t = i / num_segments
            grad.setColorAt(t, c1 if i % 2 == 0 else c2)
            
        return grad
    
    def _lerp_grad(self, pct: float) -> QtGui.QConicalGradient:
        """
        Returns a multi-stop conical gradient following the gauge's 
        Cold, Normal, and Alarm zones.
        """
        cx, cy = self.width() / 2.0, self.height() / 2.0
        
        # Gauge is clockwise (ARC_SPAN -260), but QConicalGradient is CCW.
        # We start at the logical 'end' of the gauge and flow CCW back to the start.
        start_angle = _R.ARC_START + _R.ARC_SPAN
        grad = QtGui.QConicalGradient(QtCore.QPointF(cx, cy), start_angle)
        
        # The fraction of the full 360 circle occupied by the gauge sweep
        span_ratio = abs(_R.ARC_SPAN) / 360.0
        pal = self._palette

        def add_stop(val_pct: float, color: QtGui.QColor | str):
            # Reverse the mapping: 0% value is at the far end of the CCW gradient
            stop = span_ratio * (1.0 - val_pct)
            grad.setColorAt(max(0.0, min(1.0, stop)), QtGui.QColor(color))

        # 1. Cold Zone (min to low threshold)
        add_stop(0.0, pal.cold_start)
        low_p = self._pct(self.low_threshold)
        add_stop(low_p, pal.cold_end)
        
        # 2. Normal Zone (multi-stop lerp)
        high_p = self._pct(self.high_threshold)
        norm_range = high_p - low_p
        if norm_range > 0:
            for t, rgb in pal.normal_stops:
                add_stop(low_p + t * norm_range, QtGui.QColor(*rgb))
            
        # 3. Alarm Zone (high threshold to max)
        add_stop(high_p, pal.alarm_start)
        add_stop(1.0, pal.alarm_end)
        
        return grad

    def _get_color(self, val: float) -> QtGui.QColor:
        """Three-zone gradient: cold → normal spectrum → alarm."""
        pal = self._palette
        if val < self.low_threshold:
            t = (val - self.min_val) / max(self.low_threshold - self.min_val, 1e-9)
            return self._lerp(QtGui.QColor(pal.cold_start), QtGui.QColor(pal.cold_end), t)
        if val < self.high_threshold:
            t = (val - self.low_threshold) / max(self.high_threshold - self.low_threshold, 1e-9)
            return self._multi_lerp(pal.normal_stops, t)
        t = (val - self.high_threshold) / max(self.max_val - self.high_threshold, 1e-9)
        return self._lerp(QtGui.QColor(pal.alarm_start), QtGui.QColor(pal.alarm_end), t)

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHints(
            QtGui.QPainter.RenderHint.Antialiasing
            | QtGui.QPainter.RenderHint.TextAntialiasing
        )

        p.fillRect(self.rect(), QtGui.QColor(self._palette.background))

        W, H = self.width(), self.height()
        sz   = min(W, H)
        cx   = W / 2.0
        cy   = H / 2.0

        r_out  = sz * _R.RING_OUTER
        r_in   = sz * _R.RING_INNER

        sw   = max(5, int(sz * 0.048))      # arc stroke width in px
        pad  = sw * 4.8                 # padding to prevent needle and text from touching the arc

        r_arc = (sz * 0.75) / 2.0
        rect = QtCore.QRectF(cx - r_arc, cy - r_arc, r_arc * 2, r_arc * 2)

        self._draw_threshold_marks(p, cx, cy, r_out, sz)
        self._draw_scale_ticks_outside(p, cx, cy, r_out, sz)
        self._draw_track(p, rect, sw)
        self._draw_continuous_bar(p, rect, sw)
        self._draw_labels(p, cx, cy, r_out, sz)
        self._draw_needle(p, cx, cy, r_in, sz)
        self._draw_center_text(p, cx, cy, sz)

        p.end()

    def _draw_track(self, p: QtGui.QPainter, rect: QtCore.QRectF, sw: int) -> None:
        """Draws the thick, light gray background track."""
        col = self.palette().color(QtGui.QPalette.ColorRole.Midlight)
        p.setPen(QtGui.QPen(col, sw, cap=QtCore.Qt.PenCapStyle.RoundCap))

        p.drawArc(rect, _R.ARC_START * 16, _R.ARC_SPAN * 16)

    def _draw_continuous_bar(self, p: QtGui.QPainter, rect: QtCore.QRectF, sw: int) -> None:
        """Draws the filled progress bar with gradient logic."""
        pct = self._pct(self.temp_f)
        if pct <= 0: return

        # For a truly continuous look, we use a gradient brush on the pen
        color = self._fault_grad() if self._is_fault else self._lerp_grad(pct)
        
        pen = QtGui.QPen(color, sw)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, _R.ARC_START * 16, int(_R.ARC_SPAN * pct * 16))

    def _draw_threshold_marks(
        self, p: QtGui.QPainter,
        cx: float, cy: float,
        r_out: float, sz: float,
    ) -> None:
        pal     = self._palette
        r_inner = r_out + sz * _R.MARK_INNER_GAP
        r_outer = r_inner + sz * _R.MARK_LENGTH

        for val, color_str in (
            (self.low_threshold,  pal.mark_low),
            (self.high_threshold, pal.mark_high),
        ):
            angle = self._val_to_angle(val)
            p.setPen(QtGui.QPen(
                QtGui.QColor(color_str),
                _R.MARK_PEN_W,
                cap=QtCore.Qt.PenCapStyle.RoundCap,
            ))
            p.drawLine(
                self._pt(angle, r_inner, cx, cy),
                self._pt(angle, r_outer, cx, cy),
            )

    def _draw_scale_ticks_outside(
        self, p: QtGui.QPainter,
        cx: float, cy: float,
        r_out: float, sz: float,
    ) -> None:
        """Draw scale tick marks outside the gauge ring."""
        r_inner = r_out + sz * _R.SCALE_TICK_OUTER_GAP
        r_outer = r_inner + sz * _R.SCALE_TICK_LENGTH

        p.setPen(QtGui.QPen(
            QtGui.QColor(self._palette.text_dim),
            _R.SCALE_TICK_PEN_W,
            cap=QtCore.Qt.PenCapStyle.RoundCap,
        ))

        # Draw ticks at regular intervals across the scale
        for i in range(_R.SCALE_TICK_COUNT):
            # Calculate value at this tick position
            pct = i / (_R.SCALE_TICK_COUNT - 1)
            val = self.min_val + pct * (self.max_val - self.min_val)
            angle = self._val_to_angle(val)

            p.drawLine(
                self._pt(angle, r_inner, cx, cy),
                self._pt(angle, r_outer, cx, cy),
            )

    def _draw_labels(
        self, p: QtGui.QPainter,
        cx: float, cy: float,
        r_out: float, sz: float,
    ) -> None:
        pal   = self._palette
        r_lbl = r_out + sz * _R.LABEL_GAP
        lw    = sz * _R.LABEL_RECT_W
        lh    = sz * _R.LABEL_RECT_H

        p.setPen(QtGui.QColor(pal.text_dim))
        p.setFont(QtGui.QFont(
            pal.font_family,
            max(_R.FONT_LABEL_MIN, round(sz * _R.FONT_LABEL)),
        ))

        for i in range(_R.LABEL_COUNT):
            t     = i / max(_R.LABEL_COUNT - 1, 1)
            val   = self.min_val + t * (self.max_val - self.min_val)
            angle = _R.ARC_START + _R.ARC_SPAN * t
            pos   = self._pt(angle, r_lbl, cx, cy)
            p.drawText(
                QtCore.QRectF(pos.x() - lw / 2, pos.y() - lh / 2, lw, lh),
                QtCore.Qt.AlignmentFlag.AlignCenter,
                f"{int(val)}{self.main_unit}",
            )

    def _draw_needle(self, p: QtGui.QPainter, cx: float, cy: float, r: float, sz: float) -> None:
        angle = self._val_to_angle(self.temp_f)
        rad   = math.radians(angle)
        color = self.palette().color(QtGui.QPalette.ColorRole.Midlight)
    

        # --- Geometry ---
        # Needle direction (screen): (cos θ, -sin θ)  [y-down convention]
        # Perpendicular CCW:         (sin θ,  cos θ)
        perp_x = math.sin(rad)
        perp_y = math.cos(rad)

        base_half = sz * 0.036          # half-width at pivot
        tip_half  = sz * 0.007          # half-width at tip (tapered)
        tip_dist  = r * 0.90            # how far the tip reaches

        tip_x = cx + tip_dist * math.cos(rad)
        tip_y = cy - tip_dist * math.sin(rad)

        # Trapezoid corners (consistent CCW winding)
        bl = QtCore.QPointF(cx    + base_half * perp_x, cy    + base_half * perp_y)
        br = QtCore.QPointF(cx    - base_half * perp_x, cy    - base_half * perp_y)
        tl = QtCore.QPointF(tip_x + tip_half  * perp_x, tip_y + tip_half  * perp_y)
        tr = QtCore.QPointF(tip_x - tip_half  * perp_x, tip_y - tip_half  * perp_y)

        needle_body = QtGui.QPainterPath()
        needle_body.moveTo(bl)
        needle_body.lineTo(tl)
        needle_body.lineTo(tr)
        needle_body.lineTo(br)
        needle_body.closeSubpath()

        hub_r = sz * 0.048
        hub_circle = QtGui.QPainterPath()
        hub_circle.addEllipse(QtCore.QPointF(cx, cy), hub_r, hub_r)

        # Boolean union: needle body ∪ hub — avoids even-odd artifacts
        full_shape = needle_body.united(hub_circle)

        # Punch the hollow pivot hole
        hole = QtGui.QPainterPath()
        hole.addEllipse(QtCore.QPointF(cx, cy), hub_r * 0.40, hub_r * 0.40)
        final_path = full_shape.subtracted(hole)

        # Draw order: halo → needle
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(230, 235, 240, 160))
        p.drawEllipse(QtCore.QPointF(cx, cy), sz * 0.065, sz * 0.065)

        p.setBrush(color)
        p.drawPath(final_path)

    def _draw_center_text(
        self, p: QtGui.QPainter,
        cx: float, cy: float,
        sz: float,
    ) -> None:
        pal    = self._palette
        main_w = sz * _R.TEXT_MAIN_W
        main_h = sz * _R.TEXT_MAIN_H
        sub_w  = sz * _R.TEXT_SUB_W
        sub_h  = sz * _R.TEXT_SUB_H

        # Anchor below centre; clamp so text never overflows the widget boundary
        y_main = cy + sz * _R.TEXT_Y_OFFSET
        y_max  = self.height() - main_h - sub_h - 2
        y_main = min(y_main, y_max)
        y_sub  = y_main + sz * _R.TEXT_SUB_GAP

        # Primary value: use fault color when in fault, otherwise use
        # the application's theme default text color
        col = (
            QtGui.QColor(pal.fault_primary)
            if self._is_fault
            else self.palette().color(QtGui.QPalette.ColorRole.Text)
        )
        p.setPen(col)
        p.setFont(QtGui.QFont(
            pal.font_family,
            int(sz * _R.FONT_MAIN),
            QtGui.QFont.Weight.Bold,
        ))
        p.drawText(
            QtCore.QRectF(cx - main_w / 2, y_main, main_w, main_h),
            QtCore.Qt.AlignmentFlag.AlignCenter,
            f"{self.temp_f:.1f}{self.main_unit}",
        )

        # Sub-label (°C)
        p.setPen(self.palette().color(QtGui.QPalette.ColorRole.Text).darker(120))
        p.setFont(QtGui.QFont(pal.font_family, int(sz * _R.FONT_SUB)))
        p.drawText(
            QtCore.QRectF(cx - sub_w / 2, y_sub, sub_w, sub_h),
            QtCore.Qt.AlignmentFlag.AlignCenter,
            f"{self.temp_c:.1f}°C",
        )
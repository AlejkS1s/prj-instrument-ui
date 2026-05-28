
import math
from PyQt6 import QtCore, QtGui, QtWidgets
from matplotlib.pylab import angle
from numpy import angle

class GaugePalette:
    """
    All widget colours in one immutable value.
    Pass a custom instance to RadialGauge to theme the widget.
    """

    # Cold zone gradient  (min_val → low_threshold)
    low_start:    str   = "#1A237E"
    low_end:      str   = "#26C6DA"

    # Normal zone multi-stop  (low_threshold → high_threshold)
    # Each entry: (t ∈ [0,1],  (R, G, B))
    middle_stops:  tuple = (
        (0.0, (38,  198, 218)),
        (0.3, (102, 187, 106)),
        (0.7, (255, 167,  38)),
        (1.0, (239,  83,  80)),
    )

    # Alarm zone gradient  (high_threshold → max_val)
    high_start:   str   = "#EF5350"
    high_end:     str   = "#FF0000"

    # Fault / alarm state  (solid arc colour)
    fault_primary: str   = "#F95252"

    # Threshold tick marks
    mark_low:      str   = "#2196F3"
    mark_high:     str   = "#F44336"

    # Font family for all text
    font_family:   str   = "Segoe UI"

    # Widget background
    background:    str   = "#D8C7C752"


class _R:
    """
    Single source of truth for every size ratio.
    Change a value here; every paint method that references it updates.
    """

    ARC_START: int = 220    # degrees — low-value end of the sweep
    ARC_SPAN:  int = -260   # negative → clockwise; high-value end = 320°

    # Arc ring band
    RING_OUTER       = 0.320
    RING_INNER       = 0.260

    # Threshold tick marks
    MARK_PEN_W       = 5.4     # px — intentionally not sz-scaled for crispness
    MARK_INNER_GAP   = 0.065   # gap between RING_OUTER and tick start
    MARK_LENGTH      = 0.060   # radial length of each tick

    # Scale ticks (outside)
    SCALE_TICK_OUTER_GAP       = 0.010   # gap from RING_OUTER to start of outside ticks
    SCALE_TICK_MINOR_LENGTH    = 0.014   # radial length of each minor tick
    SCALE_TICK_MAJOR_LENGTH    = 0.028   # radial length of each major tick
    SCALE_TICK_PEN_W           = 1.5     # px — thinner than threshold marks
    SCALE_MINOR_TICKS_PER_GAP  = 4       # minor ticks between labeled major ticks

    # Scale labels
    LABEL_GAP        = 0.10   # added to RING_OUTER to get label radius
    LABEL_OFFSET     = 0.018   # extra radial separation from the major tick
    LABEL_RECT_W     = 0.220   # text bounding rect width
    LABEL_RECT_H     = 0.090   # text bounding rect height
    LABEL_COUNT      = 7       # number of labels, evenly spaced (including endpoints)

    FONT_LABEL       = 0.05
    FONT_LABEL_MIN   = 6       # pt floor — prevents < 1 pt at tiny sizes


    # Needle
    HUB_R            = 0.05   # Pivot circle radius
    HUB_HOLE_RATIO   = 0.430   # Hollow-hole radius as fraction of HUB_R
    NEEDLE_BASE_HALF = 0.038   # Half-width at pivot end
    NEEDLE_TIP_HALF  = 0.013   # Half-width and tip-cap radius at tip end
    NEEDLE_REACH     = 1.0     # Fraction of r_in the tip reaches
    NEEDLE_BORDER_W  = 1.6     # Pixel width of the needle outline
    HALO_R           = 0.068   # Translucent halo disc radius

    # Centre text block
    TEXT_Y_OFFSET    = 0.180   # cy + sz * Y_OFFSET = top of primary rect
    TEXT_MAIN_W      = 0.500
    TEXT_MAIN_H      = 0.140
    FONT_MAIN        = 0.070

    TEXT_SUB_GAP     = 0.125   # additional offset below TEXT_Y_OFFSET
    TEXT_SUB_W       = 0.420
    TEXT_SUB_H       = 0.100
    FONT_SUB         = 0.060

    MIN_W_SIZE         = 200
    MIN_H_SIZE         = 150


class RadialGauge(QtWidgets.QWidget):


    def __init__(
        self,
        min_val:        float,
        max_val:        float,
        low_threshold:  float,
        high_threshold: float,
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

        self._value    = min_val
        self._temp_c    = 0.0
        self._is_fault = False
        self._track_gradient = None

        self.setMinimumSize(_R.MIN_W_SIZE, _R.MIN_H_SIZE)

    def set_value(self, val_f: float, val_c: float | None = None) -> None:
        self._value = val_f
        self._temp_c = val_c if val_c is not None else (val_f - 32) * 5/9
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
        """Convert polar coordinates (angle in degrees, radius) to Cartesian QPointF."""
        rad = math.radians(angle_deg)
        return QtCore.QPointF(cx + radius * math.cos(rad), cy - radius * math.sin(rad))

    def _generate_fault_grad(self) -> QtGui.QConicalGradient:
        """
        Returns a smooth alternating conical gradient for the fault state.
        Aligns the stripes to the start of the gauge arc.
        """
        if self._track_fault_gradient is not None:
            return self._track_fault_gradient
        
        cx, cy = self.width() / 2.0, self.height() / 2.0
        # Start the gradient at the same angle as the gauge
        grad = QtGui.QConicalGradient(QtCore.QPointF(cx, cy), _R.ARC_START)
        
        c1 = QtGui.QColor(self._palette.fault_primary)
        c2 = c1.darker(500)  # Distinct dark red for the stripe
        
        # Evenly spaced alternating stops create soft transitions instead of
        # hard block edges.
        num_segments = 20
        for i in range(num_segments + 1):
            t = i / num_segments
            grad.setColorAt(t, c1 if i % 2 == 0 else c2)
            
        self._track_fault_gradient = grad
        return grad
    
    def _generate_gradient(self) -> QtGui.QConicalGradient:
        """
        Returns the cached gradient or creates a new one if needed.
        """
        if self._track_gradient is not None:
            return self._track_gradient

        # Geometry calculations
        cx, cy = self.width() / 2.0, self.height() / 2.0
        start_angle = _R.ARC_START + _R.ARC_SPAN
        
        grad = QtGui.QConicalGradient(QtCore.QPointF(cx, cy), start_angle)
        
        cx, cy = self.width() / 2.0, self.height() / 2.0
        
        # Start angle is at the 'Max' end of the gauge
        start_angle = _R.ARC_START + _R.ARC_SPAN
        grad = QtGui.QConicalGradient(QtCore.QPointF(cx, cy), start_angle)
        
        span_ratio = abs(_R.ARC_SPAN) / 360.0
        pal = self._palette

        mid_gap = (span_ratio + 1.0) / 2.0
        
        # Everything from the Red end (0.0) into the gap becomes solid Red
        grad.setColorAt(0.0, QtGui.QColor(pal.high_end))
        grad.setColorAt(1.0, QtGui.QColor(pal.high_end)) # The wrap-around anchor
        grad.setColorAt(mid_gap + 0.001, QtGui.QColor(pal.high_end))

        # Everything from the Blue end (span_ratio) into the gap becomes solid Blue
        grad.setColorAt(span_ratio, QtGui.QColor(pal.low_start))
        grad.setColorAt(mid_gap, QtGui.QColor(pal.low_start))

        def add_stop(val_pct: float, color: QtGui.QColor | str):
            stop = span_ratio * (1.0 - val_pct)
            grad.setColorAt(max(0.0, min(1.0, stop)), QtGui.QColor(color))

        # 1. Low Zone
        add_stop(0.0, pal.low_start)
        low_p = self._pct(self.low_threshold)
        add_stop(low_p, pal.low_end)
        
        # 2. Middle Zone
        high_p = self._pct(self.high_threshold)
        norm_range = high_p - low_p
        if norm_range > 0:
            for t, rgb in pal.middle_stops:
                add_stop(low_p + t * norm_range, QtGui.QColor(*rgb))
            
        # 3. High Zone
        add_stop(high_p, pal.high_start)
        add_stop(1.0, pal.high_end) # This overwrites the 0.0 anchor perfectly

        self._track_gradient = grad
        return grad

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Invalidate the cache because the center (cx, cy) has changed
        self._track_gradient = None 
        self._track_fault_gradient = None

    def paintEvent(self, event) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.TextAntialiasing)
        
        # Standard Palette extraction
        pal = self.palette()
        color_dim  = pal.color(QtGui.QPalette.ColorRole.PlaceholderText)
        color_midLight = pal.color(QtGui.QPalette.ColorRole.Midlight)
        color_base = pal.color(QtGui.QPalette.ColorRole.Base)
        color_dark  = pal.color(QtGui.QPalette.ColorRole.Dark)

        W, H = self.width(), self.height()
        sz, cx, cy = min(W, H), W/2.0, H/2.0
        r_out, r_in = sz * _R.RING_OUTER, sz * _R.RING_INNER
        sw = max(5, int(sz * 0.048))      # arc stroke width in px
        
        rect = QtCore.QRectF(cx - (r_out+r_in)/2, cy - (r_out+r_in)/2, (r_out+r_in), (r_out+r_in))

        # 1. Background Track
        self._draw_track(p, rect, sw)
        # 2. Track
        self._draw_continuous_bar(p, rect, sw)
        # 3. Needle — draw so the tip overlaps the centreline of the track
        r_mid = (r_out + r_in) / 2.0
        self._draw_needle(p, cx, cy, r_mid, sz, color_midLight, color_base, color_dark)
        # 4. Labels
        self._draw_labels(p, cx, cy, r_out, sz)
        # 5. Chrome (overlapping labels and ticks)
        self._draw_scale_ticks(p, cx, cy, r_out, sz)
        # 6. Threshold Marks
        self._draw_threshold_marks(p, cx, cy, r_out, sz)
        # 7. Center Text value + sub-label
        self._draw_center_text(p, cx, cy, sz)

    def _draw_track(self, p: QtGui.QPainter, rect: QtCore.QRectF, sw: int) -> None:
        """Draws the thick, light gray background track."""
        col = self._generate_fault_grad() if self._is_fault else self.palette().color(QtGui.QPalette.ColorRole.Midlight)
        p.setPen(QtGui.QPen(col, sw, cap=QtCore.Qt.PenCapStyle.RoundCap))

        p.drawArc(rect, _R.ARC_START * 16, _R.ARC_SPAN * 16)

    def _draw_continuous_bar(
            self, p: QtGui.QPainter,
            rect: QtCore.QRectF, sw: int) -> None:
        """Draws the filled progress bar with gradient logic."""
        pct = max(0.0, min(1.0, self._pct(self._value)))
        if pct <= 0 or self._is_fault: return

        # For a truly continuous look, we use a gradient brush on the pen
        color = self._generate_gradient()
        
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

    def _draw_scale_ticks(
        self, p: QtGui.QPainter,
        cx: float, cy: float,
        r_out: float, sz: float,
    ) -> None:
        """Draw scale tick marks outside the gauge ring."""
        r_inner = r_out + sz * _R.SCALE_TICK_OUTER_GAP

        p.setPen(QtGui.QPen(
            QtGui.QColor(self.palette().color(QtGui.QPalette.ColorRole.Text)).darker(150),
            _R.SCALE_TICK_PEN_W,
            cap=QtCore.Qt.PenCapStyle.RoundCap,
        ))

        def draw_tick(t: float, length: float) -> None:
            val = self.min_val + t * (self.max_val - self.min_val)
            angle = self._val_to_angle(val)
            r_outer = r_inner + sz * length
            p.drawLine(
                self._pt(angle, r_inner, cx, cy),
                self._pt(angle, r_outer, cx, cy),
            )

        major_count = max(_R.LABEL_COUNT, 2)
        minor_per_gap = max(_R.SCALE_MINOR_TICKS_PER_GAP, 0)

        for major_index in range(major_count):
            t_major = major_index / (major_count - 1)
            draw_tick(t_major, _R.SCALE_TICK_MAJOR_LENGTH)

            if major_index == major_count - 1:
                continue

            next_t = (major_index + 1) / (major_count - 1)
            gap = next_t - t_major
            for minor_index in range(1, minor_per_gap + 1):
                t_minor = t_major + gap * minor_index / (minor_per_gap + 1)
                draw_tick(t_minor, _R.SCALE_TICK_MINOR_LENGTH)

    def _draw_labels(
        self, p: QtGui.QPainter,
        cx: float, cy: float,
        r_out: float, sz: float,
    ) -> None:
        pal   = self._palette
        r_lbl = r_out + sz * (_R.LABEL_GAP + _R.LABEL_OFFSET)
        lw    = sz * _R.LABEL_RECT_W
        lh    = sz * _R.LABEL_RECT_H

        p.setPen(self.palette().color(QtGui.QPalette.ColorRole.Text).darker(120))
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
                f"{round(val, 1)}",
            )

    def _draw_needle(self, p, cx, cy, r, sz, color_body, color_border,color_halo):
        angle = self._val_to_angle(max(self.min_val, min(self.max_val, self._value)))
        rad = math.radians(angle)
        px, py = math.sin(rad), math.cos(rad)
        
        base_h, tip_h = sz * _R.NEEDLE_BASE_HALF, sz * _R.NEEDLE_TIP_HALF
        tip_x, tip_y = cx + (r * _R.NEEDLE_REACH) * math.cos(rad), cy - (r * _R.NEEDLE_REACH) * math.sin(rad)

        # Build rounded needle path
        path = QtGui.QPainterPath()
        path.moveTo(cx + base_h*px, cy + base_h*py)
        path.lineTo(tip_x + tip_h*px, tip_y + tip_h*py)
        # Round the tip
        path.arcTo(tip_x - tip_h, tip_y - tip_h, tip_h*2, tip_h*2, angle - 90, 180)
        path.lineTo(cx - base_h*px, cy - base_h*py)
        path.closeSubpath()

        # Add Hub
        hub_r = sz * _R.HUB_R
        hub = QtGui.QPainterPath()
        hub.addEllipse(QtCore.QPointF(cx, cy), hub_r, hub_r)
        
        full_path = path.united(hub)
        # Punch hole
        hole = QtGui.QPainterPath()
        hole.addEllipse(QtCore.QPointF(cx, cy), hub_r * _R.HUB_HOLE_RATIO, hub_r * _R.HUB_HOLE_RATIO)
        final_path = full_path.subtracted(hole)

        # Needle Halo
        p.setBrush(QtGui.QColor(color_halo))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.drawEllipse(QtCore.QPointF(cx, cy), sz * 0.065, sz * 0.065)
        
        # Draw
        p.setBrush(color_body)
        p.setPen(QtGui.QPen(color_border, _R.NEEDLE_BORDER_W))
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
            f"{self._value:.1f}{self.main_unit}",
        )

        # Sub-label (°C)
        p.setPen(self.palette().color(QtGui.QPalette.ColorRole.Text).darker(120))
        p.setFont(QtGui.QFont(pal.font_family, int(sz * _R.FONT_SUB)))
        p.drawText(
            QtCore.QRectF(cx - sub_w / 2, y_sub, sub_w, sub_h),
            QtCore.Qt.AlignmentFlag.AlignCenter,
            f"{self._temp_c:.1f}°C",
        )
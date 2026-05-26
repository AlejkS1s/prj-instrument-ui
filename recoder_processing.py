import re

CAN_LOG_PATTERN = re.compile(
    r"\[(TX|RX)\]\t(0x[0-9A-Fa-f]+)\|\d+\|?[0-9A-Fa-fXx]*\|([-\d\.]+)(?:\tS:(0x[0-9A-Fa-f]+)-(0x[0-9A-Fa-f]+))?"
)

TELEMETRY_PATTERN = re.compile(
    r"!(TX|RX),([0-9A-Fa-f]+),([-\d\.]+),([0-9A-Fa-f]+),([0-9A-Fa-f]+)"
)

ERROR_PATTERN = re.compile(
    r"!ERR,([A-Z]+),([0-9A-Fa-f]+),?(.*)?"
)

def parse_line(line: str) -> dict | None:
    """Parse UART output from can_heartbeat_float and extract values.
    The firmware now emits temperature in Celsius on the wire; Fahrenheit is
    derived here for display compatibility. Handles telemetry lines (starting
    with '!') and error lines ("!ERR,...")."""
    if line.startswith("!"):
        # Try telemetry pattern first
        m = TELEMETRY_PATTERN.match(line)
        if m:
            direction, can_id_hex, val_str, l_alarm_hex, h_alarm_hex = m.groups()
            can_id = f"0x{can_id_hex}"
            try:
                val_f = float(val_str)
            except ValueError:
                return None
            
            # Determine standard high/low alarm flags (0xFF indicates alarm)
            l_alarm = (l_alarm_hex.upper() == "FF")
            h_alarm = (h_alarm_hex.upper() == "FF")

            # Detect custom firmware states encoded as identical non‑FF bytes.
            # The firmware uses 0x11 for DISCONNECTED, 0x22 for SHORT_CIRCUIT, and
            # 0x33 for STUCK. These are placed in both status bytes.
            state = None
            if l_alarm_hex.upper() == h_alarm_hex.upper():
                if l_alarm_hex.upper() == "11":
                    state = "DISCONNECTED"
                elif l_alarm_hex.upper() == "22":
                    state = "SHORT_CIRCUIT"
                elif l_alarm_hex.upper() == "33":
                    state = "STUCK"

            return {
                'id': can_id,
                'dir': direction,
                'temp_c': val_f,
                'temp_f': to_fahrenheit(val_f),
                'high_alarm': h_alarm,
                'low_alarm': l_alarm,
                'state': state,
            }
        # If not telemetry, try error pattern
        m_err = ERROR_PATTERN.match(line)
        if m_err:
            err_type, err_code, err_msg = m_err.groups()
            return {
                'error': True,
                'type': err_type,
                'code': err_code,
                'msg': err_msg or ''
            }
        return None

    m = CAN_LOG_PATTERN.search(line)
    if not m:
        return None
    
    direction, can_id, val_str, h_alarm_str, l_alarm_str = m.groups()
    try:
        val_f = float(val_str)
    except ValueError:
        return None
    
    h_alarm = (h_alarm_str == "0xFF") if h_alarm_str else False
    l_alarm = (l_alarm_str == "0xFF") if l_alarm_str else False

    return {
        'id': can_id,
        'dir': direction,
        'temp_f': to_fahrenheit(val_f),
        'temp_c': val_f,
        'high_alarm': h_alarm,
        'low_alarm': l_alarm
    }

def to_fahrenheit(temp_c: float) -> float:
    """Convert a temperature from Celsius to Fahrenheit."""
    return temp_c * 9 / 5 + 32

def to_celsius(temp_f: float) -> float:
    """Convert a temperature from Fahrenheit to Celsius."""
    return (temp_f - 32) * 5 / 9

def format_row(ts_ms: float, can_id: str, temp_f: float, temp_c: float, alarms: str, state: str | None = None) -> tuple[str, ...]:
    """Format telemetry data for UI tables.

    ``state`` is the optional custom firmware state (e.g. ``DISCONNECTED``).
    When provided it is appended as the final column; otherwise an empty
    string is emitted so the column count stays consistent.
    """
    return (
        f"{ts_ms:.3f}",
        can_id,
        f"{temp_f:.3f}",
        f"{temp_c:.3f}",
        alarms,
        state or "",
    )

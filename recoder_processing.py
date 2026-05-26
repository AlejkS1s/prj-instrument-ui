import re

CAN_LOG_PATTERN = re.compile(
    r"\[(TX|RX)\]\t(0x[0-9A-Fa-f]+)\|\d+\|?[0-9A-Fa-fXx]*\|([-\d\.]+)(?:\tS:(0x[0-9A-Fa-f]+)-(0x[0-9A-Fa-f]+))?"
)

TELEMETRY_PATTERN = re.compile(
    r"!(TX|RX),([0-9A-Fa-f]+),([-\d\.]+),([0-9A-Fa-f]+),([0-9A-Fa-f]+)"
)

def parse_line(line: str) -> dict | None:
    """Parse UART output from can_heartbeat_float and extract values."""
    if line.startswith("!"):
        m = TELEMETRY_PATTERN.match(line)
        if not m:
            return None
        direction, can_id_hex, val_str, l_alarm_hex, h_alarm_hex = m.groups()
        can_id = f"0x{can_id_hex}"
        try:
            val_f = float(val_str)
        except ValueError:
            return None
        
        l_alarm = (l_alarm_hex == "FF")
        h_alarm = (h_alarm_hex == "FF")

        return {
            'id': can_id,
            'dir': direction,
            'temp_f': val_f,
            'temp_c': to_celsius(val_f),
            'high_alarm': h_alarm,
            'low_alarm': l_alarm
        }

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
        'temp_f': val_f,
        'temp_c': to_celsius(val_f),
        'high_alarm': h_alarm,
        'low_alarm': l_alarm
    }

def to_fahrenheit(temp_c: float) -> float:
    """Convert a temperature from Celsius to Fahrenheit."""
    return temp_c * 9 / 5 + 32

def to_celsius(temp_f: float) -> float:
    """Convert a temperature from Fahrenheit to Celsius."""
    return (temp_f - 32) * 5 / 9

def format_row(ts_ms: float, can_id: str, temp_f: float, temp_c: float, alarms: str) -> tuple[str, ...]:
    """
    Format raw telemetry data into a tuple of string values.
    Suitable for insertion into the QTreeWidget.
    """
    return f"{ts_ms:.2f}", can_id, f"{temp_f:.2f}", f"{temp_c:.2f}", alarms

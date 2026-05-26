"""
serial_worker.py
----------------
Owns the serial port. Lives in a dedicated QThread.
Communicates exclusively through Qt signals — never touches the UI.

Thread model:
  Main thread  ──signals──▶  Worker thread (event loop)
                                  │
                             QTimer._poll()  ──data_ready──▶  Main thread
"""

import serial
from PyQt6 import QtCore

import recoder_processing as dp


class SerialWorker(QtCore.QObject):

    data_ready     = QtCore.pyqtSignal(dict)   # one fully-parsed frame
    raw_info       = QtCore.pyqtSignal(str)    # non-data debug line from firmware
    error          = QtCore.pyqtSignal(str)    # I/O error message
    connected      = QtCore.pyqtSignal()       # device ready (after reset delay)
    write_cmd      = QtCore.pyqtSignal(bytes)  # main thread → worker thread write

    _POLL_INTERVAL_MS = 20   # 50 Hz drain cadence

    def __init__(self, port: str, baud: int, parent=None):
        """
        Initialize the SerialWorker.
        
        Args:
            port (str): The serial port path/name.
            baud (int): Communication baud rate.
            parent: Optional parent QObject for the Qt object tree.
        """
        super().__init__(parent)
        self._port = port
        self._baud = baud
        self._ser: serial.Serial | None = None
        self._poll_timer: QtCore.QTimer | None = None
        self._buf = b""   # accumulates bytes until a complete line is available

    # ------------------------------------------------------------------
    # Public slots — called in the worker thread via queued connections
    # ------------------------------------------------------------------

    @QtCore.pyqtSlot()
    def open(self):
        """Entry point: called when the thread starts."""
        try:
            self._ser = serial.Serial(self._port, self._baud, timeout=0.1)
        except serial.SerialException as e:
            self.error.emit(str(e))
            return


        self._init_device()

    @QtCore.pyqtSlot()
    def close(self):
        """Stop the polling timer and safely close the serial connection."""
        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer = None
        if self._ser:
            try:
                if self._ser.is_open:
                    self._ser.close()
            except Exception:
                pass
            self._ser = None
        self._buf = b""

    @QtCore.pyqtSlot(bytes)
    def _on_write_cmd(self, data: bytes):
        """Write raw command bytes to the connected serial port."""
        if self._ser and self._ser.is_open:
            try:
                self._ser.write(data)
            except (serial.SerialException, OSError) as e:
                self.error.emit(str(e))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @QtCore.pyqtSlot()
    def _init_device(self):
        """
        Send initialization commands to the serial device and configure 
        the high-frequency polling timer for incoming data.
        """
        if not (self._ser and self._ser.is_open):
            return
        self._ser.reset_input_buffer()
        self._ser.write(b"ndbg\n")  # request slim telemetry format

        # Wire the write_cmd signal now that the device is ready
        self.write_cmd.connect(self._on_write_cmd)

        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(self._POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

        self.connected.emit()

    @QtCore.pyqtSlot()
    def _poll(self):
        """
        Periodic timer callback to drain the serial RX buffer.
        Reads available bytes, splits into complete lines, and parses them.
        """
        try:
            waiting = self._ser.in_waiting
            if not waiting:
                return
            # Grab all available bytes at once, append to the line buffer.
            # Splitting here guarantees only complete lines are processed;
            # any partial tail stays in _buf for the next poll cycle.
            self._buf += self._ser.read(waiting)
            while b'\n' in self._buf:
                line, self._buf = self._buf.split(b'\n', 1)
                raw = line.decode(errors='replace').strip()
                if not raw:
                    continue
                if raw.startswith(("[", "!")):
                    parsed = dp.parse_line(raw)
                    if parsed:
                        self.data_ready.emit(parsed)
                else:
                    self.raw_info.emit(raw)
        except (serial.SerialException, OSError) as e:
            self._poll_timer.stop()
            self.error.emit(str(e))
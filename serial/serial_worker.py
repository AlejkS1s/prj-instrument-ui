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

import serial.serial_parser as dp


class SerialWorker(QtCore.QObject):

    data_ready     = QtCore.pyqtSignal(list)   # one fully-parsed frame
    raw_info       = QtCore.pyqtSignal(str)    # non-data debug line from firmware
    error          = QtCore.pyqtSignal(str)    # I/O error message
    connected      = QtCore.pyqtSignal()       # device ready (after reset delay)
    write_cmd      = QtCore.pyqtSignal(bytes)  # main thread → worker thread write

    _POLL_INTERVAL_MS = 20   

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
            self._init_device()
        except serial.SerialException as e:
            self.error.emit(str(e))

    @QtCore.pyqtSlot()
    def close(self):
        if self._poll_timer: self._poll_timer.stop()
        if self._ser and self._ser.is_open: self._ser.close()
        self._ser = None

    @QtCore.pyqtSlot(bytes)
    def _on_write_cmd(self, data: bytes):
        """Write raw command bytes to the connected serial port."""
        if self._ser and self._ser.is_open:
            try: self._ser.write(data)
            except Exception as e: self.error.emit(str(e))

    def _init_device(self):
        self._ser.reset_input_buffer()
        self._ser.write(b"ndbg\n")
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
            if not waiting: return
            
            self._buf += self._ser.read(waiting)
            batch = [] # Accumulate all available lines
            while b'\n' in self._buf:
                line, self._buf = self._buf.split(b'\n', 1)
                raw = line.decode(errors='replace').strip()
                if not raw: continue
                if raw.startswith(("[", "!")):
                    parsed = dp.parse_line(raw)
                    if parsed: batch.append(parsed)
                    else: self.raw_info.emit(raw)
                else:
                    self.raw_info.emit(raw)
            
            if batch: # Emit as a single block
                self.data_ready.emit(batch)
        except Exception as e:
            self._poll_timer.stop()
            self.error.emit(str(e))
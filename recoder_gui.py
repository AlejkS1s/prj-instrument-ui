import re
import csv
import math
import time
from pathlib import Path
from datetime import datetime

import serial.tools.list_ports
from NodeStore import NodeStore
from NodeWidget import NodeWidget
from PyQt6 import QtWidgets, QtCore, QtGui

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt import NavigationToolbar2QT as NavigationToolbar
import matplotlib.ticker as ticker
import matplotlib.animation as animation

import recoder_processing as dp
from serial_worker import SerialWorker
from constants import (
    BAUDRATES,
    MAX_SAMPLES,
    TEMP_MAX_F,
    TEMP_MIN_F,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_ESP_VIDS     = {0x303A, 0x10C4, 0x1A86}
_PORT_PATTERN = re.compile(r"ttyACM\d+|ttyUSB\d+|cu\.usbserial|cu\.SLAB|COM\d+")

# ===========================================================================
# App — controller / main window
# ===========================================================================
class App(QtWidgets.QMainWindow):
    """
    Orchestrates: SerialWorker ↔ NodeStore ↔ NodeWidget + ChartWidget.
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CAN Bus Node Monitor")
        self.resize(1540, 920)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint)

        self._store         = NodeStore()
        self._worker: SerialWorker | None = None
        self._thread: QtCore.QThread | None = None
        self._t0: float | None = None

        self._streaming = False
        self._csv_file  = None
        self._writer    = None

        self._ani   = None
        self._lines: dict[str, object] = {}
        self._node_widgets: dict[str, NodeWidget] = {}

        self._build_ui()
        self._refresh_ports()

        if self._port_cb.count() > 0:
            self._connect()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)

        left_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        left_splitter.addWidget(self._build_config_group())
        left_splitter.addWidget(self._build_cmd_group())
        left_splitter.addWidget(self._build_readings_group())
        left_splitter.setStretchFactor(0, 0)
        left_splitter.setStretchFactor(1, 0)
        left_splitter.setStretchFactor(2, 1)

        self._btn_clear = QtWidgets.QPushButton("Clear")
        self._btn_clear.clicked.connect(self._clear)
        self._btn_clear.setEnabled(False)

        left_panel = QtWidgets.QWidget()
        left_panel.setMinimumWidth(460)
        left_col = QtWidgets.QVBoxLayout(left_panel)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.addWidget(left_splitter, stretch=1)
        left_col.addWidget(self._btn_clear, stretch=0)

        right_panel  = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Network Nodes Container - Fixed constraints to prevent layout stretching blowout
        nodes_group = QtWidgets.QGroupBox("Network Nodes")
        nodes_group.setMinimumHeight(170)
        nodes_group.setMaximumHeight(200)
        nodes_group.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        scroll_content = QtWidgets.QWidget()
        self._nodes_info_layout = QtWidgets.QHBoxLayout(scroll_content)
        self._nodes_info_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self._nodes_info_layout.setContentsMargins(6, 4, 6, 4)
        self._nodes_info_layout.setSpacing(12)
        
        scroll_area.setWidget(scroll_content)
        group_layout = QtWidgets.QVBoxLayout(nodes_group)
        group_layout.setContentsMargins(4, 4, 4, 4)
        group_layout.addWidget(scroll_area)

        right_layout.addWidget(nodes_group, stretch=0)
        right_layout.addWidget(self._build_chart_widget(), stretch=1)

        main_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(right_panel)
        main_splitter.setStretchFactor(0, 3)
        main_splitter.setStretchFactor(1, 7)

        root.addWidget(main_splitter)
        self.statusBar().showMessage("Disconnected")

    def _build_config_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Configuration")
        two_cols = QtWidgets.QHBoxLayout(group)
        two_cols.setContentsMargins(12, 12, 12, 12)
        two_cols.setSpacing(16)

        param_col = QtWidgets.QVBoxLayout()
        param_col.setSpacing(4)

        port_row = QtWidgets.QHBoxLayout()
        port_row.addWidget(QtWidgets.QLabel("Port"))
        self._port_cb = QtWidgets.QComboBox()
        port_row.addWidget(self._port_cb, stretch=1)
        btn_refresh = QtWidgets.QPushButton("Refresh")
        btn_refresh.clicked.connect(self._refresh_ports)
        port_row.addWidget(btn_refresh)
        param_col.addLayout(port_row)

        baud_row = QtWidgets.QHBoxLayout()
        baud_row.addWidget(QtWidgets.QLabel("Baud rate"))
        self._baud_cb = QtWidgets.QComboBox()
        self._baud_cb.addItems(str(b) for b in BAUDRATES)
        self._baud_cb.setCurrentText("115200")
        baud_row.addWidget(self._baud_cb, stretch=1)
        param_col.addLayout(baud_row)
        param_col.addStretch()

        btn_col = QtWidgets.QVBoxLayout()
        btn_col.setSpacing(4)
        self._btn_connect = QtWidgets.QPushButton("Connect")
        self._btn_connect.clicked.connect(self._toggle_connection)
        self._btn_toggle = QtWidgets.QPushButton("Start Log")
        self._btn_toggle.clicked.connect(self._toggle_log)
        self._btn_toggle.setEnabled(False)
        btn_finish = QtWidgets.QPushButton("Finish")
        btn_finish.clicked.connect(self.close)

        btn_col.addWidget(self._btn_connect)
        btn_col.addWidget(self._btn_toggle)
        btn_col.addStretch()
        btn_col.addWidget(btn_finish)

        two_cols.addLayout(param_col, stretch=2)
        two_cols.addLayout(btn_col,   stretch=1)
        return group

    def _build_cmd_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Commands")
        layout = QtWidgets.QHBoxLayout(group)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(QtWidgets.QLabel("Cmd"))
        self._cmd_input = QtWidgets.QLineEdit()
        self._cmd_input.setPlaceholderText("e.g. te, nte, tx, dn")
        self._cmd_input.returnPressed.connect(self._send_cmd)
        layout.addWidget(self._cmd_input, stretch=1)
        return group

    def _build_readings_group(self) -> QtWidgets.QGroupBox:
        group  = QtWidgets.QGroupBox("Readings")
        layout = QtWidgets.QVBoxLayout(group)
        self._reading_tree = QtWidgets.QTreeWidget()
        self._reading_tree.setHeaderLabels(["t(s)", "ID", "Temp (°F)", "Temp (°C)", "Alarms", "State"])
        self._reading_tree.setAlternatingRowColors(True)
        self._reading_tree.header().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._reading_tree)
        return group

    def _build_chart_widget(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        self._fig     = Figure(layout="tight")
        self._ax      = self._fig.add_subplot(111)
        self._setup_axes()
        self._canvas  = FigureCanvas(self._fig)
        self._toolbar = NavigationToolbar(self._canvas, widget)
        layout.addWidget(self._toolbar)
        layout.addWidget(self._canvas)
        return widget

    def _setup_axes(self):
        self._ax.set(
            ylim=(TEMP_MIN_F, TEMP_MAX_F), xlim=(0, 1_000),
            ylabel="Temperature (°F)", xlabel="Time (s)",
            title="CAN Bus Nodes — Temperature",
        )
        self._ax.grid(True, which='major', alpha=0.5, linewidth=0.8)
        self._ax.minorticks_on()
        self._ax.grid(True, which='minor', alpha=0.2, linestyle=':')
        self._ax.xaxis.set_major_locator(ticker.MaxNLocator(nbins=12))
        self._ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=15))
        self._ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f"{x/1000:.1f}"))
        self._legend = None

    # -----------------------------------------------------------------------
    # Port helpers
    # -----------------------------------------------------------------------
    def _refresh_ports(self):
        ports = [
            p.device for p in serial.tools.list_ports.comports()
            if (p.vid in _ESP_VIDS) or _PORT_PATTERN.search(p.device)
        ]
        self._port_cb.clear()
        self._port_cb.addItems(ports)

    # -----------------------------------------------------------------------
    # Serial connection lifecycle
    # -----------------------------------------------------------------------
    def _toggle_connection(self):
        if self._worker:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        print("connecting...")
        port = self._port_cb.currentText()
        baud = int(self._baud_cb.currentText())
        if not port:
            QtWidgets.QMessageBox.critical(self, "Error", "No port selected.")
            return

        self._thread = QtCore.QThread(self)
        self._worker = SerialWorker(port, baud)
        self._worker.moveToThread(self._thread)

        # Worker signals → main thread slots (auto queued connections across threads)
        self._thread.started.connect(self._worker.open)
        self._worker.connected.connect(self._on_serial_connected)
        self._worker.data_ready.connect(self._on_data)
        self._worker.raw_info.connect(lambda msg: print(">>", msg))
        self._worker.error.connect(self._on_serial_error)

        self._btn_connect.setText("Disconnect")
        self.statusBar().showMessage(f"Connecting → {port} @ {baud} baud …")
        self._thread.start()

    def _disconnect(self):
        if self._streaming:
            self._end_log()
        if self._ani:
            self._ani.event_source.stop()
            self._ani = None
        if self._worker:
            # Invoke close() in the worker's thread via the event queue
            QtCore.QMetaObject.invokeMethod(
                self._worker, "close", QtCore.Qt.ConnectionType.QueuedConnection
            )
        if self._thread:
            self._thread.quit()
            self._thread.wait()
            self._thread = None
        self._worker = None
        self._btn_connect.setText("Connect")
        self._btn_toggle.setEnabled(False)
        self.statusBar().showMessage("Disconnected")

    @QtCore.pyqtSlot()
    def _on_serial_connected(self):
        if self._t0 is None:
            self._t0 = time.time()
        self._btn_toggle.setEnabled(True)
        self._btn_clear.setEnabled(True)
        port, baud = self._port_cb.currentText(), self._baud_cb.currentText()
        self.statusBar().showMessage(f"Connected → {port} @ {baud} baud")
        self._start_animation()

    @QtCore.pyqtSlot(str)
    def _on_serial_error(self, message: str):
        self._disconnect()
        QtWidgets.QMessageBox.critical(self, "Serial error", message)

    def _send_cmd(self):
        if self._worker:
            cmd = self._cmd_input.text().strip()
            if cmd:
                self._worker.write_cmd.emit(f"{cmd}\n".encode("utf-8"))
                self._cmd_input.clear()

    # -----------------------------------------------------------------------
    # Data ingestion — runs on main thread
    # -----------------------------------------------------------------------
    @QtCore.pyqtSlot(dict)
    def _on_data(self, parsed: dict):
        """Handle parsed data from the serial worker.
        Supports normal telemetry dictionaries as well as error dictionaries
        emitted by ``chkCan`` when ``dbgEn`` is false. Errors are printed to the
        console (stdout) to mimic the behaviour of ``record_processing``.
        """
        if self._t0 is None:
            return

        # Error messages from chkCan are identified by the ``error`` key.
        if parsed.get('error'):
            # Print a concise error description to the console.
            print(f"{time.time():.2f} [CAN ERROR] Type={parsed.get('type')}, Code={parsed.get('code')}, Msg={parsed.get('msg')}")
            return

        ts      = round((time.time() - self._t0) * 1000, 2)
        c_id    = parsed['id']
        tf, tc  = parsed['temp_f'], parsed['temp_c']
        h_alarm = parsed['high_alarm']
        l_alarm = parsed['low_alarm']

        _, is_new = self._store.get_or_create(c_id)
        if is_new:
            color = self._store.color_for(c_id)
            display_index = len(self._node_widgets) + 1
            nw = NodeWidget(c_id, color, display_index)
            self._node_widgets[c_id] = nw
            self._nodes_info_layout.addWidget(nw)

        self._store.append(c_id, ts, tf, tc)
        # Forward custom firmware state (if any) to the widget for display.
        self._node_widgets[c_id].update_data(tf, tc, h_alarm, l_alarm, parsed.get('state'))

        alarms_str = " | ".join(filter(None, [
            "HIGH" if h_alarm else "",
            "LOW"  if l_alarm else "",
        ]))

        if self._streaming and self._writer:
            self._writer.writerow([ts, c_id, parsed['dir'], tf, tc, h_alarm, l_alarm])

        self._push_reading_row(ts, c_id, tf, tc, alarms_str, h_alarm, l_alarm, parsed.get('state'))

    # -----------------------------------------------------------------------
    # Plot animation — reads from store, no serial I/O here
    # -----------------------------------------------------------------------
    def _start_animation(self):
        self._ani = animation.FuncAnimation(
            self._fig, self._update_plot,
            interval=50, blit=False, cache_frame_data=False,
        )
        self._canvas.draw()

    def _update_plot(self, _):
        added = False
        max_ts = min_ts = 0

        for node_id, data in self._store:
            if not data["ts"]:
                continue
            if node_id not in self._lines:
                color = self._store.color_for(node_id)
                # Prefer the sequential display index if available
                if node_id in self._node_widgets:
                    label = f"Node {self._node_widgets[node_id].display_index}"
                else:
                    label = f"Node {node_id}"
                line, = self._ax.plot([], [], color=color, label=label)
                self._lines[node_id] = line
                added = True

            self._lines[node_id].set_data(data["ts"], data["temp_f"])
            curr_max = max(data["ts"])
            curr_min = min(data["ts"])
            if curr_max > max_ts:                   max_ts = curr_max
            if min_ts == 0 or curr_min < min_ts:    min_ts = curr_min

        if added:
            self._legend = self._ax.legend(loc="upper left")

        if max_ts > 0 and not self._toolbar.mode:
            self._ax.set_xlim(
                min_ts if min_ts < max_ts else 0,
                max(1_000, max_ts + 100),
            )
        return list(self._lines.values())

    def _push_reading_row(self, ts, c_id, tf, tc, alarms, h_alarm, l_alarm, state: str | None = None):
        if self._reading_tree.topLevelItemCount() >= MAX_SAMPLES:
            self._reading_tree.takeTopLevelItem(0)
        item = QtWidgets.QTreeWidgetItem(
            self._reading_tree,
            list(dp.format_row(ts / 1000.0, c_id, tf, tc, alarms, state)),
        )
        if h_alarm:
            item.setBackground(4, QtGui.QColor(255, 200, 200))
        elif l_alarm:
            item.setBackground(4, QtGui.QColor(200, 200, 255))
        self._reading_tree.scrollToBottom()

    # -----------------------------------------------------------------------
    # CSV logging
    # -----------------------------------------------------------------------

    def _toggle_log(self):
        if self._streaming:
            self._end_log()
        else:
            self._begin_log()

    def _begin_log(self):
        # Timestamp generated here, not at module load time
        output_file = Path(f"can_record-{datetime.now().strftime('%d-%m-%y-%H-%M-%S')}.csv")
        self._csv_file = open(output_file, "w", newline="")
        self._writer   = csv.writer(self._csv_file, delimiter=",")
        self._writer.writerow(["ts_ms", "can_id", "dir", "temp_f", "temp_c", "high_alarm", "low_alarm"])
        self._streaming = True
        self._btn_toggle.setText("Stop Log")
        self.statusBar().showMessage(f"Logging → {output_file}")

    def _end_log(self):
        self._close_csv()
        self._streaming = False
        self._btn_toggle.setText("Start Log")
        self.statusBar().showMessage("Logging stopped.")

    def _close_csv(self):
        if self._csv_file:
            self._csv_file.flush()
            self._csv_file.close()
        self._csv_file = None
        self._writer   = None

    # -----------------------------------------------------------------------
    # Clear
    # -----------------------------------------------------------------------

    def _clear(self):
        self._store.clear()
        self._reading_tree.clear()
        self._t0 = time.time() if self._worker else None

        for line in self._lines.values():
            line.remove()
        self._lines.clear()

        for nw in self._node_widgets.values():
            nw.deleteLater()
        self._node_widgets.clear()

        if self._legend:
            self._legend.remove()
            self._legend = None

        self._canvas.draw_idle()
        self.statusBar().showMessage("Plots cleared.")

    # -----------------------------------------------------------------------
    # Cleanup
    # -----------------------------------------------------------------------

    def closeEvent(self, event):
        self._disconnect()   # handles ani, csv, serial — everything
        event.accept()
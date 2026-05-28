import re
import csv
import math
import time
from pathlib import Path
from datetime import datetime

import serial.tools.list_ports
from gui.NodeStore import NodeStore
from gui.node_widget import NodeWidget
from PyQt6 import QtWidgets, QtCore, QtGui

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt import NavigationToolbar2QT as NavigationToolbar
import matplotlib.ticker as ticker
import matplotlib.animation as animation

import serial.serial_parser as dp
from serial.serial_worker import SerialWorker
from config import (
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

class ReadingsTableModel(QtCore.QAbstractTableModel):
    def __init__(self, max_samples):
        super().__init__()
        self._data = [] 
        self._max_samples = max_samples
        self._headers = ["t(s)", "ID", "Temp (°F)", "Temp (°C)", "Alarms", "State"]

    def rowCount(self, parent=None): return len(self._data)
    def columnCount(self, parent=None): return len(self._headers)

    def data(self, index, role):
        if not index.isValid(): return None
        formatted_row, h_alarm, l_alarm = self._data[index.row()]
        
        if role == QtCore.Qt.ItemDataRole.DisplayRole:
            return formatted_row[index.column()]
        
        if role == QtCore.Qt.ItemDataRole.BackgroundRole and index.column() == 4:
            if h_alarm: return QtGui.QColor(255, 200, 200)
            if l_alarm: return QtGui.QColor(200, 200, 255)
        return None

    def headerData(self, section, orientation, role):
        if role == QtCore.Qt.ItemDataRole.DisplayRole and orientation == QtCore.Qt.Orientation.Horizontal:
            return self._headers[section]
        return None

    def add_rows(self, new_rows):
        if not new_rows: return
        # Limit checking and insertion
        total = len(self._data) + len(new_rows)
        if total > self._max_samples:
            remove_count = total - self._max_samples
            self.beginRemoveRows(QtCore.QModelIndex(), 0, remove_count - 1)
            self._data = self._data[remove_count:]
            self.endRemoveRows()
        
        start = len(self._data)
        self.beginInsertRows(QtCore.QModelIndex(), start, start + len(new_rows) - 1)
        self._data.extend(new_rows)
        self.endInsertRows()

    def clear(self):
        self.beginResetModel()
        self._data = []
        self.endResetModel()

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

        right_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        right_splitter.addWidget(self._build_nodes_group())
        right_splitter.addWidget(self._build_chart_widget())
        # right_splitter.setStretchFactor(0, 1)
        # right_splitter.setStretchFactor(1, 1)
        # right_splitter.setSizes([1, 10])

        right_layout.addWidget(right_splitter)

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
        group = QtWidgets.QGroupBox("Readings")
        layout = QtWidgets.QVBoxLayout(group)
        self._table_model = ReadingsTableModel(MAX_SAMPLES)
        self._reading_table = QtWidgets.QTableView()
        self._reading_table.setModel(self._table_model)
        self._reading_table.setAlternatingRowColors(True)
        self._reading_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._reading_table)
        return group

    def _build_nodes_group(self) -> QtWidgets.QGroupBox:
        nodes_group = QtWidgets.QGroupBox("Network Nodes")
        # Keep the fixed policy so it leaves maximum room for the matplotlib chart
        nodes_group.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        
        scroll_area = QtWidgets.QScrollArea()
        # MUST be True so the internal widget expands to fit the scroll area's height
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        
        scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # NodeWidget has a min height of 220px. 
        # 260px provides enough padding for the widget, margins, and horizontal scrollbar.
        scroll_area.setFixedHeight(260)

        scroll_content = QtWidgets.QWidget()
        self._nodes_info_layout = QtWidgets.QHBoxLayout(scroll_content)
        self._nodes_info_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
        self._nodes_info_layout.setContentsMargins(6, 4, 6, 4)
        self._nodes_info_layout.setSpacing(12)
        
        scroll_area.setWidget(scroll_content)
        
        group_layout = QtWidgets.QVBoxLayout(nodes_group)
        group_layout.setContentsMargins(4, 4, 4, 4)
        group_layout.addWidget(scroll_area)
        
        return nodes_group

    def _build_chart_widget(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        palette = widget.palette()
        bg_color = palette.color(QtGui.QPalette.ColorRole.Window).name()
        ax_color = palette.color(QtGui.QPalette.ColorRole.Base).name()
        self._fig     = Figure(layout="tight")
        self._fig.set_facecolor(bg_color)
        self._ax      = self._fig.add_subplot(111)
        self._ax.set_facecolor(ax_color)
        self._setup_axes()
        self._canvas  = FigureCanvas(self._fig)
        self._toolbar = NavigationToolbar(self._canvas, widget)
        layout.addWidget(self._toolbar)
        layout.addWidget(self._canvas)
        return widget

    def _setup_axes(self):
        palette = self.palette()
        window_text = palette.color(QtGui.QPalette.ColorRole.WindowText).name()
        text_color = palette.color(QtGui.QPalette.ColorRole.Text).name()
        grid_color = palette.color(QtGui.QPalette.ColorRole.Mid).name()
        spine_color = palette.color(QtGui.QPalette.ColorRole.Midlight).name()

        self._ax.set(
            ylim=(TEMP_MIN_F, TEMP_MAX_F), xlim=(0, 1_000),
            ylabel="Temperature (°F)", xlabel="Time (s)",
            title="CAN Bus Nodes — Temperature",
        )
        self._ax.tick_params(colors=text_color, which="both")
        self._ax.xaxis.label.set_color(window_text)
        self._ax.yaxis.label.set_color(window_text)
        self._ax.title.set_color(window_text)

        for spine in self._ax.spines.values():
            spine.set_color(spine_color)

        self._ax.grid(True, which='major', alpha=0.5, linewidth=0.8, color=grid_color)
        self._ax.minorticks_on()
        self._ax.grid(True, which='minor', alpha=0.2, linestyle=':', color=grid_color)
        self._ax.xaxis.set_major_locator(ticker.MaxNLocator(nbins=12))
        self._ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=15))
        self._ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: f"{x/1000:.1f}"))
        self._legend = None

    def _style_legend(self):
        if not self._legend:
            return

        palette = self.palette()
        legend_text = palette.color(QtGui.QPalette.ColorRole.WindowText).name()
        legend_bg = palette.color(QtGui.QPalette.ColorRole.Base).name()
        legend_edge = palette.color(QtGui.QPalette.ColorRole.Dark).name()

        frame = self._legend.get_frame()
        frame.set_facecolor(legend_bg)
        frame.set_edgecolor(legend_edge)
        for text in self._legend.get_texts():
            text.set_color(legend_text)

    # -----------------------------------------------------------------------
    # Port helpers
    # -----------------------------------------------------------------------
    @staticmethod
    def _port_sort_key(port: str):
        parts = re.split(r"(\d+)", port)
        return [int(part) if part.isdigit() else part.lower() for part in parts]

    def _refresh_ports(self):
        ports = [
            p.device for p in serial.tools.list_ports.comports()
            if (p.vid in _ESP_VIDS) or _PORT_PATTERN.search(p.device)
        ]
        ports.sort(key=self._port_sort_key)
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
    @QtCore.pyqtSlot(list)
    def _on_data(self, parsed_batch: list):
        if self._t0 is None:
            return

        table_rows = []

        # Process the entire batch in a single UI event loop cycle
        for parsed in parsed_batch:
            if parsed.get('error'):
                print(f"{time.time():.2f} [CAN ERROR] Type={parsed.get('type')}, Code={parsed.get('code')}, Msg={parsed.get('msg')}")
                continue

            ts      = round((time.time() - self._t0) * 1000, 2)
            c_id    = parsed['id']
            tf, tc  = parsed['temp_f'], parsed['temp_c']
            h_alarm = parsed['high_alarm']
            l_alarm = parsed['low_alarm']

            # Handle Node Widgets
            _, is_new = self._store.get_or_create(c_id)
            if is_new:
                color = self._store.color_for(c_id)
                display_index = len(self._node_widgets) + 1
                nw = NodeWidget(c_id, color, display_index)
                self._node_widgets[c_id] = nw
                self._nodes_info_layout.addWidget(nw)

            self._store.append(c_id, ts, tf, tc)
            self._node_widgets[c_id].update_data(tf, h_alarm, l_alarm, parsed.get('state'))

            # Handle CSV Logging
            if self._streaming and self._writer:
                self._writer.writerow([ts, c_id, parsed['dir'], tf, tc, h_alarm, l_alarm])

            # Prepare row for the table model
            alarms_str = " | ".join(filter(None, [
                "HIGH" if h_alarm else "",
                "LOW"  if l_alarm else "",
            ]))
            
            # Format row data
            formatted_row = list(dp.format_row(ts / 1000.0, c_id, tf, tc, alarms_str, parsed.get('state')))
            
            # Append tuple of (data, high_alarm_bool, low_alarm_bool) to our batch list
            table_rows.append((formatted_row, h_alarm, l_alarm))

        # Push the batch to the table model all at once
        if table_rows:
            self._table_model.add_rows(table_rows)
            
            # Only auto-scroll if the user is already at the bottom
            scrollbar = self._reading_table.verticalScrollBar()
            if scrollbar.value() == scrollbar.maximum():
                self._reading_table.scrollToBottom()

    # -----------------------------------------------------------------------
    # Plot animation — reads from store, no serial I/O here
    # -----------------------------------------------------------------------
    def _start_animation(self):
        # Increased interval to 100ms (10Hz) and enabled blitting
        self._ani = animation.FuncAnimation(
            self._fig, self._update_plot,
            interval=100, blit=True, cache_frame_data=False
        )

    def _update_plot(self, _):
        added = False
        max_ts = min_ts = 0

        for node_id, data in self._store:
            if not data["ts"]:
                continue
            if node_id not in self._lines:
                color = self._store.color_for(node_id)
                if node_id in self._node_widgets:
                    label = f"Node {self._node_widgets[node_id].display_index}"
                else:
                    label = f"Node {node_id}"
                line, = self._ax.plot([], [], color=color, label=label)
                self._lines[node_id] = line
                added = True

            self._lines[node_id].set_data(data["ts"], data["temp_f"])
            curr_max = data["ts"][-1]
            curr_min = data["ts"][0]

            if curr_max > max_ts:                   max_ts = curr_max
            if min_ts == 0 or curr_min < min_ts:    min_ts = curr_min

        if added:
            self._legend = self._ax.legend(loc="upper left")
            self._style_legend()

        if max_ts > 0 and not self._toolbar.mode:
            self._ax.set_xlim(
                min_ts if min_ts < max_ts else 0,
                max(1_000, max_ts + 100),
            )
        return list(self._lines.values())

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
        
        # Clear the new table model instead of the tree widget
        self._table_model.clear()
        
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
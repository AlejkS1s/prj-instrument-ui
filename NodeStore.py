# ===========================================================================
# NodeStore — data model
# ===========================================================================
import collections

from constants import MAX_SAMPLES


class NodeStore:
    """
    Manages per-node circular buffers and color assignment.
    """
    def __init__(self):
        self.data: dict[str, dict] = {}

    def color_for(self, node_id: str) -> str:
        colors = [
            "#F44336", "#2196F3", "#4CAF50", "#9C27B0",
            "#FF9800", "#00BCD4", "#E91E63", "#3F51B5",
            "#009688", "#673AB7", "#03A9F4"
        ]
        return colors[hash(node_id) % len(colors)]

    def get_or_create(self, node_id: str) -> tuple[dict, bool]:
        """Returns (buffers_dict, is_new). Creates buffers on first call."""
        if node_id in self.data:
            return self.data[node_id], False
        self.data[node_id] = {
            "ts":     collections.deque(maxlen=MAX_SAMPLES),
            "temp_f": collections.deque(maxlen=MAX_SAMPLES),
            "temp_c": collections.deque(maxlen=MAX_SAMPLES),
        }
        return self.data[node_id], True

    def append(self, node_id: str, ts: float, temp_f: float, temp_c: float):
        """Append a new telemetry reading to the specified node's buffer."""
        node = self.data[node_id]
        node["ts"].append(ts)
        node["temp_f"].append(temp_f)
        node["temp_c"].append(temp_c)

    def clear(self):
        """Clear all recorded data buffers across all nodes."""
        self.data.clear()

    def __iter__(self):
        return iter(self.data.items())
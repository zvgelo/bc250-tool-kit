"""Samples, per-device statistics and the event log.

A gap is a gap: a sample taken while a device is disconnected (or while the
RSSI query failed) carries `rssi=None`, never 0. Statistics only ever see real
readings, and the graph draws a gap where there was none.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field

# sample states
S_OK = "ok"                      # connected, RSSI read
S_NO_RSSI = "no-rssi"            # connected, but no RSSI (no backend, refused, failed)
S_TIMEOUT = "timeout"            # connected, the query timed out
S_DISCONNECTED = "disconnected"  # device known but not connected
S_ABSENT = "absent"              # BlueZ does not know the device (removed, adapter gone)
S_NO_ADAPTER = "no-adapter"      # the adapter the device belongs to is missing
# USB receivers and wired pads: no signal strength exists, only link state
S_LINKED = "linked"              # receiver present and reporting a linked controller
S_LINK_IDLE = "link-idle"        # receiver present, no controller linked (its idle product ID)
S_USB_GONE = "usb-gone"          # the USB device itself disappeared

CONNECTED_STATES = (S_OK, S_NO_RSSI, S_TIMEOUT, S_LINKED)

# RSSI sources
SRC_CONN = "conn"   # mgmt Get Connection Information — the live link
SRC_ADV = "adv"     # Device1.RSSI — advertising, during discovery only


@dataclass
class Sample:
    ts: float
    state: str
    rssi: int | None = None
    source: str | None = None
    tx_power: int | None = None
    max_tx_power: int | None = None
    battery: int | None = None
    detail: str = ""
    latency: float | None = None      # how long the query took, not link latency
    activity: int | None = None       # input events since the previous sample; None = not read
    usb_id: str | None = None         # vid:pid at the time (receivers change product ID)

    @property
    def connected(self) -> bool:
        return self.state in CONNECTED_STATES


class RunningStats:
    """Welford's algorithm: mean and standard deviation without keeping every value."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.count = 0
        self.mean = 0.0
        self._m2 = 0.0
        self.min: int | None = None
        self.max: int | None = None

    def add(self, value: float) -> None:
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        self._m2 += delta * (value - self.mean)
        self.min = value if self.min is None else min(self.min, value)
        self.max = value if self.max is None else max(self.max, value)

    @property
    def stdev(self) -> float | None:
        if self.count < 2:
            return None
        return math.sqrt(self._m2 / (self.count - 1))

    @property
    def avg(self) -> float | None:
        return self.mean if self.count else None


@dataclass
class Change:
    ts: float
    field: str
    old: object
    new: object


class Track:
    """Everything recorded about one monitored device."""

    def __init__(self, key: str, address: str, adapter_identity: str, name: str,
                 maxlen: int = 3600, kind: str = "bt", profile_id: str | None = None) -> None:
        self.key = key
        self.kind = kind                    # "bt" or "usb"
        self.profile_id = profile_id        # USB receiver profile, if known
        # Bluetooth links can report RSSI; a USB receiver never invents one
        self.rssi_capable = kind == "bt"
        self.activity_total = 0
        self.activity_samples = 0
        self.last_input: float | None = None
        self.activity_status: str | None = None
        self.input_node: str | None = None
        self.address = address
        self.adapter_identity = adapter_identity
        self.name = name
        self.adapter_hint: tuple[str | None, str | None] = (None, None)
        self.lock = threading.Lock()
        self.samples: deque[Sample] = deque(maxlen=maxlen)
        self.changes: deque[Change] = deque(maxlen=500)
        self.stats = RunningStats()
        self.started = time.time()
        self.total_samples = 0
        self.connected_samples = 0
        self.rssi_samples = 0
        self.timeouts = 0
        self.disconnects = 0
        self.reconnects = 0
        self.connected: bool | None = None
        self.connected_since: float | None = None
        self.disconnected_since: float | None = None
        self.connected_total = 0.0
        self.downtime_total = 0.0
        self.last_connect: float | None = None
        self.last_disconnect: float | None = None
        self.last_disconnect_reason: str | None = None
        self.last_event: float | None = None
        self.known_addr_type: int | None = None
        self.rssi_kind: str | None = None        # "dBm" (LE) or "dB" (BR/EDR, relative)
        self.last_tx_power: int | None = None
        self.last_max_tx_power: int | None = None
        self.last_sample: Sample | None = None
        self.last_rssi_ts: float | None = None
        self.adapter_name: str | None = None
        self.last_query_status: str | None = None

    # -- recording ------------------------------------------------------------

    def add(self, sample: Sample) -> None:
        with self.lock:
            self.samples.append(sample)
            self.last_sample = sample
            self.total_samples += 1
            if sample.connected:
                self.connected_samples += 1
            if sample.state == S_TIMEOUT:
                self.timeouts += 1
            if sample.rssi is not None:
                self.rssi_samples += 1
                self.stats.add(sample.rssi)
                self.last_rssi_ts = sample.ts
            if sample.tx_power is not None:
                self.last_tx_power = sample.tx_power
            if sample.max_tx_power is not None:
                self.last_max_tx_power = sample.max_tx_power
            if sample.activity is not None:
                self.activity_samples += 1
                self.activity_total += sample.activity
                if sample.activity:
                    self.last_input = sample.ts

    def set_connected(self, connected: bool, ts: float, reason: str | None = None) -> str | None:
        """Records a connection state; returns 'connect' / 'disconnect' on a transition."""
        with self.lock:
            previous = self.connected
            if previous is connected:
                return None
            self.connected = connected
            self.last_event = ts
            if connected:
                # only a link that dropped during the session "reconnects"; one
                # that was down when monitoring began merely connects
                if self.disconnected_since is not None and self.disconnects:
                    self.downtime_total += max(0.0, ts - self.disconnected_since)
                    self.reconnects += 1
                self.disconnected_since = None
                self.connected_since = ts
                self.last_connect = ts
                return "connect" if previous is not None else None
            if self.connected_since is not None:
                self.connected_total += max(0.0, ts - self.connected_since)
            self.connected_since = None
            self.disconnected_since = ts
            if previous:
                self.disconnects += 1
                self.last_disconnect = ts
                self.last_disconnect_reason = reason
                # a new link may come back on another bearer
                self.known_addr_type = None
                return "disconnect"
            return None

    def record_change(self, ts: float, name: str, old: object, new: object) -> None:
        with self.lock:
            self.changes.append(Change(ts, name, old, new))

    def reset_stats(self) -> None:
        with self.lock:
            self.samples.clear()
            self.stats.reset()
            self.total_samples = self.connected_samples = self.rssi_samples = 0
            self.timeouts = self.disconnects = self.reconnects = 0
            self.activity_total = self.activity_samples = 0
            self.connected_total = self.downtime_total = 0.0
            now = time.time()
            self.started = now
            if self.connected:
                self.connected_since = now
            elif self.connected is False:
                self.disconnected_since = now

    # -- derived ----------------------------------------------------------------

    def uptime(self, now: float) -> float | None:
        """How long the current connection has lasted."""
        with self.lock:
            return None if self.connected_since is None else max(0.0, now - self.connected_since)

    def connected_time(self, now: float) -> float:
        with self.lock:
            total = self.connected_total
            if self.connected_since is not None:
                total += max(0.0, now - self.connected_since)
            return total

    def downtime(self, now: float) -> float:
        with self.lock:
            total = self.downtime_total
            if self.disconnected_since is not None and self.disconnects:
                total += max(0.0, now - self.disconnected_since)
            return total

    def availability(self) -> float | None:
        """Share of samples taken while connected that carried an RSSI value."""
        with self.lock:
            if not self.rssi_capable or not self.connected_samples:
                return None
            return 100.0 * self.rssi_samples / self.connected_samples

    def connected_share(self) -> float | None:
        with self.lock:
            if not self.total_samples:
                return None
            return 100.0 * self.connected_samples / self.total_samples

    def snapshot_samples(self) -> list[Sample]:
        with self.lock:
            return list(self.samples)

    def binned_activity(self, width: int, interval: float, end: float) -> list[tuple[float | None, str | None]]:
        """Input events per second in time bins, like binned(); None = not measured."""
        out: list[tuple[float | None, str | None]] = [(None, None)] * width
        if width <= 0 or interval <= 0:
            return out
        start = end - width * interval
        with self.lock:
            samples = list(self.samples)
        for sample in samples:
            if sample.ts <= start or sample.ts > end + interval:
                continue
            slot = min(width - 1, int((sample.ts - start) / interval))
            value = None if sample.activity is None else sample.activity / interval
            if out[slot][0] is None:
                out[slot] = (value, sample.state)
            elif value is not None:
                out[slot] = (out[slot][0] + value, sample.state)
        return out

    def binned(self, width: int, interval: float, end: float) -> list[tuple[int | None, str | None]]:
        """The last `width` time bins of `interval` seconds ending at `end`.

        Each bin is (rssi, state) of the newest sample inside it; a bin with no
        sample is (None, None). The x axis is time, so a period with no samples —
        a pause, a stalled query — shows as such instead of being squeezed out.
        """
        out: list[tuple[int | None, str | None]] = [(None, None)] * width
        if width <= 0 or interval <= 0:
            return out
        start = end - width * interval
        with self.lock:
            samples = list(self.samples)
        for sample in samples:
            if sample.ts <= start or sample.ts > end + interval:
                continue
            slot = min(width - 1, int((sample.ts - start) / interval))
            if slot < 0:
                continue
            current = out[slot]
            if sample.rssi is not None or current[0] is None:
                out[slot] = (sample.rssi, sample.state)
        return out


# --------------------------------------------------------------------------
# Event log
# --------------------------------------------------------------------------

LEVEL_INFO = "info"
LEVEL_WARN = "warn"
LEVEL_ERROR = "error"

CAT_ADAPTER = "adapter"
CAT_DEVICE = "device"
CAT_KERNEL = "kernel"
CAT_BACKEND = "backend"
CAT_USB = "usb"
CAT_APP = "app"


@dataclass
class Event:
    ts: float
    level: str
    category: str
    text: str
    device: str | None = None
    data: dict = field(default_factory=dict)


class EventLog:
    """In-memory ring of events; optionally appended to a JSON-lines file."""

    def __init__(self, maxlen: int = 2000, path: str | None = None,
                 max_file_bytes: int = 2 * 1024 * 1024) -> None:
        self._events: deque[Event] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self.path = path
        self.max_file_bytes = max_file_bytes
        self.write_error: str | None = None
        self.counter = 0

    def add(self, level: str, category: str, text: str, device: str | None = None,
            ts: float | None = None, **data) -> Event:
        event = Event(ts if ts is not None else time.time(), level, category, text, device, data)
        with self._lock:
            self._events.append(event)
            self.counter += 1
        if self.path:
            self._write(event)
        return event

    def events(self, device: str | None = None) -> list[Event]:
        with self._lock:
            items = list(self._events)
        if device is None:
            return items
        return [e for e in items if e.device == device]

    def _write(self, event: Event) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            try:
                if os.path.getsize(self.path) > self.max_file_bytes:
                    os.replace(self.path, self.path + ".1")
            except OSError:
                pass
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "ts": round(event.ts, 3),
                    "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(event.ts)),
                    "level": event.level, "category": event.category,
                    "device": event.device, "text": event.text, **event.data,
                }, ensure_ascii=False) + "\n")
            self.write_error = None
        except OSError as exc:
            self.write_error = str(exc)

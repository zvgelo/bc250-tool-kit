"""The monitoring core: adapter lifecycle, per-device sampling, events.

Threads:
* the BlueZ source (D-Bus reader + resync) calls `notify()` on any change;
* the engine thread turns snapshot differences into events and connection
  state — adapters disappearing and coming back under another hciN included;
* one probe thread per monitored device takes RSSI samples, so a device whose
  query hangs until its timeout does not delay the others;
* the kernel log follower and the RSSI helper deliver their own events.

The interface only reads: snapshots, tracks and the event log.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import bluez, history, inputs, kernlog, mgmt, telemetry, usb
from .config import Config
from .history import EventLog, Sample, Track
from .i18n import t
from .model import AdapterInfo, DeviceInfo, resolve_adapter, sysfs_battery

WATCHED_DEVICE_FIELDS = ("connected", "paired", "bonded", "trusted", "blocked",
                         "services_resolved", "battery", "tx_power", "alias")
WATCHED_ADAPTER_FIELDS = ("powered", "power_state", "discovering")
MAX_MONITORED = 4


def device_key(identity: str, address: str) -> str:
    return f"{identity}/{address}"


def is_usb_key(key: str) -> bool:
    return key.startswith("usb:")


@dataclass
class Target:
    """Something to monitor, whatever carries it: a Bluetooth device or a USB controller."""
    key: str
    name: str
    kind: str                          # "bt" | "usb"
    address: str                       # BT address, or the USB physical id
    adapter_identity: str = ""         # BT only
    profile_id: str | None = None      # USB receivers with a profile


def bt_target(identity: str, dev: DeviceInfo) -> Target:
    return Target(device_key(identity, dev.address), dev.display_name, "bt", dev.address, identity)


def usb_target(dev: usb.UsbDevice) -> Target:
    return Target(dev.physical_id, dev.display_name, "usb", dev.physical_id,
                  profile_id=dev.profile.id if dev.profile else None)


def diff_usb(prev: list[usb.UsbDevice] | None, cur: list[usb.UsbDevice]) -> list[dict]:
    """USB controllers added, removed, or switching product ID on the same port."""
    if prev is None:
        return [{"kind": "usb-found", "device": d} for d in cur]
    before = {d.physical_id: d for d in prev}
    after = {d.physical_id: d for d in cur}
    changes = []
    for pid, dev in after.items():
        old = before.get(pid)
        if old is None:
            changes.append({"kind": "usb-added", "device": dev})
        elif old.usb_id != dev.usb_id:
            changes.append({"kind": "usb-mode", "device": dev, "old": old})
        elif old.battery != dev.battery and dev.battery is not None:
            changes.append({"kind": "usb-battery", "device": dev, "old": old})
    for pid, dev in before.items():
        if pid not in after:
            changes.append({"kind": "usb-removed", "device": dev})
    return changes


@dataclass
class AdapterRecord:
    """What the engine remembers about an adapter, keyed by identity."""
    name: str
    index: int | None
    address: str
    usb_id: str | None
    usb_port: str | None
    present: bool = True
    registrations: int = 1
    removed_at: float | None = None
    renames: list[tuple[float, str, str]] = field(default_factory=list)


@dataclass
class KernelCounters:
    cmd_timeouts: int = 0
    cmd_failures: int = 0
    usb_resets: int = 0
    hw_errors: int = 0
    last: kernlog.KernelEvent | None = None
    scan_timeouts: int = 0


def diff_snapshots(prev: bluez.Snapshot | None, cur: bluez.Snapshot) -> list[dict]:
    """What changed between two snapshots, keyed by adapter identity, not path.

    Devices are compared by (adapter identity, address), so a device that
    returns under /org/bluez/hci1 after living under /org/bluez/hci0 is the same
    device to this function.
    """
    changes: list[dict] = []
    prev_status = prev.status if prev else None
    if prev_status != cur.status:
        changes.append({"kind": "status", "old": prev_status, "new": cur.status, "error": cur.error})

    prev_adapters = {a.identity: a for a in (prev.adapters if prev else [])}
    cur_adapters = {a.identity: a for a in cur.adapters}
    for identity, adapter in cur_adapters.items():
        old = prev_adapters.get(identity)
        if old is None:
            changes.append({"kind": "adapter-added", "adapter": adapter})
            continue
        if old.path != adapter.path:
            changes.append({"kind": "adapter-moved", "adapter": adapter, "old": old})
        for name in WATCHED_ADAPTER_FIELDS:
            before, after = getattr(old, name), getattr(adapter, name)
            if before != after:
                changes.append({"kind": "adapter-prop", "adapter": adapter, "field": name,
                                "old": before, "new": after})
    for identity, adapter in prev_adapters.items():
        if identity not in cur_adapters:
            changes.append({"kind": "adapter-removed", "adapter": adapter})

    def keyed(snap: bluez.Snapshot | None) -> dict[str, tuple[DeviceInfo, AdapterInfo | None]]:
        out = {}
        if snap is None:
            return out
        by_path = {a.path: a for a in snap.adapters}
        for dev in snap.devices:
            adapter = by_path.get(dev.adapter_path)
            identity = adapter.identity if adapter else dev.adapter_path
            out[device_key(identity, dev.address)] = (dev, adapter)
        return out

    prev_devs = keyed(prev)
    cur_devs = keyed(cur)
    for key, (dev, adapter) in cur_devs.items():
        old = prev_devs.get(key)
        if old is None:
            changes.append({"kind": "device-added", "key": key, "device": dev, "adapter": adapter})
            continue
        for name in WATCHED_DEVICE_FIELDS:
            before, after = getattr(old[0], name), getattr(dev, name)
            if before != after:
                changes.append({"kind": "device-prop", "key": key, "device": dev,
                                "field": name, "old": before, "new": after})
    for key, (dev, adapter) in prev_devs.items():
        if key not in cur_devs:
            changes.append({"kind": "device-removed", "key": key, "device": dev, "adapter": adapter})
    return changes


class Engine:
    def __init__(self, source, backend: telemetry.RssiBackend, config: Config,
                 events: EventLog, follow_kernel: bool = True,
                 kernel_factory: Callable | None = None,
                 clock: Callable[[], float] = time.time,
                 battery_reader: Callable[[str], tuple[int, str] | None] = sysfs_battery,
                 usb_source=None,
                 activity_factory: Callable[[str | None], object] | None = None,
                 bt_inputs: Callable[[str], list] = usb.bt_input_nodes) -> None:
        self.source = source
        self.usb_source = usb_source
        self.activity_factory = activity_factory or inputs.ActivityReader
        self.bt_inputs = bt_inputs
        self._readers: dict[str, object] = {}
        self._prev_usb: usb.UsbSnapshot | None = None
        self.usb_records: dict[str, list[tuple[float, str, str]]] = {}   # physical id -> PID changes
        self.backend = backend
        self.cfg = config
        self.events = events
        self.clock = clock
        self.battery_reader = battery_reader
        self.interval = config.interval
        self.paused = False
        self.tracks: dict[str, Track] = {}
        self.monitored: list[str] = []
        self.adapter_records: dict[str, AdapterRecord] = {}
        self.kernel: dict[str, KernelCounters] = {}       # by "hciN"
        self.backend_status: dict[str, str] = {}          # by track key
        self.disconnect_reasons: dict[tuple[int, str], str] = {}
        self.recorder: Callable[[Track, Sample], None] | None = None
        self.started = clock()
        self.changed = threading.Event()
        self._prev: bluez.Snapshot | None = None
        self._settled = False
        self._adapters_changing: set[str] = set()
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._probes: dict[str, _Probe] = {}
        self._follow_kernel = follow_kernel
        self._kernel_factory = kernel_factory or (lambda cb: kernlog.KernelLogFollower(cb))
        self.kernel_follower = None
        self.backend.on_event = self._on_mgmt_event
        source.on_change = self.notify
        if usb_source is not None:
            usb_source.on_change = self.notify

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        self.source.start()
        if self.usb_source is not None:
            self.usb_source.start()
        if self._follow_kernel:
            self.kernel_follower = self._kernel_factory(self._on_kernel_event)
            self.kernel_follower.start()
        self._thread = threading.Thread(target=self._run, name="engine", daemon=True)
        self._thread.start()
        self.events.add(history.LEVEL_INFO, history.CAT_APP, t("ev.started"))

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        for probe in list(self._probes.values()):
            probe.stop()
        for probe in list(self._probes.values()):
            probe.join(timeout=3.0)
        self._probes.clear()
        for reader in list(self._readers.values()):
            reader.stop()
        self._readers.clear()
        if self.usb_source is not None:
            self.usb_source.stop()
        if self.kernel_follower is not None:
            self.kernel_follower.stop()
        self.backend.stop()
        self.source.stop()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def notify(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(1.0)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.refresh()
            except Exception as exc:  # noqa: BLE001 — never let the engine thread die
                self.events.add(history.LEVEL_ERROR, history.CAT_APP, f"engine: {exc!r}")

    # -- state access ----------------------------------------------------------

    def snapshot(self) -> bluez.Snapshot:
        return self.source.snapshot()

    def resolve(self, track: Track, snap: bluez.Snapshot) -> tuple[AdapterInfo | None, DeviceInfo | None]:
        adapter = resolve_adapter(track.adapter_identity, snap.adapters, track.adapter_hint)
        if adapter is None:
            return None, None
        for dev in snap.devices:
            if dev.adapter_path == adapter.path and dev.address == track.address:
                return adapter, dev
        return adapter, None

    # -- snapshot differences -> events ------------------------------------------

    def usb_snapshot(self) -> usb.UsbSnapshot:
        return self.usb_source.snapshot() if self.usb_source is not None else usb.UsbSnapshot()

    def resolve_usb(self, track: Track, snap: usb.UsbSnapshot | None = None) -> usb.UsbDevice | None:
        snap = snap or self.usb_snapshot()
        claimed = {self.tracks[k].address for k in self.monitored
                   if k != track.key and k in self.tracks and is_usb_key(k)}
        return usb.resolve_usb(track.address, snap.devices, track.profile_id, claimed)

    def refresh(self) -> None:
        usb_snap = self.usb_snapshot()
        with self._lock:
            if self._prev_usb is None or usb_snap.generation != self._prev_usb.generation:
                now = self.clock()
                for change in diff_usb(self._prev_usb.devices if self._prev_usb else None,
                                       usb_snap.devices):
                    self._apply_usb_change(change, now)
                self._prev_usb = usb_snap
                for key in self.monitored:
                    track = self.tracks.get(key)
                    if track is not None and track.kind == "usb":
                        self._update_usb_connection(track, usb_snap, now)
                self.changed.set()
        self._refresh_bt()

    def _refresh_bt(self) -> None:
        snap = self.source.snapshot()
        with self._lock:
            if self._prev is not None and snap.generation == self._prev.generation:
                return
            changes = diff_snapshots(self._prev, snap)
            self._prev = snap
            now = self.clock()
            # devices that come and go with their adapter are covered by the
            # adapter's own event; listing each of them would only bury it
            self._adapters_changing = {c["adapter"].identity for c in changes
                                       if c["kind"] in ("adapter-added", "adapter-removed")}
            for change in changes:
                self._apply_change(change, now)
            # adapters present at start-up are "found"; later ones were "added"
            self._settled = self._settled or snap.status == bluez.STATUS_OK
            for key in self.monitored:
                track = self.tracks.get(key)
                if track is not None and track.kind == "bt":
                    self._update_connection(track, snap, now)
        self.changed.set()

    def _apply_usb_change(self, change: dict, now: float) -> None:
        dev: usb.UsbDevice = change["device"]
        kind = change["kind"]
        key = dev.physical_id
        tracked = key if key in self.tracks else None
        where = f"USB {dev.port}"
        if kind == "usb-found":
            self.events.add(history.LEVEL_INFO, history.CAT_USB,
                            t("ev.usb.found", name=dev.display_name, usb=dev.usb_id, port=dev.port,
                              mode=t_mode(dev.mode)), ts=now)
        elif kind == "usb-added":
            self.events.add(history.LEVEL_INFO, history.CAT_USB,
                            t("ev.usb.added", name=dev.display_name, usb=dev.usb_id, port=dev.port,
                              mode=t_mode(dev.mode)), device=tracked, ts=now)
        elif kind == "usb-removed":
            self.events.add(history.LEVEL_WARN, history.CAT_USB,
                            t("ev.usb.removed", name=dev.display_name, usb=dev.usb_id, port=dev.port),
                            device=tracked, ts=now)
        elif kind == "usb-mode":
            old = change["old"]
            self.usb_records.setdefault(key, []).append((now, old.usb_id, dev.usb_id))
            self.events.add(history.LEVEL_INFO if dev.mode == "active" else history.LEVEL_WARN,
                            history.CAT_USB,
                            t("ev.usb.mode", name=dev.display_name, where=where, old=old.usb_id,
                              new=dev.usb_id, mode=t_mode(dev.mode)),
                            device=tracked, ts=now, old=old.usb_id, new=dev.usb_id)
            if tracked:
                self.tracks[key].record_change(now, "usb_id", old.usb_id, dev.usb_id)
        elif kind == "usb-battery" and tracked:
            old = change["old"].battery
            self.tracks[key].record_change(now, "battery", old[0] if old else None, dev.battery[0])

    def _update_usb_connection(self, track: Track, snap: usb.UsbSnapshot, now: float) -> None:
        dev = self.resolve_usb(track, snap)
        if dev is not None and dev.physical_id != track.address:
            # same receiver profile, another port: follow it instead of inventing a new device
            self.events.add(history.LEVEL_WARN, history.CAT_USB,
                            t("ev.usb.moved", name=track.name, old=track.address, new=dev.physical_id),
                            device=track.key, ts=now)
            track.address = dev.physical_id
        if dev is None:
            connected, reason = False, t("reason.usb.gone")
        elif dev.mode == "idle":
            connected, reason = False, t("reason.usb.idle", usb=dev.usb_id)
        else:
            connected, reason = True, None
        if dev is not None:
            track.adapter_name = f"USB {dev.port}"
        transition = track.set_connected(connected, now, reason)
        if transition == "connect":
            self.events.add(history.LEVEL_INFO, history.CAT_DEVICE,
                            t("ev.device.connected", name=track.name, adapter=track.adapter_name or "USB"),
                            device=track.key, ts=now)
        elif transition == "disconnect":
            self.events.add(history.LEVEL_WARN, history.CAT_DEVICE,
                            t("ev.device.disconnected", name=track.name) + (f" — {reason}" if reason else ""),
                            device=track.key, ts=now)

    def _apply_change(self, change: dict, now: float) -> None:
        kind = change["kind"]
        add = self.events.add
        if kind == "status":
            new = change["new"]
            if new == bluez.STATUS_OK:
                if change["old"] not in (None, bluez.STATUS_STARTING):
                    add(history.LEVEL_INFO, history.CAT_ADAPTER, t("ev.bluez.back"))
            elif new == bluez.STATUS_NO_BLUEZ:
                add(history.LEVEL_ERROR, history.CAT_ADAPTER, t("ev.bluez.gone"))
            elif new == bluez.STATUS_NO_BUS:
                add(history.LEVEL_ERROR, history.CAT_ADAPTER,
                    t("ev.bus.gone", error=change.get("error") or "?"))
            elif new == bluez.STATUS_ERROR:
                add(history.LEVEL_ERROR, history.CAT_ADAPTER,
                    t("ev.bluez.error", error=change.get("error") or "?"))
        elif kind == "adapter-added":
            self._adapter_added(change["adapter"], now)
        elif kind == "adapter-removed":
            adapter = change["adapter"]
            record = self.adapter_records.get(adapter.identity)
            if record is not None:
                record.present = False
                record.removed_at = now
            add(history.LEVEL_WARN, history.CAT_ADAPTER,
                t("ev.adapter.removed", name=adapter.name, address=adapter.address), ts=now,
                adapter=adapter.identity)
        elif kind == "adapter-moved":
            adapter, old = change["adapter"], change["old"]
            self._note_rename(adapter, old.name, now)
        elif kind == "adapter-prop":
            adapter = change["adapter"]
            level = history.LEVEL_WARN if change["field"] == "powered" and not change["new"] \
                else history.LEVEL_INFO
            add(level, history.CAT_ADAPTER,
                t("ev.adapter.prop", name=adapter.name, field=change["field"],
                  old=_fmt(change["old"]), new=_fmt(change["new"])), ts=now)
        elif kind in ("device-added", "device-removed"):
            dev = change["device"]
            key = change["key"]
            adapter = change.get("adapter")
            if adapter is not None and adapter.identity in self._adapters_changing:
                return
            if key in self.tracks or dev.paired or dev.connected:
                msg = "ev.device.added" if kind == "device-added" else "ev.device.removed"
                add(history.LEVEL_INFO if kind == "device-added" else history.LEVEL_WARN,
                    history.CAT_DEVICE, t(msg, name=dev.display_name, address=dev.address),
                    device=key if key in self.tracks else None, ts=now)
        elif kind == "device-prop":
            key = change["key"]
            track = self.tracks.get(key)
            if track is not None:
                track.record_change(now, change["field"], change["old"], change["new"])
                if change["field"] != "connected":
                    add(history.LEVEL_INFO, history.CAT_DEVICE,
                        t("ev.device.prop", name=track.name, field=change["field"],
                          old=_fmt(change["old"]), new=_fmt(change["new"])),
                        device=key, ts=now)

    def _adapter_added(self, adapter: AdapterInfo, now: float) -> None:
        record = self.adapter_records.get(adapter.identity)
        if record is None:
            # an adapter that came back from a reset with a different address is
            # still the same USB device on the same port
            for other_id, other in self.adapter_records.items():
                if (not other.present and adapter.usb_id and adapter.usb_port
                        and other.usb_id == adapter.usb_id and other.usb_port == adapter.usb_port):
                    self.events.add(history.LEVEL_WARN, history.CAT_ADAPTER,
                                    t("ev.adapter.newaddr", name=adapter.name,
                                      old=other.address, new=adapter.address), ts=now)
                    for track in self.tracks.values():
                        if track.adapter_identity == other_id:
                            track.adapter_identity = adapter.identity
                    record = other
                    del self.adapter_records[other_id]
                    self.adapter_records[adapter.identity] = record
                    break
        if record is None:
            self.adapter_records[adapter.identity] = AdapterRecord(
                adapter.name, adapter.index, adapter.address, adapter.usb_id, adapter.usb_port)
            if self._settled:
                self.events.add(history.LEVEL_INFO, history.CAT_ADAPTER,
                                t("ev.adapter.added", name=adapter.name, address=adapter.address),
                                ts=now)
            else:
                self.events.add(history.LEVEL_INFO, history.CAT_ADAPTER,
                                t("ev.adapter.found", name=adapter.name, address=adapter.address,
                                  usb=adapter.usb_id or "?"), ts=now)
            return
        record.present = True
        record.registrations += 1
        record.address = adapter.address
        if record.name != adapter.name:
            self._note_rename(adapter, record.name, now)
        else:
            self.events.add(history.LEVEL_WARN, history.CAT_ADAPTER,
                            t("ev.adapter.back", name=adapter.name, address=adapter.address),
                            ts=now, adapter=adapter.identity)
        record.usb_id, record.usb_port = adapter.usb_id, adapter.usb_port

    def _note_rename(self, adapter: AdapterInfo, old_name: str, now: float) -> None:
        record = self.adapter_records.get(adapter.identity)
        if record is None:
            return
        record.renames.append((now, old_name, adapter.name))
        record.name, record.index = adapter.name, adapter.index
        self.events.add(history.LEVEL_WARN, history.CAT_ADAPTER,
                        t("ev.adapter.remap", old=old_name, new=adapter.name,
                          address=adapter.address), ts=now, adapter=adapter.identity,
                        old=old_name, new=adapter.name)

    def _update_connection(self, track: Track, snap: bluez.Snapshot, now: float) -> None:
        adapter, dev = self.resolve(track, snap)
        if adapter is not None:
            track.adapter_hint = (adapter.usb_id, adapter.usb_port)
            track.adapter_name = adapter.name
        connected = bool(dev and dev.connected)
        reason = None
        if not connected:
            if adapter is None:
                reason = t("reason.adapter.gone")
            elif dev is None:
                reason = t("reason.device.gone")
            elif adapter.index is not None:
                reason = self.disconnect_reasons.pop((adapter.index, track.address), None)
        transition = track.set_connected(connected, now, reason)
        if transition == "connect":
            self.events.add(history.LEVEL_INFO, history.CAT_DEVICE,
                            t("ev.device.connected", name=track.name,
                              adapter=adapter.name if adapter else "?"),
                            device=track.key, ts=now)
        elif transition == "disconnect":
            self.events.add(history.LEVEL_WARN, history.CAT_DEVICE,
                            t("ev.device.disconnected", name=track.name)
                            + (f" — {reason}" if reason else ""),
                            device=track.key, ts=now)

    # -- selection ---------------------------------------------------------------

    def set_monitored(self, targets: list) -> None:
        """At most four Targets (or legacy (adapter identity, DeviceInfo) pairs).

        History of a device that was monitored before is kept.
        """
        targets = [bt_target(*t_) if isinstance(t_, tuple) else t_ for t_ in targets][:MAX_MONITORED]
        names = [tg.name for tg in targets]
        keys = []
        for tg in targets:
            keys.append(tg.key)
            name = tg.name
            # two identical pads must stay distinguishable in panels and the log
            if names.count(name) > 1:
                name = f"{name} ·{tg.address[-5:]}"
            if tg.key not in self.tracks:
                self.tracks[tg.key] = Track(tg.key, tg.address, tg.adapter_identity, name,
                                            maxlen=self.cfg.history, kind=tg.kind,
                                            profile_id=tg.profile_id)
            else:
                self.tracks[tg.key].name = name
        with self._lock:
            self.monitored = keys
            snap = self.source.snapshot()
            usb_snap = self.usb_snapshot()
            now = self.clock()
            for key in keys:
                track = self.tracks[key]
                if track.kind == "usb":
                    self._update_usb_connection(track, usb_snap, now)
                else:
                    self._update_connection(track, snap, now)
        for key in list(self._probes):
            if key not in keys:
                self._probes.pop(key).stop()
                reader = self._readers.pop(key, None)
                if reader is not None:
                    reader.stop()
        for key in keys:
            if key not in self._probes:
                probe = _Probe(self, key)
                self._probes[key] = probe
                probe.start()
        self.events.add(history.LEVEL_INFO, history.CAT_APP,
                        t("ev.monitoring", names=", ".join(self.tracks[k].name for k in keys)))
        self.changed.set()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        for probe in self._probes.values():
            probe.wake()

    def set_interval(self, interval: float) -> None:
        self.interval = max(0.2, min(60.0, interval))
        for probe in self._probes.values():
            probe.wake()

    def reset_stats(self) -> None:
        for key in self.monitored:
            self.tracks[key].reset_stats()
        self.events.add(history.LEVEL_INFO, history.CAT_APP, t("ev.reset"))

    def set_discovery(self, adapter: AdapterInfo, enable: bool) -> str | None:
        error = self.source.set_discovery(adapter.path, enable)
        if error:
            self.events.add(history.LEVEL_WARN, history.CAT_ADAPTER,
                            t("ev.discovery.failed", name=adapter.name, error=error))
        else:
            self.events.add(history.LEVEL_INFO, history.CAT_ADAPTER,
                            t("ev.discovery.on" if enable else "ev.discovery.off", name=adapter.name))
        return error

    # -- sampling ------------------------------------------------------------------

    def _activity(self, track: Track, node: str | None) -> int | None:
        """Input events since the last sample from the device's evdev node, if readable."""
        if not self.cfg.input_activity:
            return None
        reader = self._readers.get(track.key)
        if reader is None or getattr(reader, "path", None) != node or \
                getattr(reader, "status", None) == inputs.ST_GONE:
            if reader is not None:
                reader.stop()
            reader = self.activity_factory(node)
            reader.start()
            self._readers[track.key] = reader
        previous = track.activity_status
        track.activity_status = reader.status
        track.input_node = node
        if previous != reader.status and reader.status == inputs.ST_PERMISSION:
            self.events.add(history.LEVEL_WARN, history.CAT_APP,
                            t("ev.input.denied", name=track.name, node=node or "?"), device=track.key)
        return reader.take()

    def _sample_usb(self, track: Track) -> Sample:
        now = self.clock()
        snap = self.usb_snapshot()
        dev = self.resolve_usb(track, snap)
        with self._lock:
            self._update_usb_connection(track, snap, now)
        if dev is None:
            reader = self._readers.pop(track.key, None)
            if reader is not None:
                reader.stop()
            return Sample(now, history.S_USB_GONE)
        nodes = dev.joystick_nodes()
        activity = self._activity(track, nodes[0].dev_path if nodes else None)
        battery = dev.battery[0] if dev.battery else None
        if dev.mode == "idle":
            return Sample(now, history.S_LINK_IDLE, battery=battery, activity=activity, usb_id=dev.usb_id)
        return Sample(now, history.S_LINKED, battery=battery, activity=activity, usb_id=dev.usb_id)

    def sample(self, key: str) -> Sample | None:
        track = self.tracks.get(key)
        if track is None:
            return None
        if track.kind == "usb":
            sample = self._sample_usb(track)
            track.add(sample)
            self._record(track, sample)
            return sample
        snap = self.source.snapshot()
        adapter, dev = self.resolve(track, snap)
        now = self.clock()
        with self._lock:
            self._update_connection(track, snap, now)

        if adapter is None:
            sample = Sample(now, history.S_NO_ADAPTER)
        elif dev is None:
            sample = Sample(now, history.S_ABSENT)
        elif not dev.connected:
            sample = Sample(now, history.S_DISCONNECTED)
        else:
            sample = self._sample_connected(track, adapter, dev, now)
            if self.cfg.input_activity:
                nodes = self.bt_inputs(dev.address)
                sample.activity = self._activity(track, nodes[0].dev_path if nodes else None)
        track.add(sample)
        self._record(track, sample)
        return sample

    def _record(self, track: Track, sample: Sample) -> None:
        if self.recorder is not None:
            try:
                self.recorder(track, sample)
            except Exception:  # noqa: BLE001
                pass
        self.changed.set()

    def _battery(self, dev: DeviceInfo) -> int | None:
        if dev.battery is not None:
            return dev.battery
        found = self.battery_reader(dev.address)
        return found[0] if found else None

    def _sample_connected(self, track: Track, adapter: AdapterInfo, dev: DeviceInfo,
                          now: float) -> Sample:
        battery = self._battery(dev)
        if not self.backend.available or adapter.index is None:
            # no connection RSSI: Device1.RSSI is only meaningful while discovering
            if dev.rssi is not None and adapter.discovering:
                return Sample(now, history.S_OK, dev.rssi, history.SRC_ADV, battery=battery,
                              detail=t("detail.adv"))
            return Sample(now, history.S_NO_RSSI, battery=battery,
                          detail=self.backend.problem or t("detail.nobackend"))

        types = telemetry.candidate_types(dev.address_type, dev.connected_bearer, dev.bearer,
                                          track.known_addr_type)
        info = self.backend.query(adapter.index, dev.address, types, self.cfg.query_timeout)
        self._note_backend_status(track, info, adapter)
        if info.status == telemetry.OK:
            track.known_addr_type = info.addr_type
            track.rssi_kind = "dB" if info.addr_type == mgmt.ADDR_BREDR else "dBm"
            if info.rssi is None:
                return Sample(now, history.S_NO_RSSI, None, history.SRC_CONN, info.tx_power,
                              info.max_tx_power, battery, t("detail.rssi.invalid"), info.elapsed)
            return Sample(now, history.S_OK, info.rssi, history.SRC_CONN, info.tx_power,
                          info.max_tx_power, battery, latency=info.elapsed)
        state = history.S_TIMEOUT if info.status == telemetry.TIMEOUT else history.S_NO_RSSI
        return Sample(now, state, battery=battery, detail=info.detail or info.status,
                      latency=info.elapsed)

    def _note_backend_status(self, track: Track, info: telemetry.ConnInfo,
                             adapter: AdapterInfo) -> None:
        previous = self.backend_status.get(track.key)
        self.backend_status[track.key] = info.status
        track.last_query_status = info.status
        if previous == info.status:
            return
        if info.status == telemetry.OK:
            if previous is not None:
                self.events.add(history.LEVEL_INFO, history.CAT_BACKEND,
                                t("ev.rssi.back", name=track.name), device=track.key)
            return
        level = history.LEVEL_WARN if info.status in (telemetry.TIMEOUT, telemetry.NOT_CONNECTED) \
            else history.LEVEL_ERROR
        self.events.add(level, history.CAT_BACKEND,
                        t("ev.rssi.problem", name=track.name, status=t("status." + info.status),
                          adapter=adapter.name, detail=info.detail or ""),
                        device=track.key, status=info.status)

    # -- external events -------------------------------------------------------------

    def _on_mgmt_event(self, event: dict) -> None:
        kind = event.get("event")
        index = event.get("index")
        name = f"hci{index}" if isinstance(index, int) else "hci?"
        if kind == "device_disconnected":
            reason = mgmt.DISCONNECT_REASONS.get(event.get("reason"), str(event.get("reason")))
            address = str(event.get("address", ""))
            if isinstance(index, int):
                self.disconnect_reasons[(index, address)] = reason
            self.events.add(history.LEVEL_INFO, history.CAT_BACKEND,
                            t("ev.mgmt.disconnected", adapter=name, address=address, reason=reason))
        elif kind == "controller_error":
            self.events.add(history.LEVEL_ERROR, history.CAT_BACKEND,
                            t("ev.mgmt.ctrlerror", adapter=name, code=event.get("code")))
        elif kind == "index_removed":
            self.events.add(history.LEVEL_WARN, history.CAT_BACKEND,
                            t("ev.mgmt.index.removed", adapter=name))
        elif kind == "index_added":
            self.events.add(history.LEVEL_INFO, history.CAT_BACKEND,
                            t("ev.mgmt.index.added", adapter=name))
        self.notify()

    def _on_kernel_event(self, event: kernlog.KernelEvent) -> None:
        if event.kind == kernlog.K_OTHER:
            return
        if event.kind == kernlog.K_USB_DISCONNECT:
            ports = {r.usb_port for r in self.adapter_records.values() if r.usb_port}
            if event.usb_port not in ports:
                return
        name = f"hci{event.hci}" if event.hci is not None else (event.usb_port or "?")
        counters = self.kernel.setdefault(name, KernelCounters())
        counters.last = event
        level = history.LEVEL_INFO
        if event.kind == kernlog.K_CMD_TIMEOUT:
            counters.cmd_timeouts += 1
            if event.opcode in kernlog.SCAN_OPCODES:
                counters.scan_timeouts += 1
            level = history.LEVEL_ERROR
        elif event.kind == kernlog.K_CMD_FAILED:
            counters.cmd_failures += 1
            level = history.LEVEL_WARN
        elif event.kind == kernlog.K_USB_RESET:
            counters.usb_resets += 1
            level = history.LEVEL_ERROR
        elif event.kind == kernlog.K_HW_ERROR:
            counters.hw_errors += 1
            level = history.LEVEL_ERROR
        elif event.kind == kernlog.K_USB_DISCONNECT:
            level = history.LEVEL_WARN
        text = event.text
        if event.opcode is not None:
            opname = event.opcode_name
            if opname:
                text += f"  [{opname}]"
        if event.errno is not None and event.errno in kernlog.ERRNO_NAMES:
            text += f"  [{kernlog.ERRNO_NAMES[event.errno]}]"
        if event.historic:
            text = t("ev.kernel.earlier") + " " + text
        self.events.add(level, history.CAT_KERNEL, text, ts=event.ts, kind=event.kind,
                        historic=event.historic)
        self.changed.set()

    # -- selection persistence --------------------------------------------------------

    def save_selection(self) -> None:
        """One line per device: bt:ADDRESS or usb:<physical id>."""
        if not self.cfg.remember_selection:
            return
        try:
            os.makedirs(os.path.dirname(self.cfg.selection_file), exist_ok=True)
            with open(self.cfg.selection_file, "w", encoding="utf-8") as handle:
                for key in self.monitored:
                    track = self.tracks[key]
                    handle.write((track.address if track.kind == "usb" else "bt:" + track.address) + "\n")
        except OSError:
            pass

    def load_selection(self) -> list[str]:
        """Selection entries; a bare address (written by bc250-btmon) means bt:ADDRESS."""
        if not self.cfg.remember_selection:
            return []
        try:
            with open(self.cfg.selection_file, encoding="utf-8") as handle:
                lines = [line.strip() for line in handle if line.strip()]
        except OSError:
            return []
        out = []
        for line in lines:
            if line.startswith("usb:"):
                out.append(line)
                continue
            address = line[3:] if line.startswith("bt:") else line
            if mgmt.valid_address(address.upper()):
                out.append("bt:" + address.upper())
        return out


class _Probe(threading.Thread):
    """Samples one device at the engine's interval until stopped."""

    def __init__(self, engine: Engine, key: str) -> None:
        super().__init__(name=f"probe-{key[-17:]}", daemon=True)
        self.engine = engine
        self.key = key
        self._stop_evt = threading.Event()
        self._wake = threading.Event()

    def stop(self) -> None:
        self._stop_evt.set()
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def run(self) -> None:
        while not self._stop_evt.is_set():
            started = time.monotonic()
            if not self.engine.paused:
                try:
                    self.engine.sample(self.key)
                except Exception as exc:  # noqa: BLE001
                    self.engine.events.add(history.LEVEL_ERROR, history.CAT_APP,
                                           f"probe {self.key}: {exc!r}")
            delay = max(0.05, self.engine.interval - (time.monotonic() - started))
            self._wake.wait(delay)
            self._wake.clear()


def t_mode(mode: str | None) -> str:
    return t("usb.mode." + (mode or "unknown"))


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return t("yes") if value else t("no")
    return str(value)

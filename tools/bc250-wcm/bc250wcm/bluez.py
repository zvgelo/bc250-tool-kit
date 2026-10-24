"""BlueZ over the system D-Bus: an object cache kept current by signals.

The cache mirrors org.freedesktop.DBus.ObjectManager under /org/bluez — adapters,
devices, batteries, bearers and GATT services. Signals keep it current; a full
GetManagedObjects runs at start-up, whenever bluetoothd (re)appears on the bus,
and periodically as a safety net against a missed signal.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import dbus
from .model import (IFACE_ADAPTER, AdapterInfo, DeviceInfo, assign_identities,
                    parse_managed_objects, read_sysfs_adapter)

BLUEZ = "org.bluez"
OM = "org.freedesktop.DBus.ObjectManager"
PROPS = "org.freedesktop.DBus.Properties"

STATUS_OK = "ok"
STATUS_NO_BUS = "no-bus"
STATUS_NO_BLUEZ = "no-bluez"
STATUS_ERROR = "error"
STATUS_STARTING = "starting"


@dataclass
class Snapshot:
    adapters: list[AdapterInfo] = field(default_factory=list)
    devices: list[DeviceInfo] = field(default_factory=list)
    status: str = STATUS_STARTING
    error: str | None = None
    generation: int = 0

    def adapter_by_path(self, path: str) -> AdapterInfo | None:
        for adapter in self.adapters:
            if adapter.path == path:
                return adapter
        return None


def build_snapshot(objects: dict, status: str, error: str | None, generation: int,
                   sysfs_reader: Callable[[str], dict] = read_sysfs_adapter) -> Snapshot:
    adapters, devices = parse_managed_objects(objects)
    adapter_list = sorted(adapters.values(), key=lambda a: (a.index is None, a.index or 0, a.path))
    for adapter in adapter_list:
        extra = sysfs_reader(adapter.name)
        adapter.usb_id = extra.get("usb_id")
        adapter.usb_port = extra.get("usb_port")
        adapter.usb_product = extra.get("usb_product")
        adapter.driver = extra.get("driver")
    assign_identities(adapter_list)
    return Snapshot(adapters=adapter_list, devices=list(devices.values()), status=status,
                    error=error, generation=generation)


def apply_signal(cache: dict, msg: dbus.Message) -> bool | None:
    """Applies one BlueZ signal to the object cache.

    Returns True when the cache changed, False when the signal was irrelevant,
    and None when the cache cannot be trusted any more and needs a resync.
    """
    if msg.interface == OM and msg.member == "InterfacesAdded" and len(msg.body) >= 2:
        path, ifaces = msg.body[0], msg.body[1]
        if not isinstance(ifaces, dict):
            return None
        entry = cache.setdefault(path, {})
        for iface, props in ifaces.items():
            entry[iface] = dict(props) if isinstance(props, dict) else {}
        return True
    if msg.interface == OM and msg.member == "InterfacesRemoved" and len(msg.body) >= 2:
        path, ifaces = msg.body[0], msg.body[1]
        entry = cache.get(path)
        if entry is None:
            return False
        for iface in ifaces or ():
            entry.pop(iface, None)
        if not any(i.startswith("org.bluez.") for i in entry):
            cache.pop(path, None)
        # removing an adapter takes everything below it along
        if IFACE_ADAPTER in (ifaces or ()):
            for other in [p for p in cache if p.startswith(path + "/")]:
                cache.pop(other, None)
        return True
    if msg.interface == PROPS and msg.member == "PropertiesChanged" and len(msg.body) >= 2:
        iface, changed = msg.body[0], msg.body[1]
        invalidated = msg.body[2] if len(msg.body) > 2 else []
        entry = cache.get(msg.path or "")
        if entry is None or iface not in entry:
            # a property of an object we never saw: the cache missed something
            return None if iface.startswith("org.bluez.") else False
        if isinstance(changed, dict):
            entry[iface].update(changed)
        for key in invalidated or ():
            entry[iface].pop(key, None)
        return True
    return False


class BluezSource:
    """The live BlueZ state. Thread-safe; `on_change` fires on every update."""

    RESYNC_EVERY = 30.0
    RECONNECT_EVERY = 3.0

    def __init__(self, on_change: Callable[[], None] | None = None,
                 sysfs_reader: Callable[[str], dict] = read_sysfs_adapter) -> None:
        self.on_change = on_change
        self._sysfs_reader = sysfs_reader
        self._lock = threading.Lock()
        self._cache: dict = {}
        self._status = STATUS_STARTING
        self._error: str | None = None
        self._generation = 0
        self._snapshot: Snapshot | None = None
        self._conn: dbus.Connection | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._need_resync = True
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="bluez", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        conn = self._conn
        if conn is not None:
            conn.close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def wait_ready(self, timeout: float = 3.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._lock:
                if self._status != STATUS_STARTING:
                    return
            time.sleep(0.02)

    # -- state ---------------------------------------------------------------

    def snapshot(self) -> Snapshot:
        with self._lock:
            if self._snapshot is not None and self._snapshot.generation == self._generation:
                return self._snapshot
            objects = {p: {i: dict(v) for i, v in ifs.items()} for p, ifs in self._cache.items()}
            status, error, generation = self._status, self._error, self._generation
        snap = build_snapshot(objects, status, error, generation, self._sysfs_reader)
        with self._lock:
            if generation == self._generation:
                self._snapshot = snap
        return snap

    def set_discovery(self, adapter_path: str, enable: bool) -> str | None:
        """Starts or stops discovery; returns an error text or None."""
        conn = self._conn
        if conn is None or not conn.connected:
            return "D-Bus not connected"
        try:
            conn.call(BLUEZ, adapter_path, "org.bluez.Adapter1",
                      "StartDiscovery" if enable else "StopDiscovery", timeout=5.0)
        except dbus.DBusError as exc:
            return f"{exc.name.rsplit('.', 1)[-1]}: {exc.message}".strip(": ")
        return None

    # -- internals -------------------------------------------------------------

    def _bump(self, status: str | None = None, error: str | None = None) -> None:
        with self._lock:
            if status is not None:
                self._status = status
                self._error = error
            self._generation += 1
        callback = self.on_change
        if callback is not None:
            callback()

    def _run(self) -> None:
        last_sync = 0.0
        while not self._stop.is_set():
            conn = self._conn
            if conn is None or not conn.connected:
                if not self._connect():
                    self._wake.wait(self.RECONNECT_EVERY)
                    self._wake.clear()
                    continue
                last_sync = 0.0
            now = time.monotonic()
            if self._need_resync or now - last_sync >= self.RESYNC_EVERY:
                self._resync()
                last_sync = time.monotonic()
            self._wake.wait(1.0)
            self._wake.clear()

    def _connect(self) -> bool:
        conn = dbus.Connection()
        try:
            conn.connect()
            conn.add_signal_handler(self._on_signal)
            conn.add_match(f"type='signal',sender='{BLUEZ}',interface='{OM}'")
            conn.add_match(f"type='signal',sender='{BLUEZ}',interface='{PROPS}',"
                           "member='PropertiesChanged',path_namespace='/org/bluez'")
            conn.add_match("type='signal',sender='org.freedesktop.DBus',"
                           "interface='org.freedesktop.DBus',member='NameOwnerChanged',"
                           f"arg0='{BLUEZ}'")
        except (OSError, dbus.DBusError) as exc:
            conn.close()
            with self._lock:
                self._cache.clear()
            self._bump(STATUS_NO_BUS, str(exc))
            return False
        conn.on_disconnect = self._on_disconnect
        self._conn = conn
        self._need_resync = True
        return True

    def _on_disconnect(self) -> None:
        with self._lock:
            self._cache.clear()
        self._bump(STATUS_NO_BUS, "the system bus closed the connection")
        self._wake.set()

    def _resync(self) -> None:
        conn = self._conn
        if conn is None:
            return
        try:
            objects = conn.call(BLUEZ, "/", OM, "GetManagedObjects", timeout=5.0)[0]
        except dbus.DBusError as exc:
            with self._lock:
                self._cache.clear()
            if exc.name in ("org.freedesktop.DBus.Error.ServiceUnknown",
                            "org.freedesktop.DBus.Error.NameHasNoOwner"):
                self._bump(STATUS_NO_BLUEZ, exc.message)
            elif exc.name == "org.bc250.Disconnected":
                self._bump(STATUS_NO_BUS, exc.message)
            else:
                self._bump(STATUS_ERROR, f"{exc.name}: {exc.message}")
            self._need_resync = True
            return
        with self._lock:
            self._cache = {p: {i: dict(v) for i, v in ifs.items()}
                           for p, ifs in (objects or {}).items()}
        self._need_resync = False
        self._bump(STATUS_OK, None)

    def _on_signal(self, msg: dbus.Message) -> None:
        if msg.member == "NameOwnerChanged" and msg.body and msg.body[0] == BLUEZ:
            new_owner = msg.body[2] if len(msg.body) > 2 else ""
            if not new_owner:
                with self._lock:
                    self._cache.clear()
                self._bump(STATUS_NO_BLUEZ, "bluetoothd left the bus")
            else:
                self._need_resync = True
                self._wake.set()
            return
        with self._lock:
            result = apply_signal(self._cache, msg)
        if result is None:
            self._need_resync = True
            self._wake.set()
        elif result:
            self._bump()

"""A simulated Bluetooth stack for --demo: two pads, a reset, a re-registration.

The simulation produces BlueZ object-manager dictionaries — the same shape the
real D-Bus source receives — so the demo exercises the real parsing, identity,
diffing and sampling code. Only the transport is fake.

USB: a fake sysfs tree (fakesys) with the 8BitDo 2.4 GHz receiver, read by
the real USB scanner.

Timeline (seconds, scaled by --demo-speed):
   0  hci0 (USB 2c4e:0115 on port 1-4) with two connected pads;
      the 2.4 GHz receiver on USB 1-3 idle (2dc8:301c)
   5  the third pad switches on: the receiver becomes 2dc8:310a
  26  that pad switches off (receiver idle again); 30 back on
  12  pad #2 drops; 16 it reconnects
  40  HCI command 0x2042 tx timeout, failed -110, USB reset; hci0 disappears
  44  the adapter re-registers as hci1; 46/47 the pads reconnect
  55  the receiver is pulled out of USB; 59 plugged back in (new hidraw/event numbers)
"""

from __future__ import annotations

import math
import random
import threading
import time

import os
import shutil
import tempfile

from . import bluez, fakesys, inputs, kernlog, telemetry, usb
from .model import (IFACE_ADAPTER, IFACE_BATTERY, IFACE_BEARER_BREDR, IFACE_BEARER_LE,
                    IFACE_DEVICE, IFACE_GATT_SERVICE)

ADAPTER_ADDR = "00:E0:4C:DE:00:01"
PAD1 = "E4:17:D8:00:00:01"
PAD2 = "E4:17:D8:00:00:02"
XBOX = "98:7A:14:00:00:03"
HEADSET = "14:3F:A6:00:00:04"
BEACON = "5C:F3:70:00:00:05"

_BASE = "0000{:04x}-0000-1000-8000-00805f9b34fb"


def _uuid(short: int) -> str:
    return _BASE.format(short)


def _pad(name: str, address: str) -> dict:
    return {
        "Address": address, "AddressType": "random", "Name": name, "Alias": name,
        "Appearance": 0x03C4, "Icon": "input-gaming", "Paired": True, "Bonded": True,
        "Trusted": True, "Blocked": False, "Connected": True, "ServicesResolved": True,
        "LegacyPairing": False, "Modalias": "usb:v2DC8p310Bd0100",
        "UUIDs": [_uuid(0x1800), _uuid(0x1801), _uuid(0x180A), _uuid(0x180F), _uuid(0x1812)],
    }


class DemoWorld:
    def __init__(self, speed: float = 1.0) -> None:
        self.speed = max(0.05, speed)
        self.t0 = time.monotonic()
        self.lock = threading.Lock()
        self.index = 0
        self.adapter_present = True
        self.connected = {PAD1: True, PAD2: True, XBOX: True, HEADSET: False}
        self.kernel_cb = None
        self.change_cb = None
        self.usb_cb = None
        self.sysfs = tempfile.mkdtemp(prefix="bc250-wcm-demo-")
        self.receiver_active = False
        self.receiver_present = True
        self._plug = 0
        fakesys.eightbitdo_receiver(self.sysfs, "1-3", active=False)
        self._done: set[str] = set()
        self._rssi = {PAD1: -64.0, PAD2: -71.0, XBOX: -57.0}

    def elapsed(self) -> float:
        return (time.monotonic() - self.t0) * self.speed

    def objects(self) -> dict:
        with self.lock:
            if not self.adapter_present:
                return {"/org/bluez": {"org.bluez.AgentManager1": {}}}
            root = f"/org/bluez/hci{self.index}"
            objs: dict = {
                "/org/bluez": {"org.bluez.AgentManager1": {}},
                root: {IFACE_ADAPTER: {
                    "Address": ADAPTER_ADDR, "AddressType": "public", "Name": "bc250",
                    "Alias": "bc250", "Powered": True, "PowerState": "on",
                    "Discovering": False}},
            }

            def device(address: str, props: dict, extra: dict | None = None) -> str:
                path = f"{root}/dev_{address.replace(':', '_')}"
                entry = {IFACE_DEVICE: dict(props, Adapter=root,
                                            Connected=self.connected.get(address, False))}
                entry.update(extra or {})
                objs[path] = entry
                return path

            for address, label in ((PAD1, "8BitDo Ultimate 2C Wireless"),
                                   (PAD2, "8BitDo Ultimate 2C Wireless")):
                connected = self.connected[address]
                path = device(address, _pad(label, address),
                              {IFACE_BEARER_LE: {"Paired": True, "Bonded": True,
                                                 "Connected": connected}})
                for n, short in enumerate((0x1800, 0x1801, 0x180A, 0x180F, 0x1812)):
                    objs[f"{path}/service{n:04x}"] = {IFACE_GATT_SERVICE: {
                        "UUID": _uuid(short), "Primary": True, "Device": path}}
            device(XBOX, {
                "Address": XBOX, "AddressType": "public", "Name": "Xbox Wireless Controller",
                "Alias": "Xbox Wireless Controller", "Appearance": 0x03C4,
                "Icon": "input-gaming", "Paired": True, "Bonded": True, "Trusted": True,
                "Modalias": "usb:v045Ep0B13d0509",
                "UUIDs": [_uuid(0x1800), _uuid(0x180F), _uuid(0x1812)]},
                {IFACE_BATTERY: {"Percentage": 71, "Source": "HID"},
                 IFACE_BEARER_LE: {"Paired": True, "Connected": self.connected[XBOX]}})
            device(HEADSET, {
                "Address": HEADSET, "AddressType": "public", "Name": "WH-1000XM4",
                "Alias": "WH-1000XM4", "Class": 0x240404, "Icon": "audio-headset",
                "Paired": True, "Bonded": True, "Trusted": True,
                "UUIDs": [_uuid(0x110B), _uuid(0x111E)]},
                {IFACE_BEARER_BREDR: {"Paired": True, "Connected": False}})
            device(BEACON, {
                "Address": BEACON, "AddressType": "random", "Paired": False,
                "Trusted": False, "RSSI": -86, "TxPower": 4})
            return objs

    def tick(self) -> None:
        """Advances the timeline; returns quickly."""
        now = self.elapsed()
        steps = [
            (12.0, "pad2-down", lambda: self._set(PAD2, False)),
            (16.0, "pad2-up", lambda: self._set(PAD2, True)),
            (40.0, "timeout", lambda: self._kernel(kernlog.K_CMD_TIMEOUT,
                                                   f"Bluetooth: hci{self.index}: command 0x2042 tx timeout")),
            (40.1, "failed", lambda: self._kernel(kernlog.K_CMD_FAILED,
                                                  f"Bluetooth: hci{self.index}: Opcode 0x2042 failed: -110")),
            (40.3, "reset", lambda: self._kernel(kernlog.K_USB_RESET,
                                                 f"Bluetooth: hci{self.index}: Resetting usb device.")),
            (40.6, "gone", self._remove_adapter),
            (44.0, "back", self._readd_adapter),
            (46.0, "pad1-up", lambda: self._set(PAD1, True)),
            (47.0, "pad2-up2", lambda: self._set(PAD2, True)),
            (47.5, "xbox-up", lambda: self._set(XBOX, True)),
            (5.0, "rx-on", lambda: self._receiver(True)),
            (26.0, "rx-off", lambda: self._receiver(False)),
            (30.0, "rx-on2", lambda: self._receiver(True)),
            (55.0, "rx-out", self._receiver_out),
            (59.0, "rx-in", lambda: self._receiver(True)),
        ]
        for when, name, action in steps:
            if now >= when and name not in self._done:
                self._done.add(name)
                action()

    def _receiver(self, active: bool) -> None:
        with self.lock:
            self._plug += 1
            self.receiver_active = active
            self.receiver_present = True
            # every re-enumeration brings new hidraw and event numbers
            fakesys.eightbitdo_receiver(self.sysfs, "1-3", active=active,
                                        hidraw_base=3 + 2 * self._plug, event_no=20 + self._plug,
                                        seq=4 + 2 * self._plug)
        if self.usb_cb:
            self.usb_cb()

    def _receiver_out(self) -> None:
        with self.lock:
            self.receiver_present = False
            self.receiver_active = False
            fakesys.remove_usb_device(self.sysfs, "1-3")
        if self.usb_cb:
            self.usb_cb()

    def activity_reader(self, path: str | None) -> "DemoActivity":
        return DemoActivity(self, path)

    def cleanup(self) -> None:
        shutil.rmtree(self.sysfs, ignore_errors=True)

    def _changed(self) -> None:
        if self.change_cb:
            self.change_cb()

    def _set(self, address: str, value: bool) -> None:
        with self.lock:
            self.connected[address] = value
        self._changed()

    def _kernel(self, _kind: str, message: str) -> None:
        if self.kernel_cb:
            event = kernlog.classify(message, time.time())
            if event:
                self.kernel_cb(event)

    def _remove_adapter(self) -> None:
        with self.lock:
            self.adapter_present = False
            for key in self.connected:
                self.connected[key] = False
        self._changed()

    def _readd_adapter(self) -> None:
        with self.lock:
            self.adapter_present = True
            self.index += 1
        self._changed()

    def rssi(self, address: str) -> int:
        with self.lock:
            value = self._rssi.get(address, -75.0)
            value += random.gauss(0, 1.6)
            # pulled back towards a slowly wandering centre
            centre = {PAD1: -64, PAD2: -71, XBOX: -57}.get(address, -75) \
                + 6 * math.sin(self.elapsed() / 17.0 + len(address))
            value += (centre - value) * 0.25
            self._rssi[address] = value
            return int(round(value))


class DemoSource(bluez.BluezSource):
    """A BluezSource fed by DemoWorld instead of D-Bus."""

    def __init__(self, world: DemoWorld) -> None:
        super().__init__(sysfs_reader=self._sysfs)
        self.world = world
        world.change_cb = self._world_changed
        self._discovering = False

    @staticmethod
    def _sysfs(_name: str) -> dict:
        return {"usb_id": "2c4e:0115", "usb_port": "1-4", "usb_product": "MA530 (demo)",
                "driver": "btusb"}

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_demo, name="demo", daemon=True)
        self._thread.start()
        self._world_changed()

    def stop(self) -> None:
        self._stop.set()

    def _run_demo(self) -> None:
        while not self._stop.is_set():
            self.world.tick()
            self._stop.wait(0.1)

    def _world_changed(self) -> None:
        objects = self.world.objects()
        with self._lock:
            self._cache = objects
        self._bump(bluez.STATUS_OK, None)

    def set_discovery(self, adapter_path: str, enable: bool) -> str | None:
        return None


class DemoBackend(telemetry.RssiBackend):
    name = "mgmt (demo)"

    def __init__(self, world: DemoWorld) -> None:
        super().__init__()
        self.world = world
        self.available = True
        self._count = 0

    def conn_info(self, index: int, address: str, addr_type: int, timeout: float) -> telemetry.ConnInfo:
        time.sleep(random.uniform(0.004, 0.03))
        with self.world.lock:
            present = self.world.adapter_present and index == self.world.index
            connected = self.world.connected.get(address, False)
        if not present:
            return telemetry.ConnInfo(telemetry.INVALID_INDEX, detail="Invalid Index")
        if not connected:
            return telemetry.ConnInfo(telemetry.NOT_CONNECTED, detail="Not Connected")
        if address == XBOX:
            self._count += 1
            if self._count % 23 == 0:
                return telemetry.ConnInfo(telemetry.TIMEOUT, detail="Timeout (demo)")
        return telemetry.ConnInfo(telemetry.OK, self.world.rssi(address), 0, 0)


class DemoKernel:
    def __init__(self, world: DemoWorld, callback) -> None:
        self.world = world
        self.callback = callback
        self.problem = None
        self.running = True

    def start(self) -> None:
        self.world.kernel_cb = self.callback

    def stop(self) -> None:
        self.world.kernel_cb = None


def demo_sysfs_battery(_address: str):
    return None


class DemoUsb(usb.UsbSource):
    """The real USB source, scanning the demo's fake sysfs tree."""

    POLL_EVERY = 0.5

    def __init__(self, world: DemoWorld) -> None:
        super().__init__(scanner=lambda: usb.scan_usb(world.sysfs, udev_data=os.path.join(world.sysfs, "udev")))
        self.world = world
        world.usb_cb = self.rescan

    def start(self) -> None:
        self.rescan()
        import threading
        thread = threading.Thread(target=self._poll, name="demo-usb", daemon=True)
        self._threads.append(thread)
        thread.start()

    def stop(self) -> None:
        super().stop()
        self.world.cleanup()


class DemoActivity:
    """Input events of the simulated 2.4 GHz pad: bursts while "played", calm otherwise."""

    def __init__(self, world: DemoWorld, path: str | None) -> None:
        self.world = world
        self.path = path
        self.status = inputs.ST_OK if path else inputs.ST_NO_NODE
        self.total = 0
        self.last_event = None
        self._last = time.monotonic()

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def take(self) -> int | None:
        if self.status != inputs.ST_OK:
            return None
        now = time.monotonic()
        span, self._last = now - self._last, now
        if not self.world.receiver_active:
            return 0
        phase = math.sin(self.world.elapsed() / 6.0)
        rate = max(0.0, 180 * phase) + random.uniform(0, 15)   # events per second
        count = int(rate * span)
        self.total += count
        return count

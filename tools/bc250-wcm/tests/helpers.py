"""Shared fixtures: BlueZ object-manager data, a scriptable source and RSSI backend."""

from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bc250wcm import bluez, telemetry  # noqa: E402
from bc250wcm.model import (IFACE_ADAPTER, IFACE_BATTERY, IFACE_BEARER_BREDR,  # noqa: E402
                              IFACE_BEARER_LE, IFACE_DEVICE, IFACE_GATT_SERVICE)

# made-up addresses (real vendor prefixes, invented device parts)
PAD1 = "E4:17:D8:A1:B2:C3"
PAD2 = "E4:17:D8:D4:E5:F6"
PAD3 = "E4:17:D8:00:00:03"
PAD4 = "E4:17:D8:00:00:04"
HEADSET = "14:3F:A6:A7:B8:C9"
MA530 = "00:E0:4C:01:02:03"
UUID_HID = "00001812-0000-1000-8000-00805f9b34fb"
UUID_BATT = "0000180f-0000-1000-8000-00805f9b34fb"


def adapter_obj(index: int, address: str = MA530, powered: bool = True,
                discovering: bool = False, power_state: str = "on") -> tuple[str, dict]:
    return f"/org/bluez/hci{index}", {
        "org.freedesktop.DBus.Introspectable": {},
        IFACE_ADAPTER: {"Address": address, "AddressType": "public", "Name": "bc250",
                        "Alias": "bc250", "Powered": powered, "PowerState": power_state,
                        "Discovering": discovering},
        "org.freedesktop.DBus.Properties": {},
    }


def pad_obj(index: int, address: str, connected: bool = True, battery: int | None = None,
            name: str = "8BitDo Ultimate 2C Wireless", bearer: bool = True,
            rssi: int | None = None) -> tuple[str, dict]:
    path = f"/org/bluez/hci{index}/dev_{address.replace(':', '_')}"
    props = {"Address": address, "AddressType": "random", "Name": name, "Alias": name,
             "Appearance": 0x03C4, "Icon": "input-gaming", "Paired": True, "Bonded": True,
             "Trusted": True, "Blocked": False, "Connected": connected,
             "ServicesResolved": connected, "Adapter": f"/org/bluez/hci{index}",
             "UUIDs": [UUID_HID, UUID_BATT], "Modalias": "usb:v2DC8p310Bd0100"}
    if rssi is not None:
        props["RSSI"] = rssi
    obj = {IFACE_DEVICE: props}
    if bearer:
        obj[IFACE_BEARER_LE] = {"Paired": True, "Bonded": True, "Connected": connected}
    if battery is not None:
        obj[IFACE_BATTERY] = {"Percentage": battery}
    return path, obj


def headset_obj(index: int, connected: bool = False) -> tuple[str, dict]:
    path = f"/org/bluez/hci{index}/dev_{HEADSET.replace(':', '_')}"
    return path, {
        IFACE_DEVICE: {"Address": HEADSET, "AddressType": "public", "Name": "WH-1000XM4",
                       "Alias": "WH-1000XM4", "Class": 2360324, "Icon": "audio-headset",
                       "Paired": True, "Bonded": True, "Trusted": True, "Connected": connected,
                       "Adapter": f"/org/bluez/hci{index}", "Modalias": "usb:v054Cp0D58d0301"},
        IFACE_BEARER_BREDR: {"Paired": True, "Bonded": True, "Connected": connected},
    }


def gatt_obj(device_path: str, n: int, uuid: str) -> tuple[str, dict]:
    path = f"{device_path}/service{n:04x}"
    return path, {IFACE_GATT_SERVICE: {"UUID": uuid, "Primary": True, "Device": device_path}}


def objects(*entries: tuple[str, dict]) -> dict:
    out = {"/org/bluez": {"org.bluez.AgentManager1": {}}}
    for path, obj in entries:
        out[path] = obj
    return out


def fake_sysfs(_name: str) -> dict:
    return {"usb_id": "2c4e:0115", "usb_port": "1-4", "usb_product": "MA530", "driver": "btusb"}


class FakeSource(bluez.BluezSource):
    """A BluezSource whose object cache the test sets directly."""

    def __init__(self, objs: dict | None = None, status: str = bluez.STATUS_OK,
                 sysfs=fake_sysfs) -> None:
        super().__init__(sysfs_reader=sysfs)
        self.discovery_calls: list[tuple[str, bool]] = []
        self.set(objs or objects(), status)

    def set(self, objs: dict, status: str = bluez.STATUS_OK, error: str | None = None) -> None:
        with self._lock:
            self._cache = {p: {i: dict(v) for i, v in ifs.items()} for p, ifs in objs.items()}
        self._bump(status, error)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def set_discovery(self, adapter_path: str, enable: bool) -> str | None:
        self.discovery_calls.append((adapter_path, enable))
        return None


class FakeBackend(telemetry.RssiBackend):
    """Answers from a table {(index, address, type): ConnInfo or callable}."""

    name = "fake"

    def __init__(self, table: dict | None = None, default=None, available: bool = True) -> None:
        super().__init__()
        self.table = table or {}
        self.default = default or telemetry.ConnInfo(telemetry.NOT_CONNECTED, detail="Not Connected")
        self.available = available
        self.calls: list[tuple[int, str, int]] = []
        self._lock = threading.Lock()

    def conn_info(self, index, address, addr_type, timeout):
        with self._lock:
            self.calls.append((index, address, addr_type))
        answer = self.table.get((index, address, addr_type), self.default)
        if callable(answer):
            answer = answer()
        return telemetry.ConnInfo(answer.status, answer.rssi, answer.tx_power,
                                  answer.max_tx_power, detail=answer.detail)


class NoKernel:
    problem = None
    running = False

    def __init__(self, callback=None) -> None:
        self.callback = callback

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

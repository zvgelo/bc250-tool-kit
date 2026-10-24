"""Adapters and devices as plain data, built from BlueZ object-manager dictionaries.

Everything here is a pure function of its input — the parsing that matters most
is covered by tests with recorded and hand-made BlueZ responses.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

IFACE_ADAPTER = "org.bluez.Adapter1"
IFACE_DEVICE = "org.bluez.Device1"
IFACE_BATTERY = "org.bluez.Battery1"
IFACE_BEARER_LE = "org.bluez.Bearer.LE1"
IFACE_BEARER_BREDR = "org.bluez.Bearer.BREDR1"
IFACE_GATT_SERVICE = "org.bluez.GattService1"

_HCI_RE = re.compile(r"^/org/bluez/hci(\d+)$")
ADDRESS_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


@dataclass
class AdapterInfo:
    path: str                       # /org/bluez/hci1
    name: str                       # hci1
    index: int | None               # 1
    address: str = ""
    address_type: str | None = None
    alias: str = ""
    powered: bool | None = None
    power_state: str | None = None  # on / off / off-blocked / ... (BlueZ 5.66+)
    discovering: bool | None = None
    usb_id: str | None = None       # "2c4e:0115"
    usb_port: str | None = None     # "1-4" — the physical port, stable across resets
    usb_product: str | None = None
    driver: str | None = None
    identity: str = ""              # see assign_identities()

    @property
    def label(self) -> str:
        return f"{self.name} {self.address}".strip()


@dataclass
class DeviceInfo:
    path: str
    adapter_path: str
    address: str
    address_type: str | None = None   # "public" / "random" (BlueZ)
    name: str | None = None
    alias: str | None = None
    icon: str | None = None
    device_class: int | None = None
    appearance: int | None = None
    connected: bool | None = None
    paired: bool | None = None
    bonded: bool | None = None
    trusted: bool | None = None
    blocked: bool | None = None
    services_resolved: bool | None = None
    legacy_pairing: bool | None = None
    rssi: int | None = None            # Device1.RSSI — advertising, discovery only
    tx_power: int | None = None        # Device1.TxPower — advertised
    uuids: list[str] = field(default_factory=list)
    modalias: str | None = None
    battery: int | None = None         # org.bluez.Battery1.Percentage
    battery_source: str | None = None
    bearer_le: dict | None = None      # org.bluez.Bearer.LE1 properties, if exposed
    bearer_bredr: dict | None = None
    gatt_services: list[tuple[str, bool]] = field(default_factory=list)  # (UUID, primary)

    @property
    def display_name(self) -> str:
        return self.alias or self.name or self.address

    @property
    def bearer(self) -> str:
        """LE, BR/EDR, dual, or '?' when nothing says which."""
        if self.bearer_le is not None and self.bearer_bredr is not None:
            return "dual"
        if self.bearer_le is not None:
            return "LE"
        if self.bearer_bredr is not None:
            return "BR/EDR"
        if self.address_type == "random":
            return "LE"
        if self.device_class is not None:
            return "BR/EDR"
        if self.appearance is not None:
            return "LE"
        return "?"

    @property
    def connected_bearer(self) -> str | None:
        """Which link is up, where BlueZ exposes per-bearer state (5.80+)."""
        if self.bearer_le and self.bearer_le.get("Connected"):
            return "LE"
        if self.bearer_bredr and self.bearer_bredr.get("Connected"):
            return "BR/EDR"
        return None

    @property
    def kind(self) -> str:
        return classify_kind(self.icon, self.appearance, self.device_class)


# --------------------------------------------------------------------------
# Parsing object-manager data
# --------------------------------------------------------------------------

def hci_index(path: str) -> int | None:
    match = _HCI_RE.match(path or "")
    return int(match.group(1)) if match else None


def _get(props: dict, key: str, kind: type):
    value = props.get(key)
    if kind is bool:
        return value if isinstance(value, bool) else None
    if kind is int:
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    if kind is str:
        return value if isinstance(value, str) else None
    return value


def parse_adapter(path: str, props: dict) -> AdapterInfo:
    name = path.rsplit("/", 1)[-1]
    return AdapterInfo(
        path=path,
        name=name,
        index=hci_index(path),
        address=(_get(props, "Address", str) or "").upper(),
        address_type=_get(props, "AddressType", str),
        alias=_get(props, "Alias", str) or _get(props, "Name", str) or "",
        powered=_get(props, "Powered", bool),
        power_state=_get(props, "PowerState", str),
        discovering=_get(props, "Discovering", bool),
    )


def parse_device(path: str, interfaces: dict) -> DeviceInfo | None:
    props = interfaces.get(IFACE_DEVICE)
    if not isinstance(props, dict):
        return None
    address = _get(props, "Address", str)
    if not address or not ADDRESS_RE.match(address):
        return None
    adapter = _get(props, "Adapter", str) or path.rsplit("/", 1)[0]
    uuids = props.get("UUIDs")
    battery_props = interfaces.get(IFACE_BATTERY)
    battery = None
    battery_source = None
    if isinstance(battery_props, dict):
        battery = _get(battery_props, "Percentage", int)
        battery_source = _get(battery_props, "Source", str) or "BlueZ Battery1"
        if battery is not None and not 0 <= battery <= 100:
            battery = None
    le = interfaces.get(IFACE_BEARER_LE)
    bredr = interfaces.get(IFACE_BEARER_BREDR)
    return DeviceInfo(
        path=path,
        adapter_path=adapter,
        address=address.upper(),
        address_type=_get(props, "AddressType", str),
        name=_get(props, "Name", str),
        alias=_get(props, "Alias", str),
        icon=_get(props, "Icon", str),
        device_class=_get(props, "Class", int),
        appearance=_get(props, "Appearance", int),
        connected=_get(props, "Connected", bool),
        paired=_get(props, "Paired", bool),
        bonded=_get(props, "Bonded", bool),
        trusted=_get(props, "Trusted", bool),
        blocked=_get(props, "Blocked", bool),
        services_resolved=_get(props, "ServicesResolved", bool),
        legacy_pairing=_get(props, "LegacyPairing", bool),
        rssi=_get(props, "RSSI", int),
        tx_power=_get(props, "TxPower", int),
        uuids=[u for u in uuids if isinstance(u, str)] if isinstance(uuids, list) else [],
        modalias=_get(props, "Modalias", str),
        battery=battery,
        battery_source=battery_source if battery is not None else None,
        bearer_le=dict(le) if isinstance(le, dict) else None,
        bearer_bredr=dict(bredr) if isinstance(bredr, dict) else None,
    )


def parse_managed_objects(objects: dict) -> tuple[dict[str, AdapterInfo], dict[str, DeviceInfo]]:
    """GetManagedObjects -> ({adapter path: AdapterInfo}, {device path: DeviceInfo})."""
    adapters: dict[str, AdapterInfo] = {}
    devices: dict[str, DeviceInfo] = {}
    services: dict[str, list[tuple[str, bool]]] = {}
    if not isinstance(objects, dict):
        return adapters, devices
    for path, interfaces in objects.items():
        if not isinstance(path, str) or not isinstance(interfaces, dict):
            continue
        if IFACE_ADAPTER in interfaces and isinstance(interfaces[IFACE_ADAPTER], dict):
            adapters[path] = parse_adapter(path, interfaces[IFACE_ADAPTER])
        elif IFACE_DEVICE in interfaces:
            device = parse_device(path, interfaces)
            if device is not None:
                devices[path] = device
        elif IFACE_GATT_SERVICE in interfaces:
            svc = interfaces[IFACE_GATT_SERVICE]
            if isinstance(svc, dict):
                owner = _get(svc, "Device", str) or path.rsplit("/", 1)[0]
                uuid = _get(svc, "UUID", str)
                if uuid:
                    services.setdefault(owner, []).append((uuid, bool(svc.get("Primary"))))
    for owner, entries in services.items():
        if owner in devices:
            devices[owner].gatt_services = sorted(set(entries))
    return adapters, devices


# --------------------------------------------------------------------------
# Adapter identity
# --------------------------------------------------------------------------

def read_sysfs_adapter(name: str, root: str = "/sys/class/bluetooth") -> dict:
    """USB details of hciN from sysfs: vendor:product, port path, product, driver."""
    info: dict = {}
    dev = os.path.join(root, name, "device")
    try:
        real = os.path.realpath(dev)
    except OSError:
        return info
    if not os.path.isdir(real):
        return info
    # .../usb3/3-10/3-10:1.0 — the interface; its parent is the USB device
    interface = os.path.basename(real)
    usb_dev = os.path.dirname(real)
    if ":" in interface:
        info["usb_port"] = interface.split(":", 1)[0]
    vendor = _read(os.path.join(usb_dev, "idVendor"))
    product = _read(os.path.join(usb_dev, "idProduct"))
    if vendor and product:
        info["usb_id"] = f"{vendor}:{product}"
    prod_name = _read(os.path.join(usb_dev, "product"))
    if prod_name:
        info["usb_product"] = prod_name
    driver = os.path.join(real, "driver")
    if os.path.islink(driver):
        info["driver"] = os.path.basename(os.path.realpath(driver))
    return info


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def assign_identities(adapters: list[AdapterInfo]) -> None:
    """A stable name for each adapter, independent of the hciN index.

    The controller address normally suffices. Cheap Realtek dongles are known
    to ship with identical addresses, so when two present adapters share one
    the physical USB port (or, lacking that, the object path) is appended.
    """
    by_address: dict[str, int] = {}
    for adapter in adapters:
        by_address[adapter.address] = by_address.get(adapter.address, 0) + 1
    for adapter in adapters:
        base = adapter.address or f"?{adapter.name}"
        if not adapter.address or by_address.get(adapter.address, 0) > 1:
            suffix = adapter.usb_port or adapter.name
            adapter.identity = f"{base}@{suffix}"
        else:
            adapter.identity = base


def resolve_adapter(identity: str, adapters: list[AdapterInfo],
                    hint_usb: tuple[str | None, str | None] = (None, None)) -> AdapterInfo | None:
    """Finds the adapter a monitored device belonged to, after any re-registration.

    Order: the same identity; then the same controller address when it is
    unique; then the same USB device on the same port (an adapter that came
    back from a reset with a different address).
    """
    for adapter in adapters:
        if adapter.identity == identity:
            return adapter
    address = identity.split("@", 1)[0]
    same_addr = [a for a in adapters if a.address and a.address == address]
    if len(same_addr) == 1:
        return same_addr[0]
    usb_id, usb_port = hint_usb
    if usb_id and usb_port:
        same_usb = [a for a in adapters if a.usb_id == usb_id and a.usb_port == usb_port]
        if len(same_usb) == 1:
            return same_usb[0]
    return None


# --------------------------------------------------------------------------
# Device type
# --------------------------------------------------------------------------

_ICON_KINDS = {
    "input-gaming": "gamepad",
    "input-keyboard": "keyboard",
    "input-mouse": "mouse",
    "input-tablet": "tablet",
    "audio-headset": "headset",
    "audio-headphones": "headphones",
    "audio-card": "audio",
    "audio-speakers": "speaker",
    "phone": "phone",
    "computer": "computer",
    "camera-video": "camera",
    "camera-photo": "camera",
    "printer": "printer",
    "network-wireless": "network",
    "multimedia-player": "media",
    "video-display": "display",
    "scanner": "scanner",
}

# GAP appearance categories (the upper ten bits)
_APPEARANCE_KINDS = {
    0x01: "phone", 0x02: "computer", 0x03: "watch", 0x04: "clock", 0x05: "display",
    0x06: "remote", 0x07: "glasses", 0x08: "tag", 0x09: "keyring", 0x0A: "media",
    0x0B: "scanner", 0x0C: "thermometer", 0x0D: "heart-rate", 0x0E: "blood-pressure",
    0x0F: "hid", 0x10: "glucose", 0x11: "sensor", 0x14: "cycling", 0x31: "pulse-oximeter",
    0x48: "speaker", 0x49: "audio", 0x4A: "audio", 0x4B: "audio", 0x4C: "audio",
}
_HID_SUB = {1: "keyboard", 2: "mouse", 3: "joystick", 4: "gamepad", 5: "tablet"}

_CLASS_MAJOR = {1: "computer", 2: "phone", 3: "network", 4: "audio", 5: "peripheral",
                6: "imaging", 7: "wearable", 8: "toy", 9: "health"}


def classify_kind(icon: str | None, appearance: int | None, device_class: int | None) -> str:
    if icon and icon in _ICON_KINDS:
        return _ICON_KINDS[icon]
    if appearance is not None:
        category = appearance >> 6
        if category == 0x0F:
            return _HID_SUB.get(appearance & 0x3F, "hid")
        if category in _APPEARANCE_KINDS:
            return _APPEARANCE_KINDS[category]
    if device_class is not None:
        major = (device_class >> 8) & 0x1F
        minor = (device_class >> 2) & 0x3F
        if major == 5:
            low = minor & 0x0F
            if low in (1, 2):
                return "gamepad" if low == 2 else "joystick"
            if minor & 0x10:
                return "keyboard"
            if minor & 0x20:
                return "mouse"
            return "peripheral"
        if major in _CLASS_MAJOR:
            return _CLASS_MAJOR[major]
    if icon:
        return icon
    return "?"


def parse_modalias(modalias: str | None) -> dict | None:
    """'usb:v2DC8p310Bd0100' -> {'source': 'usb', 'vendor': 0x2DC8, ...}."""
    if not modalias:
        return None
    match = re.match(r"^(usb|bluetooth|platform):v([0-9A-Fa-f]{4})p([0-9A-Fa-f]{4})d([0-9A-Fa-f]{4})",
                     modalias)
    if not match:
        return None
    return {"source": match.group(1), "vendor": int(match.group(2), 16),
            "product": int(match.group(3), 16), "version": int(match.group(4), 16)}


# Well-known 16-bit service UUIDs, enough to name what a gamepad or headset offers
SERVICE_NAMES = {
    0x1800: "Generic Access", 0x1801: "Generic Attribute", 0x180A: "Device Information",
    0x180F: "Battery", 0x1812: "Human Interface Device", 0x1813: "Scan Parameters",
    0x1803: "Link Loss", 0x1804: "Tx Power", 0x1802: "Immediate Alert",
    0x180D: "Heart Rate", 0x1805: "Current Time", 0x184E: "Audio Stream Control",
    0x184F: "Broadcast Audio Scan", 0x1850: "Published Audio Capabilities",
    0x1124: "HID (classic)", 0x1200: "PnP Information", 0x110A: "Audio Source",
    0x110B: "Audio Sink", 0x110C: "A/V Remote Control Target", 0x110E: "A/V Remote Control",
    0x111E: "Handsfree", 0x111F: "Handsfree Audio Gateway", 0x1108: "Headset",
    0x1112: "Headset Audio Gateway", 0x1101: "Serial Port", 0x1105: "OBEX Object Push",
    0x112F: "Phonebook Access", 0x1132: "Message Access", 0xFE2C: "Google Fast Pair",
    0xFE59: "Nordic DFU",
}


def service_name(uuid: str) -> str | None:
    """A readable name for a Bluetooth base UUID, else None."""
    match = re.match(r"^0000([0-9a-fA-F]{4})-0000-1000-8000-00805f9b34fb$", uuid or "")
    if not match:
        return None
    return SERVICE_NAMES.get(int(match.group(1), 16))


def short_uuid(uuid: str) -> str:
    match = re.match(r"^0000([0-9a-fA-F]{4})-0000-1000-8000-00805f9b34fb$", uuid or "")
    return f"0x{match.group(1).upper()}" if match else uuid


# --------------------------------------------------------------------------
# Kernel power_supply battery (HID devices)
# --------------------------------------------------------------------------

def sysfs_battery(address: str, root: str = "/sys/class/power_supply") -> tuple[int, str] | None:
    """Battery the kernel HID driver reports for a device, if any.

    For a HID-over-GATT pad hid-input may create
    /sys/class/power_supply/hid-<address>-battery when the report descriptor
    carries a battery usage. Returns (percent, status) or None.
    """
    name = f"hid-{address.lower()}-battery"
    base = os.path.join(root, name)
    capacity = _read(os.path.join(base, "capacity"))
    if capacity is None:
        return None
    try:
        value = int(capacity)
    except ValueError:
        return None
    if not 0 <= value <= 100:
        return None
    return value, _read(os.path.join(base, "status")) or "?"

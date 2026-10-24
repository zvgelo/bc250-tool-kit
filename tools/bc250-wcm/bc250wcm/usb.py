"""USB controllers and wireless receivers, found through sysfs.

Nothing here opens a device. Every fact comes from /sys (and the udev database
under /run/udev/data, which is world-readable): vendor and product IDs, the
interfaces with their drivers, the hidraw and input nodes below them, and any
power_supply the kernel registered for the device.

Identity. A receiver is identified by its physical place on the bus — the sysfs
name of the USB device, e.g. ``3-5`` or ``1-4.2`` — not by its hidraw or event
number, which change on every re-enumeration. A receiver that switches its
product ID (the 8BitDo 2.4 GHz dongle goes 2dc8:301c <-> 2dc8:310a) stays on
the same port and so stays the same device. When a device carries a real serial
number that is used instead, so it survives a move to another port.

Profiles. Only receivers with a profile are called "2.4 GHz receivers"; any
other USB device with a joystick input node is listed as a plain USB gamepad,
because from the host a wired pad and a receiver look alike.
"""

from __future__ import annotations

import os
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

SYSFS = "/sys"
UDEV_DATA = "/run/udev/data"

BTN_JOYSTICK = 0x120
BTN_GAMEPAD = 0x130

# drivers that only ever bind game controllers
NETLINK_KOBJECT_UEVENT = 15

GAMEPAD_DRIVERS = ("xpad", "hid-nintendo", "hid-sony", "hid-steam", "hid-microsoft",
                   "hid-playstation")


@dataclass(frozen=True)
class ReceiverProfile:
    """What is known about one model of dedicated USB receiver.

    `modes` maps a product ID to the receiver's state as observed on real
    hardware; `verified` says by whom and how, so nobody mistakes a guess for
    a measurement.
    """
    id: str
    vendor: int
    name: str
    modes: dict          # product id -> "active" | "idle"
    technology: str = "2.4 GHz"
    verified: str = ""
    notes: str = ""
    # product IDs the same controller also uses over a USB cable: seen alone,
    # they do not prove a receiver
    shared_with_cable: frozenset = frozenset()
    cable_name: str = ""

    def mode(self, product: int) -> str | None:
        return self.modes.get(product)


PROFILES: tuple[ReceiverProfile, ...] = (
    ReceiverProfile(
        id="8bitdo-ultimate-2c-2g4",
        vendor=0x2DC8,
        name="8BitDo Ultimate 2C · 2.4 GHz receiver",
        modes={0x310A: "active", 0x301C: "idle"},
        verified="observed on the reference BC-250 (Bazzite) and checked on a second machine "
                 "2026-10-18: pad off -> 301c (bcdDevice 2.00, product IDLE, one usbhid interface, "
                 "page 0xFFA0), pad on -> 310a (bcdDevice 1.14); the USB serial is the same in both "
                 "modes. PID meanings agree with ascendedent/8bitdo-Linux-Software and "
                 "jgus-org/8bitdo-2c-re-flake",
        notes="310a: pad linked in XInput mode (interface 0 xpad, 1 usbhid keyboard/mouse, 2 usbhid "
              "vendor page 0xFF7A); 301c: no pad linked (product string IDLE, one vendor HID interface) "
              "or, with a gamepad input device, a pad linked in DInput mode. The same pad over a USB "
              "cable is also 310a. No battery or RSSI over 2.4 GHz on stock firmware.",
        shared_with_cable=frozenset({0x310A}),
        cable_name="8BitDo Ultimate 2C · USB (2.4 GHz receiver or cable)",
    ),
)


def find_profile(vendor: int | None, product: int | None,
                 profiles: tuple[ReceiverProfile, ...] = PROFILES) -> ReceiverProfile | None:
    if vendor is None or product is None:
        return None
    for profile in profiles:
        if profile.vendor == vendor and product in profile.modes:
            return profile
    return None


@dataclass
class HidNode:
    name: str                 # hidraw3
    hid_id: str | None = None         # 0003:00002DC8:0000310A
    hid_name: str | None = None
    hid_phys: str | None = None
    hid_uniq: str | None = None
    descriptor_path: str | None = None
    sysfs: str = ""

    @property
    def dev_path(self) -> str:
        return f"/dev/{self.name}"


@dataclass
class InputNode:
    name: str                 # event7
    input_name: str | None = None
    uniq: str | None = None
    joystick: bool = False
    sysfs: str = ""

    @property
    def dev_path(self) -> str:
        return f"/dev/input/{self.name}"


@dataclass
class UsbInterface:
    number: int
    cls: int | None = None
    subclass: int | None = None
    protocol: int | None = None
    driver: str | None = None
    hidraw: list[HidNode] = field(default_factory=list)
    inputs: list[InputNode] = field(default_factory=list)

    @property
    def role(self) -> str:
        if self.driver in GAMEPAD_DRIVERS or any(i.joystick for i in self.inputs):
            return "gamepad"
        if self.cls == 0x03:
            return "hid"
        if self.cls == 0xFF:
            return "vendor"
        return "other"


@dataclass
class UsbDevice:
    port: str                 # 3-5 — the sysfs name, i.e. the physical place on the bus
    sysfs: str
    vendor: int | None = None
    product: int | None = None
    manufacturer: str | None = None
    product_name: str | None = None
    serial: str | None = None
    busnum: int | None = None
    devnum: int | None = None
    speed: str | None = None
    bcd_device: str | None = None
    interfaces: list[UsbInterface] = field(default_factory=list)
    battery: tuple[int, str] | None = None     # (percent, status) from a power_supply
    profile: ReceiverProfile | None = None
    # this physical device has been seen in an idle-receiver mode, so a later
    # "shared with cable" product ID on it is the receiver, not a cable
    receiver_confirmed: bool = False

    @property
    def usb_id(self) -> str:
        if self.vendor is None or self.product is None:
            return "????:????"
        return f"{self.vendor:04x}:{self.product:04x}"

    @property
    def physical_id(self) -> str:
        """Stable across re-enumeration and product-ID changes."""
        if self.serial and _real_serial(self.serial):
            return f"usb:{self.vendor or 0:04x}-sn-{self.serial}"
        return f"usb:{self.port}"

    @property
    def profile_mode(self) -> str | None:
        """The mode the product ID alone stands for."""
        return self.profile.mode(self.product) if self.profile and self.product is not None else None

    @property
    def mode(self) -> str | None:
        mode = self.profile_mode
        # an "idle" product ID with a gamepad input device is a pad linked in
        # another mode: the 8BitDo receiver keeps 301c in DInput mode
        if mode == "idle" and self.joystick_nodes():
            return "active"
        return mode

    @property
    def is_receiver(self) -> bool:
        """Certainly a receiver (not the pad on a cable)."""
        if self.profile is None or self.product is None:
            return False
        return self.receiver_confirmed or self.profile_mode == "idle" or \
            self.product not in self.profile.shared_with_cable

    @property
    def transport(self) -> str:
        """usb-2.4 / usb-2.4? (receiver or cable) / usb"""
        if self.profile is None:
            return "usb"
        return "usb-2.4" if self.is_receiver else "usb-2.4?"

    @property
    def is_controller(self) -> bool:
        return self.profile is not None or any(i.role == "gamepad" for i in self.interfaces)

    @property
    def display_name(self) -> str:
        if self.profile:
            return self.profile.name if self.is_receiver else (self.profile.cable_name or self.profile.name)
        parts = [p for p in (self.manufacturer, self.product_name) if p]
        if len(parts) == 2 and parts[1].startswith(parts[0]):
            parts = [parts[1]]
        return " ".join(parts) or f"USB {self.usb_id}"

    @property
    def drivers(self) -> list[str]:
        return sorted({i.driver for i in self.interfaces if i.driver})

    def hidraw_nodes(self) -> list[tuple[UsbInterface, HidNode]]:
        return [(i, h) for i in self.interfaces for h in i.hidraw]

    def joystick_nodes(self) -> list[InputNode]:
        return [n for i in self.interfaces for n in i.inputs if n.joystick]


def _real_serial(serial: str) -> bool:
    # placeholder serials are common; they would merge unrelated devices
    s = serial.strip()
    return bool(s) and len(set(s)) > 1 and s.lower() not in ("serial", "0123456789", "123456789")


# --------------------------------------------------------------------------
# sysfs scanning
# --------------------------------------------------------------------------

def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _hex(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def parse_uevent(text: str | None) -> dict[str, str]:
    out = {}
    for line in (text or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def has_key_bit(bitmap: str | None, bit: int) -> bool:
    """Tests a bit in a sysfs capability bitmap ("1000 0 0 ..."), words of 64 bits, MSB word first."""
    if not bitmap:
        return False
    words = bitmap.split()
    try:
        values = [int(w, 16) for w in reversed(words)]
    except ValueError:
        return False
    # the kernel prints longs; on 64-bit systems that is 64 bits per word
    word_bits = 64
    index, offset = divmod(bit, word_bits)
    return index < len(values) and bool(values[index] >> offset & 1)


def _udev_props(dev_numbers: str | None, udev_data: str) -> dict[str, str]:
    if not dev_numbers or ":" not in dev_numbers:
        return {}
    text = _read(os.path.join(udev_data, f"c{dev_numbers}"))
    props = {}
    for line in (text or "").splitlines():
        if line.startswith("E:") and "=" in line:
            key, value = line[2:].split("=", 1)
            props[key] = value
    return props


def _scan_inputs(base: str, udev_data: str) -> list[InputNode]:
    out = []
    for dirpath, dirnames, _files in os.walk(base):
        # keep the walk shallow and away from loops
        if dirpath.count(os.sep) - base.count(os.sep) > 4:
            dirnames[:] = []
            continue
        name = os.path.basename(dirpath)
        if re.fullmatch(r"event\d+", name) and os.path.basename(os.path.dirname(dirpath)).startswith("input"):
            input_dir = os.path.dirname(dirpath)
            props = _udev_props(_read(os.path.join(dirpath, "dev")), udev_data)
            joystick = props.get("ID_INPUT_JOYSTICK") == "1"
            if not props:
                caps = _read(os.path.join(input_dir, "capabilities", "key"))
                joystick = has_key_bit(caps, BTN_GAMEPAD) or has_key_bit(caps, BTN_JOYSTICK)
            out.append(InputNode(name=name, input_name=_read(os.path.join(input_dir, "name")),
                                 uniq=_read(os.path.join(input_dir, "uniq")) or None,
                                 joystick=joystick, sysfs=dirpath))
            dirnames[:] = []
    return sorted(out, key=lambda n: int(n.name[5:]))


def _scan_hidraw(base: str) -> list[HidNode]:
    out = []
    try:
        entries = os.listdir(base)
    except OSError:
        return out
    for entry in entries:
        hid_dir = os.path.join(base, entry)
        if not re.fullmatch(r"[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}\.[0-9A-Fa-f]+", entry):
            continue
        uevent = parse_uevent(_read(os.path.join(hid_dir, "uevent")))
        try:
            raws = os.listdir(os.path.join(hid_dir, "hidraw"))
        except OSError:
            raws = []
        for raw in raws:
            if re.fullmatch(r"hidraw\d+", raw):
                descriptor = os.path.join(hid_dir, "report_descriptor")
                out.append(HidNode(name=raw, hid_id=uevent.get("HID_ID"),
                                   hid_name=uevent.get("HID_NAME"), hid_phys=uevent.get("HID_PHYS"),
                                   hid_uniq=uevent.get("HID_UNIQ") or None,
                                   descriptor_path=descriptor if os.path.exists(descriptor) else None,
                                   sysfs=os.path.join(hid_dir, "hidraw", raw)))
    return sorted(out, key=lambda n: int(n.name[6:]))


def _battery_for(device_path: str, sysfs: str) -> tuple[int, str] | None:
    base = os.path.join(sysfs, "class", "power_supply")
    try:
        entries = os.listdir(base)
    except OSError:
        return None
    for entry in entries:
        real = os.path.realpath(os.path.join(base, entry))
        if not real.startswith(device_path + os.sep):
            continue
        if _read(os.path.join(real, "scope")) == "System":
            continue
        capacity = _int(_read(os.path.join(real, "capacity")))
        if capacity is not None and 0 <= capacity <= 100:
            return capacity, _read(os.path.join(real, "status")) or "?"
    return None


def scan_usb(sysfs: str = SYSFS, udev_data: str = UDEV_DATA,
             profiles: tuple[ReceiverProfile, ...] = PROFILES,
             controllers_only: bool = True) -> list[UsbDevice]:
    """Every USB device that is a game controller or a known receiver."""
    bus = os.path.join(sysfs, "bus", "usb", "devices")
    try:
        names = os.listdir(bus)
    except OSError:
        return []
    devices = []
    for name in sorted(names):
        if ":" in name or name.startswith("usb"):
            continue
        path = os.path.realpath(os.path.join(bus, name))
        vendor = _hex(_read(os.path.join(path, "idVendor")))
        product = _hex(_read(os.path.join(path, "idProduct")))
        if vendor is None:
            continue
        dev = UsbDevice(
            port=name, sysfs=path, vendor=vendor, product=product,
            manufacturer=_read(os.path.join(path, "manufacturer")) or None,
            product_name=_read(os.path.join(path, "product")) or None,
            serial=_read(os.path.join(path, "serial")) or None,
            busnum=_int(_read(os.path.join(path, "busnum"))),
            devnum=_int(_read(os.path.join(path, "devnum"))),
            speed=_read(os.path.join(path, "speed")),
            bcd_device=_read(os.path.join(path, "bcdDevice")),
            profile=find_profile(vendor, product, profiles),
        )
        try:
            children = sorted(os.listdir(path))
        except OSError:
            children = []
        for child in children:
            if not child.startswith(name + ":"):
                continue
            ipath = os.path.join(path, child)
            driver_link = os.path.join(ipath, "driver")
            iface = UsbInterface(
                number=_hex(_read(os.path.join(ipath, "bInterfaceNumber"))) or 0,
                cls=_hex(_read(os.path.join(ipath, "bInterfaceClass"))),
                subclass=_hex(_read(os.path.join(ipath, "bInterfaceSubClass"))),
                protocol=_hex(_read(os.path.join(ipath, "bInterfaceProtocol"))),
                driver=os.path.basename(os.path.realpath(driver_link)) if os.path.islink(driver_link) else None,
                hidraw=_scan_hidraw(ipath),
                inputs=_scan_inputs(ipath, udev_data),
            )
            dev.interfaces.append(iface)
        dev.interfaces.sort(key=lambda i: i.number)
        dev.battery = _battery_for(path, sysfs)
        if controllers_only and not dev.is_controller:
            continue
        devices.append(dev)
    return devices


def resolve_usb(physical_id: str, devices: list[UsbDevice], profile_id: str | None = None,
                claimed: set[str] | None = None) -> UsbDevice | None:
    """The monitored receiver now: the same physical id, else the only present
    device of the same profile that nobody else monitors (it moved ports)."""
    for dev in devices:
        if dev.physical_id == physical_id:
            return dev
    if profile_id:
        same = [d for d in devices if d.profile and d.profile.id == profile_id
                and d.physical_id not in (claimed or set())]
        if len(same) == 1:
            return same[0]
    return None


def bt_input_nodes(address: str, sysfs: str = SYSFS, udev_data: str = UDEV_DATA) -> list[InputNode]:
    """Input nodes of a Bluetooth HID device: the kernel puts its address in `uniq`."""
    base = os.path.join(sysfs, "class", "input")
    out = []
    try:
        entries = os.listdir(base)
    except OSError:
        return out
    wanted = address.lower()
    for entry in entries:
        if not re.fullmatch(r"event\d+", entry):
            continue
        input_dir = os.path.realpath(os.path.join(base, entry, "device"))
        if (_read(os.path.join(input_dir, "uniq")) or "").lower() != wanted:
            continue
        props = _udev_props(_read(os.path.join(base, entry, "dev")), udev_data)
        joystick = props.get("ID_INPUT_JOYSTICK") == "1" if props else (
            has_key_bit(_read(os.path.join(input_dir, "capabilities", "key")), BTN_GAMEPAD))
        out.append(InputNode(name=entry, input_name=_read(os.path.join(input_dir, "name")),
                             uniq=wanted, joystick=joystick, sysfs=os.path.join(base, entry)))
    return sorted(out, key=lambda n: (not n.joystick, int(n.name[5:])))


def signature(devices: list[UsbDevice]) -> tuple:
    """What a change of the USB picture looks like — for change detection."""
    return tuple(sorted(
        (d.physical_id, d.usb_id, d.battery,
         tuple((i.number, i.driver, tuple(h.name for h in i.hidraw), tuple(n.name for n in i.inputs))
               for i in d.interfaces))
        for d in devices))


# --------------------------------------------------------------------------
# Live source
# --------------------------------------------------------------------------

def load_known(path: str | None) -> set[str]:
    """Physical ids once seen as an idle receiver — remembered across runs."""
    if not path:
        return set()
    try:
        with open(path, encoding="utf-8") as handle:
            return {line.strip() for line in handle if line.startswith("usb:")}
    except OSError:
        return set()


def save_known(path: str | None, known: set[str]) -> None:
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("".join(k + "\n" for k in sorted(known)))
    except OSError:
        pass


@dataclass
class UsbSnapshot:
    devices: list[UsbDevice] = field(default_factory=list)
    generation: int = 0
    error: str | None = None


class UsbSource:
    """Rescans sysfs on kernel uevents (NETLINK_KOBJECT_UEVENT) and every few seconds."""

    POLL_EVERY = 2.0

    def __init__(self, on_change: Callable[[], None] | None = None,
                 scanner: Callable[[], list[UsbDevice]] = scan_usb,
                 known_file: str | None = None) -> None:
        self.on_change = on_change
        self._scanner = scanner
        self.known_file = known_file
        self.known_receivers: set[str] = load_known(known_file)
        self._lock = threading.Lock()
        self._snapshot = UsbSnapshot()
        self._signature: tuple | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self._netlink: socket.socket | None = None
        self.uevents = False

    def start(self) -> None:
        self.rescan()
        self._threads.append(threading.Thread(target=self._poll, name="usb-poll", daemon=True))
        try:
            # Python does not export NETLINK_KOBJECT_UEVENT; it is 15 in linux/netlink.h
            sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, NETLINK_KOBJECT_UEVENT)
            sock.bind((0, 1))
            sock.settimeout(1.0)
            self._netlink = sock
            self.uevents = True
            self._threads.append(threading.Thread(target=self._listen, name="usb-uevent", daemon=True))
        except (OSError, AttributeError):
            self._netlink = None
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        sock, self._netlink = self._netlink, None
        if sock is not None:
            sock.close()

    def snapshot(self) -> UsbSnapshot:
        with self._lock:
            return self._snapshot

    def rescan(self) -> None:
        try:
            devices = self._scanner()
            error = None
        except OSError as exc:      # pragma: no cover — sysfs read errors are per-file already
            devices, error = [], str(exc)
        newly = False
        for dev in devices:
            if dev.profile_mode == "idle" and dev.physical_id not in self.known_receivers:
                self.known_receivers.add(dev.physical_id)
                newly = True
            dev.receiver_confirmed = dev.physical_id in self.known_receivers
        if newly:
            save_known(self.known_file, self.known_receivers)
        sig = signature(devices) + (tuple(sorted(self.known_receivers)),)
        with self._lock:
            if sig == self._signature and error == self._snapshot.error:
                return
            self._signature = sig
            self._snapshot = UsbSnapshot(devices, self._snapshot.generation + 1, error)
        if self.on_change:
            self.on_change()

    def _poll(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.POLL_EVERY)
            self._wake.clear()
            if self._stop.is_set():
                break
            # uevents arrive in bursts; let sysfs settle before reading it
            time.sleep(0.15)
            self.rescan()

    def _listen(self) -> None:
        while not self._stop.is_set():
            sock = self._netlink
            if sock is None:
                break
            try:
                data = sock.recv(8192)
            except socket.timeout:
                continue
            except OSError:
                break
            if b"SUBSYSTEM=usb" in data or b"SUBSYSTEM=hidraw" in data or b"SUBSYSTEM=input" in data \
                    or b"SUBSYSTEM=power_supply" in data:
                self._wake.set()

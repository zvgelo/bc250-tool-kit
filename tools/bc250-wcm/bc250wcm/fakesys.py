"""A miniature sysfs tree with USB controllers, for --demo and the tests.

It mirrors the layout the scanner reads on a real system — device directory,
interface directories, driver links, HID devices with hidraw nodes and report
descriptors, input devices with event nodes — so the demo and the tests run
the real scanning code, not a stand-in.
"""

from __future__ import annotations

import os
import shutil

from .usb import BTN_GAMEPAD

# the vendor interface of the 8BitDo Ultimate 2C receiver (as reported on the reference machine)
DESC_8BITDO_VENDOR = bytes.fromhex("067AFF0901A1018502" "0902A100" "0903" "1500" "25FF" "7508" "953F"
                                   "8102" "8581" "0905" "9102" "C0C0")
# the idle receiver's single interface, vendor page 0xFFA0 — read from a real receiver
DESC_8BITDO_IDLE = bytes.fromhex("06A0FF0901A1018502150026FF0019012902" "7508953F8102"
                                 "8581150026FF0019012902" "7508953F9102C0")


def key_bitmap(*bits: int) -> str:
    """A sysfs capabilities/key string with the given bits set (64-bit words, MSB word first)."""
    words = [0] * (max(bits) // 64 + 1)
    for bit in bits:
        words[bit // 64] |= 1 << (bit % 64)
    return " ".join(f"{w:x}" for w in reversed(words))


def _write(path: str, value: str | bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(value, bytes) else "w"
    with open(path, mode) as handle:
        handle.write(value if isinstance(value, bytes) else value + "\n")


def _driver(root: str, ipath: str, name: str) -> None:
    target = os.path.join(root, "bus", "usb", "drivers", name)
    os.makedirs(target, exist_ok=True)
    link = os.path.join(ipath, "driver")
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(target, link)


def add_usb_device(root: str, port: str, vendor: int, product: int, product_name: str = "",
                   manufacturer: str = "", serial: str = "", bcd: str = "0114") -> str:
    path = os.path.join(root, "bus", "usb", "devices", port)
    if os.path.isdir(path):
        # re-enumeration: the old interfaces and their nodes go away first
        remove_usb_device(root, port)
    os.makedirs(path)
    for name, value in (("idVendor", f"{vendor:04x}"), ("idProduct", f"{product:04x}"),
                        ("bcdDevice", bcd), ("busnum", port.split("-")[0]), ("devnum", "7"),
                        ("speed", "12")):
        _write(os.path.join(path, name), value)
    if product_name:
        _write(os.path.join(path, "product"), product_name)
    if manufacturer:
        _write(os.path.join(path, "manufacturer"), manufacturer)
    if serial:
        _write(os.path.join(path, "serial"), serial)
    return path


def add_interface(root: str, port: str, number: int, cls: int, driver: str | None) -> str:
    ipath = os.path.join(root, "bus", "usb", "devices", port, f"{port}:1.{number}")
    os.makedirs(ipath, exist_ok=True)
    _write(os.path.join(ipath, "bInterfaceNumber"), f"{number:02x}")
    _write(os.path.join(ipath, "bInterfaceClass"), f"{cls:02x}")
    _write(os.path.join(ipath, "bInterfaceSubClass"), "00")
    _write(os.path.join(ipath, "bInterfaceProtocol"), "00")
    if driver:
        _driver(root, ipath, driver)
    return ipath


def add_hidraw(root: str, ipath: str, vendor: int, product: int, seq: int, node: str,
               name: str, phys: str, descriptor: bytes, bus: int = 3) -> str:
    hid_dir = os.path.join(ipath, f"{bus:04X}:{vendor:04X}:{product:04X}.{seq:04X}")
    _write(os.path.join(hid_dir, "uevent"),
           f"DRIVER=hid-generic\nHID_ID={bus:04X}:{vendor:08X}:{product:08X}\nHID_NAME={name}\n"
           f"HID_PHYS={phys}\nHID_UNIQ=\nMODALIAS=hid:b{bus:04X}g0001v{vendor:08X}p{product:08X}")
    _write(os.path.join(hid_dir, "report_descriptor"), descriptor)
    os.makedirs(os.path.join(hid_dir, "hidraw", node), exist_ok=True)
    # /sys/class/hidraw/<node>/device -> the HID device, as on a real system
    cls = os.path.join(root, "class", "hidraw", node)
    os.makedirs(cls, exist_ok=True)
    link = os.path.join(cls, "device")
    if os.path.lexists(link):
        os.remove(link)
    os.symlink(hid_dir, link)
    return hid_dir


def add_input(root: str, parent: str, input_no: int, event_no: int, name: str,
              joystick: bool = True, uniq: str = "") -> str:
    input_dir = os.path.join(parent, "input", f"input{input_no}")
    _write(os.path.join(input_dir, "name"), name)
    _write(os.path.join(input_dir, "uniq"), uniq)
    _write(os.path.join(input_dir, "capabilities", "key"),
           key_bitmap(BTN_GAMEPAD) if joystick else key_bitmap(30))
    event = os.path.join(input_dir, f"event{event_no}")
    _write(os.path.join(event, "dev"), f"13:{64 + event_no}")
    cls = os.path.join(root, "class", "input", f"event{event_no}")
    os.makedirs(cls, exist_ok=True)
    for link, target in ((os.path.join(cls, "device"), input_dir),):
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(target, link)
    _write(os.path.join(cls, "dev"), f"13:{64 + event_no}")
    return event


def remove_usb_device(root: str, port: str) -> None:
    path = os.path.join(root, "bus", "usb", "devices", port)
    shutil.rmtree(path, ignore_errors=True)
    # dangling class links are what a real unplug leaves for a moment too
    for cls in ("hidraw", "input"):
        base = os.path.join(root, "class", cls)
        for entry in os.listdir(base) if os.path.isdir(base) else []:
            link = os.path.join(base, entry, "device")
            if os.path.islink(link) and not os.path.exists(link):
                shutil.rmtree(os.path.join(base, entry), ignore_errors=True)


def eightbitdo_receiver(root: str, port: str = "1-3", active: bool = True,
                        hidraw_base: int = 3, event_no: int = 20, seq: int = 4, serial: str = "",
                        dinput: bool = False) -> str:
    """The 8BitDo Ultimate 2C 2.4 GHz receiver, as seen on the reference machine.

    active: pad linked in XInput mode (310a); dinput: pad linked in DInput mode
    (301c with a hid-generic gamepad); neither: no pad linked (301c IDLE).
    """
    vendor = 0x2DC8
    if dinput:
        name = "8BitDo Ultimate 2C Wireless Controller"
        path = add_usb_device(root, port, vendor, 0x301C, name, "8BitDo", serial=serial)
        ipath = add_interface(root, port, 0, 0x03, "usbhid")
        hid = add_hidraw(root, ipath, vendor, 0x301C, seq, f"hidraw{hidraw_base}", f"8BitDo {name}",
                         f"usb-0000:00:12.0-{port.split('-')[1]}/input0", bytes.fromhex("05010905A101C0"))
        add_input(root, hid, event_no, event_no, f"8BitDo {name}", joystick=True)
        return path
    if not active:
        path = add_usb_device(root, port, vendor, 0x301C, "IDLE", "8BitDo", bcd="0200", serial=serial)
        ipath = add_interface(root, port, 0, 0x03, "usbhid")
        add_hidraw(root, ipath, vendor, 0x301C, seq, f"hidraw{hidraw_base}", "8BitDo IDLE",
                   f"usb-0000:00:12.0-{port.split('-')[1]}/input0", DESC_8BITDO_IDLE)
        return path
    product = 0x310A
    name = "8BitDo 8BitDo Ultimate 2C Wireless Controller"
    path = add_usb_device(root, port, vendor, product, "8BitDo Ultimate 2C Wireless Controller", "8BitDo",
                          serial=serial)
    i0 = add_interface(root, port, 0, 0xFF, "xpad")
    add_input(root, i0, event_no, event_no, "8BitDo Ultimate 2C Wireless Controller", joystick=True)
    i1 = add_interface(root, port, 1, 0x03, "usbhid")
    add_hidraw(root, i1, vendor, product, seq, f"hidraw{hidraw_base}", name,
               f"usb-0000:00:12.0-{port.split('-')[1]}/input1", bytes.fromhex("05010906A101C0"))
    i2 = add_interface(root, port, 2, 0x03, "usbhid")
    add_hidraw(root, i2, vendor, product, seq + 1, f"hidraw{hidraw_base + 1}", name,
               f"usb-0000:00:12.0-{port.split('-')[1]}/input2", DESC_8BITDO_VENDOR)
    return path


def keyboard(root: str, port: str = "1-1") -> str:
    """A plain USB keyboard — must not show up as a controller."""
    path = add_usb_device(root, port, 0x046D, 0xC31C, "USB Keyboard", "Logitech")
    ipath = add_interface(root, port, 0, 0x03, "usbhid")
    add_input(root, ipath, 2, 2, "Logitech USB Keyboard", joystick=False)
    return path

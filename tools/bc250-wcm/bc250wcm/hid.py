"""HID diagnostics: report descriptors, vendor-defined interfaces, passive capture.

This module only ever *reads*. A hidraw node is opened read-only, so there is
no file descriptor through which a report could be sent; there is no write,
no SET_REPORT/SET_FEATURE request and no output report anywhere in this file.
Reading hidraw does not take reports away from the kernel driver or from other
readers — every opener gets its own copy — and it never detaches xpad/usbhid.

What a descriptor can tell: the report IDs, their directions and sizes, the
usage pages. What it cannot tell: what the bytes of a vendor-defined report
mean. The capture and analysis helpers exist to find that out empirically —
by recording reports while the user does known things and looking for the
bytes that change with them.
"""

from __future__ import annotations

import json
import os
import re
import select
import time
from dataclasses import dataclass, field

from .usb import SYSFS, _read, parse_uevent

VENDOR_PAGE_MIN = 0xFF00

USAGE_PAGES = {
    0x01: "Generic Desktop", 0x02: "Simulation", 0x05: "Game Controls", 0x06: "Generic Device",
    0x07: "Keyboard", 0x08: "LED", 0x09: "Button", 0x0C: "Consumer", 0x0D: "Digitizer",
    0x0F: "Physical Interface (force feedback)", 0x14: "Auxiliary Display",
    0x20: "Sensors", 0x84: "Power Device", 0x85: "Battery System",
}

COLLECTIONS = {0: "Physical", 1: "Application", 2: "Logical", 3: "Report",
               4: "Named Array", 5: "Usage Switch", 6: "Usage Modifier"}


def usage_page_name(page: int) -> str:
    if page >= VENDOR_PAGE_MIN:
        return f"Vendor-defined 0x{page:04X}"
    return USAGE_PAGES.get(page, f"0x{page:04X}")


@dataclass
class ReportInfo:
    report_id: int            # 0 when the device does not use report IDs
    kind: str                 # input / output / feature
    bits: int = 0
    usage_pages: set = field(default_factory=set)

    @property
    def payload_bytes(self) -> int:
        return (self.bits + 7) // 8

    @property
    def total_bytes(self) -> int:
        """Size of the report as read from hidraw: the payload plus the ID byte, if any."""
        return self.payload_bytes + (1 if self.report_id else 0)

    @property
    def vendor_defined(self) -> bool:
        return any(p >= VENDOR_PAGE_MIN for p in self.usage_pages)


@dataclass
class Descriptor:
    reports: list[ReportInfo] = field(default_factory=list)
    collections: list[tuple[int, int, int, int]] = field(default_factory=list)  # (depth, type, page, usage)
    errors: list[str] = field(default_factory=list)

    @property
    def top_pages(self) -> list[int]:
        return sorted({page for depth, _t, page, _u in self.collections if depth == 0})

    @property
    def vendor_defined(self) -> bool:
        return any(p >= VENDOR_PAGE_MIN for p in self.top_pages) or any(r.vendor_defined for r in self.reports)

    def report(self, kind: str, report_id: int) -> ReportInfo | None:
        for rep in self.reports:
            if rep.kind == kind and rep.report_id == report_id:
                return rep
        return None


def parse_descriptor(data: bytes) -> Descriptor:
    """Walks the short items of a HID report descriptor (HID 1.11, §6.2.2)."""
    desc = Descriptor()
    globals_ = {"page": 0, "size": 0, "count": 0, "id": 0}
    stack: list[dict] = []
    usages: list[int] = []
    depth = 0
    reports: dict[tuple[str, int], ReportInfo] = {}
    pos = 0
    while pos < len(data):
        prefix = data[pos]
        if prefix == 0xFE:                       # long item: skip it whole
            if pos + 2 >= len(data):
                desc.errors.append(f"truncated long item at {pos}")
                break
            pos += 3 + data[pos + 1]
            continue
        size = (0, 1, 2, 4)[prefix & 0x03]
        kind = (prefix >> 2) & 0x03
        tag = prefix >> 4
        raw = data[pos + 1:pos + 1 + size]
        if len(raw) < size:
            desc.errors.append(f"truncated item 0x{prefix:02X} at {pos}")
            break
        value = int.from_bytes(raw, "little") if size else 0
        pos += 1 + size

        if kind == 1:                            # global
            if tag == 0x0:
                globals_["page"] = value
            elif tag == 0x7:
                globals_["size"] = value
            elif tag == 0x9:
                globals_["count"] = value
            elif tag == 0x8:
                globals_["id"] = value
            elif tag == 0xA:
                stack.append(dict(globals_))
            elif tag == 0xB:
                if stack:
                    globals_ = stack.pop()
                else:
                    desc.errors.append("pop without push")
        elif kind == 2:                          # local
            if tag == 0x0:
                # a 4-byte usage carries its own page in the upper half
                usages.append(value if size == 4 else (globals_["page"] << 16) | value)
        elif kind == 0:                          # main
            if tag in (0x8, 0x9, 0xB):
                name = {0x8: "input", 0x9: "output", 0xB: "feature"}[tag]
                key = (name, globals_["id"])
                rep = reports.get(key)
                if rep is None:
                    rep = reports[key] = ReportInfo(globals_["id"], name)
                rep.bits += globals_["size"] * globals_["count"]
                rep.usage_pages.add(globals_["page"])
                usages = []
            elif tag == 0xA:
                usage = usages[0] if usages else 0
                desc.collections.append((depth, value, (usage >> 16) or globals_["page"], usage & 0xFFFF))
                depth += 1
                usages = []
            elif tag == 0xC:
                depth = max(0, depth - 1)
                usages = []
    desc.reports = sorted(reports.values(), key=lambda r: (r.kind, r.report_id))
    return desc


def descriptor_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


# --------------------------------------------------------------------------
# Enumeration
# --------------------------------------------------------------------------

@dataclass
class HidrawInfo:
    name: str
    hid_id: str | None
    hid_name: str | None
    hid_phys: str | None
    vendor: int | None
    product: int | None
    bus: int | None
    interface: int | None
    usb_port: str | None
    descriptor: Descriptor | None
    descriptor_raw: bytes = b""
    access: str = "?"            # "read" / "denied" / "missing"

    @property
    def dev_path(self) -> str:
        return f"/dev/{self.name}"

    @property
    def selector(self) -> str:
        """A name that survives renumbering: vid:pid:interface (USB) or vid:pid."""
        if self.vendor is None:
            return self.name
        base = f"{self.vendor:04x}:{self.product or 0:04x}"
        return f"{base}:{self.interface}" if self.interface is not None else base


def access_of(path: str) -> str:
    if not os.path.exists(path):
        return "missing"
    return "read" if os.access(path, os.R_OK) else "denied"


def list_hidraw(sysfs: str = SYSFS, dev_root: str = "/dev") -> list[HidrawInfo]:
    base = os.path.join(sysfs, "class", "hidraw")
    try:
        names = sorted(os.listdir(base), key=lambda n: int(n[6:]) if n[6:].isdigit() else 0)
    except OSError:
        return []
    out = []
    for name in names:
        if not re.fullmatch(r"hidraw\d+", name):
            continue
        hid_dir = os.path.realpath(os.path.join(base, name, "device"))
        uevent = parse_uevent(_read(os.path.join(hid_dir, "uevent")))
        bus = vendor = product = None
        match = re.fullmatch(r"([0-9A-Fa-f]+):([0-9A-Fa-f]+):([0-9A-Fa-f]+)", uevent.get("HID_ID", ""))
        if match:
            bus, vendor, product = (int(g, 16) for g in match.groups())
        interface = usb_port = None
        parent = os.path.basename(os.path.dirname(hid_dir))       # 3-5:1.2 for USB
        iface = re.fullmatch(r"(\d+-[\d.]+):\d+\.(\d+)", parent)
        if iface:
            usb_port, interface = iface.group(1), int(iface.group(2))
        raw = b""
        desc = None
        try:
            with open(os.path.join(hid_dir, "report_descriptor"), "rb") as handle:
                raw = handle.read()
            desc = parse_descriptor(raw)
        except OSError:
            pass
        out.append(HidrawInfo(name, uevent.get("HID_ID"), uevent.get("HID_NAME"),
                              uevent.get("HID_PHYS"), vendor, product, bus, interface, usb_port,
                              desc, raw, access_of(os.path.join(dev_root, name))))
    return out


def select_hidraw(selector: str, nodes: list[HidrawInfo]) -> HidrawInfo | None:
    """'hidraw3', '/dev/hidraw3', '2dc8:310a:2' or '2dc8:310a' (the only match)."""
    selector = selector.strip().lower()
    if selector.startswith("/dev/"):
        selector = selector[5:]
    for node in nodes:
        if node.name == selector:
            return node
    matches = [n for n in nodes if n.selector == selector]
    if not matches and selector.count(":") == 1:
        matches = [n for n in nodes if n.selector.startswith(selector + ":") or n.selector == selector]
    return matches[0] if len(matches) == 1 else None


# --------------------------------------------------------------------------
# Passive capture
# --------------------------------------------------------------------------

@dataclass
class Capture:
    reports: list[tuple[float, bytes]] = field(default_factory=list)
    marks: list[tuple[float, str]] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def open_readonly(path: str) -> int:
    """The only way this module opens a device: read-only, non-blocking."""
    return os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)


def capture(fd: int, seconds: float, report_size: int = 4096, on_report=None,
            marks_fd: int | None = None, clock=time.time, stop=None) -> Capture:
    """Reads input reports from an already opened fd for `seconds` (0 = until stop()).

    `marks_fd` (normally stdin) is watched for lines: each line becomes a
    labelled timestamp ("pressed A"), so reports can later be correlated with
    what the user did.
    """
    cap = Capture()
    end = None if seconds <= 0 else clock() + seconds
    pending = b""
    while end is None or clock() < end:
        if stop is not None and stop():
            break
        watch = [fd] + ([marks_fd] if marks_fd is not None else [])
        timeout = 0.2 if end is None else max(0.0, min(0.2, end - clock()))
        try:
            ready, _, _ = select.select(watch, [], [], timeout)
        except (OSError, ValueError):
            break
        if fd in ready:
            try:
                data = os.read(fd, report_size)
            except BlockingIOError:
                data = None
            except OSError as exc:
                cap.meta["error"] = str(exc)      # the device went away
                break
            if data:
                stamp = clock()
                cap.reports.append((stamp, data))
                if on_report:
                    on_report(stamp, data)
        if marks_fd is not None and marks_fd in ready:
            chunk = os.read(marks_fd, 1024)
            if not chunk:
                marks_fd = None
                continue
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                label = line.decode("utf-8", "replace").strip() or "mark"
                cap.marks.append((clock(), label))
    return cap


def save_capture(cap: Capture, path: str) -> None:
    """JSON lines: a header, then {"t", "hex"} reports and {"t", "mark"} marks in time order."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    rows = [{"t": round(t, 6), "hex": data.hex()} for t, data in cap.reports]
    rows += [{"t": round(t, 6), "mark": label} for t, label in cap.marks]
    rows.sort(key=lambda r: r["t"])
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"format": "bc250-wcm-hid-capture", "version": 1, **cap.meta}) + "\n")
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def load_capture(path: str) -> Capture:
    cap = Capture()
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{path}:{number + 1}: not JSON") from exc
            if number == 0 and row.get("format"):
                cap.meta = {k: v for k, v in row.items() if k not in ("format", "version")}
                continue
            if "hex" in row:
                try:
                    cap.reports.append((float(row["t"]), bytes.fromhex(row["hex"])))
                except (ValueError, TypeError, KeyError) as exc:
                    raise ValueError(f"{path}:{number + 1}: damaged report") from exc
            elif "mark" in row:
                cap.marks.append((float(row["t"]), str(row["mark"])))
    return cap


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

@dataclass
class ByteStats:
    offset: int
    values: dict = field(default_factory=dict)     # value -> count

    @property
    def constant(self) -> bool:
        return len(self.values) <= 1

    @property
    def lo(self) -> int:
        return min(self.values)

    @property
    def hi(self) -> int:
        return max(self.values)


def split_by_id(reports: list[tuple[float, bytes]], numbered: bool = True) -> dict[int, list[tuple[float, bytes]]]:
    out: dict[int, list] = {}
    for stamp, data in reports:
        if not data:
            continue
        key = data[0] if numbered else 0
        out.setdefault(key, []).append((stamp, data))
    return out


def byte_stats(reports: list[tuple[float, bytes]], expected_len: int | None = None) -> tuple[list[ByteStats], int]:
    """Per-offset value histograms; returns (stats, number of reports of an unexpected length)."""
    stats: list[ByteStats] = []
    malformed = 0
    for _stamp, data in reports:
        if expected_len is not None and len(data) != expected_len:
            malformed += 1
            continue
        while len(stats) < len(data):
            stats.append(ByteStats(len(stats)))
        for i, byte in enumerate(data):
            stats[i].values[byte] = stats[i].values.get(byte, 0) + 1
    return stats, malformed


def changed_offsets(a: bytes, b: bytes) -> list[int]:
    length = max(len(a), len(b))
    return [i for i in range(length) if (a[i] if i < len(a) else None) != (b[i] if i < len(b) else None)]


def correlate_marks(cap: Capture, window: float = 1.0) -> list[tuple[str, float, list[int]]]:
    """For each mark: offsets whose value right after the mark differs from right before it."""
    out = []
    reports = sorted(cap.reports)
    for stamp, label in sorted(cap.marks):
        before = [d for t, d in reports if stamp - window <= t < stamp]
        after = [d for t, d in reports if stamp <= t <= stamp + window]
        if not before or not after:
            out.append((label, stamp, []))
            continue
        base = before[-1]
        # compare like with like: same report ID, same length (a damaged report is no evidence)
        after = [d for d in after if len(d) == len(base) and d[:1] == base[:1]]
        offsets = sorted({i for d in after for i in changed_offsets(base, d)})
        out.append((label, stamp, offsets))
    return out


def rate(reports: list[tuple[float, bytes]]) -> float | None:
    if len(reports) < 2:
        return None
    span = reports[-1][0] - reports[0][0]
    return (len(reports) - 1) / span if span > 0 else None


def hex_line(data: bytes, previous: bytes | None = None, group: int = 8) -> str:
    """Hex dump of one report; bytes that differ from `previous` are bracketed."""
    parts = []
    for i, byte in enumerate(data):
        text = f"{byte:02x}"
        if previous is not None and (i >= len(previous) or previous[i] != byte):
            text = f"[{text}]"
        else:
            text = f" {text} "
        parts.append(text)
        if group and (i + 1) % group == 0 and i + 1 < len(data):
            parts.append(" ")
    return "".join(parts).rstrip()

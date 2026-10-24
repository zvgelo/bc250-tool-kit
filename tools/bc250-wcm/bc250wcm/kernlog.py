"""Bluetooth messages from the kernel log: HCI timeouts, USB resets, firmware.

The kernel log is read through `journalctl -k -f -o json`. On Fedora and
Bazzite members of `wheel`, `adm` and `systemd-journal` may read it without
root, while `dmesg` is restricted (kernel.dmesg_restrict=1). Without access the
follower reports why and the monitor carries on without kernel events.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

K_CMD_TIMEOUT = "cmd-timeout"
K_CMD_FAILED = "cmd-failed"
K_USB_RESET = "usb-reset"
K_HW_ERROR = "hw-error"
K_FIRMWARE = "firmware"
K_USB_DISCONNECT = "usb-disconnect"
K_OTHER = "other"

# HCI opcodes likely to show up in a timeout on a USB dongle
OPCODE_NAMES = {
    0x0406: "Disconnect",
    0x0C03: "Reset",
    0x0C2D: "Read Transmit Power Level",
    0x0C52: "Write Extended Inquiry Response",
    0x1001: "Read Local Version Information",
    0x1009: "Read BD_ADDR",
    0x1405: "Read RSSI",
    0x200B: "LE Set Scan Parameters",
    0x200C: "LE Set Scan Enable",
    0x200D: "LE Create Connection",
    0x2013: "LE Connection Update",
    0x2016: "LE Read Remote Features",
    0x2020: "LE Remote Connection Parameter Request Reply",
    0x2039: "LE Set Extended Advertising Enable",
    0x2041: "LE Set Extended Scan Parameters",
    0x2042: "LE Set Extended Scan Enable",
    0x2043: "LE Extended Create Connection",
    0xFC20: "Vendor (Realtek) command",
    0xFC61: "Vendor (Realtek) command",
}

# opcodes that only ever go out while scanning
SCAN_OPCODES = {0x200B, 0x200C, 0x2041, 0x2042}

_PATTERNS = [
    (K_CMD_TIMEOUT, re.compile(r"hci(\d+): command (0x[0-9a-fA-F]{4}) tx timeout")),
    (K_CMD_FAILED, re.compile(r"hci(\d+): Opcode (0x[0-9a-fA-F]{4}) failed: (-?\d+)")),
    (K_USB_RESET, re.compile(r"hci(\d+): Resetting usb device")),
    (K_HW_ERROR, re.compile(r"hci(\d+): Hardware error (0x[0-9a-fA-F]+)")),
    (K_FIRMWARE, re.compile(r"hci(\d+): RTL: (?:fw version|loading|cfg_sz|rom_version|examining)")),
    (K_FIRMWARE, re.compile(r"hci(\d+): .*[Ff]irmware")),
]
_HCI_ANY = re.compile(r"hci(\d+):")
_USB_DISCONNECT = re.compile(r"usb (\d+-[\d.]+): USB disconnect")

ERRNO_NAMES = {-110: "ETIMEDOUT", -16: "EBUSY", -19: "ENODEV", -5: "EIO", -22: "EINVAL",
               -95: "EOPNOTSUPP", -12: "ENOMEM", -71: "EPROTO", -32: "EPIPE",
               -112: "EHOSTDOWN", -104: "ECONNRESET", -107: "ENOTCONN"}


@dataclass
class KernelEvent:
    ts: float
    kind: str
    text: str
    hci: int | None = None
    opcode: int | None = None
    errno: int | None = None
    usb_port: str | None = None
    historic: bool = False

    @property
    def opcode_name(self) -> str | None:
        if self.opcode is None:
            return None
        return OPCODE_NAMES.get(self.opcode)


def classify(message: str, ts: float) -> KernelEvent | None:
    """One kernel log line -> KernelEvent, or None when it is not about Bluetooth."""
    text = message.strip()
    usb = _USB_DISCONNECT.search(text)
    if usb:
        return KernelEvent(ts, K_USB_DISCONNECT, text, usb_port=usb.group(1))
    if "Bluetooth" not in text and "btusb" not in text and not _HCI_ANY.search(text):
        return None
    for kind, pattern in _PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        event = KernelEvent(ts, kind, text, hci=int(match.group(1)))
        if kind in (K_CMD_TIMEOUT, K_CMD_FAILED):
            event.opcode = int(match.group(2), 16)
        if kind == K_CMD_FAILED:
            event.errno = int(match.group(3))
        return event
    hci = _HCI_ANY.search(text)
    return KernelEvent(ts, K_OTHER, text, hci=int(hci.group(1)) if hci else None)


def parse_journal_line(line: bytes | str, session_start: float) -> KernelEvent | None:
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    if not isinstance(entry, dict):
        return None
    message = entry.get("MESSAGE")
    if isinstance(message, list):      # journald sends non-UTF-8 as a byte array
        try:
            message = bytes(message).decode("utf-8", "replace")
        except (TypeError, ValueError):
            return None
    if not isinstance(message, str):
        return None
    try:
        ts = int(entry.get("__REALTIME_TIMESTAMP", "0")) / 1e6
    except (TypeError, ValueError):
        ts = time.time()
    event = classify(message, ts or time.time())
    if event is not None:
        event.historic = event.ts < session_start
    return event


class KernelLogFollower:
    """Runs journalctl in the background and hands classified events to a callback."""

    def __init__(self, on_event: Callable[[KernelEvent], None], backlog: int = 400,
                 journalctl: str | None = None) -> None:
        self.on_event = on_event
        self.backlog = backlog
        self.journalctl = journalctl or shutil.which("journalctl")
        self.problem: str | None = None
        self.running = False
        self._proc: subprocess.Popen | None = None
        self._session_start = time.time()
        self._stderr: list[str] = []

    def start(self) -> None:
        if not self.journalctl:
            self.problem = "journalctl not found"
            return
        try:
            self._proc = subprocess.Popen(
                [self.journalctl, "-k", "-f", "-o", "json", "--no-pager", "-n", str(self.backlog)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                close_fds=True)
        except OSError as exc:
            self.problem = str(exc)
            return
        self.running = True
        threading.Thread(target=self._read, name="kernlog", daemon=True).start()
        threading.Thread(target=self._read_err, name="kernlog-err", daemon=True).start()

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        self.running = False
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass

    def _read(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            event = parse_journal_line(line, self._session_start)
            if event is not None:
                try:
                    self.on_event(event)
                except Exception:  # noqa: BLE001
                    pass
        self.running = False
        if self._proc is not None and not self.problem:
            self.problem = self._stderr_text() or "journalctl exited"

    def _read_err(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for raw in proc.stderr:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            self._stderr.append(line)
            del self._stderr[:-10]
            lowered = line.lower()
            if "insufficient permissions" in lowered or "not seeing messages" in lowered \
                    or "no journal files were opened" in lowered:
                self.problem = "no permission to read the kernel log (needs wheel/adm/systemd-journal)"

    def _stderr_text(self) -> str:
        return self._stderr[-1] if self._stderr else ""

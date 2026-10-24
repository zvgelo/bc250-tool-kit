"""Input activity: how many input events a controller produced, from evdev.

This is the honest metric for a link that exposes no signal strength. It
counts button, axis and trigger events read from the controller's
/dev/input/eventN — it says the controller is being *used*, not how good the
radio link is, and a pad lying still produces nothing while being perfectly
connected.

Reading is passive: the node is opened read-only and never grabbed
(EVIOCGRAB), so the game, Steam and the xpad driver see every event as before.
Gamepad event nodes normally carry the `uaccess` tag (systemd's
70-uaccess.rules: ID_INPUT_JOYSTICK), so the logged-in user may read them
without extra rules.
"""

from __future__ import annotations

import errno
import os
import select
import struct
import threading
import time

EV_SYN = 0x00
EV_MSC = 0x04

# struct input_event on 64-bit Linux: struct timeval (2 × long), __u16 type, __u16 code, __s32 value
EVENT_FORMAT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FORMAT)

ST_OK = "ok"
ST_PERMISSION = "permission"
ST_NO_NODE = "no-node"
ST_GONE = "gone"
ST_ERROR = "error"


def count_events(data: bytes) -> tuple[int, int]:
    """(meaningful events, trailing bytes) in a chunk of input_event structs.

    SYN and MSC frames are bookkeeping, not user input, and are not counted.
    """
    whole = len(data) - len(data) % EVENT_SIZE
    count = 0
    for offset in range(0, whole, EVENT_SIZE):
        _sec, _usec, ev_type, _code, _value = struct.unpack_from(EVENT_FORMAT, data, offset)
        if ev_type not in (EV_SYN, EV_MSC):
            count += 1
    return count, len(data) - whole


class ActivityReader:
    """Counts events on one evdev node in a background thread."""

    def __init__(self, path: str | None, opener=None) -> None:
        self.path = path
        self.status = ST_NO_NODE if not path else ST_OK
        self.detail = ""
        self.total = 0
        self.last_event: float | None = None
        self._pending = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fd: int | None = None
        self._opener = opener or (lambda p: os.open(p, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC))

    def start(self) -> None:
        if not self.path:
            return
        try:
            self._fd = self._opener(self.path)
        except PermissionError as exc:
            self.status, self.detail = ST_PERMISSION, str(exc)
            return
        except FileNotFoundError as exc:
            self.status, self.detail = ST_GONE, str(exc)
            return
        except OSError as exc:
            self.status, self.detail = ST_ERROR, str(exc)
            return
        self._thread = threading.Thread(target=self._run, name=f"evdev-{os.path.basename(self.path)}",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def take(self) -> int | None:
        """Events since the previous call; None when the node cannot be read."""
        if self.status != ST_OK:
            return None
        with self._lock:
            count, self._pending = self._pending, 0
        return count

    def _run(self) -> None:
        rest = b""
        while not self._stop.is_set():
            fd = self._fd
            if fd is None:
                return
            try:
                ready, _, _ = select.select([fd], [], [], 0.25)
            except (OSError, ValueError):
                self.status = ST_GONE
                return
            if not ready:
                continue
            try:
                data = os.read(fd, EVENT_SIZE * 64)
            except BlockingIOError:
                continue
            except OSError as exc:
                # ENODEV: the device was unplugged under us
                self.status = ST_GONE if exc.errno in (errno.ENODEV, errno.EIO) else ST_ERROR
                self.detail = str(exc)
                return
            if not data:
                self.status = ST_GONE
                return
            count, leftover = count_events(rest + data)
            rest = (rest + data)[len(rest + data) - leftover:] if leftover else b""
            if count:
                with self._lock:
                    self._pending += count
                    self.total += count
                    self.last_event = time.time()

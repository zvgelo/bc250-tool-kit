"""Connection telemetry: the RSSI of a live link, from the kernel mgmt interface.

Backends, all with the same interface:

* `HelperBackend` — the privileged mgmt helper (mgmt.py) started through sudo;
  the monitor itself stays unprivileged. The preferred one.
* `DirectBackend` — the mgmt socket opened in-process; only when the monitor
  already has CAP_NET_ADMIN (i.e. runs as root, which is not recommended).
* `BtmgmtBackend` — `btmgmt conn-info` as a subprocess per query: argument
  list, no shell, a hard timeout. A fallback when the helper cannot run.
* `NullBackend` — no connection RSSI; the monitor still shows state, battery
  and the advertising RSSI BlueZ reports during discovery.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from . import mgmt

# statuses of a ConnInfo
OK = "ok"
NOT_CONNECTED = "not-connected"
PERMISSION = "permission"
TIMEOUT = "timeout"
INVALID_INDEX = "invalid-index"
NOT_POWERED = "not-powered"
UNSUPPORTED = "unsupported"
INVALID_PARAMS = "invalid-params"
UNAVAILABLE = "unavailable"
ERROR = "error"

_STATUS_FROM_MGMT = {
    mgmt.ST_SUCCESS: OK,
    mgmt.ST_NOT_CONNECTED: NOT_CONNECTED,
    0x0E: NOT_CONNECTED,                # Disconnected
    mgmt.ST_PERMISSION_DENIED: PERMISSION,
    0x08: TIMEOUT,
    mgmt.ST_TIMEOUT_LOCAL: TIMEOUT,
    mgmt.ST_INVALID_INDEX: INVALID_INDEX,
    mgmt.ST_NOT_POWERED: NOT_POWERED,
    0x12: NOT_POWERED,                  # RFKilled
    mgmt.ST_NOT_SUPPORTED: UNSUPPORTED,
    0x01: UNSUPPORTED,                  # Unknown Command
    mgmt.ST_INVALID_PARAMS: INVALID_PARAMS,
    mgmt.ST_UNAVAILABLE: UNAVAILABLE,
}


def status_from_mgmt(code: int) -> str:
    return _STATUS_FROM_MGMT.get(code, ERROR)


@dataclass
class ConnInfo:
    status: str
    rssi: int | None = None
    tx_power: int | None = None
    max_tx_power: int | None = None
    addr_type: int | None = None
    detail: str = ""
    elapsed: float = 0.0


def candidate_types(address_type: str | None, connected_bearer: str | None,
                    bearer: str, known: int | None = None) -> list[int]:
    """mgmt address types to try for a device, most likely first.

    BlueZ says "public" for both a BR/EDR device and an LE device with a public
    address, so a public device may need two attempts; a random address is LE
    by definition. Per-bearer state (BlueZ 5.80+) or a type that already worked
    removes the guessing.
    """
    if known is not None:
        return [known]
    le_type = mgmt.ADDR_LE_RANDOM if address_type == "random" else mgmt.ADDR_LE_PUBLIC
    if connected_bearer == "LE":
        return [le_type]
    if connected_bearer == "BR/EDR":
        return [mgmt.ADDR_BREDR]
    if address_type == "random":
        return [mgmt.ADDR_LE_RANDOM]
    if bearer == "LE":
        return [mgmt.ADDR_LE_PUBLIC, mgmt.ADDR_BREDR]
    return [mgmt.ADDR_BREDR, mgmt.ADDR_LE_PUBLIC]


class RssiBackend:
    name = "none"
    available = False
    problem: str | None = None

    def __init__(self) -> None:
        self.on_event: Callable[[dict], None] | None = None

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def conn_info(self, index: int, address: str, addr_type: int, timeout: float) -> ConnInfo:
        return ConnInfo(UNAVAILABLE, detail=self.problem or "")

    def query(self, index: int, address: str, types: list[int], timeout: float) -> ConnInfo:
        """Tries the candidate address types in order; returns the first real answer."""
        result = ConnInfo(UNAVAILABLE)
        for addr_type in types:
            result = self.conn_info(index, address, addr_type, timeout)
            result.addr_type = addr_type
            if result.status not in (NOT_CONNECTED, INVALID_PARAMS):
                return result
        return result


class NullBackend(RssiBackend):
    def __init__(self, problem: str | None = None) -> None:
        super().__init__()
        self.problem = problem


# --------------------------------------------------------------------------
# In-process mgmt
# --------------------------------------------------------------------------

class DirectBackend(RssiBackend):
    name = "mgmt"

    def __init__(self) -> None:
        super().__init__()
        self._client = mgmt.MgmtClient()

    def start(self) -> None:
        try:
            self._client.open()
        except OSError as exc:
            self.problem = f"mgmt socket: {exc}"
            return
        self._client.on_event = lambda ev: self.on_event and self.on_event(ev)
        self.available = True

    def stop(self) -> None:
        self._client.close()

    def conn_info(self, index: int, address: str, addr_type: int, timeout: float) -> ConnInfo:
        start = time.monotonic()
        try:
            info = self._client.conn_info(index, address, addr_type, timeout)
        except mgmt.MgmtError as exc:
            return ConnInfo(status_from_mgmt(exc.status), detail=mgmt.status_name(exc.status),
                            elapsed=time.monotonic() - start)
        except ValueError as exc:
            return ConnInfo(ERROR, detail=str(exc), elapsed=time.monotonic() - start)
        return ConnInfo(OK, info["rssi"], info["tx_power"], info["max_tx_power"],
                        elapsed=time.monotonic() - start)


# --------------------------------------------------------------------------
# Privileged helper
# --------------------------------------------------------------------------

HELPER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mgmt.py")
INSTALLED_HELPER = "/usr/local/libexec/bc250-wcm/rssi-helper"
# where bc250-btmon's install.sh put it; still honoured until it is replaced
LEGACY_HELPER = "/usr/local/libexec/bc250-btmon/rssi-helper"


def installed_helper_ok(path: str = INSTALLED_HELPER) -> bool:
    """The system copy is usable only if root owns it and nobody else can write it."""
    try:
        st = os.stat(path)
        parent = os.stat(os.path.dirname(path))
    except OSError:
        return False
    return (st.st_uid == 0 and not st.st_mode & 0o022
            and parent.st_uid == 0 and not parent.st_mode & 0o022)


def system_python() -> str:
    for candidate in ("/usr/bin/python3", shutil.which("python3") or ""):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return "python3"


class HelperBackend(RssiBackend):
    """Talks JSON lines to mgmt.py running with privileges."""

    name = "mgmt helper"

    def __init__(self, argv: list[str], ready_timeout: float = 6.0) -> None:
        super().__init__()
        self.argv = argv
        self.ready_timeout = ready_timeout
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, list] = {}
        self._ready = threading.Event()
        self._stderr: deque[str] = deque(maxlen=20)
        self.helper_euid: int | None = None

    def start(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, close_fds=True, bufsize=0)
        except OSError as exc:
            self.problem = f"cannot start the helper: {exc}"
            return
        threading.Thread(target=self._read_stdout, name="helper-out", daemon=True).start()
        threading.Thread(target=self._read_stderr, name="helper-err", daemon=True).start()
        if not self._ready.wait(self.ready_timeout):
            self.problem = self._failure_text() or "the helper did not start in time"
            self.stop()
            return
        if not self.available and not self.problem:
            self.problem = self._failure_text() or "the helper exited"

    def _failure_text(self) -> str:
        lines = [line for line in self._stderr if line.strip()]
        return lines[-1].strip() if lines else ""

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        self.available = False
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()      # EOF makes the helper exit on its own
        except OSError:
            pass
        try:
            proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
        self._fail_all("helper stopped")

    @property
    def stderr_tail(self) -> list[str]:
        return list(self._stderr)

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for raw in proc.stderr:
            self._stderr.append(raw.decode("utf-8", "replace").rstrip())

    def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for raw in proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            event = msg.get("event")
            if event == "ready":
                self.helper_euid = msg.get("euid")
                if msg.get("protocol") != mgmt.PROTOCOL_VERSION:
                    self.problem = "helper protocol mismatch — reinstall the helper"
                else:
                    self.available = True
                self._ready.set()
            elif event == "fatal":
                self.problem = str(msg.get("error"))
                self._ready.set()
            elif event:
                callback = self.on_event
                if callback is not None:
                    try:
                        callback(msg)
                    except Exception:  # noqa: BLE001
                        pass
            else:
                with self._lock:
                    slot = self._pending.get(msg.get("id"))
                if slot is not None:
                    slot[1] = msg
                    slot[0].set()
        # stdout closed: the helper is gone
        if self.available:
            self.problem = self._failure_text() or "the helper exited"
        self.available = False
        self._ready.set()
        self._fail_all("helper exited")

    def _fail_all(self, text: str) -> None:
        with self._lock:
            slots = list(self._pending.values())
        for slot in slots:
            if slot[1] is None:
                slot[1] = {"ok": False, "status": mgmt.ST_UNAVAILABLE, "error": text}
                slot[0].set()

    def request(self, payload: dict, timeout: float) -> dict:
        proc = self._proc
        if proc is None or proc.stdin is None or not self.available:
            return {"ok": False, "status": mgmt.ST_UNAVAILABLE,
                    "error": self.problem or "helper not running"}
        with self._lock:
            self._next_id += 1
            req_id = self._next_id
            slot: list = [threading.Event(), None]
            self._pending[req_id] = slot
        try:
            line = json.dumps({"id": req_id, **payload}).encode() + b"\n"
            with self._write_lock:
                proc.stdin.write(line)
                proc.stdin.flush()
            if not slot[0].wait(timeout):
                return {"ok": False, "status": mgmt.ST_TIMEOUT_LOCAL, "error": "no reply"}
            return slot[1]
        except (OSError, ValueError) as exc:
            return {"ok": False, "status": mgmt.ST_UNAVAILABLE, "error": str(exc)}
        finally:
            with self._lock:
                self._pending.pop(req_id, None)

    def conn_info(self, index: int, address: str, addr_type: int, timeout: float) -> ConnInfo:
        start = time.monotonic()
        # the helper has its own timeout; give it a little longer to report it
        reply = self.request({"op": "conn_info", "index": index, "address": address,
                              "type": addr_type}, timeout + 0.5)
        elapsed = time.monotonic() - start
        return conn_info_from_reply(reply, elapsed)

    def connections(self, index: int, timeout: float = 2.0) -> list[dict] | None:
        reply = self.request({"op": "connections", "index": index}, timeout)
        if reply.get("ok") and isinstance(reply.get("connections"), list):
            return reply["connections"]
        return None


def conn_info_from_reply(reply: dict, elapsed: float = 0.0) -> ConnInfo:
    """A helper JSON reply -> ConnInfo, tolerant of missing or odd fields."""
    if not isinstance(reply, dict):
        return ConnInfo(ERROR, detail="malformed reply", elapsed=elapsed)
    if reply.get("ok"):
        def num(key):
            value = reply.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) \
                and -127 <= value <= 126 else None
        return ConnInfo(OK, num("rssi"), num("tx_power"), num("max_tx_power"), elapsed=elapsed)
    code = reply.get("status")
    if not isinstance(code, int):
        return ConnInfo(ERROR, detail=str(reply.get("error", "malformed reply")), elapsed=elapsed)
    return ConnInfo(status_from_mgmt(code), detail=str(reply.get("error") or mgmt.status_name(code)),
                    elapsed=elapsed)


# --------------------------------------------------------------------------
# btmgmt subprocess
# --------------------------------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CONN_INFO_RE = re.compile(r"RSSI\s+(-?\d+)\s+TX power\s+(-?\d+)\s+maximum TX power\s+(-?\d+)")
_STATUS_RE = re.compile(r"status\s+0x([0-9a-fA-F]+)")


def parse_btmgmt_conn_info(output: str, returncode: int = 0) -> ConnInfo:
    """Output of `btmgmt -i N conn-info -t T ADDR` -> ConnInfo."""
    text = _ANSI_RE.sub("", output or "")
    match = _CONN_INFO_RE.search(text)
    if match:
        rssi, tx, tx_max = (int(g) for g in match.groups())
        return ConnInfo(OK,
                        None if rssi == mgmt.HCI_INVALID_S8 else rssi,
                        None if tx == mgmt.HCI_INVALID_S8 else tx,
                        None if tx_max == mgmt.HCI_INVALID_S8 else tx_max)
    status = _STATUS_RE.search(text)
    if status:
        code = int(status.group(1), 16)
        return ConnInfo(status_from_mgmt(code), detail=mgmt.status_name(code))
    lowered = text.lower()
    if "permission" in lowered or "operation not permitted" in lowered:
        return ConnInfo(PERMISSION, detail=text.strip()[:120])
    if "invalid index" in lowered:
        return ConnInfo(INVALID_INDEX, detail=text.strip()[:120])
    if "password is required" in lowered or "a terminal is required" in lowered:
        return ConnInfo(PERMISSION, detail="sudo needs a password")
    return ConnInfo(ERROR, detail=(text.strip() or f"exit status {returncode}")[:120])


class BtmgmtBackend(RssiBackend):
    name = "btmgmt"

    def __init__(self, use_sudo: bool, btmgmt: str | None = None,
                 runner: Callable = subprocess.run) -> None:
        super().__init__()
        self.btmgmt = btmgmt or shutil.which("btmgmt")
        self.use_sudo = use_sudo
        self._run = runner

    def start(self) -> None:
        if not self.btmgmt:
            self.problem = "btmgmt not found (bluez package)"
            return
        self.available = True

    def argv(self, index: int, address: str, addr_type: int) -> list[str]:
        if not mgmt.valid_address(address):
            raise ValueError(f"invalid address {address!r}")
        if not isinstance(index, int) or not 0 <= index < mgmt.HCI_DEV_NONE:
            raise ValueError(f"invalid index {index!r}")
        if addr_type not in mgmt.ADDR_TYPE_NAMES:
            raise ValueError(f"invalid address type {addr_type!r}")
        prefix = ["sudo", "-n", "--"] if self.use_sudo else []
        return prefix + [self.btmgmt or "btmgmt", "-i", str(index), "conn-info",
                         "-t", str(addr_type), address]

    def conn_info(self, index: int, address: str, addr_type: int, timeout: float) -> ConnInfo:
        start = time.monotonic()
        try:
            argv = self.argv(index, address, addr_type)
        except ValueError as exc:
            return ConnInfo(ERROR, detail=str(exc))
        env = dict(os.environ, LC_ALL="C", TERM="dumb")
        try:
            proc = self._run(argv, capture_output=True, text=True, timeout=timeout,
                             stdin=subprocess.DEVNULL, env=env, check=False)
        except subprocess.TimeoutExpired:
            return ConnInfo(TIMEOUT, detail=f"btmgmt did not answer within {timeout:.1f} s",
                            elapsed=time.monotonic() - start)
        except OSError as exc:
            return ConnInfo(ERROR, detail=str(exc), elapsed=time.monotonic() - start)
        result = parse_btmgmt_conn_info((proc.stdout or "") + "\n" + (proc.stderr or ""),
                                        proc.returncode)
        result.elapsed = time.monotonic() - start
        return result

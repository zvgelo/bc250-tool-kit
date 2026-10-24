#!/usr/bin/python3 -I
"""Linux Bluetooth Management API (mgmt) — the RSSI of active connections.

BlueZ refreshes `Device1.RSSI` only while discovering, so for a gamepad that is
already connected it stays empty or stale. The kernel answers "Get Connection
Information" (opcode 0x0031) with the RSSI of the live link instead — the same
command `btmgmt conn-info` sends. It needs CAP_NET_ADMIN.

This file has two roles:

* a module imported by bc250-wcm (packet encoding and decoding, and an
  in-process client used when the monitor itself already runs as root);
* a stand-alone privileged helper, `python3 -I mgmt.py`, started through sudo.
  It speaks JSON lines on stdin/stdout, accepts only read-only queries with
  strictly validated arguments, and exits when stdin closes — so it never
  outlives the monitor. It imports nothing but the standard library and nothing
  from its own directory, which is why it can be installed as a single file.

Reference: doc/mgmt-api.txt in the BlueZ source tree.
"""

from __future__ import annotations

import json
import os
import re
import select
import socket
import struct
import sys
import threading
import time

HCI_DEV_NONE = 0xFFFF
HCI_CHANNEL_CONTROL = 3

# commands
OP_READ_VERSION = 0x0001
OP_READ_INDEX_LIST = 0x0003
OP_READ_INFO = 0x0004
OP_GET_CONNECTIONS = 0x0015
OP_GET_CONN_INFO = 0x0031

# events
EV_CMD_COMPLETE = 0x0001
EV_CMD_STATUS = 0x0002
EV_CONTROLLER_ERROR = 0x0003
EV_INDEX_ADDED = 0x0004
EV_INDEX_REMOVED = 0x0005
EV_DEVICE_CONNECTED = 0x000B
EV_DEVICE_DISCONNECTED = 0x000C

# address types used by mgmt
ADDR_BREDR = 0
ADDR_LE_PUBLIC = 1
ADDR_LE_RANDOM = 2
ADDR_TYPE_NAMES = {ADDR_BREDR: "BR/EDR", ADDR_LE_PUBLIC: "LE Public", ADDR_LE_RANDOM: "LE Random"}

# HCI reports 127 for "no valid value"; the kernel keeps it in place of an
# unknown TX power (HCI_TX_POWER_INVALID) and an unknown RSSI (HCI_RSSI_INVALID)
HCI_INVALID_S8 = 127

STATUS_NAMES = {
    0x00: "Success",
    0x01: "Unknown Command",
    0x02: "Not Connected",
    0x03: "Failed",
    0x04: "Connect Failed",
    0x05: "Authentication Failed",
    0x06: "Not Paired",
    0x07: "No Resources",
    0x08: "Timeout",
    0x09: "Already Connected",
    0x0A: "Busy",
    0x0B: "Rejected",
    0x0C: "Not Supported",
    0x0D: "Invalid Parameters",
    0x0E: "Disconnected",
    0x0F: "Not Powered",
    0x10: "Cancelled",
    0x11: "Invalid Index",
    0x12: "RFKilled",
    0x13: "Already Paired",
    0x14: "Permission Denied",
}
ST_SUCCESS = 0x00
ST_NOT_CONNECTED = 0x02
ST_NOT_SUPPORTED = 0x0C
ST_INVALID_PARAMS = 0x0D
ST_NOT_POWERED = 0x0F
ST_INVALID_INDEX = 0x11
ST_PERMISSION_DENIED = 0x14

# pseudo-statuses for failures that never reached the kernel
ST_TIMEOUT_LOCAL = 0x100
ST_UNAVAILABLE = 0x101

ADDRESS_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")

PROTOCOL_VERSION = 1


def status_name(status: int) -> str:
    if status == ST_TIMEOUT_LOCAL:
        return "Timeout (no reply)"
    if status == ST_UNAVAILABLE:
        return "Unavailable"
    return STATUS_NAMES.get(status, f"Unknown status 0x{status:02x}")


# --------------------------------------------------------------------------
# Encoding and decoding
# --------------------------------------------------------------------------

def valid_address(address: str) -> bool:
    return isinstance(address, str) and bool(ADDRESS_RE.match(address))


def encode_address(address: str) -> bytes:
    """'E4:17:D8:A1:B2:C3' -> six bytes, least significant first, as on the wire."""
    if not valid_address(address):
        raise ValueError(f"invalid Bluetooth address {address!r}")
    return bytes(int(part, 16) for part in reversed(address.split(":")))


def decode_address(raw: bytes) -> str:
    return ":".join(f"{b:02X}" for b in reversed(raw[:6]))


def encode_command(opcode: int, index: int, params: bytes = b"") -> bytes:
    return struct.pack("<HHH", opcode, index, len(params)) + params


def decode_packet(data: bytes) -> tuple[int, int, bytes]:
    """(event, index, parameters). Raises ValueError for a truncated packet."""
    if len(data) < 6:
        raise ValueError("mgmt packet shorter than its header")
    event, index, length = struct.unpack_from("<HHH", data)
    params = data[6:6 + length]
    if len(params) != length:
        raise ValueError("mgmt packet shorter than its declared length")
    return event, index, params


def conn_info_params(address: str, addr_type: int) -> bytes:
    if addr_type not in ADDR_TYPE_NAMES:
        raise ValueError(f"invalid address type {addr_type!r}")
    return encode_address(address) + bytes([addr_type])


def parse_command_reply(event: int, params: bytes) -> tuple[int, int, bytes] | None:
    """(opcode, status, return parameters) of a Command Complete/Status event."""
    if event == EV_CMD_COMPLETE and len(params) >= 3:
        opcode, status = struct.unpack_from("<HB", params)
        return opcode, status, params[3:]
    if event == EV_CMD_STATUS and len(params) >= 3:
        opcode, status = struct.unpack_from("<HB", params)
        return opcode, status, b""
    return None


def parse_conn_info(rp: bytes) -> dict:
    """Return parameters of Get Connection Information.

    Address (6), Address_Type (1), RSSI (1), TX_Power (1), Max_TX_Power (1);
    the three values are signed. 127 means the controller gave no value.
    """
    if len(rp) < 10:
        raise ValueError("Get Connection Information reply too short")
    rssi, tx, tx_max = struct.unpack_from("<bbb", rp, 7)
    return {
        "address": decode_address(rp[0:6]),
        "type": rp[6],
        "rssi": None if rssi == HCI_INVALID_S8 else rssi,
        "tx_power": None if tx == HCI_INVALID_S8 else tx,
        "max_tx_power": None if tx_max == HCI_INVALID_S8 else tx_max,
    }


def parse_index_list(rp: bytes) -> list[int]:
    if len(rp) < 2:
        raise ValueError("Read Index List reply too short")
    count = struct.unpack_from("<H", rp)[0]
    if len(rp) < 2 + 2 * count:
        raise ValueError("Read Index List reply truncated")
    return list(struct.unpack_from(f"<{count}H", rp, 2))


def parse_connections(rp: bytes) -> list[dict]:
    if len(rp) < 2:
        raise ValueError("Get Connections reply too short")
    count = struct.unpack_from("<H", rp)[0]
    out = []
    for i in range(count):
        chunk = rp[2 + 7 * i:2 + 7 * (i + 1)]
        if len(chunk) < 7:
            raise ValueError("Get Connections reply truncated")
        out.append({"address": decode_address(chunk[:6]), "type": chunk[6]})
    return out


def parse_event(event: int, index: int, params: bytes) -> dict | None:
    """Unsolicited events worth passing on, as a JSON-friendly dict."""
    if event == EV_INDEX_ADDED:
        return {"event": "index_added", "index": index}
    if event == EV_INDEX_REMOVED:
        return {"event": "index_removed", "index": index}
    if event == EV_CONTROLLER_ERROR and params:
        return {"event": "controller_error", "index": index, "code": params[0]}
    if event == EV_DEVICE_CONNECTED and len(params) >= 7:
        return {"event": "device_connected", "index": index,
                "address": decode_address(params[:6]), "type": params[6]}
    if event == EV_DEVICE_DISCONNECTED and len(params) >= 7:
        return {"event": "device_disconnected", "index": index,
                "address": decode_address(params[:6]), "type": params[6],
                "reason": params[7] if len(params) > 7 else None}
    return None


DISCONNECT_REASONS = {
    0: "unspecified",
    1: "connection timeout",
    2: "terminated by local host",
    3: "terminated by remote host",
    4: "authentication failure",
    5: "terminated by local host for suspend",
}


# --------------------------------------------------------------------------
# Socket client
# --------------------------------------------------------------------------

class MgmtError(Exception):
    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(message or status_name(status))
        self.status = status


class MgmtClient:
    """The kernel control channel. Thread-safe; replies are matched to requests.

    Several requests may be outstanding at once, so a controller that is slow to
    answer for one link does not hold up a query about another.
    """

    def __init__(self) -> None:
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._pending: list[dict] = []
        self._reader: threading.Thread | None = None
        self._closed = threading.Event()
        self.on_event = None   # callable(dict), runs on the reader thread

    def open(self) -> None:
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW | socket.SOCK_CLOEXEC,
                             socket.BTPROTO_HCI)
        try:
            sock.bind((HCI_DEV_NONE, HCI_CHANNEL_CONTROL))
        except OSError:
            sock.close()
            raise
        self._sock = sock
        self._reader = threading.Thread(target=self._read_loop, name="mgmt-reader", daemon=True)
        self._reader.start()

    def close(self) -> None:
        self._closed.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        with self._lock:
            pending, self._pending = self._pending, []
        for slot in pending:
            slot["status"] = ST_UNAVAILABLE
            slot["done"].set()

    def request(self, opcode: int, index: int, params: bytes = b"",
                match: bytes | None = None, timeout: float = 3.0) -> bytes:
        """Sends a command and returns its reply parameters, or raises MgmtError.

        `match` is a prefix the reply parameters must start with — Get Connection
        Information echoes the address, so concurrent queries for different
        devices are told apart by it.
        """
        sock = self._sock
        if sock is None:
            raise MgmtError(ST_UNAVAILABLE, "mgmt socket not open")
        slot = {"opcode": opcode, "index": index, "match": match,
                "done": threading.Event(), "status": None, "rp": b""}
        with self._lock:
            self._pending.append(slot)
        try:
            sock.send(encode_command(opcode, index, params))
        except OSError as exc:
            with self._lock:
                if slot in self._pending:
                    self._pending.remove(slot)
            raise MgmtError(ST_UNAVAILABLE, str(exc)) from exc
        if not slot["done"].wait(timeout):
            with self._lock:
                if slot in self._pending:
                    self._pending.remove(slot)
            raise MgmtError(ST_TIMEOUT_LOCAL)
        if slot["status"] != ST_SUCCESS:
            raise MgmtError(slot["status"])
        return slot["rp"]

    # convenience wrappers --------------------------------------------------

    def index_list(self, timeout: float = 3.0) -> list[int]:
        return parse_index_list(self.request(OP_READ_INDEX_LIST, HCI_DEV_NONE, timeout=timeout))

    def connections(self, index: int, timeout: float = 3.0) -> list[dict]:
        return parse_connections(self.request(OP_GET_CONNECTIONS, index, timeout=timeout))

    def conn_info(self, index: int, address: str, addr_type: int, timeout: float = 3.0) -> dict:
        params = conn_info_params(address, addr_type)
        rp = self.request(OP_GET_CONN_INFO, index, params, match=params, timeout=timeout)
        return parse_conn_info(rp)

    # reader ----------------------------------------------------------------

    def _read_loop(self) -> None:
        while not self._closed.is_set():
            sock = self._sock
            if sock is None:
                break
            try:
                data = sock.recv(1024)
            except OSError:
                break
            if not data:
                break
            try:
                event, index, params = decode_packet(data)
            except ValueError:
                continue
            reply = parse_command_reply(event, params)
            if reply is not None:
                self._complete(index, *reply)
                continue
            parsed = parse_event(event, index, params)
            if parsed is not None and self.on_event is not None:
                try:
                    self.on_event(parsed)
                except Exception:  # noqa: BLE001
                    pass

    def _complete(self, index: int, opcode: int, status: int, rp: bytes) -> None:
        with self._lock:
            chosen = None
            for slot in self._pending:
                if slot["opcode"] != opcode or slot["index"] != index:
                    continue
                if slot["match"] is not None and rp and not rp.startswith(slot["match"]):
                    continue
                chosen = slot
                break
            if chosen is None:
                return
            self._pending.remove(chosen)
        chosen["status"] = status
        chosen["rp"] = rp
        chosen["done"].set()


# --------------------------------------------------------------------------
# Privileged helper: JSON lines over stdin/stdout
# --------------------------------------------------------------------------

MAX_LINE = 4096
# at most this many queries in flight; four gamepads need four
MAX_INFLIGHT = 16


def validate_request(req: object) -> tuple[str, dict]:
    """Checks a request strictly; returns (op, arguments) or raises ValueError."""
    if not isinstance(req, dict):
        raise ValueError("request is not an object")
    op = req.get("op")
    if op == "ping" or op == "index_list":
        return op, {}
    index = req.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < HCI_DEV_NONE:
        raise ValueError("invalid controller index")
    if op == "connections":
        return op, {"index": index}
    if op == "conn_info":
        address = req.get("address")
        addr_type = req.get("type")
        if not valid_address(address):
            raise ValueError("invalid address")
        if not isinstance(addr_type, int) or isinstance(addr_type, bool) \
                or addr_type not in ADDR_TYPE_NAMES:
            raise ValueError("invalid address type")
        return op, {"index": index, "address": address, "type": addr_type}
    raise ValueError("unknown operation")


def handle_request(client: MgmtClient, op: str, args: dict, timeout: float = 3.0) -> dict:
    try:
        if op == "ping":
            return {"ok": True}
        if op == "index_list":
            return {"ok": True, "indexes": client.index_list(timeout)}
        if op == "connections":
            return {"ok": True, "connections": client.connections(args["index"], timeout)}
        if op == "conn_info":
            info = client.conn_info(args["index"], args["address"], args["type"], timeout)
            return {"ok": True, "status": 0, **info}
    except MgmtError as exc:
        return {"ok": False, "status": exc.status, "error": status_name(exc.status)}
    except ValueError as exc:
        return {"ok": False, "status": ST_UNAVAILABLE, "error": str(exc)}
    return {"ok": False, "status": ST_UNAVAILABLE, "error": "unknown operation"}


def helper_main(argv: list[str]) -> int:
    if argv:
        sys.stderr.write("bc250-wcm helper: takes no arguments\n")
        return 2
    out_lock = threading.Lock()

    def emit(obj: dict) -> None:
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        with out_lock:
            try:
                sys.stdout.write(line)
                sys.stdout.flush()
            except (BrokenPipeError, ValueError):
                os._exit(0)

    client = MgmtClient()
    try:
        client.open()
    except OSError as exc:
        emit({"event": "fatal", "error": f"cannot open the mgmt socket: {exc}"})
        return 1
    client.on_event = emit
    inflight = threading.BoundedSemaphore(MAX_INFLIGHT)

    def serve(req_id, op: str, args: dict) -> None:
        try:
            emit({"id": req_id, **handle_request(client, op, args)})
        finally:
            inflight.release()
    emit({"event": "ready", "protocol": PROTOCOL_VERSION, "euid": os.geteuid(),
          "pid": os.getpid()})

    stdin = sys.stdin.buffer
    workers: list[threading.Thread] = []
    buf = b""
    try:
        while True:
            ready, _, _ = select.select([stdin], [], [], 30.0)
            if not ready:
                continue
            chunk = os.read(stdin.fileno(), 4096)
            if not chunk:
                break           # the monitor went away — so do we
            buf += chunk
            if len(buf) > MAX_LINE * 4 and b"\n" not in buf:
                buf = b""
                continue
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                req_id = None
                try:
                    if len(line) > MAX_LINE:
                        raise ValueError("request too long")
                    req = json.loads(line)
                    if isinstance(req, dict) and isinstance(req.get("id"), int):
                        req_id = req["id"]
                    op, args = validate_request(req)
                except ValueError as exc:
                    emit({"id": req_id, "ok": False, "status": ST_UNAVAILABLE, "error": str(exc)})
                    continue
                if not inflight.acquire(blocking=False):
                    emit({"id": req_id, "ok": False, "status": 0x0A, "error": "Busy"})
                    continue
                # each query on its own thread: one slow link must not stall the rest
                worker = threading.Thread(target=serve, args=(req_id, op, args), daemon=True)
                workers.append(worker)
                worker.start()
                workers[:] = [w for w in workers if w.is_alive()]
    except KeyboardInterrupt:
        pass
    finally:
        # answer what is already in flight (each query has its own timeout), then go
        deadline = time.monotonic() + 4.0
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))
        client.close()
    return 0


if __name__ == "__main__":
    # the shebang carries -I; started as `python3 -I mgmt.py` it is isolated too
    sys.exit(helper_main(sys.argv[1:]))

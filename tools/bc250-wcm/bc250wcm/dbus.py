"""A minimal D-Bus client written against the wire protocol, standard library only.

Atomic images (Bazzite, Silverblue) cannot rely on `dbus-python` or PyGObject
being importable from the interpreter a tool happens to run under, and pulling
them in with pip needs a compiler. The subset needed to talk to BlueZ is small:
EXTERNAL authentication over the system bus socket, method calls, and signal
subscription. That subset is implemented here.

Values are unmarshalled into plain Python types: arrays become lists, dicts
become dicts, structs become tuples and variants are unwrapped to their value.
To send a variant, wrap it in `Variant(signature, value)`.
"""

from __future__ import annotations

import os
import socket
import struct
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

SYSTEM_BUS_DEFAULT = "/run/dbus/system_bus_socket"

# message types
METHOD_CALL = 1
METHOD_RETURN = 2
ERROR = 3
SIGNAL = 4

# header field codes
F_PATH = 1
F_INTERFACE = 2
F_MEMBER = 3
F_ERROR_NAME = 4
F_REPLY_SERIAL = 5
F_DESTINATION = 6
F_SENDER = 7
F_SIGNATURE = 8
F_UNIX_FDS = 9

FLAG_NO_REPLY_EXPECTED = 0x1

# messages larger than this are refused (the spec maximum is 128 MiB)
MAX_MESSAGE = 64 * 1024 * 1024

_FIXED = {
    "y": ("B", 1), "b": ("I", 4), "n": ("h", 2), "q": ("H", 2),
    "i": ("i", 4), "u": ("I", 4), "x": ("q", 8), "t": ("Q", 8),
    "d": ("d", 8), "h": ("I", 4),
}
_ALIGN = {"y": 1, "b": 4, "n": 2, "q": 2, "i": 4, "u": 4, "x": 8, "t": 8,
          "d": 8, "h": 4, "s": 4, "o": 4, "g": 1, "a": 4, "(": 8, "{": 8, "v": 1}


class DBusError(Exception):
    """An error reply, or a failure of the connection itself."""

    def __init__(self, name: str, message: str = "") -> None:
        super().__init__(f"{name}: {message}" if message else name)
        self.name = name
        self.message = message


@dataclass
class Variant:
    signature: str
    value: Any


@dataclass
class Message:
    type: int
    serial: int = 0
    flags: int = 0
    path: str | None = None
    interface: str | None = None
    member: str | None = None
    error_name: str | None = None
    reply_serial: int | None = None
    destination: str | None = None
    sender: str | None = None
    signature: str = ""
    body: list = field(default_factory=list)


# --------------------------------------------------------------------------
# Signatures
# --------------------------------------------------------------------------

def split_signature(sig: str) -> list[str]:
    """Splits a signature into complete types: 'sa{sv}i' -> ['s', 'a{sv}', 'i']."""
    out = []
    pos = 0
    while pos < len(sig):
        end = _complete_type_end(sig, pos)
        out.append(sig[pos:end])
        pos = end
    return out


def _complete_type_end(sig: str, pos: int) -> int:
    if pos >= len(sig):
        raise ValueError(f"truncated signature {sig!r}")
    char = sig[pos]
    if char == "a":
        return _complete_type_end(sig, pos + 1)
    if char in "({":
        close = ")" if char == "(" else "}"
        depth = 0
        for index in range(pos, len(sig)):
            if sig[index] == char:
                depth += 1
            elif sig[index] == close:
                depth -= 1
                if depth == 0:
                    return index + 1
        raise ValueError(f"unbalanced signature {sig!r}")
    if char in _FIXED or char in "sogv":
        return pos + 1
    raise ValueError(f"unknown type code {char!r} in {sig!r}")


# --------------------------------------------------------------------------
# Marshalling
# --------------------------------------------------------------------------

class _Writer:
    def __init__(self, endian: str = "<") -> None:
        self.buf = bytearray()
        self.endian = endian

    def align(self, n: int) -> None:
        pad = (-len(self.buf)) % n
        self.buf.extend(b"\0" * pad)

    def write(self, sig: str, value: Any) -> None:
        char = sig[0]
        if char in _FIXED:
            fmt, size = _FIXED[char]
            self.align(size)
            if char == "b":
                value = 1 if value else 0
            self.buf.extend(struct.pack(self.endian + fmt, value))
        elif char in "so":
            data = value.encode("utf-8")
            self.align(4)
            self.buf.extend(struct.pack(self.endian + "I", len(data)))
            self.buf.extend(data + b"\0")
        elif char == "g":
            data = value.encode("ascii")
            self.buf.append(len(data))
            self.buf.extend(data + b"\0")
        elif char == "v":
            if not isinstance(value, Variant):
                raise TypeError("a variant needs a Variant(signature, value)")
            self.write("g", value.signature)
            self.write(value.signature, value.value)
        elif char == "a":
            elem = sig[1:]
            self.align(4)
            len_pos = len(self.buf)
            self.buf.extend(b"\0\0\0\0")
            self.align(_ALIGN[elem[0]])
            start = len(self.buf)
            if elem[0] == "{":
                key_sig, val_sig = split_signature(elem[1:-1])
                for key, val in value.items():
                    self.align(8)
                    self.write(key_sig, key)
                    self.write(val_sig, val)
            else:
                for item in value:
                    self.write(elem, item)
            struct.pack_into(self.endian + "I", self.buf, len_pos, len(self.buf) - start)
        elif char == "(":
            self.align(8)
            for sub_sig, item in zip(split_signature(sig[1:-1]), value):
                self.write(sub_sig, item)
        else:
            raise ValueError(f"cannot marshal {sig!r}")


class _Reader:
    def __init__(self, data: bytes, endian: str, pos: int = 0) -> None:
        self.data = data
        self.endian = endian
        self.pos = pos

    def align(self, n: int) -> None:
        self.pos += (-self.pos) % n

    def _take(self, size: int) -> bytes:
        if self.pos + size > len(self.data):
            raise DBusError("org.bc250.Malformed", "message truncated")
        chunk = self.data[self.pos:self.pos + size]
        self.pos += size
        return chunk

    def read(self, sig: str) -> Any:
        char = sig[0]
        if char in _FIXED:
            fmt, size = _FIXED[char]
            self.align(size)
            value = struct.unpack(self.endian + fmt, self._take(size))[0]
            return bool(value) if char == "b" else value
        if char in "so":
            self.align(4)
            length = struct.unpack(self.endian + "I", self._take(4))[0]
            text = self._take(length).decode("utf-8", "replace")
            self._take(1)
            return text
        if char == "g":
            length = self._take(1)[0]
            text = self._take(length).decode("ascii", "replace")
            self._take(1)
            return text
        if char == "v":
            inner = self.read("g")
            return self.read(inner)
        if char == "a":
            elem = sig[1:]
            self.align(4)
            length = struct.unpack(self.endian + "I", self._take(4))[0]
            if length > MAX_MESSAGE:
                raise DBusError("org.bc250.Malformed", "array too long")
            self.align(_ALIGN[elem[0]])
            end = self.pos + length
            if elem[0] == "{":
                key_sig, val_sig = split_signature(elem[1:-1])
                result: dict = {}
                while self.pos < end:
                    self.align(8)
                    key = self.read(key_sig)
                    result[key] = self.read(val_sig)
                return result
            items = []
            while self.pos < end:
                items.append(self.read(elem))
            return items
        if char == "(":
            self.align(8)
            return tuple(self.read(sub) for sub in split_signature(sig[1:-1]))
        raise DBusError("org.bc250.Malformed", f"unknown type {char!r}")


def marshal_body(signature: str, args: list | tuple, endian: str = "<") -> bytes:
    writer = _Writer(endian)
    types = split_signature(signature)
    if len(types) != len(args):
        raise ValueError(f"signature {signature!r} wants {len(types)} arguments, got {len(args)}")
    for sig, value in zip(types, args):
        writer.write(sig, value)
    return bytes(writer.buf)


def marshal_message(msg: Message) -> bytes:
    body = marshal_body(msg.signature, msg.body) if msg.signature else b""
    fields = []
    for code, sig, value in (
        (F_PATH, "o", msg.path),
        (F_INTERFACE, "s", msg.interface),
        (F_MEMBER, "s", msg.member),
        (F_ERROR_NAME, "s", msg.error_name),
        (F_REPLY_SERIAL, "u", msg.reply_serial),
        (F_DESTINATION, "s", msg.destination),
        (F_SENDER, "s", msg.sender),
        (F_SIGNATURE, "g", msg.signature or None),
    ):
        if value is not None:
            fields.append((code, Variant(sig, value)))
    writer = _Writer("<")
    writer.write("y", ord("l"))
    writer.write("y", msg.type)
    writer.write("y", msg.flags)
    writer.write("y", 1)
    writer.write("u", len(body))
    writer.write("u", msg.serial)
    writer.write("a(yv)", fields)
    writer.align(8)
    return bytes(writer.buf) + body


def message_length(header: bytes) -> int | None:
    """The full length of a message from its first 16 bytes; None if too short."""
    if len(header) < 16:
        return None
    endian = "<" if header[0:1] == b"l" else ">"
    if header[0:1] not in (b"l", b"B"):
        raise DBusError("org.bc250.Malformed", "bad endianness marker")
    body_len, _serial, fields_len = struct.unpack(endian + "III", header[4:16])
    header_len = 16 + fields_len
    header_len += (-header_len) % 8
    total = header_len + body_len
    if total > MAX_MESSAGE:
        raise DBusError("org.bc250.Malformed", "message too large")
    return total


def unmarshal_message(data: bytes) -> Message:
    endian = "<" if data[0:1] == b"l" else ">"
    reader = _Reader(data, endian, 0)
    reader.read("y")
    msg_type = reader.read("y")
    flags = reader.read("y")
    reader.read("y")
    body_len = reader.read("u")
    serial = reader.read("u")
    fields = reader.read("a(yv)")
    reader.align(8)
    msg = Message(type=msg_type, serial=serial, flags=flags)
    for code, value in fields:
        if code == F_PATH:
            msg.path = value
        elif code == F_INTERFACE:
            msg.interface = value
        elif code == F_MEMBER:
            msg.member = value
        elif code == F_ERROR_NAME:
            msg.error_name = value
        elif code == F_REPLY_SERIAL:
            msg.reply_serial = value
        elif code == F_DESTINATION:
            msg.destination = value
        elif code == F_SENDER:
            msg.sender = value
        elif code == F_SIGNATURE:
            msg.signature = value
    body_start = reader.pos
    if body_start + body_len > len(data):
        raise DBusError("org.bc250.Malformed", "body truncated")
    if msg.signature:
        body_reader = _Reader(data[:body_start + body_len], endian, body_start)
        # alignment is relative to the message start, which body_start keeps
        msg.body = [body_reader.read(sig) for sig in split_signature(msg.signature)]
    return msg


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------

def system_bus_path() -> str:
    """The socket path from DBUS_SYSTEM_BUS_ADDRESS, or the standard location."""
    address = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", "")
    for part in address.split(";"):
        if part.startswith("unix:"):
            for kv in part[5:].split(","):
                if kv.startswith("path="):
                    return kv[5:]
    return SYSTEM_BUS_DEFAULT


SignalHandler = Callable[[Message], None]


class Connection:
    """A blocking connection with a background reader thread.

    Method calls may be issued from any thread. Signal handlers run on the
    reader thread and must return quickly.
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = path or system_bus_path()
        self.unique_name: str | None = None
        self._sock: socket.socket | None = None
        self._serial = 0
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._pending: dict[int, list] = {}
        self._handlers: list[SignalHandler] = []
        self._reader: threading.Thread | None = None
        self._closed = threading.Event()
        self.on_disconnect: Callable[[], None] | None = None

    # -- lifecycle ---------------------------------------------------------

    def connect(self, timeout: float = 5.0) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(self.path)
            self._authenticate(sock)
        except OSError:
            sock.close()
            raise
        sock.settimeout(None)
        self._sock = sock
        self._closed.clear()
        self._reader = threading.Thread(target=self._read_loop, name="dbus-reader", daemon=True)
        self._reader.start()
        self.unique_name = self.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                     "org.freedesktop.DBus", "Hello", timeout=timeout)[0]

    @staticmethod
    def _authenticate(sock: socket.socket) -> None:
        uid_hex = str(os.getuid()).encode().hex()
        sock.sendall(b"\0AUTH EXTERNAL " + uid_hex.encode() + b"\r\n")
        reply = b""
        while not reply.endswith(b"\r\n"):
            chunk = sock.recv(512)
            if not chunk:
                raise DBusError("org.bc250.AuthFailed", "bus closed during authentication")
            reply += chunk
            if len(reply) > 4096:
                raise DBusError("org.bc250.AuthFailed", "oversized authentication reply")
        if not reply.startswith(b"OK "):
            raise DBusError("org.bc250.AuthFailed", reply.decode("ascii", "replace").strip())
        sock.sendall(b"BEGIN\r\n")

    def close(self) -> None:
        self._closed.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        self._fail_pending(DBusError("org.bc250.Disconnected", "connection closed"))

    @property
    def connected(self) -> bool:
        return self._sock is not None and not self._closed.is_set()

    # -- calls and signals -------------------------------------------------

    def add_signal_handler(self, handler: SignalHandler) -> None:
        self._handlers.append(handler)

    def add_match(self, rule: str, timeout: float = 5.0) -> None:
        self.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                  "AddMatch", "s", [rule], timeout=timeout)

    def call(self, destination: str, path: str, interface: str, member: str,
             signature: str = "", args: list | tuple = (), timeout: float = 5.0) -> list:
        if not self.connected:
            raise DBusError("org.bc250.Disconnected", "not connected")
        with self._lock:
            self._serial += 1
            serial = self._serial
            slot: list = [threading.Event(), None]
            self._pending[serial] = slot
        msg = Message(type=METHOD_CALL, serial=serial, path=path, interface=interface,
                      member=member, destination=destination, signature=signature,
                      body=list(args))
        try:
            self._send(marshal_message(msg))
            if not slot[0].wait(timeout):
                raise DBusError("org.freedesktop.DBus.Error.Timeout",
                                f"{interface}.{member} did not answer within {timeout:.1f} s")
        finally:
            with self._lock:
                self._pending.pop(serial, None)
        reply = slot[1]
        if isinstance(reply, Exception):
            raise reply
        if reply.type == ERROR:
            text = reply.body[0] if reply.body and isinstance(reply.body[0], str) else ""
            raise DBusError(reply.error_name or "org.freedesktop.DBus.Error.Failed", text)
        return reply.body

    def _send(self, data: bytes) -> None:
        sock = self._sock
        if sock is None:
            raise DBusError("org.bc250.Disconnected", "not connected")
        with self._send_lock:
            try:
                sock.sendall(data)
            except OSError as exc:
                raise DBusError("org.bc250.Disconnected", str(exc)) from exc

    # -- reader ------------------------------------------------------------

    def _read_loop(self) -> None:
        buf = b""
        sock = self._sock
        try:
            while sock is not None and not self._closed.is_set():
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while True:
                    total = message_length(buf[:16])
                    if total is None or len(buf) < total:
                        break
                    raw, buf = buf[:total], buf[total:]
                    try:
                        msg = unmarshal_message(raw)
                    except (DBusError, ValueError, struct.error, IndexError):
                        continue
                    self._dispatch(msg)
        except (OSError, DBusError):
            pass
        was_open = not self._closed.is_set()
        self._closed.set()
        self._fail_pending(DBusError("org.bc250.Disconnected", "the bus closed the connection"))
        if was_open and self.on_disconnect is not None:
            self.on_disconnect()

    def _dispatch(self, msg: Message) -> None:
        if msg.type in (METHOD_RETURN, ERROR) and msg.reply_serial is not None:
            with self._lock:
                slot = self._pending.get(msg.reply_serial)
            if slot is not None:
                slot[1] = msg
                slot[0].set()
        elif msg.type == SIGNAL:
            for handler in list(self._handlers):
                try:
                    handler(msg)
                except Exception:  # noqa: BLE001 — a handler must not kill the reader
                    pass

    def _fail_pending(self, exc: Exception) -> None:
        with self._lock:
            slots = list(self._pending.values())
        for slot in slots:
            if slot[1] is None:
                slot[1] = exc
                slot[0].set()

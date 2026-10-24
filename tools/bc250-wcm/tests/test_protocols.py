"""D-Bus marshalling, the mgmt protocol, btmgmt output and the helper protocol."""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers  # noqa: E402,F401  (sets sys.path)
from bc250wcm import bluez, dbus, mgmt, telemetry  # noqa: E402


class TestDBusMarshalling(unittest.TestCase):
    def test_split_signature(self):
        self.assertEqual(dbus.split_signature("sa{sv}as"), ["s", "a{sv}", "as"])
        self.assertEqual(dbus.split_signature("a{oa{sa{sv}}}"), ["a{oa{sa{sv}}}"])
        self.assertEqual(dbus.split_signature("(ii)ay"), ["(ii)", "ay"])
        with self.assertRaises(ValueError):
            dbus.split_signature("a{sv")
        with self.assertRaises(ValueError):
            dbus.split_signature("z")

    def test_roundtrip_of_a_bluez_shaped_reply(self):
        body = [{"/org/bluez/hci1": {"org.bluez.Adapter1": {
            "Address": dbus.Variant("s", "00:E0:4C:01:02:03"),
            "Powered": dbus.Variant("b", True),
            "Class": dbus.Variant("u", 0x7C010C),
            "UUIDs": dbus.Variant("as", ["0000110e-0000-1000-8000-00805f9b34fb"]),
            "RSSI": dbus.Variant("n", -71),
        }}}]
        msg = dbus.Message(type=dbus.METHOD_RETURN, serial=7, reply_serial=3,
                           signature="a{oa{sa{sv}}}", body=body)
        raw = dbus.marshal_message(msg)
        self.assertEqual(dbus.message_length(raw[:16]), len(raw))
        back = dbus.unmarshal_message(raw)
        self.assertEqual(back.reply_serial, 3)
        props = back.body[0]["/org/bluez/hci1"]["org.bluez.Adapter1"]
        self.assertEqual(props["Address"], "00:E0:4C:01:02:03")
        self.assertIs(props["Powered"], True)
        self.assertEqual(props["RSSI"], -71)
        self.assertEqual(props["UUIDs"], ["0000110e-0000-1000-8000-00805f9b34fb"])

    def test_struct_alignment_and_empty_arrays(self):
        msg = dbus.Message(type=dbus.SIGNAL, serial=1, path="/x", interface="a.b", member="C",
                           signature="ya(yv)ats", body=[1, [(1, dbus.Variant("t", 2 ** 40))], [], "z"])
        back = dbus.unmarshal_message(dbus.marshal_message(msg))
        self.assertEqual(back.body, [1, [(1, 2 ** 40)], [], "z"])

    def test_truncated_and_corrupt_messages_raise_dbus_errors(self):
        msg = dbus.Message(type=dbus.SIGNAL, serial=1, path="/x", interface="a.b", member="C",
                           signature="s", body=["hello"])
        raw = dbus.marshal_message(msg)
        with self.assertRaises(dbus.DBusError):
            dbus.unmarshal_message(raw[:-4])
        with self.assertRaises(dbus.DBusError):
            dbus.message_length(b"X" + raw[1:16])

    def test_variant_needs_a_wrapper(self):
        with self.assertRaises(TypeError):
            dbus.marshal_body("v", ["plain"])

    def test_system_bus_path_from_environment(self):
        old = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS")
        try:
            os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = "unix:path=/tmp/bus,guid=1"
            self.assertEqual(dbus.system_bus_path(), "/tmp/bus")
            os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = "tcp:host=x"
            self.assertEqual(dbus.system_bus_path(), dbus.SYSTEM_BUS_DEFAULT)
        finally:
            if old is None:
                os.environ.pop("DBUS_SYSTEM_BUS_ADDRESS", None)
            else:
                os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = old


class TestFakeBus(unittest.TestCase):
    """A Connection against an in-process fake bus over a socketpair."""

    def _serve(self, server: socket.socket, replies) -> None:
        data = b""
        while b"AUTH EXTERNAL" not in data or not data.endswith(b"\r\n"):
            data += server.recv(4096)
        server.sendall(b"OK 1234567890abcdef\r\n")
        while b"BEGIN\r\n" not in data:
            data += server.recv(4096)
        rest = data.split(b"BEGIN\r\n", 1)[1]
        buf = rest
        while True:
            total = dbus.message_length(buf[:16])
            while total is None or len(buf) < total:
                chunk = server.recv(65536)
                if not chunk:
                    return
                buf += chunk
                total = dbus.message_length(buf[:16])
            raw, buf = buf[:total], buf[total:]
            msg = dbus.unmarshal_message(raw)
            reply = replies(msg)
            if reply is not None:
                server.sendall(dbus.marshal_message(reply))

    def test_hello_call_error_and_signal(self):
        client_sock, server_sock = socket.socketpair()

        def replies(msg):
            if msg.member == "Hello":
                return dbus.Message(type=dbus.METHOD_RETURN, serial=100, reply_serial=msg.serial,
                                    signature="s", body=[":1.42"])
            if msg.member == "Fail":
                return dbus.Message(type=dbus.ERROR, serial=101, reply_serial=msg.serial,
                                    error_name="org.bluez.Error.NotReady", signature="s",
                                    body=["Resource Not Ready"])
            if msg.member == "Ping":
                # a signal first, then the reply
                server_sock.sendall(dbus.marshal_message(dbus.Message(
                    type=dbus.SIGNAL, serial=102, path="/org/bluez/hci0",
                    interface="org.freedesktop.DBus.Properties", member="PropertiesChanged",
                    signature="sa{sv}as",
                    body=["org.bluez.Adapter1", {"Powered": dbus.Variant("b", False)}, []])))
                return dbus.Message(type=dbus.METHOD_RETURN, serial=103, reply_serial=msg.serial)
            return None   # "Hang" never answers

        threading.Thread(target=self._serve, args=(server_sock, replies), daemon=True).start()
        conn = dbus.Connection()
        # connect() opens its own socket; hand it the pair instead
        conn._authenticate(client_sock)
        conn._sock = client_sock
        conn._reader = threading.Thread(target=conn._read_loop, daemon=True)
        conn._reader.start()
        self.assertEqual(conn.call("org.freedesktop.DBus", "/", "org.freedesktop.DBus", "Hello")[0],
                         ":1.42")
        seen = []
        conn.add_signal_handler(lambda m: seen.append(m))
        conn.call("x", "/", "x", "Ping")
        self.assertEqual(seen[0].member, "PropertiesChanged")
        with self.assertRaises(dbus.DBusError) as ctx:
            conn.call("x", "/", "x", "Fail")
        self.assertEqual(ctx.exception.name, "org.bluez.Error.NotReady")
        with self.assertRaises(dbus.DBusError) as ctx:
            conn.call("x", "/", "x", "Hang", timeout=0.2)
        self.assertIn("Timeout", ctx.exception.name)
        conn.close()
        with self.assertRaises(dbus.DBusError):
            conn.call("x", "/", "x", "Hello")
        server_sock.close()


class TestMgmtEncoding(unittest.TestCase):
    def test_address_is_little_endian_on_the_wire(self):
        raw = mgmt.encode_address(helpers.PAD1)
        self.assertEqual(raw, bytes([0xC3, 0xB2, 0xA1, 0xD8, 0x17, 0xE4]))
        self.assertEqual(mgmt.decode_address(raw), helpers.PAD1)

    def test_invalid_addresses_and_types_are_refused(self):
        for bad in ("e4:17:d8:a1:b2:c3", "E4:17:D8:A1:B2", "E4-17-D8-A1-B2-C3",
                    "E4:17:D8:A1:B2:C3; reboot", "", None):
            self.assertFalse(mgmt.valid_address(bad), bad)
        with self.assertRaises(ValueError):
            mgmt.conn_info_params(helpers.PAD1, 3)

    def test_command_packet(self):
        packet = mgmt.encode_command(mgmt.OP_GET_CONN_INFO, 1, mgmt.conn_info_params(helpers.PAD1, 2))
        self.assertEqual(struct.unpack_from("<HHH", packet), (0x0031, 1, 7))
        self.assertEqual(packet[-1], 2)

    def _complete(self, opcode, status, rp=b""):
        return struct.pack("<HB", opcode, status) + rp

    def test_conn_info_reply_with_rssi(self):
        rp = mgmt.encode_address(helpers.PAD1) + bytes([2]) + struct.pack("<bbb", -71, 0, 0)
        opcode, status, params = mgmt.parse_command_reply(
            mgmt.EV_CMD_COMPLETE, self._complete(mgmt.OP_GET_CONN_INFO, 0, rp))
        self.assertEqual((opcode, status), (mgmt.OP_GET_CONN_INFO, 0))
        info = mgmt.parse_conn_info(params)
        self.assertEqual(info, {"address": helpers.PAD1, "type": 2, "rssi": -71,
                                "tx_power": 0, "max_tx_power": 0})

    def test_127_means_no_value_not_a_reading(self):
        rp = mgmt.encode_address(helpers.PAD1) + bytes([2]) + struct.pack("<bbb", 127, 127, 4)
        info = mgmt.parse_conn_info(rp)
        self.assertIsNone(info["rssi"])
        self.assertIsNone(info["tx_power"])
        self.assertEqual(info["max_tx_power"], 4)

    def test_partial_replies_raise(self):
        with self.assertRaises(ValueError):
            mgmt.parse_conn_info(b"\x01\x02")
        with self.assertRaises(ValueError):
            mgmt.parse_index_list(b"\x02\x00\x01\x00")      # says 2, carries 1
        with self.assertRaises(ValueError):
            mgmt.parse_connections(b"\x01\x00\x01\x02")
        with self.assertRaises(ValueError):
            mgmt.decode_packet(b"\x01\x00\x00\x00\x05\x00\x01")

    def test_index_list_and_connections(self):
        self.assertEqual(mgmt.parse_index_list(b"\x02\x00\x00\x00\x01\x00"), [0, 1])
        rp = b"\x02\x00" + mgmt.encode_address(helpers.PAD1) + b"\x02" \
            + mgmt.encode_address(helpers.HEADSET) + b"\x00"
        self.assertEqual(mgmt.parse_connections(rp),
                         [{"address": helpers.PAD1, "type": 2}, {"address": helpers.HEADSET, "type": 0}])

    def test_events(self):
        self.assertEqual(mgmt.parse_event(mgmt.EV_INDEX_REMOVED, 0, b""),
                         {"event": "index_removed", "index": 0})
        ev = mgmt.parse_event(mgmt.EV_DEVICE_DISCONNECTED, 1,
                              mgmt.encode_address(helpers.PAD2) + b"\x02\x01")
        self.assertEqual((ev["address"], ev["reason"]), (helpers.PAD2, 1))
        self.assertEqual(mgmt.parse_event(mgmt.EV_CONTROLLER_ERROR, 1, b"\x05")["code"], 5)
        self.assertIsNone(mgmt.parse_event(mgmt.EV_DEVICE_CONNECTED, 1, b"\x00"))


class TestMgmtClientMatching(unittest.TestCase):
    """Concurrent queries are matched to their replies by address, not order."""

    def test_out_of_order_replies(self):
        client = mgmt.MgmtClient()
        ours, kernel = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        client._sock = ours
        client._reader = threading.Thread(target=client._read_loop, daemon=True)
        client._reader.start()
        results = {}

        def ask(address):
            try:
                results[address] = client.conn_info(1, address, 2, timeout=2)
            except mgmt.MgmtError as exc:
                results[address] = exc.status

        threads = [threading.Thread(target=ask, args=(a,)) for a in (helpers.PAD1, helpers.PAD2)]
        for th in threads:
            th.start()
        # the "kernel" reads both commands, then answers PAD2 first, PAD1 with an error
        requests = [kernel.recv(64), kernel.recv(64)]
        self.assertTrue(all(r[:2] == b"\x31\x00" for r in requests))

        def complete(address, status, rssi):
            rp = mgmt.encode_address(address) + b"\x02" + struct.pack("<bbb", rssi, 0, 0)
            params = struct.pack("<HB", mgmt.OP_GET_CONN_INFO, status) + rp
            kernel.send(struct.pack("<HHH", mgmt.EV_CMD_COMPLETE, 1, len(params)) + params)

        complete(helpers.PAD2, 0, -66)
        complete(helpers.PAD1, mgmt.ST_NOT_CONNECTED, 0)
        for th in threads:
            th.join(3)
        self.assertEqual(results[helpers.PAD2]["rssi"], -66)
        self.assertEqual(results[helpers.PAD1], mgmt.ST_NOT_CONNECTED)
        client.close()
        kernel.close()

    def test_no_reply_is_a_local_timeout(self):
        client = mgmt.MgmtClient()
        ours, kernel = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        client._sock = ours
        client._reader = threading.Thread(target=client._read_loop, daemon=True)
        client._reader.start()
        with self.assertRaises(mgmt.MgmtError) as ctx:
            client.conn_info(1, helpers.PAD1, 2, timeout=0.2)
        self.assertEqual(ctx.exception.status, mgmt.ST_TIMEOUT_LOCAL)
        client.close()
        kernel.close()


class TestHelperValidation(unittest.TestCase):
    def test_valid_requests(self):
        self.assertEqual(mgmt.validate_request({"op": "conn_info", "index": 1,
                                                "address": helpers.PAD1, "type": 2}),
                         ("conn_info", {"index": 1, "address": helpers.PAD1, "type": 2}))
        self.assertEqual(mgmt.validate_request({"op": "index_list"})[0], "index_list")

    def test_hostile_requests_are_refused(self):
        bad = [
            [], "conn_info", {"op": "power_off", "index": 0},
            {"op": "conn_info", "index": -1, "address": helpers.PAD1, "type": 2},
            {"op": "conn_info", "index": 0xFFFF, "address": helpers.PAD1, "type": 2},
            {"op": "conn_info", "index": True, "address": helpers.PAD1, "type": 2},
            {"op": "conn_info", "index": "1", "address": helpers.PAD1, "type": 2},
            {"op": "conn_info", "index": 1, "address": "$(reboot)", "type": 2},
            {"op": "conn_info", "index": 1, "address": helpers.PAD1, "type": 7},
            {"op": "conn_info", "index": 1, "address": helpers.PAD1, "type": True},
        ]
        for request in bad:
            with self.assertRaises(ValueError, msg=repr(request)):
                mgmt.validate_request(request)

    def test_helper_refuses_arguments(self):
        proc = subprocess.run([sys.executable, "-I", mgmt.__file__, "--anything"],
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 2)


class TestBtmgmtParsing(unittest.TestCase):
    def test_success_line_from_the_reference_machine(self):
        out = ("Connection Information for E4:17:D8:A1:B2:C3 (LE Random)\n"
               "\tRSSI -71\tTX power 0\tmaximum TX power 0\n")
        info = telemetry.parse_btmgmt_conn_info(out)
        self.assertEqual((info.status, info.rssi, info.tx_power, info.max_tx_power),
                         (telemetry.OK, -71, 0, 0))

    def test_colour_codes_and_failures(self):
        cases = {
            "\x1b[0;91mGet Conn Info failed, status 0x14 (Permission Denied)\n\x1b[0m": telemetry.PERMISSION,
            "Get Conn Info failed, status 0x02 (Not Connected)": telemetry.NOT_CONNECTED,
            "Reading hci7 info failed with status 0x11 (Invalid Index)": telemetry.INVALID_INDEX,
            "Get Conn Info failed, status 0x08 (Timeout)": telemetry.TIMEOUT,
            "sudo: a password is required": telemetry.PERMISSION,
            "Unable to open mgmt_socket: Operation not permitted": telemetry.PERMISSION,
            "": telemetry.ERROR,
            "RSSI garbage": telemetry.ERROR,
        }
        for text, expected in cases.items():
            self.assertEqual(telemetry.parse_btmgmt_conn_info(text, 1).status, expected, text)

    def test_127_is_unknown(self):
        info = telemetry.parse_btmgmt_conn_info("RSSI 127 TX power 127 maximum TX power 127")
        self.assertEqual(info.status, telemetry.OK)
        self.assertIsNone(info.rssi)
        self.assertIsNone(info.tx_power)

    def test_argv_has_no_shell_and_is_validated(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, "RSSI -60 TX power 0 maximum TX power 0", "")

        backend = telemetry.BtmgmtBackend(use_sudo=True, btmgmt="/usr/bin/btmgmt", runner=runner)
        backend.start()
        info = backend.conn_info(1, helpers.PAD1, 2, timeout=1.0)
        self.assertEqual(info.rssi, -60)
        argv, kwargs = calls[0]
        self.assertEqual(argv, ["sudo", "-n", "--", "/usr/bin/btmgmt", "-i", "1", "conn-info",
                                "-t", "2", helpers.PAD1])
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["timeout"], 1.0)
        bad = backend.conn_info(1, "E4:17:D8:A1:B2:C3 && id", 2, 1.0)
        self.assertEqual(bad.status, telemetry.ERROR)
        self.assertEqual(len(calls), 1)

    def test_timeout_and_missing_binary(self):
        def slow(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

        backend = telemetry.BtmgmtBackend(use_sudo=False, btmgmt="/usr/bin/btmgmt", runner=slow)
        backend.start()
        self.assertEqual(backend.conn_info(0, helpers.PAD1, 2, 0.5).status, telemetry.TIMEOUT)
        missing = telemetry.BtmgmtBackend(use_sudo=False, btmgmt=None)
        missing.btmgmt = None
        missing.start()
        self.assertFalse(missing.available)


class TestCandidateTypes(unittest.TestCase):
    def test_address_types(self):
        ct = telemetry.candidate_types
        self.assertEqual(ct("random", None, "LE"), [mgmt.ADDR_LE_RANDOM])
        self.assertEqual(ct("random", "LE", "LE"), [mgmt.ADDR_LE_RANDOM])
        self.assertEqual(ct("public", "LE", "dual"), [mgmt.ADDR_LE_PUBLIC])
        self.assertEqual(ct("public", "BR/EDR", "dual"), [mgmt.ADDR_BREDR])
        self.assertEqual(ct("public", None, "BR/EDR"), [mgmt.ADDR_BREDR, mgmt.ADDR_LE_PUBLIC])
        self.assertEqual(ct("public", None, "LE"), [mgmt.ADDR_LE_PUBLIC, mgmt.ADDR_BREDR])
        self.assertEqual(ct(None, None, "?"), [mgmt.ADDR_BREDR, mgmt.ADDR_LE_PUBLIC])
        self.assertEqual(ct("public", None, "?", known=1), [1])

    def test_query_falls_through_not_connected(self):
        backend = helpers.FakeBackend({
            (0, helpers.HEADSET, mgmt.ADDR_LE_PUBLIC): telemetry.ConnInfo(telemetry.OK, -50, 0, 0)})
        info = backend.query(0, helpers.HEADSET, [mgmt.ADDR_BREDR, mgmt.ADDR_LE_PUBLIC], 1)
        self.assertEqual((info.status, info.rssi, info.addr_type), (telemetry.OK, -50, 1))
        self.assertEqual(len(backend.calls), 2)


FAKE_HELPER = textwrap.dedent("""
    import json, sys
    mode = sys.argv[1]
    if mode == "sudo-fail":
        sys.stderr.write("sudo: a password is required\\n")
        sys.exit(1)
    if mode == "old":
        print(json.dumps({"event": "ready", "protocol": 0, "euid": 0}), flush=True)
    else:
        print(json.dumps({"event": "ready", "protocol": 1, "euid": 0}), flush=True)
    for line in sys.stdin:
        req = json.loads(line)
        if mode == "hang":
            continue
        if req.get("op") == "conn_info":
            if req["address"].endswith("C3"):
                print(json.dumps({"id": req["id"], "ok": True, "status": 0, "rssi": -71,
                                  "tx_power": 0, "max_tx_power": 0}), flush=True)
            else:
                print(json.dumps({"event": "device_disconnected", "index": 1,
                                  "address": req["address"], "type": 2, "reason": 1}), flush=True)
                print(json.dumps({"id": req["id"], "ok": False, "status": 2,
                                  "error": "Not Connected"}), flush=True)
""")


class TestHelperBackend(unittest.TestCase):
    """HelperBackend against a stand-in helper process (no sudo, no kernel)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.script = os.path.join(self.dir, "fake_helper.py")
        with open(self.script, "w") as handle:
            handle.write(FAKE_HELPER)

    def _backend(self, mode, ready_timeout=5.0):
        backend = telemetry.HelperBackend([sys.executable, "-I", self.script, mode],
                                          ready_timeout=ready_timeout)
        events = []
        backend.on_event = events.append
        backend.start()
        return backend, events

    def test_queries_events_and_clean_exit(self):
        backend, events = self._backend("ok")
        try:
            self.assertTrue(backend.available)
            self.assertEqual(backend.helper_euid, 0)
            info = backend.conn_info(1, helpers.PAD1, 2, 1.0)
            self.assertEqual((info.status, info.rssi), (telemetry.OK, -71))
            info = backend.conn_info(1, helpers.PAD2, 2, 1.0)
            self.assertEqual(info.status, telemetry.NOT_CONNECTED)
            time.sleep(0.1)
            self.assertEqual(events[0]["event"], "device_disconnected")
            proc = backend._proc
        finally:
            backend.stop()
        self.assertIsNotNone(proc.returncode)       # the helper is gone, not orphaned

    def test_sudo_failure_is_reported(self):
        backend, _ = self._backend("sudo-fail")
        self.assertFalse(backend.available)
        self.assertIn("password is required", backend.problem)
        self.assertEqual(backend.conn_info(1, helpers.PAD1, 2, 0.5).status, telemetry.UNAVAILABLE)

    def test_protocol_mismatch(self):
        backend, _ = self._backend("old")
        try:
            self.assertFalse(backend.available)
            self.assertIn("protocol", backend.problem)
        finally:
            backend.stop()

    def test_unanswered_query_times_out(self):
        backend, _ = self._backend("hang")
        try:
            start = time.monotonic()
            info = backend.conn_info(1, helpers.PAD1, 2, 0.2)
            self.assertEqual(info.status, telemetry.TIMEOUT)
            self.assertLess(time.monotonic() - start, 2.0)
        finally:
            backend.stop()

    def test_reply_parsing_is_defensive(self):
        f = telemetry.conn_info_from_reply
        self.assertEqual(f(None).status, telemetry.ERROR)
        self.assertEqual(f({"ok": False}).status, telemetry.ERROR)
        self.assertEqual(f({"ok": False, "status": 0x14}).status, telemetry.PERMISSION)
        self.assertEqual(f({"ok": False, "status": 0x11}).status, telemetry.INVALID_INDEX)
        odd = f({"ok": True, "rssi": "x", "tx_power": True, "max_tx_power": 500})
        self.assertEqual((odd.status, odd.rssi, odd.tx_power, odd.max_tx_power),
                         (telemetry.OK, None, None, None))


class TestHelperProcessUnprivileged(unittest.TestCase):
    """The real helper against the real kernel, as an ordinary user: the
    untrusted Read Index List works, Get Connection Information is refused."""

    def test_real_mgmt_socket(self):
        if not os.path.isdir("/sys/class/bluetooth") or os.geteuid() == 0:
            self.skipTest("needs a Bluetooth stack and an unprivileged user")
        requests = [
            {"id": 1, "op": "index_list"},
            {"id": 2, "op": "conn_info", "index": 0, "address": helpers.PAD1, "type": 2},
            {"id": 3, "op": "conn_info", "index": 0, "address": "nope", "type": 2},
        ]
        try:
            proc = subprocess.run([sys.executable, "-I", mgmt.__file__],
                                  input="\n".join(json.dumps(r) for r in requests) + "\n",
                                  capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            self.fail("helper did not exit on EOF")
        lines = [json.loads(line) for line in proc.stdout.splitlines()]
        if lines and lines[0].get("event") == "fatal":
            self.skipTest(lines[0]["error"])
        by_id = {line.get("id"): line for line in lines if "id" in line}
        self.assertTrue(by_id[1]["ok"])
        self.assertEqual(by_id[2]["status"], mgmt.ST_PERMISSION_DENIED)
        self.assertEqual(by_id[3]["error"], "invalid address")


class TestSystemBus(unittest.TestCase):
    """Read-only check against the real system bus, skipped without BlueZ."""

    def test_get_managed_objects(self):
        source = bluez.BluezSource()
        source.start()
        try:
            source.wait_ready(5)
            snap = source.snapshot()
        finally:
            source.stop()
        if snap.status != bluez.STATUS_OK:
            self.skipTest(f"no BlueZ here: {snap.status} {snap.error}")
        for adapter in snap.adapters:
            self.assertTrue(adapter.identity)
            self.assertRegex(adapter.name, r"^hci\d+$")


if __name__ == "__main__":
    unittest.main()

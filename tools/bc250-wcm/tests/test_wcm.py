"""WCM: USB receivers, HID research tools, input activity, mixed BT + USB monitoring, migration."""

from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers  # noqa: E402
from helpers import PAD1, PAD2, FakeBackend, FakeSource, adapter_obj, objects, pad_obj  # noqa: E402
from bc250wcm import (config, diagnostics, engine, export, fakesys, hid, hidcli, history,  # noqa: E402
                      i18n, inputs, telemetry, usb)
from bc250wcm.tui import grid_layout  # noqa: E402

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def setUpModule():
    i18n.set_language("en")


def scan(root):
    return usb.scan_usb(root, udev_data=os.path.join(root, "no-udev"))


class FakeUsbSource(usb.UsbSource):
    """The real UsbSource over a fake sysfs tree; rescans only when told to."""

    def __init__(self, root, known_file=None):
        super().__init__(scanner=lambda: scan(root), known_file=known_file)

    def start(self):
        self.rescan()

    def stop(self):
        pass


class FakeActivity:
    def __init__(self, path, counts=None, status=inputs.ST_OK):
        self.path = path
        self.status = status if path else inputs.ST_NO_NODE
        self.counts = list(counts or [5])

    def start(self):
        pass

    def stop(self):
        pass

    def take(self):
        if self.status != inputs.ST_OK:
            return None
        return self.counts.pop(0) if self.counts else 0


# --------------------------------------------------------------------------
# USB scanning and identity
# --------------------------------------------------------------------------

class TestUsbScan(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_no_devices(self):
        self.assertEqual(scan(self.root), [])
        self.assertEqual(usb.scan_usb(os.path.join(self.root, "missing")), [])

    def test_idle_receiver_is_detected(self):
        fakesys.eightbitdo_receiver(self.root, "1-3", active=False)
        fakesys.keyboard(self.root, "1-1")            # not a controller
        (dev,) = scan(self.root)
        self.assertEqual((dev.usb_id, dev.mode, dev.transport, dev.physical_id),
                         ("2dc8:301c", "idle", "usb-2.4", "usb:1-3"))
        self.assertTrue(dev.is_receiver)
        self.assertEqual(dev.joystick_nodes(), [])

    def test_active_receiver_interfaces(self):
        fakesys.eightbitdo_receiver(self.root, "1-3", active=True)
        (dev,) = scan(self.root)
        self.assertEqual(dev.mode, "active")
        self.assertEqual([(i.number, i.driver, i.role) for i in dev.interfaces],
                         [(0, "xpad", "gamepad"), (1, "usbhid", "hid"), (2, "usbhid", "hid")])
        self.assertEqual([n.name for n in dev.joystick_nodes()], ["event20"])
        self.assertEqual([h.name for _, h in dev.hidraw_nodes()], ["hidraw3", "hidraw4"])
        # 310a alone could be the pad on a cable
        self.assertEqual(dev.transport, "usb-2.4?")
        self.assertIn("cable", dev.display_name)

    def test_pid_change_keeps_the_physical_identity(self):
        fakesys.eightbitdo_receiver(self.root, "1-3", active=False)
        before = scan(self.root)[0]
        fakesys.eightbitdo_receiver(self.root, "1-3", active=True, hidraw_base=9, event_no=31)
        after = scan(self.root)[0]
        self.assertEqual(before.physical_id, after.physical_id)
        self.assertNotEqual(before.usb_id, after.usb_id)

    def test_serial_identity_spans_both_modes(self):
        """As on real hardware: the receiver keeps its USB serial when it goes IDLE."""
        fakesys.eightbitdo_receiver(self.root, "3-6", active=True, serial="A1B2C3D4E5")
        linked = scan(self.root)[0]
        fakesys.eightbitdo_receiver(self.root, "3-6", active=False, serial="A1B2C3D4E5")
        idle = scan(self.root)[0]
        self.assertEqual(linked.physical_id, idle.physical_id)
        self.assertEqual(idle.physical_id, "usb:2dc8-sn-A1B2C3D4E5")
        self.assertEqual((idle.usb_id, idle.mode, idle.bcd_device), ("2dc8:301c", "idle", "0200"))
        desc = hid.parse_descriptor(fakesys.DESC_8BITDO_IDLE)
        self.assertEqual(desc.top_pages, [0xFFA0])
        self.assertEqual((desc.report("input", 2).total_bytes, desc.report("output", 0x81).total_bytes),
                         (64, 64))

    def test_dinput_mode_is_a_linked_pad_not_idle(self):
        """301c with a gamepad input device is a pad in DInput mode (as bc250-paddles needs it)."""
        fakesys.eightbitdo_receiver(self.root, "3-6", active=False, serial="A1B2C3D4E5")
        idle = scan(self.root)[0]
        fakesys.remove_usb_device(self.root, "3-6")
        fakesys.eightbitdo_receiver(self.root, "3-6", active=False, dinput=True, serial="A1B2C3D4E5")
        dinput = scan(self.root)[0]
        self.assertEqual((idle.mode, dinput.mode), ("idle", "active"))
        self.assertEqual((idle.usb_id, dinput.usb_id), ("2dc8:301c", "2dc8:301c"))
        self.assertEqual(dinput.profile_mode, "idle")
        self.assertEqual([n.name for n in dinput.joystick_nodes()], ["event20"])
        self.assertEqual(idle.physical_id, dinput.physical_id)
        # 301c is never the pad on a cable
        self.assertEqual(dinput.transport, "usb-2.4")

    def test_known_receiver_is_confirmed_after_idle(self):
        known = os.path.join(self.root, "state", "receivers")
        source = FakeUsbSource(self.root, known)
        fakesys.eightbitdo_receiver(self.root, "1-3", active=False)
        source.rescan()
        fakesys.eightbitdo_receiver(self.root, "1-3", active=True)
        source.rescan()
        dev = source.snapshot().devices[0]
        self.assertEqual(dev.transport, "usb-2.4")
        # remembered for the next run
        self.assertEqual(usb.load_known(known), {"usb:1-3"})
        fresh = FakeUsbSource(self.root, known)
        fresh.rescan()
        self.assertTrue(fresh.snapshot().devices[0].is_receiver)

    def test_generic_usb_gamepad_and_serial_identity(self):
        fakesys.add_usb_device(self.root, "2-1", 0x045E, 0x028E, "Controller", "Microsoft",
                               serial="0A1B2C3D")
        ipath = fakesys.add_interface(self.root, "2-1", 0, 0xFF, "xpad")
        fakesys.add_input(self.root, ipath, 40, 40, "Xbox 360 Controller")
        (dev,) = scan(self.root)
        self.assertIsNone(dev.profile)
        self.assertEqual(dev.transport, "usb")
        self.assertEqual(dev.physical_id, "usb:045e-sn-0A1B2C3D")

    def test_battery_from_power_supply(self):
        path = fakesys.eightbitdo_receiver(self.root, "1-3", active=True)
        ps = os.path.join(path, "1-3:1.0", "power_supply", "xpad_battery")
        os.makedirs(ps)
        for name, value in (("capacity", "64"), ("status", "Discharging"), ("scope", "Device")):
            with open(os.path.join(ps, name), "w") as handle:
                handle.write(value)
        os.makedirs(os.path.join(self.root, "class", "power_supply"))
        os.symlink(ps, os.path.join(self.root, "class", "power_supply", "xpad_battery"))
        self.assertEqual(scan(self.root)[0].battery, (64, "Discharging"))

    def test_resolve_follows_a_receiver_to_another_port(self):
        fakesys.eightbitdo_receiver(self.root, "1-4", active=True)
        devices = scan(self.root)
        self.assertIs(usb.resolve_usb("usb:1-3", devices, "8bitdo-ultimate-2c-2g4"), devices[0])
        self.assertIsNone(usb.resolve_usb("usb:1-3", devices, "8bitdo-ultimate-2c-2g4", {"usb:1-4"}))
        self.assertIsNone(usb.resolve_usb("usb:1-3", devices, None))

    def test_key_bitmap(self):
        self.assertTrue(usb.has_key_bit(fakesys.key_bitmap(usb.BTN_GAMEPAD), usb.BTN_GAMEPAD))
        self.assertFalse(usb.has_key_bit(fakesys.key_bitmap(30), usb.BTN_GAMEPAD))
        self.assertFalse(usb.has_key_bit("zz", 1))
        self.assertTrue(usb.has_key_bit("1 0", 64))


# --------------------------------------------------------------------------
# HID descriptor, enumeration, capture, analysis
# --------------------------------------------------------------------------

class TestHid(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_vendor_descriptor_of_the_receiver(self):
        desc = hid.parse_descriptor(fakesys.DESC_8BITDO_VENDOR)
        self.assertTrue(desc.vendor_defined)
        self.assertEqual(desc.top_pages, [0xFF7A])
        inp = desc.report("input", 0x02)
        out = desc.report("output", 0x81)
        self.assertEqual((inp.payload_bytes, inp.total_bytes), (63, 64))
        self.assertEqual(out.payload_bytes, 63)
        self.assertEqual(desc.errors, [])

    def test_damaged_descriptor(self):
        desc = hid.parse_descriptor(bytes.fromhex("067AFF0901A101850275"))   # cut mid-item
        self.assertTrue(desc.errors)
        self.assertEqual(hid.parse_descriptor(b"").reports, [])
        self.assertTrue(hid.parse_descriptor(bytes.fromhex("B4")).errors)   # pop without push

    def test_enumeration_and_stable_selector_after_renumbering(self):
        fakesys.eightbitdo_receiver(self.root, "1-3", active=True, hidraw_base=3)
        nodes = hid.list_hidraw(self.root, dev_root=os.path.join(self.root, "dev"))
        self.assertEqual(hid.select_hidraw("2dc8:310a:2", nodes).name, "hidraw4")
        fakesys.eightbitdo_receiver(self.root, "1-3", active=True, hidraw_base=7, seq=9)
        nodes = hid.list_hidraw(self.root, dev_root=os.path.join(self.root, "dev"))
        node = hid.select_hidraw("2dc8:310a:2", nodes)
        self.assertEqual(node.name, "hidraw8")
        self.assertEqual(node.usb_port, "1-3")
        self.assertTrue(node.descriptor.vendor_defined)
        self.assertIsNone(hid.select_hidraw("2dc8:310a", nodes))        # two interfaces: ambiguous
        self.assertEqual(hid.select_hidraw("/dev/hidraw7", nodes).name, "hidraw7")

    def test_access_check(self):
        dev = os.path.join(self.root, "dev")
        os.makedirs(dev)
        path = os.path.join(dev, "hidraw1")
        open(path, "w").close()
        self.assertEqual(hid.access_of(path), "read")
        os.chmod(path, 0)
        if os.geteuid() != 0:
            self.assertEqual(hid.access_of(path), "denied")
        self.assertEqual(hid.access_of(os.path.join(dev, "hidraw9")), "missing")

    def test_capture_from_a_pipe_with_marks(self):
        rfd, wfd = os.pipe()
        mrfd, mwfd = os.pipe()
        base = bytes([0x02]) + bytes(63)
        os.write(wfd, base)
        os.write(mwfd, b"pressed A\n")
        seen = []
        cap = hid.capture(rfd, seconds=0.3, report_size=64, on_report=lambda t_, d: seen.append(d),
                          marks_fd=mrfd)
        for fd in (rfd, wfd, mrfd, mwfd):
            os.close(fd)
        self.assertEqual([d for _, d in cap.reports], [base])
        self.assertEqual(seen, [base])
        self.assertEqual(cap.marks[0][1], "pressed A")

    def test_save_load_analyze_and_marks(self):
        cap = hid.Capture(meta={"expected_input_sizes": {"2": 64}})
        base = bytes([0x02]) + bytes(63)
        moved = bytearray(base)
        moved[7] = 0x80
        cap.reports = [(1.0, base), (1.5, base), (2.1, bytes(moved)), (2.5, bytes(moved)),
                       (3.0, b"\x02\x01")]                               # the last one is short
        cap.marks = [(2.0, "left stick right")]
        path = os.path.join(self.root, "c.jsonl")
        hid.save_capture(cap, path)
        back = hid.load_capture(path)
        self.assertEqual(len(back.reports), 5)
        stats, malformed = hid.byte_stats(hid.split_by_id(back.reports)[2], 64)
        self.assertEqual(malformed, 1)
        self.assertEqual([s.offset for s in stats if not s.constant], [7])
        self.assertEqual(hid.correlate_marks(back), [("left stick right", 2.0, [7])])
        self.assertEqual(hid.changed_offsets(b"\x01\x02", b"\x01\x03\x04"), [1, 2])
        self.assertIn("[80]", hid.hex_line(bytes(moved), base))

    def test_damaged_capture_file(self):
        path = os.path.join(self.root, "bad.jsonl")
        with open(path, "w") as handle:
            handle.write('{"format": "bc250-wcm-hid-capture"}\n{"t": 1, "hex": "zz"}\n')
        with self.assertRaises(ValueError):
            hid.load_capture(path)
        self.assertEqual(hidcli.main(["analyze", path]), 1)

    def test_hid_tool_never_writes(self):
        """The research tool has no way to send a report: no write, no ioctl, read-only opens."""
        for name in ("hid.py", "hidcli.py", "inputs.py"):
            with open(os.path.join(TOOL_DIR, "bc250wcm", name), encoding="utf-8") as handle:
                source = handle.read()
            self.assertNotIn("os.write", source, name)
            self.assertNotIn("ioctl", source, name)
            self.assertNotIn("O_RDWR", source, name)
            self.assertNotIn("O_WRONLY", source, name)

    def test_capture_without_permission(self):
        fakesys.eightbitdo_receiver(self.root, "1-3", active=True)
        dev = os.path.join(self.root, "dev")
        os.makedirs(dev)
        with mock.patch.object(hid, "open_readonly", side_effect=PermissionError("denied")):
            code = hidcli.main(["capture", "2dc8:310a:2", "-t", "0.1"], sysfs=self.root, dev_root=dev)
        self.assertEqual(code, 3)
        self.assertEqual(hidcli.main(["capture", "nope"], sysfs=self.root, dev_root=dev), 1)



# --------------------------------------------------------------------------
# Input activity
# --------------------------------------------------------------------------

class TestInputs(unittest.TestCase):
    def test_count_events(self):
        key = struct.pack(inputs.EVENT_FORMAT, 0, 0, 0x01, 0x130, 1)
        syn = struct.pack(inputs.EVENT_FORMAT, 0, 0, 0x00, 0, 0)
        self.assertEqual(inputs.count_events(key + syn + key + syn[:5]), (2, 5))

    def test_reader_counts_from_a_pipe(self):
        rfd, wfd = os.pipe()
        reader = inputs.ActivityReader("/dev/input/event99", opener=lambda _p: rfd)
        reader.start()
        abs_ev = struct.pack(inputs.EVENT_FORMAT, 0, 0, 0x03, 0x00, 1234)
        syn = struct.pack(inputs.EVENT_FORMAT, 0, 0, 0x00, 0, 0)
        os.write(wfd, (abs_ev + syn) * 3)
        deadline = time.time() + 2
        while reader.total < 3 and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(reader.take(), 3)
        self.assertEqual(reader.take(), 0)
        os.close(wfd)                                 # the device went away
        deadline = time.time() + 2
        while reader.status == inputs.ST_OK and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(reader.status, inputs.ST_GONE)
        self.assertIsNone(reader.take())
        reader.stop()

    def test_reader_without_permission_or_node(self):
        def deny(_path):
            raise PermissionError(13, "Permission denied")

        reader = inputs.ActivityReader("/dev/input/event5", opener=deny)
        reader.start()
        self.assertEqual(reader.status, inputs.ST_PERMISSION)
        self.assertIsNone(reader.take())
        none = inputs.ActivityReader(None)
        none.start()
        self.assertEqual(none.status, inputs.ST_NO_NODE)


# --------------------------------------------------------------------------
# Engine: USB receivers alone and next to Bluetooth pads
# --------------------------------------------------------------------------

class WcmCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.usb = FakeUsbSource(self.root)
        self.counts = {}

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def make(self, bt_objs=None, table=None, activity_status=inputs.ST_OK):
        self.source = FakeSource(bt_objs or objects())
        cfg = config.Config()
        cfg.remember_selection = False
        self.events = history.EventLog()
        self.engine = engine.Engine(
            self.source, FakeBackend(table or {}), cfg, self.events, follow_kernel=False,
            battery_reader=lambda a: None, usb_source=self.usb,
            activity_factory=lambda path: FakeActivity(path, [7, 0, 3], activity_status),
            bt_inputs=lambda address: [])
        self.usb.on_change = self.engine.notify
        self.usb.rescan()
        self.engine.refresh()
        return self.engine

    def monitor(self, *targets):
        for tg in targets:
            self.engine.tracks[tg.key] = history.Track(tg.key, tg.address, tg.adapter_identity, tg.name,
                                                       kind=tg.kind, profile_id=tg.profile_id)
            self.engine.monitored.append(tg.key)
        with self.engine._lock:
            for tg in targets:
                track = self.engine.tracks[tg.key]
                if track.kind == "usb":
                    self.engine._update_usb_connection(track, self.engine.usb_snapshot(), time.time())
                else:
                    self.engine._update_connection(track, self.engine.snapshot(), time.time())
        return [tg.key for tg in targets]

    def replug(self, active):
        fakesys.eightbitdo_receiver(self.root, "1-3", active=active, hidraw_base=11, event_no=33, seq=12)
        self.usb.rescan()
        self.engine.refresh()

    def texts(self):
        return [e.text for e in self.events.events()]


class TestEngineUsb(WcmCase):
    def test_idle_then_linked_is_one_device(self):
        fakesys.eightbitdo_receiver(self.root, "1-3", active=False)
        self.make()
        (dev,) = self.engine.usb_snapshot().devices
        (key,) = self.monitor(engine.usb_target(dev))
        sample = self.engine.sample(key)
        self.assertEqual((sample.state, sample.rssi, sample.activity), (history.S_LINK_IDLE, None, None))
        self.replug(active=True)
        sample = self.engine.sample(key)
        self.assertEqual((sample.state, sample.rssi, sample.activity), (history.S_LINKED, None, 7))
        self.assertEqual(len(self.engine.usb_snapshot().devices), 1)
        self.assertEqual(list(self.engine.tracks), [key])         # no phantom second controller
        self.assertTrue(any("2dc8:301c → 2dc8:310a" in t for t in self.texts()))
        self.assertEqual(self.engine.usb_records[key][0][1:], ("2dc8:301c", "2dc8:310a"))
        self.assertTrue(self.engine.usb_snapshot().devices[0].is_receiver)

    def test_unplug_and_return_with_new_nodes(self):
        fakesys.eightbitdo_receiver(self.root, "1-3", active=True)
        self.make()
        (key,) = self.monitor(engine.usb_target(self.engine.usb_snapshot().devices[0]))
        self.engine.sample(key)
        fakesys.remove_usb_device(self.root, "1-3")
        self.usb.rescan()
        self.engine.refresh()
        sample = self.engine.sample(key)
        self.assertEqual(sample.state, history.S_USB_GONE)
        track = self.engine.tracks[key]
        self.assertEqual(track.disconnects, 1)
        self.assertIn("left the USB bus", track.last_disconnect_reason)
        self.replug(active=True)
        sample = self.engine.sample(key)
        self.assertEqual(sample.state, history.S_LINKED)
        self.assertEqual(track.reconnects, 1)
        self.assertEqual(track.input_node, "/dev/input/event33")   # the new event number

    def test_pad_off_is_a_radio_disconnect_not_a_usb_one(self):
        fakesys.eightbitdo_receiver(self.root, "1-3", active=True)
        self.make()
        (key,) = self.monitor(engine.usb_target(self.engine.usb_snapshot().devices[0]))
        self.replug(active=False)
        self.engine.sample(key)
        track = self.engine.tracks[key]
        self.assertIn("no pad linked", track.last_disconnect_reason)

    def test_no_rssi_and_no_battery_on_a_receiver(self):
        fakesys.eightbitdo_receiver(self.root, "1-3", active=True)
        self.make()
        (key,) = self.monitor(engine.usb_target(self.engine.usb_snapshot().devices[0]))
        for _ in range(3):
            sample = self.engine.sample(key)
            self.assertIsNone(sample.rssi)
            self.assertIsNone(sample.battery)
        track = self.engine.tracks[key]
        self.assertFalse(track.rssi_capable)
        self.assertIsNone(track.availability())
        self.assertIsNone(track.stats.min)
        self.assertEqual(track.activity_total, 10)
        path = os.path.join(self.root, "o.csv")
        export.export_csv([track], path)
        with open(path) as handle:
            rows = handle.read().splitlines()[1:]
        self.assertTrue(all(",linked,," in row for row in rows))      # state linked, rssi empty

    def test_inactivity_is_not_a_disconnect(self):
        fakesys.eightbitdo_receiver(self.root, "1-3", active=True)
        self.make()
        (key,) = self.monitor(engine.usb_target(self.engine.usb_snapshot().devices[0]))
        track = self.engine.tracks[key]
        self.engine._readers[key] = FakeActivity("/dev/input/event20", [0, 0, 0, 0])
        for _ in range(4):
            self.assertEqual(self.engine.sample(key).state, history.S_LINKED)
        self.assertEqual(track.disconnects, 0)
        track.last_input = time.time() - 120
        found = " ".join(t for _, t in diagnostics.diagnose(self.engine))
        self.assertIn("not a link failure", found)

    def test_input_node_permission_denied(self):
        fakesys.eightbitdo_receiver(self.root, "1-3", active=True)
        self.make(activity_status=inputs.ST_PERMISSION)
        (key,) = self.monitor(engine.usb_target(self.engine.usb_snapshot().devices[0]))
        sample = self.engine.sample(key)
        self.assertEqual((sample.state, sample.activity), (history.S_LINKED, None))
        self.assertTrue(any("no read access" in t for t in self.texts()))
        self.assertTrue(any("Cannot read" in t for _, t in diagnostics.diagnose(self.engine)))

    def test_bluetooth_and_usb_together(self):
        fakesys.eightbitdo_receiver(self.root, "1-3", active=True)
        table = {(1, PAD1, 2): telemetry.ConnInfo(telemetry.OK, -68, 0, 0),
                 (1, PAD2, 2): telemetry.ConnInfo(telemetry.OK, -66, 0, 0)}
        self.make(objects(adapter_obj(1), pad_obj(1, PAD1), pad_obj(1, PAD2)), table)
        snap = self.engine.snapshot()
        ident = snap.adapters[0].identity
        targets = [engine.bt_target(ident, d) for d in snap.devices]
        targets.append(engine.usb_target(self.engine.usb_snapshot().devices[0]))
        self.assertEqual(len(targets), 3)
        keys = self.monitor(*targets)
        results = {self.engine.tracks[k].kind + ":" + str(self.engine.sample(k).rssi) for k in keys}
        self.assertEqual(results, {"bt:-68", "bt:-66", "usb:None"})

    def test_reset_of_the_bt_adapter_leaves_usb_alone(self):
        fakesys.eightbitdo_receiver(self.root, "1-3", active=True)
        table = {(0, PAD1, 2): telemetry.ConnInfo(telemetry.OK, -70, 0, 0),
                 (1, PAD1, 2): telemetry.ConnInfo(telemetry.OK, -64, 0, 0)}
        self.make(objects(adapter_obj(0), pad_obj(0, PAD1)), table)
        snap = self.engine.snapshot()
        bt_key, usb_key = self.monitor(engine.bt_target(snap.adapters[0].identity, snap.devices[0]),
                                       engine.usb_target(self.engine.usb_snapshot().devices[0]))
        self.source.set(objects())
        self.engine.refresh()
        self.source.set(objects(adapter_obj(1), pad_obj(1, PAD1)))
        self.engine.refresh()
        self.assertEqual(self.engine.sample(bt_key).rssi, -64)
        self.assertEqual(self.engine.tracks[usb_key].disconnects, 0)
        self.assertTrue(any("hci0 → hci1" in t for t in self.texts()))

    def test_selection_round_trip_with_legacy_entries(self):
        fakesys.eightbitdo_receiver(self.root, "1-3", active=True)
        self.make(objects(adapter_obj(0), pad_obj(0, PAD1)))
        cfg = self.engine.cfg
        cfg.remember_selection = True
        cfg.selection_file = os.path.join(self.root, "state", "selection")
        snap = self.engine.snapshot()
        self.monitor(engine.bt_target(snap.adapters[0].identity, snap.devices[0]),
                     engine.usb_target(self.engine.usb_snapshot().devices[0]))
        self.engine.save_selection()
        self.assertEqual(self.engine.load_selection(), [f"bt:{PAD1}", "usb:1-3"])
        with open(cfg.selection_file, "w") as handle:
            handle.write(PAD2.lower() + "\nrubbish\n")                 # written by bc250-btmon
        self.assertEqual(self.engine.load_selection(), [f"bt:{PAD2}"])


# --------------------------------------------------------------------------
# Migration from bc250-btmon
# --------------------------------------------------------------------------

class TestMigration(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": os.path.join(self.home, "config"),
            "XDG_STATE_HOME": os.path.join(self.home, "state"),
            "XDG_DATA_HOME": os.path.join(self.home, "data")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def _old(self, *parts, text="x"):
        path = os.path.join(self.home, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def test_copies_and_keeps_the_old_files(self):
        old_cfg = self._old("config", "bc250-btmon", "config.ini", text="[monitor]\ninterval = 0.5\n")
        old_ev = self._old("state", "bc250-btmon", "events.jsonl", text='{"text": "old"}\n')
        self._old("state", "bc250-btmon", "selection", text=PAD1 + "\n")
        self._old("data", "bc250-btmon", "exports", "a.csv")
        done = config.migrate_legacy()
        self.assertEqual(len(done), 4)
        self.assertTrue(os.path.exists(old_cfg) and os.path.exists(old_ev))   # copied, not moved
        cfg, warnings = config.load()
        self.assertEqual((cfg.interval, warnings), (0.5, []))
        with open(os.path.join(self.home, "state", "bc250-wcm", "events.jsonl")) as handle:
            self.assertIn("old", handle.read())
        self.assertTrue(os.path.exists(os.path.join(self.home, "data", "bc250-wcm", "exports", "a.csv")))
        self.assertEqual(config.migrate_legacy(), [])                         # once only

    def test_existing_new_history_is_not_overwritten(self):
        self._old("state", "bc250-btmon", "events.jsonl", text="old\n")
        self._old("state", "bc250-wcm", "events.jsonl", text="new\n")
        config.migrate_legacy()
        with open(os.path.join(self.home, "state", "bc250-wcm", "events.jsonl")) as handle:
            self.assertEqual(handle.read(), "new\n")
        with open(os.path.join(self.home, "state", "bc250-wcm", "events-btmon.jsonl")) as handle:
            self.assertEqual(handle.read(), "old\n")

    def test_nothing_to_migrate(self):
        self.assertEqual(config.migrate_legacy(), [])

    def test_usb_settings(self):
        path = self._old("c.ini", text="[usb]\nenabled = no\ninput_activity = no\n")
        cfg, warnings = config.load(path)
        self.assertEqual((cfg.usb, cfg.input_activity, warnings), (False, False, []))


# --------------------------------------------------------------------------
# The interface with 1-4 devices, from a pseudo-terminal
# --------------------------------------------------------------------------

def run_tui(keys: list[str], cols: int = 150, rows: int = 45, warmup: float = 1.5) -> str:
    """Runs --demo in a pty and returns the screen as curses last drew it."""
    tmp = tempfile.mkdtemp()
    dump = os.path.join(tmp, "screen.txt")
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update(TERM="xterm-256color", LANG="en_US.UTF-8", WCM_DUMP=dump,
                          XDG_STATE_HOME=tmp, XDG_CONFIG_HOME=tmp, LINES=str(rows), COLUMNS=str(cols))
        os.execv(sys.executable, [sys.executable, os.path.join(TOOL_DIR, "bin", "bc250-wcm"), "--demo",
                                  "--demo-speed", "3", "-i", "0.25", "--lang", "en", "--no-sudo"])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                try:
                    if not os.read(fd, 65536):
                        return
                except OSError:
                    return

    pump(warmup)
    for key in keys:
        os.write(fd, key.encode())
        pump(0.4)
    pump(1.5)
    os.write(fd, b"q")
    pump(0.5)
    try:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
    except (ProcessLookupError, ChildProcessError):
        pass
    try:
        with open(dump, encoding="utf-8") as handle:
            return handle.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class TestInterface(unittest.TestCase):
    def test_one_to_four_devices(self):
        # demo rows by name: 1 beacon, 2 pad, 3 pad, 4 receiver, 5 headset, 6 xbox
        for picks in (["4"], ["2", "4"], ["2", "3", "4"], ["2", "3", "4", "6"]):
            screen = run_tui(picks + ["\r"])
            with self.subTest(picks=picks):
                for number in range(1, len(picks) + 1):
                    self.assertRegex(screen, rf"╭─ {number} ")
                self.assertNotRegex(screen, rf"╭─ {len(picks) + 1} ")
                self.assertIn("2.4 GHz receiver", screen)
                self.assertIn("not signal strength", screen)
                if len(picks) in (1, 3):
                    self.assertIn("Device events", screen)

    def test_picker_lists_bluetooth_and_usb(self):
        screen = run_tui([])
        self.assertIn("Controllers · Bluetooth 5 · USB 1", screen)
        self.assertIn("2dc8:", screen)
        self.assertIn("WIRELESS CONTROLLER MONITOR", screen)

    def test_layout_cells(self):
        self.assertEqual(len(grid_layout(4, 160, 44)), 4)
        self.assertEqual(sorted({x for _, x, _, _ in grid_layout(4, 160, 44)}), [0, 80])


class TestCli(unittest.TestCase):
    def test_list_and_json_include_usb(self):
        env = dict(os.environ, XDG_STATE_HOME=tempfile.mkdtemp(), XDG_CONFIG_HOME=tempfile.mkdtemp())
        out = subprocess.run([sys.executable, os.path.join(TOOL_DIR, "bin", "bc250-wcm"), "--demo",
                              "--json", "--lang", "en"], capture_output=True, text=True, timeout=30, env=env)
        doc = json.loads(out.stdout)
        self.assertEqual(doc["usb"][0]["usb_id"], "2dc8:301c")
        self.assertIsNone(doc["usb"][0]["rssi"])
        out = subprocess.run([sys.executable, os.path.join(TOOL_DIR, "bin", "bc250-wcm"), "--help"],
                             capture_output=True, text=True, timeout=30, env=env)
        self.assertIn("Wireless Controller Monitor", out.stdout)

    def test_compat_launcher(self):
        out = subprocess.run([sys.executable, os.path.join(TOOL_DIR, "bin", "bc250-btmon"), "--version"],
                             capture_output=True, text=True, timeout=30)
        self.assertIn("bc250-wcm", out.stdout)
        self.assertIn("bc250-wcm", out.stderr)


if __name__ == "__main__":
    unittest.main()

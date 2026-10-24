"""Model, BlueZ cache, engine scenarios, history, kernel log, export, config, i18n, layout."""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import helpers  # noqa: E402
from helpers import (HEADSET, MA530, PAD1, PAD2, PAD3, PAD4, FakeBackend, FakeSource,  # noqa: E402
                     NoKernel, adapter_obj, gatt_obj, headset_obj, objects, pad_obj)
from bc250wcm import (bluez, config, dbus, diagnostics, engine, export, history, i18n,  # noqa: E402
                        kernlog, model, telemetry)
from bc250wcm.tui import fmt_duration, grid_layout  # noqa: E402

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def setUpModule():
    i18n.set_language("en")


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------

class TestModel(unittest.TestCase):
    def test_parse_pads_headset_and_gatt(self):
        pad_path, pad = pad_obj(1, PAD1, battery=80)
        objs = objects(adapter_obj(1), (pad_path, pad), pad_obj(1, PAD2, connected=False),
                       headset_obj(1), gatt_obj(pad_path, 1, helpers.UUID_HID),
                       gatt_obj(pad_path, 2, helpers.UUID_BATT))
        adapters, devices = model.parse_managed_objects(objs)
        self.assertEqual(list(adapters), ["/org/bluez/hci1"])
        self.assertEqual(adapters["/org/bluez/hci1"].index, 1)
        d1 = devices[pad_path]
        self.assertEqual((d1.kind, d1.bearer, d1.connected_bearer, d1.battery),
                         ("gamepad", "LE", "LE", 80))
        self.assertEqual(len(d1.gatt_services), 2)
        hs = [d for d in devices.values() if d.address == HEADSET][0]
        self.assertEqual((hs.kind, hs.bearer, hs.connected_bearer), ("headset", "BR/EDR", None))

    def test_partial_and_invalid_objects_are_skipped(self):
        objs = {
            "/org/bluez/hci0": {model.IFACE_ADAPTER: {"Address": 5}},      # wrong type
            "/org/bluez/hci0/dev_X": {model.IFACE_DEVICE: {"Address": "not-a-mac"}},
            "/org/bluez/hci0/dev_Y": {model.IFACE_DEVICE: "garbage"},
            "/org/bluez/hci0/dev_Z": {model.IFACE_DEVICE: {"Address": PAD1, "Connected": "yes",
                                                           "RSSI": "loud", "UUIDs": "x"},
                                      model.IFACE_BATTERY: {"Percentage": 250}},
            42: {},
        }
        adapters, devices = model.parse_managed_objects(objs)
        self.assertEqual(adapters["/org/bluez/hci0"].address, "")
        self.assertEqual(len(devices), 1)
        dev = list(devices.values())[0]
        self.assertIsNone(dev.connected)
        self.assertIsNone(dev.rssi)
        self.assertIsNone(dev.battery)          # 250 % is not a battery level
        self.assertEqual(dev.uuids, [])
        self.assertEqual(model.parse_managed_objects("nonsense"), ({}, {}))

    def test_classify_kind(self):
        self.assertEqual(model.classify_kind(None, 0x03C4, None), "gamepad")
        self.assertEqual(model.classify_kind(None, 0x03C2, None), "mouse")
        self.assertEqual(model.classify_kind(None, None, 0x002508), "gamepad")
        self.assertEqual(model.classify_kind(None, None, 2360324), "audio")
        self.assertEqual(model.classify_kind("audio-headset", None, None), "headset")
        self.assertEqual(model.classify_kind(None, None, None), "?")

    def test_identity_and_duplicate_addresses(self):
        a = model.AdapterInfo("/org/bluez/hci0", "hci0", 0, MA530, usb_port="1-4")
        b = model.AdapterInfo("/org/bluez/hci1", "hci1", 1, MA530, usb_port="1-2")
        c = model.AdapterInfo("/org/bluez/hci2", "hci2", 2, "11:22:33:44:55:66")
        model.assign_identities([a, b, c])
        self.assertEqual(a.identity, f"{MA530}@1-4")
        self.assertEqual(b.identity, f"{MA530}@1-2")
        self.assertEqual(c.identity, "11:22:33:44:55:66")

    def test_resolve_after_reindex_and_address_change(self):
        old = model.AdapterInfo("/org/bluez/hci0", "hci0", 0, MA530, usb_id="2c4e:0115", usb_port="1-4")
        model.assign_identities([old])
        identity = old.identity
        new = model.AdapterInfo("/org/bluez/hci1", "hci1", 1, MA530, usb_id="2c4e:0115", usb_port="1-4")
        model.assign_identities([new])
        self.assertIs(model.resolve_adapter(identity, [new]), new)
        changed = model.AdapterInfo("/org/bluez/hci2", "hci2", 2, "00:00:00:00:00:01",
                                    usb_id="2c4e:0115", usb_port="1-4")
        model.assign_identities([changed])
        self.assertIsNone(model.resolve_adapter(identity, [changed]))
        self.assertIs(model.resolve_adapter(identity, [changed], ("2c4e:0115", "1-4")), changed)
        self.assertIsNone(model.resolve_adapter(identity, []))

    def test_sysfs_adapter_and_battery(self):
        root = tempfile.mkdtemp()
        usb = os.path.join(root, "devices", "usb1", "1-4")
        iface = os.path.join(usb, "1-4:1.0")
        os.makedirs(os.path.join(root, "drivers", "btusb"))
        os.makedirs(iface)
        for name, value in (("idVendor", "2c4e"), ("idProduct", "0115"), ("product", "MA530")):
            with open(os.path.join(usb, name), "w") as handle:
                handle.write(value + "\n")
        os.symlink(os.path.join(root, "drivers", "btusb"), os.path.join(iface, "driver"))
        cls = os.path.join(root, "class", "bluetooth", "hci1")
        os.makedirs(cls)
        os.symlink(iface, os.path.join(cls, "device"))
        info = model.read_sysfs_adapter("hci1", os.path.join(root, "class", "bluetooth"))
        self.assertEqual(info, {"usb_port": "1-4", "usb_id": "2c4e:0115", "usb_product": "MA530",
                                "driver": "btusb"})
        self.assertEqual(model.read_sysfs_adapter("hci9", os.path.join(root, "class", "bluetooth")), {})

        ps = os.path.join(root, "power_supply", f"hid-{PAD1.lower()}-battery")
        os.makedirs(ps)
        with open(os.path.join(ps, "capacity"), "w") as handle:
            handle.write("55\n")
        self.assertEqual(model.sysfs_battery(PAD1, os.path.join(root, "power_supply")), (55, "?"))
        self.assertIsNone(model.sysfs_battery(PAD2, os.path.join(root, "power_supply")))

    def test_modalias_and_service_names(self):
        self.assertEqual(model.parse_modalias("usb:v2DC8p310Bd0100"),
                         {"source": "usb", "vendor": 0x2DC8, "product": 0x310B, "version": 0x100})
        self.assertIsNone(model.parse_modalias("garbage"))
        self.assertEqual(model.service_name(helpers.UUID_HID), "Human Interface Device")
        self.assertIsNone(model.service_name("12345678-1234-1234-1234-123456789abc"))


# --------------------------------------------------------------------------
# BlueZ signal cache
# --------------------------------------------------------------------------

def _sig(path, iface, member, sig, body):
    return dbus.Message(type=dbus.SIGNAL, path=path, interface=iface, member=member,
                        signature=sig, body=body)


class TestBluezCache(unittest.TestCase):
    def test_signals_update_the_cache(self):
        cache = {}
        pad_path, pad = pad_obj(0, PAD1)
        self.assertTrue(bluez.apply_signal(cache, _sig("/", bluez.OM, "InterfacesAdded", "oa{sa{sv}}",
                                                       ["/org/bluez/hci0", adapter_obj(0)[1]])))
        bluez.apply_signal(cache, _sig("/", bluez.OM, "InterfacesAdded", "oa{sa{sv}}", [pad_path, pad]))
        self.assertTrue(bluez.apply_signal(cache, _sig(pad_path, bluez.PROPS, "PropertiesChanged",
                                                       "sa{sv}as",
                                                       [model.IFACE_DEVICE, {"Connected": False}, []])))
        self.assertFalse(cache[pad_path][model.IFACE_DEVICE]["Connected"])
        # removing the adapter takes its devices along
        bluez.apply_signal(cache, _sig("/", bluez.OM, "InterfacesRemoved", "oas",
                                       ["/org/bluez/hci0", [model.IFACE_ADAPTER]]))
        self.assertEqual(cache, {})

    def test_unknown_object_requests_resync(self):
        msg = _sig("/org/bluez/hci3/dev_X", bluez.PROPS, "PropertiesChanged", "sa{sv}as",
                   [model.IFACE_DEVICE, {"RSSI": -50}, []])
        self.assertIsNone(bluez.apply_signal({}, msg))
        other = _sig("/x", bluez.PROPS, "PropertiesChanged", "sa{sv}as", ["org.other", {}, []])
        self.assertFalse(bluez.apply_signal({}, other))
        self.assertIsNone(bluez.apply_signal({}, _sig("/", bluez.OM, "InterfacesAdded", "os",
                                                      ["/x", "not a dict"])))


# --------------------------------------------------------------------------
# Engine scenarios
# --------------------------------------------------------------------------

class EngineCase(unittest.TestCase):
    def make(self, objs, backend=None, status=bluez.STATUS_OK):
        self.source = FakeSource(objs, status)
        self.backend = backend or FakeBackend()
        cfg = config.Config()
        cfg.remember_selection = False
        cfg.query_timeout = 0.5
        self.events = history.EventLog()
        self.engine = engine.Engine(self.source, self.backend, cfg, self.events,
                                    follow_kernel=False, battery_reader=lambda a: None)
        self.engine.refresh()
        return self.engine

    def select(self, *addresses):
        snap = self.engine.snapshot()
        chosen = []
        for address in addresses:
            for dev in snap.devices:
                if dev.address == address:
                    adapter = snap.adapter_by_path(dev.adapter_path)
                    chosen.append((adapter.identity, dev))
        self.engine.monitored = []
        # set_monitored starts probe threads; tests drive sampling by hand instead
        for identity, dev in chosen:
            key = engine.device_key(identity, dev.address)
            track = history.Track(key, dev.address, identity, dev.display_name)
            self.engine.tracks[key] = track
            self.engine.monitored.append(key)
            self.engine._update_connection(track, snap, time.time())
        return list(self.engine.monitored)

    def texts(self):
        return [e.text for e in self.events.events()]


class TestEngineScenarios(EngineCase):
    def test_no_adapter(self):
        self.make(objects())
        found = diagnostics.diagnose(self.engine)
        self.assertTrue(any("No Bluetooth adapter" in text for _, text in found))

    def test_bluetoothd_not_running(self):
        self.make(objects(), status=bluez.STATUS_NO_BLUEZ)
        self.assertEqual(diagnostics.diagnose(self.engine)[0][0], diagnostics.ERROR)

    def test_no_devices(self):
        self.make(objects(adapter_obj(0)))
        self.assertEqual(self.engine.snapshot().devices, [])
        self.assertTrue(any("Adapter hci0" in text for text in self.texts()))

    def test_one_controller_rssi(self):
        backend = FakeBackend({(1, PAD1, 2): telemetry.ConnInfo(telemetry.OK, -68, 0, 0)})
        self.make(objects(adapter_obj(1), pad_obj(1, PAD1)), backend)
        (key,) = self.select(PAD1)
        sample = self.engine.sample(key)
        self.assertEqual((sample.state, sample.rssi, sample.source), (history.S_OK, -68, "conn"))
        track = self.engine.tracks[key]
        self.assertEqual(track.rssi_kind, "dBm")
        self.assertEqual(track.known_addr_type, 2)
        self.assertEqual(backend.calls, [(1, PAD1, 2)])          # LE Random, no guessing

    def test_four_controllers_independently(self):
        table = {(0, a, 2): telemetry.ConnInfo(telemetry.OK, -60 - i, 0, 0)
                 for i, a in enumerate((PAD1, PAD2, PAD3, PAD4))}
        self.make(objects(adapter_obj(0), *(pad_obj(0, a) for a in (PAD1, PAD2, PAD3, PAD4))),
                  FakeBackend(table))
        keys = self.select(PAD1, PAD2, PAD3, PAD4)
        self.assertEqual(len(keys), 4)
        values = [self.engine.sample(k).rssi for k in keys]
        self.assertEqual(sorted(values), [-63, -62, -61, -60])

    def test_slow_device_does_not_block_others(self):
        def slow():
            time.sleep(0.6)
            return telemetry.ConnInfo(telemetry.TIMEOUT, detail="slow")

        backend = FakeBackend({(0, PAD1, 2): slow,
                               (0, PAD2, 2): telemetry.ConnInfo(telemetry.OK, -60, 0, 0)})
        self.make(objects(adapter_obj(0), pad_obj(0, PAD1), pad_obj(0, PAD2)), backend)
        self.engine.cfg.interval = self.engine.interval = 0.1
        snap = self.engine.snapshot()
        chosen = [(snap.adapters[0].identity, d) for d in snap.devices]
        self.engine.set_monitored(chosen)
        try:
            time.sleep(0.55)
            fast = [t for t in self.engine.tracks.values() if t.address == PAD2][0]
            slow_track = [t for t in self.engine.tracks.values() if t.address == PAD1][0]
            self.assertGreaterEqual(fast.total_samples, 3)
            self.assertLessEqual(slow_track.total_samples, 1)
        finally:
            self.engine.stop()

    def test_device_disappears_and_returns(self):
        backend = FakeBackend({(0, PAD1, 2): telemetry.ConnInfo(telemetry.OK, -70, 0, 0)})
        self.make(objects(adapter_obj(0), pad_obj(0, PAD1)), backend)
        (key,) = self.select(PAD1)
        self.engine.sample(key)
        self.source.set(objects(adapter_obj(0)))
        self.engine.refresh()
        sample = self.engine.sample(key)
        self.assertEqual((sample.state, sample.rssi), (history.S_ABSENT, None))
        track = self.engine.tracks[key]
        self.assertEqual(track.disconnects, 1)
        self.assertIn("device gone", track.last_disconnect_reason)
        self.source.set(objects(adapter_obj(0), pad_obj(0, PAD1)))
        self.engine.refresh()
        self.assertEqual(self.engine.sample(key).rssi, -70)
        self.assertEqual(track.reconnects, 1)

    def test_reset_hci0_to_hci1_resumes_monitoring(self):
        table = {(0, PAD1, 2): telemetry.ConnInfo(telemetry.OK, -71, 0, 0),
                 (1, PAD1, 2): telemetry.ConnInfo(telemetry.OK, -66, 0, 0)}
        self.make(objects(adapter_obj(0), pad_obj(0, PAD1)), FakeBackend(table))
        (key,) = self.select(PAD1)
        self.assertEqual(self.engine.sample(key).rssi, -71)

        # the kernel log, as on the reference machine
        for line in ("Bluetooth: hci0: command 0x2042 tx timeout",
                     "Bluetooth: hci0: Opcode 0x2042 failed: -110",
                     "Bluetooth: hci0: Resetting usb device."):
            self.engine._on_kernel_event(kernlog.classify(line, time.time()))
        self.source.set(objects())                       # hci0 gone
        self.engine.refresh()
        self.assertEqual(self.engine.sample(key).state, history.S_NO_ADAPTER)
        self.source.set(objects(adapter_obj(1), pad_obj(1, PAD1, connected=False)))
        self.engine.refresh()
        self.assertEqual(self.engine.sample(key).state, history.S_DISCONNECTED)
        self.source.set(objects(adapter_obj(1), pad_obj(1, PAD1)))
        self.engine.refresh()
        sample = self.engine.sample(key)
        self.assertEqual((sample.state, sample.rssi), (history.S_OK, -66))
        track = self.engine.tracks[key]
        self.assertEqual(track.adapter_name, "hci1")
        self.assertEqual((track.disconnects, track.reconnects), (1, 1))
        texts = self.texts()
        self.assertTrue(any("re-registered: hci0 → hci1" in t for t in texts))
        self.assertTrue(any("LE Set Extended Scan Enable" in t for t in texts))
        self.assertTrue(any("ETIMEDOUT" in t for t in texts))
        counters = self.engine.kernel["hci0"]
        self.assertEqual((counters.cmd_timeouts, counters.usb_resets, counters.scan_timeouts), (1, 1, 1))
        found = " ".join(text for _, text in diagnostics.diagnose(self.engine))
        self.assertIn("hci0 → hci1", found)
        self.assertIn("LE scan", found)

    def test_reset_within_one_snapshot_is_a_move(self):
        self.make(objects(adapter_obj(0), pad_obj(0, PAD1)))
        self.select(PAD1)
        self.source.set(objects(adapter_obj(1), pad_obj(1, PAD1)))
        self.engine.refresh()
        self.assertTrue(any("hci0 → hci1" in t for t in self.texts()))

    def test_adapter_back_with_another_address(self):
        self.make(objects(adapter_obj(0), pad_obj(0, PAD1)))
        (key,) = self.select(PAD1)
        self.source.set(objects())
        self.engine.refresh()
        self.source.set(objects(adapter_obj(1, address="00:11:22:33:44:55"), pad_obj(1, PAD1)))
        self.engine.refresh()
        self.assertEqual(self.engine.tracks[key].adapter_identity, "00:11:22:33:44:55")
        self.assertTrue(any("different address" in t for t in self.texts()))

    def test_no_rssi_backend(self):
        self.make(objects(adapter_obj(0), pad_obj(0, PAD1, rssi=-80)),
                  telemetry.NullBackend("needs privileges"))
        (key,) = self.select(PAD1)
        sample = self.engine.sample(key)
        # Device1.RSSI is stale outside discovery and is not passed off as the link RSSI
        self.assertEqual((sample.state, sample.rssi), (history.S_NO_RSSI, None))
        self.assertIn("needs privileges", sample.detail)
        found = " ".join(text for _, text in diagnostics.diagnose(self.engine))
        self.assertIn("No link RSSI", found)

    def test_advertising_rssi_only_while_discovering(self):
        self.make(objects(adapter_obj(0, discovering=True), pad_obj(0, PAD1, rssi=-80)),
                  telemetry.NullBackend())
        (key,) = self.select(PAD1)
        sample = self.engine.sample(key)
        self.assertEqual((sample.rssi, sample.source), (-80, history.SRC_ADV))

    def test_invalid_index(self):
        backend = FakeBackend(default=telemetry.ConnInfo(telemetry.INVALID_INDEX, detail="Invalid Index"))
        self.make(objects(adapter_obj(0), pad_obj(0, PAD1)), backend)
        (key,) = self.select(PAD1)
        sample = self.engine.sample(key)
        self.assertEqual((sample.state, sample.rssi), (history.S_NO_RSSI, None))
        self.assertTrue(any("invalid adapter index" in t for t in self.texts()))
        self.assertTrue(any("0x11" in t for _, t in diagnostics.diagnose(self.engine)))

    def test_hci_timeout(self):
        backend = FakeBackend(default=telemetry.ConnInfo(telemetry.TIMEOUT, detail="Timeout"))
        self.make(objects(adapter_obj(0), pad_obj(0, PAD1)), backend)
        (key,) = self.select(PAD1)
        for _ in range(3):
            self.assertEqual(self.engine.sample(key).state, history.S_TIMEOUT)
        track = self.engine.tracks[key]
        self.assertEqual(track.timeouts, 3)
        self.assertEqual(track.availability(), 0.0)
        # the problem is logged once, not on every sample
        self.assertEqual(sum("timeout" in t for t in self.texts()), 1)

    def test_insufficient_permissions(self):
        backend = FakeBackend(default=telemetry.ConnInfo(telemetry.PERMISSION, detail="Permission Denied"))
        self.make(objects(adapter_obj(0), pad_obj(0, PAD1)), backend)
        (key,) = self.select(PAD1)
        self.engine.sample(key)
        found = diagnostics.diagnose(self.engine)
        self.assertTrue(any("0x14" in t and lvl == diagnostics.ERROR for lvl, t in found))

    def test_public_address_tries_bredr_then_le(self):
        backend = FakeBackend({(0, HEADSET, 0): telemetry.ConnInfo(telemetry.OK, -4, 0, 4)})
        hs_path, hs = headset_obj(0, connected=True)
        hs.pop(model.IFACE_BEARER_BREDR)         # an older BlueZ without bearer objects
        self.make(objects(adapter_obj(0), (hs_path, hs)), backend)
        (key,) = self.select(HEADSET)
        sample = self.engine.sample(key)
        self.assertEqual(sample.rssi, -4)
        track = self.engine.tracks[key]
        self.assertEqual(track.rssi_kind, "dB")      # BR/EDR: relative, not dBm
        self.assertTrue(any("golden" in t for _, t in diagnostics.diagnose(self.engine)))

    def test_disconnect_is_a_gap_not_zero(self):
        backend = FakeBackend({(0, PAD1, 2): telemetry.ConnInfo(telemetry.OK, -70, 0, 0)})
        self.make(objects(adapter_obj(0), pad_obj(0, PAD1)), backend)
        (key,) = self.select(PAD1)
        self.engine.sample(key)
        self.source.set(objects(adapter_obj(0), pad_obj(0, PAD1, connected=False)))
        self.engine.refresh()
        sample = self.engine.sample(key)
        self.assertEqual((sample.state, sample.rssi), (history.S_DISCONNECTED, None))
        track = self.engine.tracks[key]
        self.assertEqual((track.stats.min, track.stats.max), (-70, -70))

    def test_property_changes_are_recorded(self):
        self.make(objects(adapter_obj(0), pad_obj(0, PAD1, battery=80)))
        (key,) = self.select(PAD1)
        self.source.set(objects(adapter_obj(0), pad_obj(0, PAD1, battery=75)))
        self.engine.refresh()
        change = list(self.engine.tracks[key].changes)[-1]
        self.assertEqual((change.field, change.old, change.new), ("battery", 80, 75))

    def test_twin_pads_get_distinct_names(self):
        self.make(objects(adapter_obj(0), pad_obj(0, PAD1), pad_obj(0, PAD2)))
        snap = self.engine.snapshot()
        self.engine.set_monitored([(snap.adapters[0].identity, d) for d in snap.devices])
        try:
            names = sorted(t.name for t in self.engine.tracks.values())
            self.assertEqual(len(set(names)), 2)
            self.assertTrue(names[0].endswith("B2:C3") or names[0].endswith("E5:F6"))
        finally:
            self.engine.stop()

    def test_mgmt_disconnect_reason_reaches_the_track(self):
        self.make(objects(adapter_obj(0), pad_obj(0, PAD1)))
        (key,) = self.select(PAD1)
        self.engine._on_mgmt_event({"event": "device_disconnected", "index": 0,
                                    "address": PAD1, "type": 2, "reason": 1})
        self.source.set(objects(adapter_obj(0), pad_obj(0, PAD1, connected=False)))
        self.engine.refresh()
        self.assertEqual(self.engine.tracks[key].last_disconnect_reason, "connection timeout")

    def test_discovery_toggle_is_logged(self):
        self.make(objects(adapter_obj(0)))
        adapter = self.engine.snapshot().adapters[0]
        self.assertIsNone(self.engine.set_discovery(adapter, True))
        self.assertEqual(self.source.discovery_calls, [("/org/bluez/hci0", True)])

    def test_diff_snapshots(self):
        a = bluez.build_snapshot(objects(adapter_obj(0), pad_obj(0, PAD1)), "ok", None, 1,
                                 helpers.fake_sysfs)
        b = bluez.build_snapshot(objects(adapter_obj(0, powered=False), pad_obj(0, PAD1, connected=False)),
                                 "ok", None, 2, helpers.fake_sysfs)
        kinds = {(c["kind"], c.get("field")) for c in engine.diff_snapshots(a, b)}
        self.assertIn(("adapter-prop", "powered"), kinds)
        self.assertIn(("device-prop", "connected"), kinds)
        first = engine.diff_snapshots(None, a)
        self.assertEqual([c["kind"] for c in first][:2], ["status", "adapter-added"])


# --------------------------------------------------------------------------
# History and statistics
# --------------------------------------------------------------------------

class TestHistory(unittest.TestCase):
    def test_running_stats(self):
        stats = history.RunningStats()
        for value in (-60, -70, -80):
            stats.add(value)
        self.assertEqual((stats.min, stats.max), (-80, -60))
        self.assertAlmostEqual(stats.avg, -70.0)
        self.assertAlmostEqual(stats.stdev, 10.0)

    def test_connection_accounting(self):
        track = history.Track("k", PAD1, MA530, "pad")
        self.assertIsNone(track.set_connected(False, 100.0))
        self.assertEqual(track.set_connected(True, 105.0), "connect")
        self.assertEqual((track.reconnects, track.downtime_total), (0, 0.0))
        self.assertEqual(track.set_connected(False, 160.0, "timeout"), "disconnect")
        self.assertIsNone(track.set_connected(False, 161.0))
        self.assertEqual(track.set_connected(True, 170.0), "connect")
        self.assertEqual((track.disconnects, track.reconnects), (1, 1))
        self.assertAlmostEqual(track.downtime_total, 10.0)
        self.assertAlmostEqual(track.connected_time(180.0), 55.0 + 10.0)
        self.assertEqual(track.last_disconnect_reason, "timeout")

    def test_binned_keeps_gaps_and_time(self):
        track = history.Track("k", PAD1, MA530, "pad")
        base = 1000.0
        for i, (state, rssi) in enumerate([("ok", -60), ("ok", -62), ("disconnected", None),
                                           ("disconnected", None), ("ok", -65)]):
            track.add(history.Sample(base + i + 0.5, state, rssi))
        bins = track.binned(8, 1.0, base + 5)
        self.assertEqual(bins[:3], [(None, None)] * 3)
        self.assertEqual(bins[3:], [(-60, "ok"), (-62, "ok"), (None, "disconnected"),
                                    (None, "disconnected"), (-65, "ok")])
        self.assertEqual(track.availability(), 100.0)
        self.assertEqual(track.connected_share(), 60.0)

    def test_event_log_file(self):
        path = os.path.join(tempfile.mkdtemp(), "sub", "events.jsonl")
        log = history.EventLog(path=path)
        log.add(history.LEVEL_WARN, history.CAT_ADAPTER, "gone", adapter="x")
        with open(path) as handle:
            entry = json.loads(handle.readline())
        self.assertEqual((entry["level"], entry["text"], entry["adapter"]), ("warn", "gone", "x"))
        bad = history.EventLog(path="/proc/forbidden/events.jsonl")
        bad.add(history.LEVEL_INFO, history.CAT_APP, "x")
        self.assertIsNotNone(bad.write_error)


# --------------------------------------------------------------------------
# Kernel log
# --------------------------------------------------------------------------

class TestKernelLog(unittest.TestCase):
    def test_reference_machine_lines(self):
        ev = kernlog.classify("Bluetooth: hci0: command 0x2042 tx timeout", 1.0)
        self.assertEqual((ev.kind, ev.hci, ev.opcode, ev.opcode_name),
                         (kernlog.K_CMD_TIMEOUT, 0, 0x2042, "LE Set Extended Scan Enable"))
        ev = kernlog.classify("Bluetooth: hci0: Opcode 0x2042 failed: -110", 1.0)
        self.assertEqual((ev.kind, ev.errno), (kernlog.K_CMD_FAILED, -110))
        ev = kernlog.classify("Bluetooth: hci0: Resetting usb device.", 1.0)
        self.assertEqual(ev.kind, kernlog.K_USB_RESET)
        ev = kernlog.classify("Bluetooth: hci1: RTL: loading rtl_bt/rtl8761bu_fw.bin", 1.0)
        self.assertEqual((ev.kind, ev.hci), (kernlog.K_FIRMWARE, 1))
        self.assertEqual(kernlog.classify("usb 1-4: USB disconnect, device number 5", 1.0).usb_port, "1-4")
        self.assertIsNone(kernlog.classify("nvme0: temperature ok", 1.0))

    def test_journal_json(self):
        line = json.dumps({"MESSAGE": "Bluetooth: hci1: Hardware error 0x0c",
                           "__REALTIME_TIMESTAMP": "1700000000000000"})
        ev = kernlog.parse_journal_line(line, session_start=1800000000.0)
        self.assertEqual((ev.kind, ev.historic), (kernlog.K_HW_ERROR, True))
        raw = json.dumps({"MESSAGE": list(b"Bluetooth: hci0: x\xff")})
        self.assertEqual(kernlog.parse_journal_line(raw, 0).hci, 0)
        self.assertIsNone(kernlog.parse_journal_line("{not json", 0))
        self.assertIsNone(kernlog.parse_journal_line(json.dumps({"MESSAGE": 5}), 0))

    def test_follower_reports_missing_journalctl(self):
        follower = kernlog.KernelLogFollower(lambda e: None, journalctl="")
        follower.journalctl = None
        follower.start()
        self.assertIn("journalctl", follower.problem)


# --------------------------------------------------------------------------
# Export, configuration, translations, layout
# --------------------------------------------------------------------------

class TestExport(unittest.TestCase):
    def _track(self):
        track = history.Track("k", PAD1, MA530, "8BitDo, \"quoted\"")
        track.rssi_kind = "dBm"
        track.add(history.Sample(1000.0, history.S_OK, -70, "conn", 0, 0))
        track.add(history.Sample(1001.0, history.S_DISCONNECTED))
        return track

    def test_csv_keeps_gaps_empty(self):
        path = os.path.join(tempfile.mkdtemp(), "out.csv")
        self.assertEqual(export.export_csv([self._track()], path), 2)
        with open(path, newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["rssi"], "-70")
        self.assertEqual(rows[1]["rssi"], "")
        self.assertEqual(rows[1]["state"], "disconnected")
        self.assertEqual(rows[0]["device"], "8BitDo, \"quoted\"")

    def test_json(self):
        path = os.path.join(tempfile.mkdtemp(), "out.json")
        log = history.EventLog()
        log.add(history.LEVEL_INFO, history.CAT_APP, "x")
        export.export_json([self._track()], log, path, {"interval_s": 1.0})
        with open(path) as handle:
            doc = json.load(handle)
        summary = doc["devices"][0]["summary"]
        self.assertEqual((summary["rssi_min"], summary["samples_total"]), (-70, 2))
        self.assertIsNone(doc["devices"][0]["samples"][1]["rssi"] or None)
        self.assertEqual(doc["interval_s"], 1.0)

    def test_recorder(self):
        path = os.path.join(tempfile.mkdtemp(), "rec.csv")
        rec = export.CsvRecorder(path)
        track = self._track()
        for sample in track.snapshot_samples():
            rec(track, sample)
        rec.close()
        with open(path) as handle:
            self.assertEqual(len(handle.readlines()), 3)


class TestConfig(unittest.TestCase):
    def test_defaults_and_overrides(self):
        path = os.path.join(tempfile.mkdtemp(), "c.ini")
        with open(path, "w") as handle:
            handle.write("[monitor]\ninterval = 0.5\nbogus = 1\n[rssi]\nbackend = btmgmt\n"
                         "[thresholds]\ngood = -50\nfair = -40\n[ui]\ncolors = rainbow\n")
        cfg, warnings = config.load(path)
        self.assertEqual((cfg.interval, cfg.backend, cfg.colors), (0.5, "btmgmt", "auto"))
        self.assertEqual((cfg.good, cfg.fair, cfg.weak), (-60, -70, -80))   # reset: not ordered
        self.assertEqual(len(warnings), 3)
        self.assertEqual(config.load(os.path.join(tempfile.mkdtemp(), "none.ini"))[1], [])

    def test_example_parses_cleanly(self):
        path = os.path.join(tempfile.mkdtemp(), "e.ini")
        with open(path, "w") as handle:
            handle.write(config.EXAMPLE)
        self.assertEqual(config.load(path)[1], [])

    def test_levels(self):
        cfg = config.Config()
        self.assertEqual([cfg.rssi_level(v) for v in (-55, -65, -75, -95, None)],
                         ["good", "fair", "weak", "poor", None])


class TestTranslations(unittest.TestCase):
    def test_every_key_in_the_source_exists(self):
        # literal keys only; t("sort." + name) style families are checked below
        pattern = re.compile(r"""\bt\(\s*["']([a-z0-9_.?-]*[a-z0-9_?-])["']\s*[,)]""")
        missing = set()
        package = os.path.join(TOOL_DIR, "bc250wcm")
        for name in os.listdir(package):
            if name.endswith(".py"):
                with open(os.path.join(package, name), encoding="utf-8") as handle:
                    for key in pattern.findall(handle.read()):
                        if key not in i18n.keys():
                            missing.add(f"{name}: {key}")
        self.assertEqual(missing, set())

    def test_both_languages_filled_and_placeholders_match(self):
        for key, (pl, en) in i18n._MESSAGES.items():
            fields_pl = set(re.findall(r"{(\w+)}", pl))
            fields_en = set(re.findall(r"{(\w+)}", en))
            self.assertEqual(fields_pl, fields_en, key)
            if key != "col.sel":
                self.assertTrue(pl and en, key)

    def test_dynamic_status_keys(self):
        for status in (telemetry.OK, telemetry.NOT_CONNECTED, telemetry.PERMISSION, telemetry.TIMEOUT,
                       telemetry.INVALID_INDEX, telemetry.NOT_POWERED, telemetry.UNSUPPORTED,
                       telemetry.INVALID_PARAMS, telemetry.UNAVAILABLE, telemetry.ERROR):
            self.assertIn("status." + status, i18n.keys())
        for status in (bluez.STATUS_OK, bluez.STATUS_NO_BUS, bluez.STATUS_NO_BLUEZ,
                       bluez.STATUS_ERROR, bluez.STATUS_STARTING):
            self.assertIn("bluez." + status, i18n.keys())
        from bc250wcm import tui
        families = {"filter.": tui.FILTERS, "sort.": tui.SORTS, "logfilter.": tui.LOG_FILTERS,
                    "cat.": (history.CAT_ADAPTER, history.CAT_DEVICE, history.CAT_KERNEL,
                             history.CAT_BACKEND, history.CAT_APP),
                    "band.": ("good", "fair", "weak", "poor"),
                    "detail.src.": (history.SRC_CONN, history.SRC_ADV),
                    "col.": ("num", "sel", "name", "mac", "type", "adapter", "conn", "paired",
                             "bonded", "trusted", "rssi", "battery")}
        for prefix, names in families.items():
            for name in names:
                self.assertIn(prefix + name, i18n.keys())


class TestLayout(unittest.TestCase):
    def test_four_panels_make_a_grid_when_they_fit(self):
        rects = grid_layout(4, 160, 44)
        self.assertEqual(len({(y, x) for y, x, _, _ in rects}), 4)
        self.assertEqual(sorted({x for _, x, _, _ in rects}), [0, 80])

    def test_small_window_stacks(self):
        rects = grid_layout(4, 60, 30)
        self.assertEqual({x for _, x, _, _ in rects}, {0})

    def test_panels_cover_the_area(self):
        for count in (1, 2, 3, 4):
            for width, height in ((80, 24), (200, 60), (41, 9)):
                rects = grid_layout(count, width, height)
                area = sum(h * w for _, _, h, w in rects)
                self.assertEqual(area, width * height, (count, width, height))

    def test_durations(self):
        self.assertEqual([fmt_duration(s) for s in (None, 5, 65, 3700)], ["—", "5s", "1m05s", "1h01m"])


if __name__ == "__main__":
    unittest.main()

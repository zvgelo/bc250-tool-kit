"""Tests of the parsing and formatting logic (no BC-250 required)."""

from __future__ import annotations

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bc250mon import collector as coll  # noqa: E402
from bc250mon import i18n, sysfs, widgets  # noqa: E402
from bc250mon.demo import DemoCollector  # noqa: E402


def setUpModule():
    # tests that check message content must know which language they are in
    i18n.set_language("pl")


class TestDpmParsing(unittest.TestCase):
    def test_parses_levels_and_active_marker(self):
        levels = sysfs._parse_dpm("0: 1000Mhz\n1: 1500Mhz *\n2: 2000Mhz\n")
        self.assertEqual([l.mhz for l in levels], [1000, 1500, 2000])
        self.assertEqual([l.active for l in levels], [False, True, False])

    def test_ignores_garbage(self):
        self.assertEqual(sysfs._parse_dpm(None), [])
        self.assertEqual(sysfs._parse_dpm("brak danych"), [])

    def test_eight_core_unlock_scaling_bug_is_flagged(self):
        # after the 8-core unlock the kernel reports "15Mhz" instead of 1500
        snap = _snapshot_with_sclk("0: 10Mhz\n1: 15Mhz *\n2: 20Mhz\n")
        self.assertTrue(snap.gpu.sclk_suspect)
        self.assertEqual(snap.gpu_clock_mhz, 1500)

    def test_normal_clocks_are_not_rescaled(self):
        snap = _snapshot_with_sclk("0: 1000Mhz\n1: 1500Mhz *\n")
        self.assertFalse(snap.gpu.sclk_suspect)
        self.assertEqual(snap.gpu_clock_mhz, 1500)


class TestUnreliableGpuClock(unittest.TestCase):
    """The 8-core unlock breaks pp_dpm_sclk in two different ways."""

    def _snap(self, raw):
        snap = _snapshot(is_bc250=True)
        snap.gpu.sclk = sysfs._parse_dpm(raw)
        snap.gpu.sclk_suspect = (bool(snap.gpu.sclk)
                                 and max(l.mhz for l in snap.gpu.sclk) < 100)
        current = sysfs.active_level(snap.gpu.sclk)
        snap.gpu.sclk_unreliable = (
            not snap.gpu.sclk_suspect
            and current is not None
            and current.mhz < 100
            and any(level.mhz >= 100 for level in snap.gpu.sclk))
        return snap

    def test_gpu_metrics_is_used_when_the_telemetry_is_healthy(self):
        snap = self._snap("0: 1000Mhz\n1: 15Mhz *\n2: 2000Mhz\n")
        snap.gpu.gfxclk_average = 1493
        self.assertEqual(snap.gpu_clock_mhz, 1493)
        self.assertTrue(any("gpu_metrics" in n for n in coll.build_notes(snap)))

    def test_active_level_below_the_table_minimum_is_not_reported(self):
        # observed on the reference board: 0: 1000Mhz  1: 15Mhz *  2: 2000Mhz
        snap = self._snap("0: 1000Mhz\n1: 15Mhz *\n2: 2000Mhz\n")
        self.assertTrue(snap.gpu.sclk_unreliable)
        self.assertFalse(snap.gpu.sclk_suspect)
        # no invented scale factor — 15 x 100 would be right, 46 x 100 would not
        self.assertIsNone(snap.gpu_clock_mhz)

    def test_that_case_is_explained_in_the_diagnostics(self):
        snap = self._snap("0: 1000Mhz\n1: 15Mhz *\n2: 2000Mhz\n")
        notes = coll.build_notes(snap)
        self.assertTrue(any("pp_dpm_sclk" in n and "1000" in n for n in notes))

    def test_a_uniformly_scaled_table_is_still_corrected(self):
        snap = self._snap("0: 10Mhz\n1: 15Mhz *\n2: 20Mhz\n")
        self.assertTrue(snap.gpu.sclk_suspect)
        self.assertFalse(snap.gpu.sclk_unreliable)
        self.assertEqual(snap.gpu_clock_mhz, 1500)

    def test_a_healthy_table_is_left_alone(self):
        snap = self._snap("0: 1000Mhz\n1: 1500Mhz *\n2: 2000Mhz\n")
        self.assertFalse(snap.gpu.sclk_unreliable)
        self.assertEqual(snap.gpu_clock_mhz, 1500)
        self.assertEqual(coll.build_notes(snap), [])


class TestCpuList(unittest.TestCase):
    def test_ranges_and_singles(self):
        self.assertEqual(sysfs._count_cpu_list("0-5"), 6)
        self.assertEqual(sysfs._count_cpu_list("0,2-3"), 3)
        self.assertEqual(sysfs._count_cpu_list("0"), 1)
        self.assertEqual(sysfs._count_cpu_list(None), 0)


class TestGpuActivity(unittest.TestCase):
    def _metrics(self, activity):
        return sysfs.GpuMetrics(format_revision=2, content_revision=2,
                                gfx_activity=activity, average_gfxclk=0,
                                current_gfxclk=0)

    def _decode(self, activity):
        real = sysfs.read_int
        sysfs.read_int = lambda path: None      # no usable gpu_busy_percent
        try:
            return sysfs._read_gpu_activity("/dev/null", self._metrics(activity))
        finally:
            sysfs.read_int = real

    def test_uninitialised_field_is_reported_not_returned_as_655_percent(self):
        # the heart of the "MangoHud shows 655%" bug
        value, source, note = self._decode(0xFFFF)
        self.assertIsNone(value)
        self.assertIsNone(source)
        self.assertIn("governora", note)

    def test_plain_percent_is_used_as_is(self):
        value, source, _ = self._decode(42)
        self.assertEqual(value, 42.0)
        self.assertEqual(source, "gpu_metrics")

    def test_hundredths_are_scaled_down(self):
        value, _, _ = self._decode(5000)
        self.assertEqual(value, 50.0)

    def test_absent_table_is_reported(self):
        real = sysfs.read_int
        sysfs.read_int = lambda path: None
        try:
            value, source, note = sysfs._read_gpu_activity("/dev/null", None)
        finally:
            sysfs.read_int = real
        self.assertIsNone(value)
        self.assertIn("gpu_metrics", note)


class TestVramSplit(unittest.TestCase):
    """mem_info_vram_total is the guaranteed minimum, not the ceiling."""

    def _snap(self, vram_total, vram_used=0, gtt_total=0, gtt_used=0):
        snap = _snapshot(is_bc250=True)
        snap.gpu.vram_total, snap.gpu.vram_used = vram_total, vram_used
        snap.gpu.gtt_total, snap.gpu.gtt_used = gtt_total, gtt_used
        return snap

    def test_small_split_is_recognised(self):
        self.assertTrue(self._snap(512 * 1024 ** 2).small_vram_split)
        self.assertTrue(self._snap(1024 ** 3).small_vram_split)
        self.assertFalse(self._snap(8 * 1024 ** 3).small_vram_split)

    def test_gpu_memory_adds_the_carveout_and_gtt(self):
        snap = self._snap(512 * 1024 ** 2, 400 * 1024 ** 2,
                          8 * 1024 ** 3, 1024 ** 3)
        used, total = snap.gpu_memory_total
        self.assertEqual(used, 400 * 1024 ** 2 + 1024 ** 3)
        self.assertEqual(total, 512 * 1024 ** 2 + 8 * 1024 ** 3)

    def test_small_split_is_explained_once(self):
        snap = self._snap(512 * 1024 ** 2, 417 * 1024 ** 2, 8 * 1024 ** 3)
        matching = [n for n in coll.build_notes(snap) if "VRAM" in n]
        self.assertEqual(len(matching), 1)

    def test_a_large_split_needs_no_explanation(self):
        snap = self._snap(8 * 1024 ** 3, 1024 ** 3, 8 * 1024 ** 3)
        self.assertFalse([n for n in coll.build_notes(snap) if "VRAM" in n])


REFERENCE_CONFIG = """
[timing.intervals]
sample = 250
adjust = 100_000

[frequency-range]
min = 1000    # MHz
max = 1850   # MHz

[[safe-points]]
frequency = 500
voltage = 700

[[safe-points]]
frequency = 1500
voltage = 900

[[safe-points]]
frequency = 1600
voltage = 900

[[safe-points]]
frequency = 1700
voltage = 910

[[safe-points]]
frequency = 1850
voltage = 920

# [[safe-points]]
# frequency = 2300
# voltage = 1110
"""


class TestGovernorCurve(unittest.TestCase):
    """The governor sets frequency and voltage together, so vddgfx points back."""

    def setUp(self):
        self.config = sysfs.parse_governor_config(REFERENCE_CONFIG, "test")

    def test_parses_range_and_points(self):
        self.assertEqual((self.config.min_mhz, self.config.max_mhz), (1000, 1850))
        self.assertEqual(self.config.safe_points,
                         [(500, 700), (1500, 900), (1600, 900), (1700, 910), (1850, 920)])

    def test_commented_out_points_are_ignored(self):
        self.assertNotIn((2300, 1110), self.config.safe_points)

    def test_interpolates_between_points(self):
        # measured under load on the reference board
        self.assertEqual(sysfs.clock_for_voltage(self.config.safe_points, 0.912),
                         (1730, 1730))

    def test_a_shared_voltage_yields_a_range(self):
        # 1500 and 1600 MHz both sit at 900 mV, so a range is the honest answer
        self.assertEqual(sysfs.clock_for_voltage(self.config.safe_points, 0.900),
                         (1500, 1600))

    def test_voltage_off_the_curve_gives_nothing(self):
        self.assertIsNone(sysfs.clock_for_voltage(self.config.safe_points, 1.200))
        self.assertIsNone(sysfs.clock_for_voltage(self.config.safe_points, 0.600))
        self.assertIsNone(sysfs.clock_for_voltage(self.config.safe_points, None))
        self.assertIsNone(sysfs.clock_for_voltage([], 0.912))

    def test_busctl_output_is_parsed(self):
        self.assertEqual(sysfs.parse_busctl_numbers("u 1000\nu 1850\n"), [1000, 1850])
        self.assertEqual(sysfs.parse_busctl_numbers('b false\ns "x"\nu 500\n'), [500])
        self.assertEqual(sysfs.parse_busctl_numbers(""), [])

    def test_live_range_takes_precedence_over_the_file(self):
        self.config.live_min_mhz, self.config.live_max_mhz = 1200, 1600
        self.assertEqual(self.config.effective_range, (1200, 1600))
        self.assertTrue(self.config.range_overridden)

    def test_matching_live_range_is_not_an_override(self):
        self.config.live_min_mhz, self.config.live_max_mhz = 1000, 1850
        self.assertFalse(self.config.range_overridden)

    def test_without_dbus_the_file_range_is_used(self):
        self.assertEqual(self.config.effective_range, (1000, 1850))
        self.assertFalse(self.config.range_overridden)

    def test_a_runtime_override_is_reported(self):
        snap = _snapshot(is_bc250=True)
        snap.governor = self.config
        snap.governor.live_min_mhz, snap.governor.live_max_mhz = 1500, 1500
        self.assertTrue(any("1500" in n for n in coll.build_notes(snap)))

    def test_snapshot_exposes_the_estimate(self):
        snap = _snapshot(is_bc250=True)
        snap.governor = self.config
        snap.chips = [sysfs.HwmonChip("/x", "amdgpu", [
            sysfs.Channel(key="amdgpu/in0", label="vddgfx", kind="volt",
                          value=0.912, unit="V")])]
        self.assertEqual(snap.estimated_gpu_clock, (1730, 1730))

    def test_no_governor_config_means_no_estimate(self):
        snap = _snapshot(is_bc250=True)
        self.assertIsNone(snap.estimated_gpu_clock)


class TestAveragedClock(unittest.TestCase):
    """Average.GfxclkFrequency is the only working GFX clock on this ASIC."""

    def _metrics(self, average, current=90):
        return sysfs.GpuMetrics(format_revision=2, content_revision=2,
                                gfx_activity=0, average_gfxclk=average,
                                current_gfxclk=current)

    def test_values_measured_on_the_reference_board_are_accepted(self):
        # umr dumps: 505 MHz at 36% activity, 931 at 73%, 932 at 56% — all while
        # the instantaneous field stayed pinned below 100
        for average in (505, 931, 932):
            self.assertEqual(sysfs.usable_average_clock(self._metrics(average)), average)

    def test_a_low_average_at_idle_is_still_a_reading(self):
        # an average includes the gated gaps, so at idle it is genuinely small
        self.assertEqual(sysfs.usable_average_clock(self._metrics(45)), 45)

    def test_unpopulated_and_absurd_values_are_rejected(self):
        self.assertIsNone(sysfs.usable_average_clock(self._metrics(0)))
        self.assertIsNone(sysfs.usable_average_clock(self._metrics(0xFFFF)))
        self.assertIsNone(sysfs.usable_average_clock(None))


class TestGpuMetricsTable(unittest.TestCase):
    """gpu_metrics_v2_2 is the second, independent source of the GFX clock."""

    def _blob(self, activity=0, average_gfxclk=0, current_gfxclk=0,
              fmt=2, content=2, size=128):
        buf = bytearray(size)
        struct.pack_into("<HBB", buf, 0, size, fmt, content)
        struct.pack_into("<H", buf, 0x1C, activity)
        struct.pack_into("<H", buf, 0x40, average_gfxclk)
        struct.pack_into("<H", buf, 0x4C, current_gfxclk)
        return bytes(buf)

    def _read(self, blob):
        real = sysfs.read_bytes
        sysfs.read_bytes = lambda path, size=4096: blob
        try:
            return sysfs.read_gpu_metrics("/dev/null")
        finally:
            sysfs.read_bytes = real

    def test_parses_the_fields_we_rely_on(self):
        metrics = self._read(self._blob(activity=4200, average_gfxclk=1493,
                                        current_gfxclk=15))
        self.assertEqual(metrics.format_revision, 2)
        self.assertEqual(metrics.gfx_activity, 4200)
        self.assertEqual(metrics.average_gfxclk, 1493)
        self.assertEqual(metrics.current_gfxclk, 15)

    def test_rejects_a_table_in_another_format(self):
        self.assertIsNone(self._read(self._blob(fmt=1)))

    def test_rejects_a_truncated_table(self):
        self.assertIsNone(self._read(b"\x80\x00\x02\x02"))

    def test_clock_plausibility_gate(self):
        self.assertTrue(sysfs.plausible_clock(1500))
        self.assertTrue(sysfs.plausible_clock(350))     # patched-kernel minimum
        self.assertFalse(sysfs.plausible_clock(15))     # the broken reading
        self.assertFalse(sysfs.plausible_clock(100))    # the cap it is pinned to
        self.assertFalse(sysfs.plausible_clock(4600))   # what x100 would invent
        self.assertFalse(sysfs.plausible_clock(None))


class TestFormatting(unittest.TestCase):
    def test_voltage_switches_to_millivolts_below_one_volt(self):
        self.assertEqual(coll.format_value(0.699, "V"), "699 mV")
        self.assertEqual(coll.format_value(1.10, "V"), "1.10 V")

    def test_missing_value_renders_as_dash(self):
        self.assertEqual(coll.format_value(None, "°C"), "—")

    def test_zero_bytes_is_not_missing_data(self):
        self.assertEqual(coll.format_bytes(0), "0 B")
        self.assertEqual(coll.format_bytes(None), "—")
        self.assertEqual(coll.format_bytes(16 * 1024 ** 3), "16.0 GiB")


class TestSensorStat(unittest.TestCase):
    def test_tracks_min_max_average(self):
        stat = coll.SensorStat(key="k", chip="c", label="l", kind="temp", unit="°C")
        for value in (40.0, 60.0, 50.0):
            stat.update(value)
        self.assertEqual((stat.minimum, stat.maximum, stat.average), (40.0, 60.0, 50.0))

    def test_missing_samples_do_not_pollute_statistics(self):
        stat = coll.SensorStat(key="k", chip="c", label="l", kind="temp", unit="°C")
        stat.update(50.0)
        stat.update(None)
        self.assertEqual(stat.samples, 1)
        self.assertEqual(stat.average, 50.0)


class TestNotes(unittest.TestCase):
    def test_generic_hardware_gets_no_bc250_advice(self):
        snap = _snapshot(is_bc250=False)
        notes = coll.build_notes(snap)
        self.assertEqual(len(notes), 1)
        self.assertIn("Nie wykryto BC-250", notes[0])

    def test_missing_superio_module_is_reported(self):
        snap = _snapshot(is_bc250=True, modules={"amdgpu"})
        self.assertTrue(any("nct6687" in n for n in coll.build_notes(snap)))

    def test_read_only_module_warns_about_pwm(self):
        snap = _snapshot(is_bc250=True, modules={"nct6683"})
        self.assertTrue(any("nct6683" in n and "PWM" in n for n in coll.build_notes(snap)))

    def test_no_fan_warning_when_the_chip_reports_no_fans(self):
        snap = _snapshot(is_bc250=True)
        self.assertFalse(any("RPM" in n for n in coll.build_notes(snap)))

    def test_gpu_stuck_at_1500_without_governor(self):
        snap = _snapshot_with_sclk("0: 1500Mhz *\n", is_bc250=True)
        snap.services = {"cyan-skillfish-governor-smu.service": "inactive"}
        self.assertTrue(any("1500 MHz" in n for n in coll.build_notes(snap)))


class TestCoreSummary(unittest.TestCase):
    """After a core unlock the logical CPU count alone is misleading."""

    def _summary(self, **kwargs):
        from bc250mon.tui import _core_summary

        return _core_summary(sysfs.CpuInfo(**kwargs))

    def test_shows_cores_and_threads_separately(self):
        self.assertEqual(
            self._summary(cores_physical=8, cores_online=16, cores_present=16, usage=[0.0] * 16),
            "8C/16T")

    def test_stock_configuration(self):
        self.assertEqual(
            self._summary(cores_physical=6, cores_online=12, cores_present=12, usage=[0.0] * 12),
            "6C/12T")

    def test_falls_back_when_topology_unavailable(self):
        self.assertEqual(
            self._summary(cores_physical=0, cores_online=16, cores_present=16, usage=[0.0] * 16),
            "16 wątków")

    def test_offline_cpus_are_flagged(self):
        self.assertEqual(
            self._summary(cores_physical=8, cores_online=12, cores_present=16, usage=[0.0] * 12),
            "8C/12T · 16 obecnych")


class TestBoardPanelFans(unittest.TestCase):
    """A fan header with nothing plugged in must not clutter the dashboard."""

    def _monitor(self, samples):
        from bc250mon.tui import Monitor

        collector = coll.Collector()
        stat = coll.SensorStat(key="nct6686/fan1", chip="nct6686", label="CPU Fan",
                               kind="fan", unit="RPM")
        for value in samples:
            stat.update(value)
        collector.stats[stat.key] = stat
        return Monitor(collector, 1.0)

    def _channel(self, key="nct6686/fan1", value=0.0):
        return sysfs.Channel(key=key, label="CPU Fan", kind="fan", value=value, unit="RPM")

    def test_fan_stuck_at_zero_is_hidden(self):
        monitor = self._monitor([0.0, 0.0, 0.0])
        self.assertFalse(monitor._fan_ever_spun(self._channel()))

    def test_fan_that_once_span_stays_visible(self):
        # a fan that stopped is a fact worth showing, unlike an empty header
        monitor = self._monitor([1200.0, 0.0, 0.0])
        self.assertTrue(monitor._fan_ever_spun(self._channel()))

    def test_unknown_fan_falls_back_to_the_current_reading(self):
        monitor = self._monitor([])
        monitor.collector.stats.clear()
        self.assertTrue(monitor._fan_ever_spun(self._channel(value=900.0)))
        self.assertFalse(monitor._fan_ever_spun(self._channel(value=0.0)))

    def test_channel_index_pairs_fan_with_pwm(self):
        from bc250mon.tui import _channel_index

        fan = sysfs.Channel(key="nct6686/fan2", label="Pump Fan", kind="fan",
                            value=2000.0, unit="RPM")
        pwm = sysfs.Channel(key="nct6686/pwm2", label="PWM 2", kind="pwm",
                            value=80.0, unit="%")
        other = sysfs.Channel(key="nct6686/pwm3", label="PWM 3", kind="pwm",
                              value=50.0, unit="%")
        self.assertEqual(_channel_index(fan), "2")
        self.assertEqual(_channel_index(fan), _channel_index(pwm))
        self.assertNotEqual(_channel_index(fan), _channel_index(other))

    def test_channel_index_without_digits(self):
        from bc250mon.tui import _channel_index

        self.assertEqual(_channel_index(sysfs.Channel(
            key="acpi/fan", label="Fan", kind="fan", value=0.0, unit="RPM")), "")


class TestTranslations(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("pl")

    def test_every_key_has_both_languages(self):
        for key, entry in i18n._MESSAGES.items():
            self.assertEqual(len(entry), 2, key)
            self.assertTrue(all(isinstance(v, str) and v for v in entry), key)

    def test_placeholders_match_between_languages(self):
        import re

        placeholders = re.compile(r"\{(\w+)")
        for key, (pl, en) in i18n._MESSAGES.items():
            self.assertEqual(set(placeholders.findall(pl)),
                             set(placeholders.findall(en)), key)

    def test_switching_language_changes_notes(self):
        snap = _snapshot(is_bc250=True, modules={"nct6683"})

        i18n.set_language("pl")
        self.assertTrue(any("odczyt" in n for n in coll.build_notes(snap)))
        i18n.set_language("en")
        self.assertTrue(any("read-only" in n for n in coll.build_notes(snap)))

    def test_gauge_labels_fit_the_column(self):
        # draw_gauge slices the label to label_w - 1, so anything longer is shown
        # truncated: "Zegar GPU (śr.)" once rendered as "Zegar GPU ("
        from bc250mon.widgets import GAUGE_LABEL_WIDTH

        gauge_keys = [key for key in i18n._MESSAGES if key.startswith("gauge.")]
        self.assertTrue(gauge_keys)
        for lang in i18n.LANGUAGES:
            i18n.set_language(lang)
            for key in gauge_keys:
                text = i18n.t(key)
                self.assertLessEqual(len(text), GAUGE_LABEL_WIDTH - 1,
                                     f"{lang}/{key}: {text!r}")

    def test_unknown_key_returns_itself(self):
        self.assertEqual(i18n.t("nie.ma.takiego.klucza"), "nie.ma.takiego.klucza")

    def test_detection_order_and_fallback(self):
        self.assertEqual(i18n.detect_language("pl"), "pl")
        self.assertEqual(i18n.detect_language("en"), "en")
        # an unsupported language falls back to English
        self.assertEqual(i18n.detect_language("de"), "en")

    def test_detection_reads_environment(self):
        original = os.environ.get("BC250_LANG")
        try:
            os.environ["BC250_LANG"] = "pl"
            self.assertEqual(i18n.detect_language(None), "pl")
            # an explicit argument takes precedence over the variable
            self.assertEqual(i18n.detect_language("en"), "en")
        finally:
            if original is None:
                os.environ.pop("BC250_LANG", None)
            else:
                os.environ["BC250_LANG"] = original

    def test_locale_style_values_are_understood(self):
        original = {k: os.environ.get(k) for k in ("BC250_LANG", "LC_ALL", "LANG")}
        try:
            os.environ.pop("BC250_LANG", None)
            os.environ.pop("LC_ALL", None)
            os.environ["LANG"] = "pl_PL.UTF-8"
            self.assertEqual(i18n.detect_language(None), "pl")
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


class TestBrailleGraph(unittest.TestCase):
    def test_shape_and_blank_input(self):
        rows = widgets.braille_rows([], 10, 3)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(len(r) == 10 for r in rows))

    def test_series_renders_braille_characters(self):
        rows = widgets.braille_rows([0.0, 5.0, 10.0], 4, 2, 0.0, 10.0)
        self.assertEqual(len(rows), 2)
        self.assertTrue(any(ch != " " and ord(ch) >= 0x2800 for row in rows for ch in row))

    def test_flat_series_does_not_divide_by_zero(self):
        rows = widgets.braille_rows([7.0] * 20, 6, 2)
        self.assertEqual(len(rows), 2)


class TestBlockBar(unittest.TestCase):
    def test_bounds_are_clamped(self):
        self.assertEqual(len(widgets.block_bar(2.0, 8)), 8)
        self.assertEqual(widgets.block_bar(-1.0, 4).strip(), "")
        self.assertEqual(widgets.block_bar(1.0, 4), "████")


class TestDemoCollector(unittest.TestCase):
    def test_reflects_unlocked_reference_machine(self):
        # the reference machine has eight cores unlocked, so sixteen threads
        snap = DemoCollector().collect()
        self.assertEqual(snap.cpu.cores_physical, 8)
        self.assertEqual(snap.cpu.cores_online, 16)
        self.assertEqual(len(snap.cpu.usage), 16)
        self.assertEqual(len(snap.cpu.freqs_mhz), 16)

    def test_produces_consistent_bc250_snapshots(self):
        collector = DemoCollector()
        for _ in range(5):
            snap = collector.collect()
        self.assertTrue(snap.is_bc250)
        self.assertEqual(snap.superio_module, "nct6687")
        self.assertIsNotNone(snap.gpu_temp)
        self.assertIn(snap.gpu_clock_mhz, (1000, 1500, 2000))
        self.assertGreater(len(collector.stats), 10)
        self.assertGreater(len(collector.history("gpu.temp")), 1)

    def test_snapshot_serialises_to_json(self):
        import json

        from bc250mon.cli import snapshot_dict

        snap = DemoCollector().collect()
        payload = json.loads(json.dumps(snapshot_dict(snap), ensure_ascii=False))
        self.assertTrue(payload["bc250"])
        self.assertEqual(payload["gpu"]["pci_id"], "0x13FE")

    def test_text_report_renders(self):
        from bc250mon.cli import text_report

        report = text_report(DemoCollector().collect())
        self.assertIn("Cyan Skillfish", report)
        self.assertIn("Pump Fan", report)

    def test_text_report_follows_language(self):
        from bc250mon.cli import text_report

        snap = DemoCollector().collect()
        i18n.set_language("en")
        try:
            self.assertIn("BC-250 detected", text_report(snap))
        finally:
            i18n.set_language("pl")
        self.assertIn("BC-250 wykryta", text_report(snap))


# --------------------------------------------------------------------------

def _snapshot(is_bc250: bool = True, modules: set[str] | None = None) -> coll.Snapshot:
    return coll.Snapshot(
        ts=0.0,
        system=sysfs.SystemInfo(),
        cpu=sysfs.CpuInfo(),
        mem=sysfs.MemInfo(),
        gpu=sysfs.GpuInfo(device_path="/dev/null"),
        chips=[],
        modules=modules if modules is not None else {"nct6687"},
        services={"cyan-skillfish-governor-smu.service": "active"},
        is_bc250=is_bc250,
    )


def _snapshot_with_sclk(raw: str, is_bc250: bool = True) -> coll.Snapshot:
    snap = _snapshot(is_bc250=is_bc250)
    snap.gpu.sclk = sysfs._parse_dpm(raw)
    snap.gpu.sclk_suspect = bool(snap.gpu.sclk) and max(l.mhz for l in snap.gpu.sclk) < 100
    return snap


if __name__ == "__main__":
    unittest.main(verbosity=2)

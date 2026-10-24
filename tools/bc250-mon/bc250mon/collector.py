"""Assembling a hardware snapshot plus HWiNFO-style min/max/avg statistics."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from . import sysfs
from .i18n import t
from .sysfs import (Channel, CpuInfo, GovernorConfig, GpuInfo, HwmonChip, MemInfo,
                    SystemInfo)

# GPU governor services described in the BC-250 documentation
GOVERNOR_UNITS = ["cyan-skillfish-governor-smu.service", "cyan-skillfish-governor-tt.service"]

# Thresholds from the documentation: warning / critical
THRESHOLDS: dict[str, tuple[float, float]] = {
    "gpu": (85.0, 90.0),
    "cpu": (80.0, 90.0),
    "nvme": (70.0, 80.0),
    "board": (70.0, 85.0),
}

# A Braille graph holds two samples per character, so on a wide window
# 240 samples would fill barely a part of it.
HISTORY_LEN = 1024


@dataclass
class SensorStat:
    """One row of the sensor table: the current value plus historical extremes."""

    key: str
    chip: str
    label: str
    kind: str
    unit: str
    value: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    total: float = 0.0
    samples: int = 0

    @property
    def average(self) -> float | None:
        return self.total / self.samples if self.samples else None

    def update(self, value: float | None) -> None:
        self.value = value
        if value is None:
            return
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        self.total += value
        self.samples += 1


@dataclass
class Snapshot:
    ts: float
    system: SystemInfo
    cpu: CpuInfo
    mem: MemInfo
    gpu: GpuInfo
    chips: list[HwmonChip]
    modules: set[str]
    services: dict[str, str]
    is_bc250: bool
    governor: GovernorConfig | None = None
    notes: list[str] = field(default_factory=list)

    # --- shortcuts to the most important sensors ------------------------------

    def chip(self, *names: str) -> HwmonChip | None:
        return sysfs.find_chip(self.chips, *names)

    def channel(self, chip_names: tuple[str, ...], *labels: str) -> Channel | None:
        chip = self.chip(*chip_names)
        if not chip:
            return None
        wanted = [l.lower() for l in labels]
        for want in wanted:
            for ch in chip.channels:
                if ch.label.lower() == want:
                    return ch
        return None

    @property
    def board_chip(self) -> HwmonChip | None:
        return self.chip("nct6686", "nct6687", "nct6683")

    @property
    def gpu_chip(self) -> HwmonChip | None:
        return self.chip("amdgpu")

    @property
    def gpu_temp(self) -> float | None:
        ch = self.channel(("amdgpu",), "edge", "junction", "Temp 1")
        return ch.value if ch else None

    @property
    def gpu_power(self) -> float | None:
        chip = self.gpu_chip
        if not chip:
            return None
        for ch in chip.channels:
            if ch.kind == "power":
                return ch.value
        return None

    @property
    def gpu_vddgfx(self) -> float | None:
        ch = self.channel(("amdgpu",), "vddgfx", "VIN0")
        return ch.value if ch else None

    @property
    def gpu_vddnb(self) -> float | None:
        ch = self.channel(("amdgpu",), "vddnb", "VIN1")
        return ch.value if ch else None

    @property
    def _cpu_temp_channel(self) -> Channel | None:
        return (self.channel(("k10temp",), "Tctl", "Tdie", "Temp 1")
                or self.channel(("coretemp",), "Package id 0", "Temp 1"))

    @property
    def cpu_temp(self) -> float | None:
        ch = self._cpu_temp_channel
        return ch.value if ch else None

    @property
    def cpu_temp_label(self) -> str:
        ch = self._cpu_temp_channel
        if not ch:
            return t("label.temperature")
        return t("label.cpu.package") if ch.label.startswith("Package id") else ch.label

    @property
    def other_chips(self) -> list[HwmonChip]:
        """Chips other than GPU/CPU/NVMe — they stand in for SuperIO in generic mode."""
        skip = ("amdgpu", "k10temp", "coretemp", "nvme", "nct668")
        return [c for c in self.chips if not c.name.startswith(skip)]

    @property
    def estimated_gpu_clock(self) -> tuple[int, int] | None:
        """The frequency the governor asked for, read back from vddgfx.

        The firmware's own GFXCLK field is broken on an unlocked board, but the
        governor sets frequency and voltage together from its safe-point curve,
        so the measured voltage points back at the frequency.
        """
        if self.governor is None:
            return None
        return sysfs.clock_for_voltage(self.governor.safe_points, self.gpu_vddgfx)

    @property
    def gpu_clock_is_average(self) -> bool:
        """gpu_metrics carries Average.GfxclkFrequency, a window average rather
        than an instantaneous clock — the label has to say so."""
        return self.gpu.gfxclk_average is not None

    @property
    def small_vram_split(self) -> bool:
        """A carve-out of 1 GiB or less is the documented dynamic-split setup."""
        total = self.gpu.vram_total
        return bool(total) and total <= 1024 ** 3

    @property
    def gpu_memory_total(self) -> tuple[int, int]:
        """GPU memory actually in use and the practical ceiling: carve-out + GTT.

        mem_info_vram_total is only the guaranteed minimum on this platform, so on
        its own it says nothing about how much memory the GPU can still take.
        """
        used = (self.gpu.vram_used or 0) + (self.gpu.gtt_used or 0)
        total = (self.gpu.vram_total or 0) + (self.gpu.gtt_total or 0)
        return used, total

    @property
    def nvme_temps(self) -> list[tuple[str, float]]:
        out: list[tuple[str, float]] = []
        for chip in self.chips:
            if not chip.name.startswith("nvme"):
                continue
            for ch in chip.channels:
                if ch.kind == "temp" and ch.value is not None and ch.label.lower() == "composite":
                    out.append((chip.path.rsplit("/", 1)[-1], ch.value))
        return out

    @property
    def fans(self) -> list[Channel]:
        chip = self.board_chip
        if not chip:
            return []
        return [ch for ch in chip.channels if ch.kind == "fan"]

    @property
    def gpu_clock_mhz(self) -> int | None:
        # gpu_metrics carries a different SMU field and can be right where
        # pp_dpm_sclk is not, so it wins when it looks sane
        if self.gpu.gfxclk_average is not None:
            return self.gpu.gfxclk_average
        if self.gpu.sclk_unreliable:
            # the driver reports a nonsensical figure; inventing a scale factor
            # would only replace one wrong number with another
            return None
        level = sysfs.active_level(self.gpu.sclk)
        if level:
            # after the 8-core unlock the kernel loses the x100 factor
            return level.mhz * 100 if self.gpu.sclk_suspect else level.mhz
        ch = self.channel(("amdgpu",), "sclk", "Freq 1")
        return int(ch.value) if ch and ch.value else None

    @property
    def gpu_mem_clock_mhz(self) -> int | None:
        level = sysfs.active_level(self.gpu.mclk)
        return level.mhz if level else None

    @property
    def governor_state(self) -> tuple[str, str]:
        """(unit name, state) — the first active one, otherwise the first known."""
        for unit, state in self.services.items():
            if state == "active":
                return unit.replace(".service", ""), state
        for unit, state in self.services.items():
            if state != "n/d":
                return unit.replace(".service", ""), state
        return "cyan-skillfish-governor", "n/d"

    @property
    def superio_module(self) -> str | None:
        for mod in ("nct6687", "nct6683"):
            if mod in self.modules:
                return mod
        return None


class Collector:
    """Collects snapshots and keeps the history the graphs and table need."""

    def __init__(self) -> None:
        self._cpu_sampler = sysfs.CpuSampler()
        self._services: dict[str, str] = {u: "n/d" for u in GOVERNOR_UNITS}
        self._services_checked = 0.0
        self._governor: GovernorConfig | None = None
        self._governor_checked = 0.0
        self.stats: dict[str, SensorStat] = {}
        self.series: dict[str, deque[float]] = {}
        self.samples = 0
        # the first /proc/stat sample only serves as a baseline
        self._cpu_sampler.sample()

    # -- history ----------------------------------------------------------

    def push(self, key: str, value: float | None) -> None:
        if value is None:
            return
        series = self.series.setdefault(key, deque(maxlen=HISTORY_LEN))
        series.append(value)

    def history(self, key: str) -> list[float]:
        return list(self.series.get(key, ()))

    # -- collecting ---------------------------------------------------------

    def collect(self) -> Snapshot:
        now = sysfs.monotonic()
        if now - self._services_checked > 10.0 or not self.samples:
            self._services = sysfs.systemd_state(GOVERNOR_UNITS)
            self._services_checked = now
        # the curve rarely changes, but re-reading lets an edit show up live
        if now - self._governor_checked > 30.0 or not self.samples:
            self._governor = sysfs.read_governor_config()
            self._governor_checked = now

        system = sysfs.scan_system()
        gpu = sysfs.scan_gpu()
        chips = sysfs.scan_hwmon()

        snap = Snapshot(
            ts=now,
            system=system,
            cpu=sysfs.scan_cpu(self._cpu_sampler),
            mem=sysfs.scan_memory(),
            gpu=gpu,
            chips=chips,
            modules=sysfs.loaded_modules(),
            services=dict(self._services),
            is_bc250=_detect_bc250(gpu, system),
            governor=self._governor,
        )
        snap.notes = build_notes(snap)

        self._record(snap)
        self.samples += 1
        return snap

    def _record(self, snap: Snapshot) -> None:
        for chip in snap.chips:
            for ch in chip.channels:
                stat = self.stats.get(ch.key)
                if stat is None:
                    stat = SensorStat(key=ch.key, chip=chip.name, label=ch.label,
                                      kind=ch.kind, unit=ch.unit)
                    self.stats[ch.key] = stat
                stat.update(ch.value)

        for key, label, kind, unit, value in (
            ("calc/gpu_clock",
             t("calc.gpu.clock.avg") if snap.gpu_clock_is_average else t("calc.gpu.clock"),
             "freq", "MHz", _as_float(snap.gpu_clock_mhz)),
            ("calc/gpu_mclk", t("calc.gpu.mclk"), "freq", "MHz",
             _as_float(snap.gpu_mem_clock_mhz)),
            ("calc/gpu_busy", t("calc.gpu.busy"), "usage", "%", snap.gpu.busy_percent),
            ("calc/cpu_usage", t("calc.cpu.usage"), "usage", "%", snap.cpu.total_usage),
            ("calc/cpu_clock", t("calc.cpu.clock"), "freq", "MHz",
             _mean(snap.cpu.freqs_mhz)),
            ("calc/mem_used", t("calc.mem.used"), "usage", "%",
             _pct(snap.mem.used, snap.mem.total)),
            ("calc/vram_used",
             t("calc.vram.carveout.used") if snap.small_vram_split else t("calc.vram.used"),
             "usage", "%", _pct(snap.gpu.vram_used, snap.gpu.vram_total)),
            ("calc/gpu_memory_used", t("calc.gpu.memory.used"), "usage", "%",
             _pct(*snap.gpu_memory_total)),
        ):
            stat = self.stats.get(key)
            if stat is None:
                stat = SensorStat(key=key, chip=t("chip.computed"), label=label,
                                  kind=kind, unit=unit)
                self.stats[key] = stat
            else:
                # the VRAM label depends on the split, so it can change between reads
                stat.label = label
            stat.update(value)

        self.push("gpu.temp", snap.gpu_temp)
        self.push("gpu.power", snap.gpu_power)
        self.push("gpu.clock", _as_float(snap.gpu_clock_mhz))
        self.push("gpu.busy", snap.gpu.busy_percent)
        self.push("cpu.temp", snap.cpu_temp)
        self.push("cpu.usage", snap.cpu.total_usage)
        self.push("cpu.clock", _mean(snap.cpu.freqs_mhz))
        self.push("mem.used", _pct(snap.mem.used, snap.mem.total))
        fans = [ch.value for ch in snap.fans if ch.value]
        self.push("fan.rpm", max(fans) if fans else None)

    def reset_stats(self) -> None:
        self.stats.clear()
        self.series.clear()
        self.samples = 0


# --------------------------------------------------------------------------
# BC-250 specific diagnostics
# --------------------------------------------------------------------------

def _detect_bc250(gpu: GpuInfo, system: SystemInfo) -> bool:
    if gpu.is_cyan_skillfish:
        return True
    haystack = " ".join(filter(None, (system.board_name, system.product_name))).upper()
    return "BC-250" in haystack or "BC250" in haystack


def build_notes(snap: Snapshot) -> list[str]:
    """Hints drawn from the BC-250 documentation — the point of this tool."""
    notes: list[str] = []

    if not snap.is_bc250:
        # Off a BC-250, advice about SuperIO or the governor would mislead —
        # only the temperature thresholds remain.
        notes.append(t("note.generic"))
        notes.extend(_temperature_notes(snap))
        return notes

    module = snap.superio_module
    if module is None:
        notes.append(t("note.superio.missing"))
    elif module == "nct6683":
        notes.append(t("note.superio.readonly"))

    if snap.gpu.device_path is None:
        notes.append(t("note.gpu.missing"))
    else:
        if snap.gpu.metrics_note:
            notes.append(t("note.gpu.busy", reason=snap.gpu.metrics_note))
        if snap.gpu.sclk_suspect:
            notes.append(t("note.gpu.sclk"))
        elif snap.gpu.sclk_unreliable:
            current = sysfs.active_level(snap.gpu.sclk)
            sane = [level.mhz for level in snap.gpu.sclk if level.mhz >= 100]
            key = ("note.gpu.sclk.from.metrics" if snap.gpu.gfxclk_average is not None
                   else "note.gpu.sclk.unreliable")
            notes.append(t(key, active=current.mhz if current else 0,
                           low=min(sane), high=max(sane)))

        gov_name, gov_state = snap.governor_state
        levels = [l.mhz for l in snap.gpu.sclk]
        if gov_state != "active" and levels and max(levels) <= 1500:
            notes.append(t("note.gpu.locked", unit=gov_name, state=gov_state))

    if snap.governor and snap.governor.range_overridden:
        notes.append(t("note.governor.range.overridden",
                       live_min=snap.governor.live_min_mhz,
                       live_max=snap.governor.live_max_mhz,
                       config_min=snap.governor.min_mhz,
                       config_max=snap.governor.max_mhz))

    if snap.small_vram_split and snap.gpu.gtt_total:
        notes.append(t("note.vram.small.split",
                       size=format_bytes(snap.gpu.vram_total),
                       ceiling=format_bytes(snap.gpu_memory_total[1])))

    notes.extend(_temperature_notes(snap))

    # with no fan channels at all there is nothing to complain about
    if snap.superio_module and snap.fans and not any(ch.value for ch in snap.fans):
        notes.append(t("note.fans.zero"))

    return notes


def _temperature_notes(snap: Snapshot) -> list[str]:
    notes = []
    for label, temp, kind in _monitored_temps(snap):
        warn, crit = THRESHOLDS[kind]
        if temp >= crit:
            notes.append(t("note.temp.crit", label=label, value=temp, limit=crit))
        elif temp >= warn:
            notes.append(t("note.temp.warn", label=label, value=temp, limit=warn))
    return notes


def _monitored_temps(snap: Snapshot) -> list[tuple[str, float, str]]:
    out: list[tuple[str, float, str]] = []
    if snap.gpu_temp is not None:
        out.append(("GPU", snap.gpu_temp, "gpu"))
    if snap.cpu_temp is not None:
        out.append(("CPU", snap.cpu_temp, "cpu"))
    for name, temp in snap.nvme_temps:
        out.append((f"NVMe ({name})", temp, "nvme"))
    return out


def temp_kind(chip_name: str, label: str) -> str:
    """Picks the temperature thresholds that fit a sensor."""
    name = chip_name.lower()
    if name.startswith("amdgpu"):
        return "gpu"
    if name.startswith(("k10temp", "coretemp")):
        return "cpu"
    if name.startswith("nvme"):
        return "nvme"
    return "board"


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------

def _as_float(value: int | None) -> float | None:
    return None if value is None else float(value)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _pct(used: int | None, total: int | None) -> float | None:
    if not used or not total:
        return None
    return 100.0 * used / total


def format_value(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    if unit == "RPM":
        return f"{value:.0f} RPM"
    if unit == "°C":
        return f"{value:.1f} °C"
    if unit == "V":
        return f"{value * 1000:.0f} mV" if abs(value) < 1.0 else f"{value:.2f} V"
    if unit == "W":
        return f"{value:.2f} W"
    if unit == "A":
        return f"{value:.2f} A"
    if unit == "MHz":
        return f"{value:.0f} MHz"
    if unit == "%":
        return f"{value:.1f} %"
    return f"{value:.2f} {unit}"


def format_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    if value == 0:
        return "0 B"
    step = 1024.0
    size = float(value)
    for suffix in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < step or suffix == "TiB":
            return f"{size:.1f} {suffix}" if suffix not in ("B", "KiB") else f"{size:.0f} {suffix}"
        size /= step
    return f"{size:.1f} TiB"

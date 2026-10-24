"""The hardware reading layer: sysfs, hwmon, amdgpu, procfs.

Everything here is read-only and tolerant of missing files — on the BC-250 the
set of available sensors depends on which SuperIO module is loaded
(nct6683 vs nct6687) and on whether a governor is running.
"""

from __future__ import annotations

import glob
import os
import re
import struct
import time
from dataclasses import dataclass, field

from .i18n import t

HWMON_ROOT = "/sys/class/hwmon"
DRM_ROOT = "/sys/class/drm"
DMI_ROOT = "/sys/class/dmi/id"

# PCI IDs of the Cyan Skillfish APU (BC-250). 0x13FE is the variant fitted to
# mining boards, 0x143F appears on some revisions.
CYAN_SKILLFISH_IDS = {0x13FE, 0x143F}


# --------------------------------------------------------------------------
# Basic readers
# --------------------------------------------------------------------------

def read_text(path: str) -> str | None:
    try:
        with open(path, "r", errors="replace") as fh:
            return fh.read().strip()
    except (OSError, UnicodeError):
        return None


def read_int(path: str) -> int | None:
    raw = read_text(path)
    if raw is None:
        return None
    try:
        return int(raw, 0)
    except ValueError:
        return None


def read_bytes(path: str, size: int = 4096) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read(size)
    except OSError:
        return None


def first_existing(*paths: str) -> str | None:
    for path in paths:
        if os.path.exists(path):
            return path
    return None


# --------------------------------------------------------------------------
# hwmon
# --------------------------------------------------------------------------

@dataclass
class Channel:
    """A single hwmon channel (one sensor)."""

    key: str            # a stable identifier, e.g. "nct6686/temp3"
    label: str          # the kernel label, or a generic name
    kind: str           # temp | fan | volt | power | pwm | curr | freq
    value: float | None
    unit: str
    limit: float | None = None   # warning threshold (max/target)
    crit: float | None = None    # critical threshold


@dataclass
class HwmonChip:
    path: str
    name: str
    channels: list[Channel] = field(default_factory=list)


# (file prefix, kind, divisor, unit)
_HWMON_KINDS = (
    ("temp", "temp", 1000.0, "°C"),
    ("fan", "fan", 1.0, "RPM"),
    ("in", "volt", 1000.0, "V"),
    ("power", "power", 1_000_000.0, "W"),
    ("curr", "curr", 1000.0, "A"),
    ("freq", "freq", 1_000_000.0, "MHz"),
)


def _channel_label(chip_path: str, prefix: str, idx: str, kind: str) -> str:
    label = read_text(f"{chip_path}/{prefix}{idx}_label")
    if label:
        return label
    generic = {
        "temp": f"Temp {idx}",
        "fan": f"Fan {idx}",
        "volt": f"VIN{idx}",
        "power": f"Power {idx}",
        "curr": f"Curr {idx}",
        "freq": f"Freq {idx}",
    }
    return generic.get(kind, f"{prefix}{idx}")


def scan_chip(path: str) -> HwmonChip:
    name = read_text(f"{path}/name") or os.path.basename(path)
    chip = HwmonChip(path=path, name=name)

    for prefix, kind, divisor, unit in _HWMON_KINDS:
        # power comes in two flavours: _average (preferred) and _input
        suffixes = ("_average", "_input") if prefix == "power" else ("_input",)
        seen: set[str] = set()
        for suffix in suffixes:
            for src in sorted(glob.glob(f"{path}/{prefix}[0-9]*{suffix}")):
                idx = re.sub(rf".*/{prefix}(\d+){suffix}$", r"\1", src)
                if idx in seen:
                    continue
                raw = read_int(src)
                if raw is None:
                    continue
                seen.add(idx)
                chip.channels.append(Channel(
                    key=f"{name}/{prefix}{idx}",
                    label=_channel_label(path, prefix, idx, kind),
                    kind=kind,
                    value=raw / divisor,
                    unit=unit,
                    limit=_opt_div(read_int(f"{path}/{prefix}{idx}_max"), divisor),
                    crit=_opt_div(read_int(f"{path}/{prefix}{idx}_crit"), divisor),
                ))

    # PWM is reported as a percentage — a raw 0-255 tells the user nothing
    for src in sorted(glob.glob(f"{path}/pwm[0-9]*")):
        if not re.fullmatch(r"pwm\d+", os.path.basename(src)):
            continue
        raw = read_int(src)
        if raw is None:
            continue
        idx = os.path.basename(src)[3:]
        chip.channels.append(Channel(
            key=f"{name}/pwm{idx}",
            label=f"PWM {idx}",
            kind="pwm",
            value=raw * 100.0 / 255.0,
            unit="%",
        ))

    return chip


def _opt_div(value: int | None, divisor: float) -> float | None:
    return None if value is None else value / divisor


def scan_hwmon() -> list[HwmonChip]:
    chips = [scan_chip(p) for p in sorted(glob.glob(f"{HWMON_ROOT}/hwmon*"))]
    return [c for c in chips if c.channels]


def find_chip(chips: list[HwmonChip], *names: str) -> HwmonChip | None:
    for name in names:
        for chip in chips:
            if chip.name.startswith(name):
                return chip
    return None


# --------------------------------------------------------------------------
# amdgpu
# --------------------------------------------------------------------------

# Field offsets in struct gpu_metrics_v2_2, the table cyan_skillfish_ppt.c emits.
# The header is 4 bytes, then the layout follows kgd_pp_interface.h. The activity
# field landing on 0x1C matches the offset the BC-250 documentation names, which
# is a useful check that the layout is right.
GPU_METRICS_FORMAT = 2
GPU_METRICS_MIN_SIZE = 0x4E
METRICS_OFF_ACTIVITY = 0x1C
METRICS_OFF_AVERAGE_GFXCLK = 0x40
METRICS_OFF_CURRENT_GFXCLK = 0x4C

# A GFX clock outside this range is not something this APU does; the driver
# reporting 2 MHz means the SMU metric is broken, not that the GPU stopped.
# The floor sits above the 100 MHz ceiling the broken instantaneous field is
# pinned to, and below the 350 MHz the patched kernel allows as a real minimum.
CLOCK_PLAUSIBLE_MIN = 200
CLOCK_PLAUSIBLE_MAX = 3000


@dataclass
class GpuMetrics:
    format_revision: int
    content_revision: int
    gfx_activity: int
    average_gfxclk: int
    current_gfxclk: int


def read_gpu_metrics(device_path: str) -> GpuMetrics | None:
    """Parses the binary gpu_metrics table, or None when it is unusable."""
    blob = read_bytes(f"{device_path}/gpu_metrics", 512)
    if not blob or len(blob) < GPU_METRICS_MIN_SIZE + 2:
        return None
    size, fmt, content = struct.unpack_from("<HBB", blob, 0)
    # the offsets below only hold for the v2 tables used by APUs
    if fmt != GPU_METRICS_FORMAT or not GPU_METRICS_MIN_SIZE <= size <= len(blob):
        return None
    return GpuMetrics(
        format_revision=fmt,
        content_revision=content,
        gfx_activity=struct.unpack_from("<H", blob, METRICS_OFF_ACTIVITY)[0],
        average_gfxclk=struct.unpack_from("<H", blob, METRICS_OFF_AVERAGE_GFXCLK)[0],
        current_gfxclk=struct.unpack_from("<H", blob, METRICS_OFF_CURRENT_GFXCLK)[0],
    )


def plausible_clock(mhz: int | None) -> bool:
    return mhz is not None and CLOCK_PLAUSIBLE_MIN <= mhz <= CLOCK_PLAUSIBLE_MAX


def usable_average_clock(metrics: GpuMetrics | None) -> int | None:
    """The averaged GFX clock from gpu_metrics, when it looks like a reading.

    Measured on the reference board with umr: the field moves with load — 505 MHz
    at 36% activity, 931 MHz at 73% — while its neighbours in the same Average
    block (socclk 1254, uclk 450, dclk 1111) all read correctly. So it is a real
    average, unlike Current.GfxclkFrequency, which stays pinned below 100.

    Being an average over the firmware's window, it includes the gaps when the
    GPU is clock-gated, so it sits well below the frequency the governor sets.
    That is expected and is why no lower bound is imposed here: at idle a genuine
    average really is small.
    """
    if metrics is None:
        return None
    value = metrics.average_gfxclk
    if 0 < value <= CLOCK_PLAUSIBLE_MAX:
        return value
    return None


# The GPU governor keeps its frequency/voltage curve here. Reading it back lets a
# measured vddgfx be mapped to the frequency the governor asked for, which is the
# only way to see the running clock while the firmware's own GFXCLK field is broken.
GOVERNOR_CONFIGS = (
    "/etc/cyan-skillfish-governor-smu/config.toml",
    "/etc/cyan-skillfish-governor-tt/config.toml",
)


DBUS_SERVICE = "com.cyanskillfish.Governor"
DBUS_RANGE_INTERFACE = "com.cyanskillfish.Governor.Range"
DBUS_RANGE_PATHS = {
    "current": "/com/cyanskillfish/Governor/Range/Current",
    "allowed": "/com/cyanskillfish/Governor/Range/Allowed",
}


@dataclass
class GovernorConfig:
    path: str
    min_mhz: int | None = None
    max_mhz: int | None = None
    safe_points: list[tuple[int, int]] = field(default_factory=list)   # (MHz, mV)
    # read live over D-Bus; the range can be narrowed at runtime with SetRange,
    # in which case the configuration file no longer describes what is in force
    live_min_mhz: int | None = None
    live_max_mhz: int | None = None

    @property
    def effective_range(self) -> tuple[int | None, int | None]:
        return (self.live_min_mhz or self.min_mhz, self.live_max_mhz or self.max_mhz)

    @property
    def range_overridden(self) -> bool:
        if self.live_min_mhz is None or self.min_mhz is None:
            return False
        return (self.live_min_mhz, self.live_max_mhz) != (self.min_mhz, self.max_mhz)


def parse_busctl_numbers(output: str) -> list[int]:
    """busctl get-property prints one 'u 1000' line per requested property."""
    values = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in ("u", "i", "x", "t"):
            try:
                values.append(int(parts[1]))
            except ValueError:
                pass
    return values


def read_governor_range(timeout: float = 1.5) -> tuple[int, int] | None:
    """The frequency range the governor is actually enforcing right now."""
    import subprocess

    try:
        proc = subprocess.run(
            ["busctl", "get-property", DBUS_SERVICE, DBUS_RANGE_PATHS["current"],
             DBUS_RANGE_INTERFACE, "Min", "Max"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    values = parse_busctl_numbers(proc.stdout)
    return (values[0], values[1]) if len(values) >= 2 else None


def _toml_number(raw: str) -> int | None:
    """Just enough TOML for this file: an integer, maybe with _ separators."""
    value = raw.split("#", 1)[0].strip().replace("_", "")
    try:
        return int(value)
    except ValueError:
        return None


def parse_governor_config(text: str, path: str = "") -> GovernorConfig:
    config = GovernorConfig(path=path)
    section = ""
    frequency: int | None = None
    voltage: int | None = None

    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        if stripped.startswith("["):
            if section == "safe-points" and frequency and voltage:
                config.safe_points.append((frequency, voltage))
            frequency = voltage = None
            section = stripped.strip("[]")
            continue
        if "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        key, number = key.strip(), _toml_number(raw)
        if number is None:
            continue
        if section == "frequency-range" and key == "min":
            config.min_mhz = number or None
        elif section == "frequency-range" and key == "max":
            config.max_mhz = number or None
        elif section == "safe-points" and key == "frequency":
            frequency = number
        elif section == "safe-points" and key == "voltage":
            voltage = number

    if section == "safe-points" and frequency and voltage:
        config.safe_points.append((frequency, voltage))
    config.safe_points.sort()
    return config


def read_governor_config() -> GovernorConfig | None:
    for path in GOVERNOR_CONFIGS:
        text = read_text(path)
        if text:
            config = parse_governor_config(text, path)
            live = read_governor_range()
            if live:
                config.live_min_mhz, config.live_max_mhz = live
            return config
    return None


def clock_for_voltage(points: list[tuple[int, int]], volts: float | None) -> tuple[int, int] | None:
    """Maps a measured vddgfx back onto the governor curve.

    Returns the bracketing frequencies as (low, high); they are equal when the
    voltage interpolates to a single point. The curve is not injective — two
    frequencies can share a voltage — so a range is the honest answer.
    """
    if volts is None or not points:
        return None
    millivolts = round(volts * 1000)

    exact = [mhz for mhz, mv in points if mv == millivolts]
    if exact:
        return min(exact), max(exact)

    below = [(mhz, mv) for mhz, mv in points if mv < millivolts]
    above = [(mhz, mv) for mhz, mv in points if mv > millivolts]
    if not below or not above:
        return None

    low_mhz, low_mv = max(below, key=lambda p: p[1])
    high_mhz, high_mv = min(above, key=lambda p: p[1])
    span = high_mv - low_mv
    estimate = round(low_mhz + (millivolts - low_mv) / span * (high_mhz - low_mhz))
    return estimate, estimate


@dataclass
class DpmLevel:
    index: int
    mhz: int
    active: bool


@dataclass
class GpuInfo:
    card: str | None = None
    device_path: str | None = None
    pci_id: int | None = None
    pci_slot: str | None = None
    revision: str | None = None
    is_cyan_skillfish: bool = False

    sclk: list[DpmLevel] = field(default_factory=list)
    mclk: list[DpmLevel] = field(default_factory=list)
    sclk_suspect: bool = False      # whole table scaled down by a factor of 100
    sclk_unreliable: bool = False   # active level below the table's own minimum
    gfxclk_average: int | None = None   # from gpu_metrics, only when telemetry is sane
    gfxclk_current: int | None = None   # the raw instantaneous field, for diagnostics
    metrics_revision: tuple[int, int] | None = None

    busy_percent: float | None = None
    busy_source: str | None = None   # where the load reading came from
    metrics_note: str | None = None  # why the load is unavailable

    vram_total: int | None = None
    vram_used: int | None = None
    gtt_total: int | None = None
    gtt_used: int | None = None

    perf_level: str | None = None
    od_table: str | None = None


def _parse_dpm(raw: str | None) -> list[DpmLevel]:
    """Parses '0: 1000Mhz *' into a list of DPM levels."""
    levels: list[DpmLevel] = []
    if not raw:
        return levels
    for line in raw.splitlines():
        m = re.match(r"\s*(\d+):\s*(\d+)\s*Mhz\s*(\*?)", line, re.IGNORECASE)
        if m:
            levels.append(DpmLevel(int(m.group(1)), int(m.group(2)), bool(m.group(3))))
    return levels


def _read_gpu_activity(device_path: str,
                       metrics: GpuMetrics | None) -> tuple[float | None, str | None, str | None]:
    """Returns (load%, source, note).

    On the BC-250 gpu_busy_percent is not populated by amdgpu, and the activity
    field in gpu_metrics stays at 0xFFFF (hence the famous 655% in MangoHud).
    The smu governor bind-mounts over gpu_metrics and writes a sensible value
    there — then the reading works.
    """
    busy = read_int(f"{device_path}/gpu_busy_percent")
    if busy is not None and 0 <= busy <= 100:
        return float(busy), "gpu_busy_percent", None

    if metrics is not None:
        raw = metrics.gfx_activity
        if raw == 0xFFFF:
            return None, None, t("reason.metrics.uninitialised")
        # v1 reports plain percent, the APU variants hundredths of a percent
        pct = float(raw) if raw <= 100 else raw / 100.0
        if 0 <= pct <= 100:
            return pct, "gpu_metrics", None
        return None, None, t("reason.metrics.invalid", raw=raw)

    return None, None, t("reason.metrics.absent")


def active_level(levels: list[DpmLevel]) -> DpmLevel | None:
    for level in levels:
        if level.active:
            return level
    return None


def scan_gpu() -> GpuInfo:
    info = GpuInfo()

    for card in sorted(glob.glob(f"{DRM_ROOT}/card[0-9]*")):
        if "-" in os.path.basename(card):   # pomijamy connectory card0-DP-1
            continue
        device = f"{card}/device"
        driver = os.path.realpath(f"{device}/driver")
        if os.path.basename(driver) != "amdgpu":
            continue
        info.card = os.path.basename(card)
        info.device_path = device
        break

    if not info.device_path:
        return info

    device = info.device_path
    info.pci_id = read_int(f"{device}/device")
    info.revision = read_text(f"{device}/revision")
    info.pci_slot = os.path.basename(os.path.realpath(device))
    info.is_cyan_skillfish = info.pci_id in CYAN_SKILLFISH_IDS

    info.sclk = _parse_dpm(read_text(f"{device}/pp_dpm_sclk"))
    info.mclk = _parse_dpm(read_text(f"{device}/pp_dpm_mclk"))
    # Two distinct symptoms of the 8-core unlock. Either the whole table is
    # scaled down by 100 (documented as "1: 15Mhz" for 1500 MHz), or the table
    # keeps a sane min and max while the active level reads nonsense.
    info.sclk_suspect = bool(info.sclk) and max(l.mhz for l in info.sclk) < 100
    current = active_level(info.sclk)
    info.sclk_unreliable = (
        not info.sclk_suspect
        and current is not None
        and current.mhz < 100
        and any(level.mhz >= 100 for level in info.sclk)
    )

    metrics = read_gpu_metrics(device)
    if metrics is not None:
        info.metrics_revision = (metrics.format_revision, metrics.content_revision)
        info.gfxclk_current = metrics.current_gfxclk
        info.gfxclk_average = usable_average_clock(metrics)

    info.busy_percent, info.busy_source, info.metrics_note = _read_gpu_activity(device, metrics)

    info.vram_total = read_int(f"{device}/mem_info_vram_total")
    info.vram_used = read_int(f"{device}/mem_info_vram_used")
    info.gtt_total = read_int(f"{device}/mem_info_gtt_total")
    info.gtt_used = read_int(f"{device}/mem_info_gtt_used")

    info.perf_level = read_text(f"{device}/power_dpm_force_performance_level")
    info.od_table = read_text(f"{device}/pp_od_clk_voltage")
    return info


# --------------------------------------------------------------------------
# CPU
# --------------------------------------------------------------------------

@dataclass
class CpuInfo:
    model: str = "nieznany"
    cores_online: int = 0        # logical CPUs visible to the kernel
    cores_present: int = 0
    cores_physical: int = 0      # rdzenie fizyczne (bez SMT)
    freqs_mhz: list[float] = field(default_factory=list)
    usage: list[float] = field(default_factory=list)   # per core, %
    total_usage: float = 0.0
    governor: str | None = None
    driver: str | None = None
    loadavg: tuple[float, float, float] = (0.0, 0.0, 0.0)


def _count_physical_cores() -> int:
    """Counts physical cores as unique package:core pairs from the topology.

    Without this, 8C/16T after a core unlock would look like sixteen cores.
    """
    seen: set[tuple[str, str]] = set()
    for topo in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/topology"):
        core = read_text(f"{topo}/core_id")
        if core is None:
            continue
        package = read_text(f"{topo}/physical_package_id") or "0"
        seen.add((package, core))
    if seen:
        return len(seen)

    # fallback for kernels without sysfs topology
    m = re.search(r"^cpu cores\s*:\s*(\d+)$", read_text("/proc/cpuinfo") or "", re.MULTILINE)
    return int(m.group(1)) if m else 0


def _count_cpu_list(raw: str | None) -> int:
    """Counts CPUs from a '0-5' / '0,2-3' style list."""
    if not raw:
        return 0
    total = 0
    for part in raw.split(","):
        if "-" in part:
            lo, hi = part.split("-", 1)
            total += int(hi) - int(lo) + 1
        else:
            total += 1
    return total


class CpuSampler:
    """Derives per-core load from differences between /proc/stat readings."""

    def __init__(self) -> None:
        self._prev: dict[str, tuple[int, int]] = {}

    def _stat(self) -> dict[str, tuple[int, int]]:
        out: dict[str, tuple[int, int]] = {}
        raw = read_text("/proc/stat") or ""
        for line in raw.splitlines():
            if not line.startswith("cpu"):
                break
            fields = line.split()
            values = [int(v) for v in fields[1:]]
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            out[fields[0]] = (sum(values), idle)
        return out

    def sample(self) -> tuple[float, list[float]]:
        current = self._stat()
        total_usage = 0.0
        per_core: list[float] = []

        for name, (total, idle) in current.items():
            prev = self._prev.get(name)
            usage = 0.0
            if prev:
                d_total = total - prev[0]
                d_idle = idle - prev[1]
                if d_total > 0:
                    usage = max(0.0, min(100.0, 100.0 * (d_total - d_idle) / d_total))
            if name == "cpu":
                total_usage = usage
            else:
                per_core.append(usage)

        self._prev = current
        return total_usage, per_core


def scan_cpu(sampler: CpuSampler) -> CpuInfo:
    info = CpuInfo()

    cpuinfo = read_text("/proc/cpuinfo") or ""
    m = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
    if m:
        info.model = m.group(1).strip()

    info.cores_online = _count_cpu_list(read_text("/sys/devices/system/cpu/online"))
    info.cores_present = _count_cpu_list(read_text("/sys/devices/system/cpu/present"))
    info.cores_physical = _count_physical_cores()
    info.governor = read_text("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    info.driver = read_text("/sys/devices/system/cpu/cpu0/cpufreq/scaling_driver")

    freqs: list[float] = []
    for cpu_dir in sorted(glob.glob("/sys/devices/system/cpu/cpu[0-9]*"),
                          key=lambda p: int(re.sub(r".*cpu", "", p))):
        khz = read_int(f"{cpu_dir}/cpufreq/scaling_cur_freq")
        if khz is not None:
            freqs.append(khz / 1000.0)
    if not freqs:
        # without cpufreq (a BC-250 lacking SSDT-PST) the clock comes from /proc/cpuinfo
        freqs = [float(v) for v in re.findall(r"^cpu MHz\s*:\s*([\d.]+)$", cpuinfo, re.MULTILINE)]
    info.freqs_mhz = freqs

    info.total_usage, info.usage = sampler.sample()

    load = (read_text("/proc/loadavg") or "").split()
    if len(load) >= 3:
        info.loadavg = (float(load[0]), float(load[1]), float(load[2]))

    return info


# --------------------------------------------------------------------------
# Memory and system
# --------------------------------------------------------------------------

@dataclass
class MemInfo:
    total: int = 0        # bajty
    available: int = 0
    used: int = 0
    swap_total: int = 0
    swap_used: int = 0


def scan_memory() -> MemInfo:
    values: dict[str, int] = {}
    raw = read_text("/proc/meminfo") or ""
    for line in raw.splitlines():
        m = re.match(r"(\w+):\s+(\d+) kB", line)
        if m:
            values[m.group(1)] = int(m.group(2)) * 1024

    info = MemInfo()
    info.total = values.get("MemTotal", 0)
    info.available = values.get("MemAvailable", 0)
    info.used = max(0, info.total - info.available)
    info.swap_total = values.get("SwapTotal", 0)
    info.swap_used = max(0, info.swap_total - values.get("SwapFree", 0))
    return info


@dataclass
class SystemInfo:
    hostname: str = "?"
    kernel: str = "?"
    os_name: str = "?"
    uptime: float = 0.0
    board_vendor: str | None = None
    board_name: str | None = None
    product_name: str | None = None
    bios_version: str | None = None
    bios_date: str | None = None


def scan_system() -> SystemInfo:
    info = SystemInfo()
    info.hostname = read_text("/proc/sys/kernel/hostname") or os.uname().nodename
    info.kernel = os.uname().release

    os_release = read_text("/etc/os-release") or ""
    m = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', os_release, re.MULTILINE)
    if m:
        info.os_name = m.group(1)

    uptime = read_text("/proc/uptime")
    if uptime:
        info.uptime = float(uptime.split()[0])

    info.board_vendor = read_text(f"{DMI_ROOT}/board_vendor")
    info.board_name = read_text(f"{DMI_ROOT}/board_name")
    info.product_name = read_text(f"{DMI_ROOT}/product_name")
    info.bios_version = read_text(f"{DMI_ROOT}/bios_version")
    info.bios_date = read_text(f"{DMI_ROOT}/bios_date")
    return info


def loaded_modules() -> set[str]:
    raw = read_text("/proc/modules") or ""
    return {line.split()[0] for line in raw.splitlines() if line.strip()}


def systemd_state(units: list[str], timeout: float = 1.5) -> dict[str, str]:
    """Service state through systemctl; returns 'n/d' when systemd is absent."""
    import subprocess

    try:
        proc = subprocess.run(
            ["systemctl", "is-active", *units],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        states = proc.stdout.split()
    except (OSError, subprocess.SubprocessError):
        states = []

    if len(states) != len(units):
        return {unit: "n/d" for unit in units}
    return dict(zip(units, states))


def uptime_str(seconds: float) -> str:
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def monotonic() -> float:
    return time.monotonic()

"""A simulated BC-250 — lets the monitor be developed and shown without a board."""

from __future__ import annotations

import math
import random

from . import collector as coll
from .collector import Collector, Snapshot
from .sysfs import Channel, CpuInfo, DpmLevel, GpuInfo, HwmonChip, MemInfo, SystemInfo

GIB = 1024 ** 3


class DemoCollector(Collector):
    """Generates plausible BC-250 readings driven by a slow load wave."""

    def __init__(self) -> None:
        super().__init__()
        self._tick = 0.0
        self._drift = 0.0
        self._rng = random.Random(0xBC250)

    def collect(self) -> Snapshot:
        self._tick += 0.08
        # A single sine made the demo graphs look like a test pattern. Three
        # waves with unrelated periods plus a slow random drift read as a real
        # workload rising and falling.
        wave = (0.50 * math.sin(self._tick)
                + 0.30 * math.sin(self._tick * 0.37 + 1.1)
                + 0.20 * math.sin(self._tick * 2.13 + 2.7))
        self._drift = max(-0.30, min(0.30, self._drift + self._rng.uniform(-0.02, 0.02)))
        load = max(0.0, min(1.0, 0.5 + 0.45 * wave + self._drift))   # 0..1
        jitter = lambda scale: self._rng.uniform(-scale, scale)

        gpu_temp = 46.0 + 34.0 * load + jitter(1.2)
        gpu_power = 38.0 + 160.0 * load + jitter(4.0)
        gpu_busy = max(0.0, min(100.0, 100.0 * load + jitter(6.0)))
        cpu_temp = 45.0 + 28.0 * load + jitter(1.0)
        cpu_load = max(0.0, min(100.0, 90.0 * load + jitter(8.0)))
        sclk = 1000 if load < 0.25 else (1500 if load < 0.6 else 2000)
        fan_rpm = 1200 + 1400 * load + jitter(40)

        gpu = GpuInfo(
            card="card0",
            device_path="/sys/class/drm/card0/device",
            pci_id=0x13FE,
            pci_slot="0000:01:00.0",
            revision="0xc1",
            is_cyan_skillfish=True,
            sclk=[DpmLevel(i, mhz, mhz == sclk) for i, mhz in enumerate((1000, 1500, 2000))],
            mclk=[DpmLevel(0, 1000, True)],
            busy_percent=gpu_busy,
            busy_source="gpu_metrics",
            vram_total=16 * GIB,
            vram_used=int((1.1 + 4.5 * load) * GIB),
            gtt_total=8 * GIB,
            gtt_used=int((0.4 + 1.2 * load) * GIB),
            perf_level="auto",
        )

        chips = [
            HwmonChip("/sys/class/hwmon/hwmon1", "amdgpu", [
                _ch("amdgpu/temp1", "edge", "temp", gpu_temp, "°C"),
                _ch("amdgpu/power1", "PPT", "power", gpu_power, "W"),
                _ch("amdgpu/in0", "vddgfx", "volt", 0.70 + 0.30 * load, "V"),
                _ch("amdgpu/in1", "vddnb", "volt", 1.10, "V"),
                _ch("amdgpu/freq1", "sclk", "freq", float(sclk), "MHz"),
            ]),
            HwmonChip("/sys/class/hwmon/hwmon2", "k10temp", [
                _ch("k10temp/temp1", "Tctl", "temp", cpu_temp, "°C"),
            ]),
            HwmonChip("/sys/class/hwmon/hwmon3", "nct6686", [
                _ch("nct6686/temp1", "CPU", "temp", cpu_temp - 2.0, "°C"),
                _ch("nct6686/temp2", "System", "temp", 38.0 + 8.0 * load, "°C"),
                _ch("nct6686/temp3", "VRM MOS", "temp", 40.0 + 14.0 * load, "°C"),
                _ch("nct6686/fan1", "CPU Fan", "fan", 0.0, "RPM"),
                _ch("nct6686/fan2", "Pump Fan", "fan", fan_rpm, "RPM"),
                _ch("nct6686/pwm2", "PWM 2", "pwm", 35.0 + 60.0 * load, "%"),
                _ch("nct6686/in0", "+12V", "volt", 12.08, "V"),
                _ch("nct6686/in1", "+5V", "volt", 5.02, "V"),
                _ch("nct6686/in2", "+3.3V", "volt", 3.36, "V"),
                _ch("nct6686/in3", "CPU Vcore", "volt", 0.95 + 0.25 * load, "V"),
                _ch("nct6686/in4", "CPU Soc", "volt", 1.05, "V"),
            ]),
            HwmonChip("/sys/class/hwmon/hwmon4", "nvme", [
                _ch("nvme/temp1", "Composite", "temp", 36.0 + 9.0 * load, "°C"),
            ]),
        ]

        threads = 16          # the reference machine has eight cores unlocked
        cpu = CpuInfo(
            model="AMD 4700S (Cyan Skillfish)",
            cores_online=threads,
            cores_present=threads,
            cores_physical=8,
            freqs_mhz=[max(800.0, 800 + 2700 * min(1.0, load + jitter(0.2))) for _ in range(threads)],
            usage=[max(0.0, min(100.0, cpu_load + jitter(18))) for _ in range(threads)],
            total_usage=cpu_load,
            governor="schedutil",
            driver="acpi-cpufreq",
            loadavg=(cpu_load / 25, cpu_load / 30, cpu_load / 40),
        )

        mem_total = 16 * GIB
        mem_used = int((2.2 + 4.0 * load) * GIB)
        snap = Snapshot(
            ts=self._tick,
            system=SystemInfo(
                hostname="bc250-demo",
                kernel="6.18.18-lts",
                os_name="Bazzite 41 (demo)",
                uptime=7345 + self._tick * 60,
                board_vendor="AMD",
                board_name="BC-250",
                product_name="BC-250",
                bios_version="BC250.017",
                bios_date="2023-04-11",
            ),
            cpu=cpu,
            mem=MemInfo(total=mem_total, available=mem_total - mem_used, used=mem_used,
                        swap_total=8 * GIB, swap_used=int(0.3 * GIB)),
            gpu=gpu,
            chips=chips,
            modules={"amdgpu", "k10temp", "nct6687"},
            services={"cyan-skillfish-governor-smu.service": "active",
                      "cyan-skillfish-governor-tt.service": "inactive"},
            is_bc250=True,
        )
        snap.notes = coll.build_notes(snap)

        self._record(snap)
        self.samples += 1
        return snap


def _ch(key: str, label: str, kind: str, value: float, unit: str) -> Channel:
    return Channel(key=key, label=label, kind=kind, value=value, unit=unit)

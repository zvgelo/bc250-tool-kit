#!/usr/bin/env python3
"""Dump the raw gpu_metrics table of a BC-250 — a diagnostic tool.

Every clock the amdgpu driver exposes for this ASIC comes from one SMU metrics
table, described in smu11_driver_if_cyan_skillfish.h:

    struct SmuMetrics_t {
        SmuMetricsTable_t Current;   // instantaneous sample
        SmuMetricsTable_t Average;   // averaged over the SMU sampling window
        ...
    };

pp_dpm_sclk, the hwmon sclk channel and pp_od_clk_voltage all read
Current.GfxclkFrequency, while gpu_metrics also carries Average.GfxclkFrequency.
This script prints both side by side, together with the CPU core clocks that
come from the very same Current table — if those look right while the GFX clock
does not, the breakage is specific to GFXCLK rather than to the whole table.

    python3 dev/smu-metrics.py            # one sample
    python3 dev/smu-metrics.py -n 30 -i 2 # 30 samples every 2 s, e.g. under load
"""

from __future__ import annotations

import argparse
import glob
import pathlib
import struct
import sys
import time

# Offsets into struct gpu_metrics_v2_2 (kgd_pp_interface.h)
OFF_ACTIVITY = 0x1C
OFF_AVERAGE_GFXCLK = 0x40
OFF_CURRENT_GFXCLK = 0x4C
OFF_CURRENT_CORECLK = 0x58      # current_coreclk[8], the driver fills the first 6
OFF_CURRENT_SOCCLK = 0x4E
OFF_CURRENT_UCLK = 0x50
OFF_TEMPERATURE_CORE = 0x08     # temperature_core[8], centi-Celsius

# average_socket_power is uint16 in gpu_metrics, but the driver assigns
# metrics.Current.CurrentSocketPower, a uint32 in mW, so anything above 65.5 W
# wraps. Power is therefore read from hwmon, which goes through read_sensor and
# keeps the full value.


def find_card() -> pathlib.Path | None:
    for card in sorted(glob.glob("/sys/class/drm/card[0-9]*")):
        device = pathlib.Path(card) / "device"
        driver = (device / "driver").resolve().name if (device / "driver").exists() else ""
        if driver == "amdgpu":
            return device
    return None


def read_u16(blob: bytes, offset: int) -> int:
    return struct.unpack_from("<H", blob, offset)[0]


def cpufreq_average() -> float | None:
    values = []
    for path in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"):
        try:
            values.append(int(pathlib.Path(path).read_text()) / 1000.0)
        except (OSError, ValueError):
            pass
    return sum(values) / len(values) if values else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Raw BC-250 SMU metrics")
    parser.add_argument("-n", "--samples", type=int, default=1)
    parser.add_argument("-i", "--interval", type=float, default=2.0)
    args = parser.parse_args()

    device = find_card()
    if device is None:
        sys.stderr.write("no amdgpu card found\n")
        return 1

    hwmon = next(iter(sorted((device / "hwmon").glob("hwmon*"))), None)
    metrics_path = device / "gpu_metrics"
    if not metrics_path.exists():
        sys.stderr.write(f"{metrics_path} does not exist\n")
        return 1

    blob = metrics_path.read_bytes()
    size, fmt, content = struct.unpack_from("<HBB", blob, 0)
    print(f"gpu_metrics v{fmt}.{content}, {size} B, read from {metrics_path}")
    if fmt != 2:
        sys.stderr.write("unexpected format revision — the offsets below assume v2\n")

    header = (f"{'#':>3} {'avg gfxclk':>11} {'cur gfxclk':>11} {'uclk':>8} {'socclk':>8} "
              f"{'busy':>6} {'PPT':>7} {'core0 °C':>9} {'SMU core clocks (MHz)':>34} "
              f"{'cpufreq avg':>12}")
    print(header)
    print("-" * len(header))

    for index in range(args.samples):
        blob = metrics_path.read_bytes()
        average = read_u16(blob, OFF_AVERAGE_GFXCLK)
        current = read_u16(blob, OFF_CURRENT_GFXCLK)
        activity = read_u16(blob, OFF_ACTIVITY)
        cores = [read_u16(blob, OFF_CURRENT_CORECLK + 2 * i) for i in range(6)]
        uclk = read_u16(blob, OFF_CURRENT_UCLK)
        socclk = read_u16(blob, OFF_CURRENT_SOCCLK)
        core_temp = read_u16(blob, OFF_TEMPERATURE_CORE) / 100.0

        busy = "n/a" if activity == 0xFFFF else f"{activity / 100:.0f}%"
        power = 0.0
        if hwmon is not None:
            try:
                power = int((hwmon / "power1_average").read_text()) / 1e6
            except (OSError, ValueError):
                pass
        kernel = cpufreq_average()
        kernel_text = f"{kernel:.0f}" if kernel else "?"

        print(f"{index:>3} {average:>7} MHz {current:>7} MHz {uclk:>4} MHz {socclk:>4} MHz "
              f"{busy:>6} {power:>5.1f} W {core_temp:>7.1f} °C "
              f"{' '.join(f'{c:>5}' for c in cores)} {kernel_text:>12}")

        if index + 1 < args.samples:
            time.sleep(args.interval)

    print()
    print("All of these come from one firmware table. CoreFrequency sits at its very")
    print("start, MemclkFrequency ten bytes past GfxclkFrequency. If the core clocks")
    print("track cpufreq and uclk reads a sane figure while the GFX clock does not,")
    print("there is no struct-offset problem — the firmware writes that one field wrong.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Where `bc250-mon` gets its data

**English** · [Polski](data-sources.pl.md)

The full map of readings. Everything here is read-only — the program writes
nothing to `sysfs` and calls no tool that modifies hardware.

## GPU (amdgpu)

The card is located through `/sys/class/drm/card*/device`, where the `driver`
symlink points at `amdgpu`. That directory is called `$DEV` below.

| Path | What it gives | Notes |
|---|---|---|
| `$DEV/device` | PCI ID | `0x13FE` or `0x143F` → Cyan Skillfish recognised |
| `$DEV/pp_dpm_sclk` | core DPM levels + the active one (`*`) | see [scale correction](#clocks-reported-too-low-after-the-8-core-unlock) |
| `$DEV/pp_dpm_mclk` | memory DPM levels | |
| `$DEV/gpu_busy_percent` | GPU load | usually broken on the BC-250 |
| `$DEV/gpu_metrics` | GPU load (bytes 0x1C–0x1D) | `0xFFFF` means the field is not populated |
| `$DEV/mem_info_vram_total`, `..._used` | VRAM carve-out | the guaranteed **minimum**, not the ceiling — see below |
| `$DEV/mem_info_gtt_total`, `..._used` | GTT | |
| `$DEV/power_dpm_force_performance_level` | performance level | `auto`, `manual`, … |
| `$DEV/hwmon/hwmon*/temp1_input` | `edge` temperature | millidegrees |
| `$DEV/hwmon/hwmon*/power1_average` | PPT | microwatts; `power1_input` as a fallback |
| `$DEV/hwmon/hwmon*/in0_input`, `in1_input` | `vddgfx`, `vddnb` | millivolts |

### VRAM is a minimum, not a capacity

On this platform the whole 16 GB is shared, and the split set with
[`bc250memcfg`](https://github.com/fanoush/bc250_memcfg) only fixes the **smallest**
amount that is always reserved for the GPU. `mem_info_vram_total` reports that
carve-out. Anything beyond it is borrowed dynamically through GTT, so with the
common 512 MB split the bar sits near full whenever anything renders — that is
normal, not pressure.

The monitor therefore reports two figures: the carve-out (labelled `VRAM (min)`
when the split is 1 GiB or smaller) and `GPU memory`, which is
`vram_used + gtt_used` against `vram_total + gtt_total` — the practical ceiling.
With a 512 MB split and the default TTM limit that ceiling lands near 8 GiB,
matching the community documentation's ~8.25 GB figure. Raising it is a matter of
the `ttm.pages_limit` kernel parameter, and a bigger split is what fixes the
separate crash where scanout framebuffers cannot be pinned.

### Reading the running clock off the voltage curve

The averaged field is an average; the instantaneous one is broken. Neither shows
the frequency the GPU is running at right now. But the governor sets frequency
and voltage **together**, from the safe-point curve in its own configuration:

    /etc/cyan-skillfish-governor-smu/config.toml
    /etc/cyan-skillfish-governor-tt/config.toml

So a measured `vddgfx` can be mapped back onto that curve. On the reference board
under load, 912 mV falls between the 1700 MHz / 910 mV and 1850 MHz / 920 mV
points, which puts the running clock near **1730 MHz** — while the firmware's
average read 931 MHz at the same moment. The two are consistent: roughly half
duty at ~1730 MHz averages to ~930.

The monitor shows this next to the voltage it comes from, as
`vddgfx 912 mV · from curve ≈1730 MHz`, so the provenance is never in doubt. The
curve is not injective — 1500 and 1600 MHz share 900 mV on the stock curve — so a
shared voltage is reported as a range rather than a made-up single figure.

### The governor's D-Bus interface

`cyan-skillfish-governor-smu` also exposes a D-Bus service. It carries **no
current frequency** — introspection shows control methods and range properties
only — so it does not solve the clock problem. It does, however, give the range
actually in force, which the configuration file cannot: `SetRange` can narrow it
at runtime.

| Object | Interface | Contents |
|---|---|---|
| `/com/cyanskillfish/Governor/Range/Current` | `…Governor.Range` | `Min`, `Max` — the range being enforced |
| `/com/cyanskillfish/Governor/Range/Allowed` | `…Governor.Range` | the hard limits, 500–2000 MHz |
| `/com/cyanskillfish/Governor` | `…PerformanceMode` | `Enabled`, load targets, temperature thresholds, and the `Set*` methods |

The monitor reads `Range/Current` with `busctl get-property`, uses it for the
header and to scale the clock gauge, and falls back to `[frequency-range]` from
the file when D-Bus is unavailable. If the two disagree, the Diagnostics panel
says so — the file is then no longer describing what the GPU is doing.

### GPU load — the order of attempts

1. `gpu_busy_percent`, if it falls in the 0–100 range.
2. `gpu_metrics`, a little-endian uint16 at offset `0x1C`:
   - `0xFFFF` → the field is not populated by the driver, so the cause is
     reported instead of a number,
   - a value ≤ 100 → treated as percent,
   - a value ≤ 10000 → treated as hundredths of a percent (divided by 100).
3. Neither available → `n/a` in the interface, with an explanation.

It is exactly the `0xFFFF` reading divided by 100 that produces the famous
"655% GPU" in MangoHud. The `cyan-skillfish-governor-smu` service bind-mounts
over `gpu_metrics` and writes a sensible value there — then step 2 returns the
real load.

### Clocks reported too low after the 8-core unlock

After unlocking eight CPU cores `pp_dpm_sclk` starts lying, in one of two ways.

**The whole table is scaled down.** Documented as `1: 15Mhz` where 1500 MHz was
meant. Detected when the **highest** DPM level is below 100 MHz; the reading is
multiplied by 100 and the correction is reported in the Diagnostics panel.

**Only the active level is nonsense.** Reading `cyan_skillfish_ppt.c` explains
why. That table is not a DPM list at all: entries 0 and 2 are the compile-time
constants `CYAN_SKILLFISH_SCLK_MIN` (1000) and `CYAN_SKILLFISH_SCLK_MAX` (2000),
and only entry 1 is live — it is `metrics->Current.GfxclkFrequency` straight from
the SMU. When that SMU field goes bad the middle entry follows, which is what the
reference board shows: `0: 1000Mhz  1: 15Mhz *  2: 2000Mhz`, with successive
readings of 2, 15, 46 and 59 MHz. The hwmon `sclk` channel agrees, because
`read_sensor` feeds from the same field.

No single factor fixes those (15 x 100 = 1500 is right, 46 x 100 = 4600 is not),
so the monitor does not invent one. Instead it prefers **`gpu_metrics`**: the same
driver fills `average_gfxclk_frequency` from `metrics->Average.GfxclkFrequency` —
a *different* SMU field — so it can be sound where `Current` is not. The clock is
taken from there when it lands in a plausible 100–3000 MHz range, and reported as
unavailable otherwise. Either way the Diagnostics panel says which happened.

**What that figure actually is.** `smu11_driver_if_cyan_skillfish.h` defines the
table the firmware fills as

```c
struct SmuMetrics_t {
    SmuMetricsTable_t Current;   // instantaneous sample
    SmuMetricsTable_t Average;   // averaged over the SMU sampling window
    uint32_t SampleStartTime, SampleStopTime, Accnt;
};
```

So `Average.GfxclkFrequency` is a **time average over the firmware's sampling
window, idle gaps included** — not the clock the GPU runs at while rendering. The
monitor labels it `GPU clock (avg)` for that reason. A figure well below the
governor's configured minimum under load is expected from an average, and is not
by itself evidence that the governor is failing to boost.

**What the reference board actually reports.** Measured with `dev/smu-metrics.py`
during a Cyberpunk benchmark and cross-checked with `umr -gm`:

| Field | Reading |
|---|---|
| `Current.CoreFrequency[0..5]` | 3500 MHz, matching `cpufreq` at ~3490 |
| `Current.MemclkFrequency` | 450 MHz, correct — and it sits ten bytes *past* the GFX field |
| `Current.GfxclkFrequency` | 18–100 MHz, **never above exactly 100** |
| `Average.GfxclkFrequency` | 505 MHz at 36% activity, 931 at 73%, 932 at 56% |
| `Average.SocclkFrequency` / `UclkFrequency` / `DclkFrequency` | 1254 / 450 / 1111 MHz, all correct |

Two conclusions follow. First, the table is being read correctly: fields both
before and after the GFX one are right, so this is not a struct-offset problem
and the firmware simply writes that one field wrong. Second, the two GFX fields
behave differently — the instantaneous one is pinned below 100 whatever happens,
while the averaged one moves with load and sits among healthy neighbours.

So `Average.GfxclkFrequency` **is** a real reading and the monitor uses it. Being
an average over the firmware's window it includes the gaps where the GPU is
clock-gated, so it lands well below the frequency the governor actually sets —
930 MHz average is consistent with roughly half duty at ~1850 MHz. That is why it
is labelled `GPU clock (avg)`, and why a figure below the governor's configured
minimum is not evidence of anything being wrong.

`dev/smu-metrics.py` dumps both fields side by side, together with the CPU core
clocks from the same `Current` table, so this can be re-checked on any board.

### A driver bug worth knowing about

`gpu_metrics->average_socket_power` is a `uint16`, but `cyan_skillfish_ppt.c`
assigns `metrics.Current.CurrentSocketPower` into it — a `uint32` in **mW**.
Anything above 65.5 W wraps, which is why the field jumps around erratically
while the hwmon `power1_average` channel, which goes through `read_sensor` and
keeps the full value, reads a steady figure. Read power from hwmon, never from
that field.

In both cases the GPU clock itself is correct — this is a reporting bug, not a
performance one. The valid range is still visible in the DPM line.

## CPU

| Path | What it gives |
|---|---|
| `/proc/stat` | total and per-core load (differences between readings) |
| `/proc/cpuinfo` | CPU model, fallback clocks when `cpufreq` is absent |
| `/proc/loadavg` | 1/5/15-minute load averages |
| `/sys/devices/system/cpu/online`, `present` | count of logical CPUs online and present |
| `/sys/devices/system/cpu/cpu*/topology/core_id` + `physical_package_id` | physical cores — counted as unique package:core pairs |
| `/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq` | current clocks (kHz) |
| `/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor`, `scaling_driver` | governor and driver |
| `hwmon` `k10temp` → `Tctl` | CPU temperature (fallback: `coretemp` → `Package id 0`) |

Telling cores from threads matters after the eight-core unlock: the kernel then
reports 16 logical CPUs, so that number alone would look like 16 cores. The
monitor shows `8C/16T`. When `topology/` is unavailable, the value comes from the
`cpu cores` field in `/proc/cpuinfo`.

Without the SSDT-PST from [bc250-acpi-fix](https://github.com/bc250-collective/bc250-acpi-fix)
the `cpufreq` directory does not exist — clocks then come from `/proc/cpuinfo`
and the governor field reads "no cpufreq".

## Motherboard (SuperIO)

The chip reports as `nct6686-isa-0a20` regardless of whether `nct6683` or
`nct6687` is loaded. All of its channels are scanned:

| File pattern | Kind | Conversion |
|---|---|---|
| `temp*_input` | temperature | ÷ 1000 → °C |
| `fan*_input` | fan | RPM unchanged |
| `in*_input` | voltage | ÷ 1000 → V |
| `power*_average`, `power*_input` | power | ÷ 1 000 000 → W |
| `curr*_input` | current | ÷ 1000 → A |
| `freq*_input` | frequency | ÷ 1 000 000 → MHz |
| `pwm*` | PWM | 0–255 → percent |
| `*_label` | label | falls back to a generic name |
| `*_max`, `*_crit` | vendor thresholds | |

`nct6687` provides named labels (`CPU Fan`, `Pump Fan`, `VRM MOS`, `+12V`),
`nct6683` generic ones (`fan2`, `VIN0`, `Thermistor 14`).

### Fans on the dashboard

Two rules keep the board panel readable:

- **A fan that has never moved is hidden.** An unused header reads 0 RPM forever
  and tells you nothing; the BC-250 typically uses only one of J1 / J4003. A fan
  that spun at any point during the session stays on screen even after it stops,
  because that is a fact worth seeing. The sensor table (`Tab`) always lists every
  channel, hidden ones included.
- **RPM is shown together with the duty cycle**, for example `2633 RPM · 95%`.
  On the NCT6686 `pwmN` drives `fanN`, so the two are paired by channel number.
  A PWM channel with no matching fan is listed separately.

## Memory, drives, system

| Path | What it gives |
|---|---|
| `/proc/meminfo` | RAM and swap |
| `hwmon` `nvme` → `Composite` | drive temperature |
| `/proc/modules` | detection of `nct6683` / `nct6687` |
| `/sys/class/dmi/id/*` | board vendor and name, BIOS version |
| `/etc/os-release`, `/proc/uptime`, `uname` | system, uptime, kernel |
| `systemctl is-active` | state of `cyan-skillfish-governor-smu` and `-tt` (polled every 10 s) |

## Temperature thresholds

| Component | Warning | Critical |
|---|---|---|
| GPU (edge) | 85 °C | 90 °C |
| CPU (Tctl) | 80 °C | 90 °C |
| NVMe | 70 °C | 80 °C |
| Board (SuperIO) | 70 °C | 85 °C |

The values live in `bc250mon/collector.py`, in the `THRESHOLDS` dictionary.

# Troubleshooting — `bc250-mon`

**English** · [Polski](troubleshooting.pl.md)

## The "Board" panel is empty

The SuperIO module is not loaded. Check:

```bash
lsmod | grep nct
```

Load one of the two (never both at once):

```bash
sudo modprobe nct6687 force=true   # read + PWM control
sudo modprobe nct6683 force=true   # read-only
```

On Bazzite with the stock kernel `nct6687` ships as an akmod and `modprobe` is
enough. On a custom kernel (for example `-ogc`) the module has to be built from
[Fred78290/nct6687d](https://github.com/Fred78290/nct6687d) and loaded with
`insmod` — `make install` will not work because `/usr/lib/modules` is read-only.

## GPU load shows `n/a`

This is expected without the smu governor. The amdgpu driver does not populate
the activity field in `gpu_metrics`. The fix:

```bash
systemctl status cyan-skillfish-governor-smu
sudo systemctl enable --now cyan-skillfish-governor-smu.service
```

The governor bind-mounts over `gpu_metrics` and writes a sampled value there.
Checking it live:

```bash
sudo xxd -s 0x1c -l 2 /sys/class/drm/card0/device/gpu_metrics
# ffff = field not populated, anything else = working
```

## The GPU clock looks absurdly low

If the Diagnostics panel shows an entry about `pp_dpm_sclk`, this is the known
effect of the eight-core CPU unlock. When the whole table is scaled down
(`15Mhz` for 1500 MHz), the program multiplies it by 100. When only the active
level reads nonsense (98 MHz in a 1000–2000 MHz range), the GPU clock is taken
from the averaged field of `gpu_metrics` instead — an average that includes
clock-gated time, so it is lower than the frequency the governor sets. To check
the raw value:

```bash
cat /sys/class/drm/card0/device/pp_dpm_sclk
```

GPU performance is not affected — only the reporting is broken.

## The message "GPU locked at 1500 MHz"

Without a governor the GPU neither drops below nor rises above 1500 MHz.

```bash
sudo rpm-ostree install cyan-skillfish-governor-smu   # Bazzite / Fedora Atomic
sudo systemctl enable --now cyan-skillfish-governor-smu.service
```

## All fans report 0 RPM

The board has two headers: **J1** and **J4003**. The main fan usually reports as
`Pump Fan` (`fan2`). Zero RPM on a spinning fan most often means a 3-pin fan, a
splitter losing the tachometer signal, or a fan connected outside the monitored
headers. If you can hear the fan running, use the GPU temperature as
confirmation that cooling works.

A header that reads 0 RPM for the whole session is hidden from the dashboard on
purpose — look in the sensor table (`Tab`) if you want to see it anyway.

## The program exits with status 2

```
bc250-mon: no terminal — use --once or --json
```

The curses interface requires an interactive terminal. In a pipe, in cron or in a
script use `--once` or `--json`.

## "Terminal window too small"

The interface needs at least 40×8 characters. The full two-column layout starts at
100 columns; below that the program shows a single column. The sensor table
(`Tab`) works at any size and always shows the complete data.

## The thread list is cut short

When the CPU panel cannot fit every thread, it prints how many it omitted. The
complete list is always available in the sensors view (`Tab`), or in a taller
window.

## CPU temperature exceeds the threshold during a stress test

The critical CPU threshold is 90 °C, following the BC-250 documentation. A
synthetic CPU+GPU stress test runs hotter than real gaming, so crossing the
threshold is an expected warning rather than a false alarm. The thresholds can be
changed in the `THRESHOLDS` dictionary in `bc250mon/collector.py`.

## The interface is in the wrong language

The language is chosen from `--lang`, then `BC250_LANG`, then the system locale,
falling back to English:

```bash
bc250-mon --lang pl
BC250_LANG=en bc250-mon
```

Manual pages follow the same rule through the standard mechanism:
`man bc250-mon` is English, `LANG=pl_PL.UTF-8 man bc250-mon` is Polish.

## Reporting a bug

Attach the following:

```bash
bc250-mon --once > bc250-state.txt
bc250-mon --json > bc250-state.json
uname -r; lsmod | grep -E 'nct|amdgpu|k10temp'
```

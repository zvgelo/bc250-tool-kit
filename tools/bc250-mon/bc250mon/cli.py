"""Command-line entry point: TUI, one-shot text report, JSON dump."""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import __version__
from . import sysfs
from .i18n import LANGUAGES, set_language, t
from .collector import Collector, Snapshot, format_bytes, format_value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bc250-mon", description=t("cli.description"))
    parser.add_argument("-i", "--interval", type=float, default=1.0,
                        metavar=t("cli.interval.metavar"), help=t("cli.interval"))
    parser.add_argument("--once", action="store_true", help=t("cli.once"))
    parser.add_argument("--json", action="store_true", help=t("cli.json"))
    parser.add_argument("--demo", action="store_true", help=t("cli.demo"))
    parser.add_argument("--no-diagnostics", action="store_true", help=t("cli.nodiag"))
    parser.add_argument("--lang", choices=LANGUAGES, help=t("cli.lang"))
    parser.add_argument("-V", "--version", action="version",
                        version=f"bc250-mon {__version__}")
    return parser


def make_collector(demo: bool) -> Collector:
    if demo:
        from .demo import DemoCollector
        return DemoCollector()
    return Collector()


def main(argv: list[str] | None = None) -> int:
    # the language is settled before the parser is built, so --help speaks it too
    argv_list = list(sys.argv[1:] if argv is None else argv)
    set_language(_language_from_argv(argv_list))

    args = build_parser().parse_args(argv_list)
    set_language(args.lang)
    collector = make_collector(args.demo)

    if args.json or args.once:
        # the first /proc/stat reading is only a baseline for CPU load
        collector.collect()
        time.sleep(min(0.4, max(0.1, args.interval)))
        snap = collector.collect()
        if args.json:
            json.dump(snapshot_dict(snap), sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(text_report(snap, show_notes=not args.no_diagnostics))
        return 0

    if not sys.stdout.isatty():
        sys.stderr.write(t("cli.notty") + "\n")
        return 2

    from .tui import run
    try:
        run(collector, max(0.1, args.interval), show_notes=not args.no_diagnostics)
    except KeyboardInterrupt:
        pass
    return 0


def _language_from_argv(argv: list[str]) -> str | None:
    """Extracts --lang ahead of argparse, so the help text is in the right language."""
    for index, item in enumerate(argv):
        if item == "--lang" and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith("--lang="):
            return item.split("=", 1)[1]
    return None


# --------------------------------------------------------------------------
# Text report
# --------------------------------------------------------------------------

def text_report(snap: Snapshot, show_notes: bool = True) -> str:
    out: list[str] = []
    add = out.append

    def section(title: str) -> None:
        add("")
        add(f"── {title} " + "─" * max(0, 60 - len(title)))

    gov_name, gov_state = snap.governor_state
    add(f"bc250-mon {__version__} — "
        f"{t('report.detected') if snap.is_bc250 else t('mode.generic')}")
    add(f"  {t('report.host'):<9} {snap.system.hostname}  ({snap.system.os_name})")
    add(f"  {t('report.kernel'):<9} {snap.system.kernel}"
        f"   {t('header.uptime')} {sysfs.uptime_str(snap.system.uptime)}")
    add(f"  {t('report.board'):<9} {snap.system.board_vendor or '?'}"
        f" {snap.system.board_name or '?'}"
        f"   BIOS {snap.system.bios_version or '?'} ({snap.system.bios_date or '?'})")
    add(f"  {t('report.superio'):<9} {snap.superio_module or t('report.superio.none')}")
    add(f"  {t('report.governor'):<9} {gov_name}: {gov_state}")

    section("GPU")
    gpu = snap.gpu
    if gpu.device_path:
        add(f"  {t('report.chip'):<14}"
            f"{'Cyan Skillfish (gfx1013)' if gpu.is_cyan_skillfish else 'amdgpu'}"
            f"  {gpu.pci_slot or ''} 0x{gpu.pci_id:04X}" if gpu.pci_id else "  amdgpu")
        add(f"  {t('report.temperature'):<14}{format_value(snap.gpu_temp, '°C')}")
        add(f"  {t('report.power'):<14}{format_value(snap.gpu_power, 'W')}")
        add(f"  {t('report.load'):<14}"
            f"{format_value(gpu.busy_percent, '%') if gpu.busy_percent is not None else t('value.unavailable')}"
            f"{'' if not gpu.metrics_note else '  (' + gpu.metrics_note + ')'}")
        add(f"  {t('report.core.clock'):<14}{format_value(_f(snap.gpu_clock_mhz), 'MHz')}")
        add(f"  {t('report.mem.clock'):<14}{format_value(_f(snap.gpu_mem_clock_mhz), 'MHz')}")
        add(f"  {'vddgfx':<14}{format_value(snap.gpu_vddgfx, 'V')}")
        add(f"  {'vddnb':<14}{format_value(snap.gpu_vddnb, 'V')}")
        add(f"  {'VRAM':<14}{format_bytes(gpu.vram_used)} / {format_bytes(gpu.vram_total)}")
        if gpu.sclk:
            scale = 100 if gpu.sclk_suspect else 1
            levels = "  ".join(f"{'[' if l.active else ' '}{l.mhz * scale}"
                               f"{']' if l.active else ' '}" for l in gpu.sclk)
            add(f"  {'DPM sclk':<14}{levels}   {t('gpu.level')}: {gpu.perf_level or '?'}")
    else:
        add("  " + t("report.nogpu"))

    section("CPU")
    cpu = snap.cpu
    add(f"  {t('report.model'):<14}{cpu.model}")
    add(f"  {t('report.cores'):<14}"
        + t("report.cores.value", physical=cpu.cores_physical,
            online=cpu.cores_online, present=cpu.cores_present))
    add(f"  {snap.cpu_temp_label:<14}{format_value(snap.cpu_temp, '°C')}")
    add(f"  {t('report.load'):<14}{format_value(cpu.total_usage, '%')}"
        f"   load {' '.join(f'{v:.2f}' for v in cpu.loadavg)}")
    if cpu.freqs_mhz:
        add(f"  {t('report.clocks'):<14}"
            + "  ".join(f"{f:.0f}" for f in cpu.freqs_mhz) + " MHz")
    add(f"  {'governor':<14}{cpu.governor or t('cpu.nocpufreq')}"
        f"{f' ({cpu.driver})' if cpu.driver else ''}")

    section(t("report.memory"))
    add(f"  {'RAM':<14}{format_bytes(snap.mem.used)} / {format_bytes(snap.mem.total)}")
    if snap.mem.swap_total:
        add(f"  {t('report.swap'):<14}{format_bytes(snap.mem.swap_used)} / {format_bytes(snap.mem.swap_total)}")
    for name, temp in snap.nvme_temps:
        add(f"  {'NVMe ' + name:<14}{format_value(temp, '°C')}")

    for chip in snap.chips:
        if chip.name.startswith(("amdgpu", "k10temp", "nvme")):
            continue
        section(f"{t('report.hwmon')}: {chip.name}")
        for ch in chip.channels:
            add(f"  {ch.label:<20}{format_value(ch.value, ch.unit):>12}")

    if snap.notes and show_notes:
        section(t("panel.notes"))
        for note in snap.notes:
            add(f"  • {note}")

    add("")
    return "\n".join(out)


# --------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------

def snapshot_dict(snap: Snapshot) -> dict:
    gov_name, gov_state = snap.governor_state
    return {
        "bc250": snap.is_bc250,
        "system": {
            "hostname": snap.system.hostname,
            "kernel": snap.system.kernel,
            "os": snap.system.os_name,
            "uptime_s": round(snap.system.uptime, 1),
            "board_vendor": snap.system.board_vendor,
            "board_name": snap.system.board_name,
            "bios_version": snap.system.bios_version,
            "superio_module": snap.superio_module,
            "governor_service": {"unit": gov_name, "state": gov_state},
        },
        "gpu": {
            "card": snap.gpu.card,
            "pci_id": f"0x{snap.gpu.pci_id:04X}" if snap.gpu.pci_id else None,
            "pci_slot": snap.gpu.pci_slot,
            "cyan_skillfish": snap.gpu.is_cyan_skillfish,
            "temp_c": snap.gpu_temp,
            "power_w": snap.gpu_power,
            "busy_percent": snap.gpu.busy_percent,
            "busy_source": snap.gpu.busy_source,
            "busy_note": snap.gpu.metrics_note,
            "sclk_mhz": snap.gpu_clock_mhz,
            "mclk_mhz": snap.gpu_mem_clock_mhz,
            "sclk_suspect": snap.gpu.sclk_suspect,
            "vddgfx_v": snap.gpu_vddgfx,
            "vddnb_v": snap.gpu_vddnb,
            "vram_used": snap.gpu.vram_used,
            "vram_total": snap.gpu.vram_total,
            "perf_level": snap.gpu.perf_level,
            "dpm_sclk": [{"index": l.index, "mhz": l.mhz, "active": l.active}
                         for l in snap.gpu.sclk],
        },
        "cpu": {
            "model": snap.cpu.model,
            "cores_online": snap.cpu.cores_online,
            "cores_present": snap.cpu.cores_present,
            "temp_c": snap.cpu_temp,
            "usage_percent": round(snap.cpu.total_usage, 2),
            "per_core_usage": [round(v, 2) for v in snap.cpu.usage],
            "freqs_mhz": [round(v, 1) for v in snap.cpu.freqs_mhz],
            "governor": snap.cpu.governor,
            "driver": snap.cpu.driver,
            "loadavg": list(snap.cpu.loadavg),
        },
        "memory": {
            "total": snap.mem.total, "used": snap.mem.used,
            "swap_total": snap.mem.swap_total, "swap_used": snap.mem.swap_used,
        },
        "hwmon": {
            chip.name: [
                {"label": ch.label, "kind": ch.kind, "value": ch.value, "unit": ch.unit}
                for ch in chip.channels
            ] for chip in snap.chips
        },
        "notes": snap.notes,
    }


def _f(value: int | None) -> float | None:
    return None if value is None else float(value)

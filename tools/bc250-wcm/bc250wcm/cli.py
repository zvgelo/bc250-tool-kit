"""Command-line entry point: the TUI, --list, --json and --headless sampling."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time

from . import __version__, bluez, config, export, mgmt, telemetry, usb
from .engine import MAX_MONITORED, Engine, Target, bt_target, usb_target
from .history import EventLog
from .i18n import LANGUAGES, set_language, t


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bc250-wcm", description=t("cli.description"),
                                     epilog=t("cli.epilog"))
    parser.add_argument("-i", "--interval", type=float, metavar=t("cli.interval.metavar"),
                        help=t("cli.interval"))
    parser.add_argument("-m", "--monitor", nargs="+", metavar="ID", default=[], help=t("cli.monitor"))
    parser.add_argument("--list", action="store_true", help=t("cli.list"))
    parser.add_argument("--json", action="store_true", help=t("cli.json"))
    parser.add_argument("--headless", type=float, metavar=t("cli.interval.metavar"),
                        help=t("cli.headless"))
    parser.add_argument("--export", metavar="FILE", help=t("cli.export"))
    parser.add_argument("--record", metavar="FILE.csv", help=t("cli.record"))
    parser.add_argument("--rssi-backend", choices=config.BACKENDS, help=t("cli.backend"))
    parser.add_argument("--no-sudo", action="store_true", help=t("cli.nosudo"))
    parser.add_argument("--no-kernel-log", action="store_true", help=t("cli.nokernel"))
    parser.add_argument("--no-usb", action="store_true", help=t("cli.nousb"))
    parser.add_argument("--no-input-activity", action="store_true", help=t("cli.noactivity"))
    parser.add_argument("--config", metavar="FILE", help=t("cli.config"))
    parser.add_argument("--print-config", action="store_true", help=t("cli.printconfig"))
    parser.add_argument("--ascii", action="store_true", help=t("cli.ascii"))
    parser.add_argument("--no-color", action="store_true", help=t("cli.nocolor"))
    parser.add_argument("--demo", action="store_true", help=t("cli.demo"))
    parser.add_argument("--demo-speed", type=float, default=1.0, help=t("cli.demospeed"))
    parser.add_argument("--lang", choices=LANGUAGES, help=t("cli.lang"))
    parser.add_argument("-V", "--version", action="version", version=f"bc250-wcm {__version__}")
    return parser


def _language_from_argv(argv: list[str]) -> str | None:
    for index, item in enumerate(argv):
        if item == "--lang" and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith("--lang="):
            return item.split("=", 1)[1]
    return None


# --------------------------------------------------------------------------
# RSSI backend selection
# --------------------------------------------------------------------------

def sudo_validate(helper_path: str) -> bool:
    """`sudo -v` on the terminal, before curses takes it over. False if declined."""
    sys.stderr.write(t("cli.sudo.why", path=helper_path) + "\n")
    sys.stderr.flush()
    try:
        result = subprocess.run(["sudo", "-v", "-p", t("cli.sudo.prompt")], check=False)
    except KeyboardInterrupt:
        sys.stderr.write("\n")
        return False
    except OSError:
        return False
    return result.returncode == 0


def make_backend(choice: str, sudo_mode: str, interactive: bool) -> telemetry.RssiBackend:
    """Picks the least privileged way to read link RSSI that works.

    auto (unprivileged): the installed helper through a sudoers rule
    (no prompt), else the bundled helper after one `sudo -v`, else nothing.
    """
    euid = os.geteuid()
    if choice == "none":
        return telemetry.NullBackend(t("backend.disabled"))
    if choice in ("auto", "direct") and euid == 0:
        backend = telemetry.DirectBackend()
        backend.start()
        return backend
    if choice == "direct":
        return telemetry.NullBackend(t("backend.direct.notroot"))
    if choice == "btmgmt":
        backend = telemetry.BtmgmtBackend(use_sudo=euid != 0)
        if euid != 0 and sudo_mode == "ask" and interactive:
            sudo_validate("btmgmt")
        backend.start()
        return backend

    # helper / auto
    for installed in (telemetry.INSTALLED_HELPER, telemetry.LEGACY_HELPER):
        if telemetry.installed_helper_ok(installed):
            backend = telemetry.HelperBackend(["sudo", "-n", "--", installed])
            backend.start()
            if backend.available:
                return backend
    argv = ["sudo", "-n", "--", telemetry.system_python(), "-I", telemetry.HELPER_FILE]
    # a cached sudo timestamp needs no prompt
    if _sudo_cached():
        backend = telemetry.HelperBackend(argv)
        backend.start()
        if backend.available:
            return backend
    if sudo_mode == "ask" and interactive:
        if not sudo_validate(telemetry.HELPER_FILE):
            return telemetry.NullBackend(t("backend.sudo.declined"))
        backend = telemetry.HelperBackend(argv)
        backend.start()
        if not backend.available:
            sys.stderr.write(t("cli.helper.failed", reason=backend.problem or "?") + "\n")
            return telemetry.NullBackend(backend.problem)
        return backend
    return telemetry.NullBackend(t("backend.needs.sudo"))


def _sudo_cached() -> bool:
    try:
        return subprocess.run(["sudo", "-n", "-v"], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=5, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    set_language(_language_from_argv(argv_list))
    if argv_list and argv_list[0] == "hid":
        from . import hidcli
        rest = [a for a in argv_list[1:] if not a.startswith("--lang")]
        return hidcli.main(rest)
    args = build_parser().parse_args(argv_list)
    set_language(args.lang)

    if args.print_config:
        sys.stdout.write(config.EXAMPLE)
        return 0

    if not args.config:
        for line in config.migrate_legacy():
            sys.stderr.write(t("cli.migrated", what=line) + "\n")
    cfg, warnings = config.load(args.config)
    for warning in warnings:
        sys.stderr.write(t("cli.config.warning", text=warning) + "\n")
    if args.interval is not None:
        cfg.interval = max(0.2, min(60.0, args.interval))
    if args.ascii:
        cfg.graph = "ascii"
        cfg.ascii = True
    if args.no_color:
        cfg.colors = "none"
    if args.no_sudo:
        cfg.sudo = "never"
    if args.rssi_backend:
        cfg.backend = args.rssi_backend
    if args.no_kernel_log:
        cfg.follow_kernel = False
    if args.no_usb:
        cfg.usb = False
    if args.no_input_activity:
        cfg.input_activity = False

    monitor = [normalize_id(a) for a in args.monitor]
    for given, ident in zip(args.monitor, monitor):
        if ident is None:
            sys.stderr.write(t("cli.monitor.invalid", address=given) + "\n")
            return 2
    if len(monitor) > MAX_MONITORED:
        sys.stderr.write(t("cli.monitor.max", count=MAX_MONITORED) + "\n")
        return 2

    one_shot = args.list or args.json
    if args.headless is not None and not monitor:
        sys.stderr.write(t("cli.headless.needs") + "\n")
        return 2
    if not one_shot and args.headless is None and not sys.stdout.isatty():
        sys.stderr.write(t("cli.notty") + "\n")
        return 2

    # --- sources ---------------------------------------------------------------
    if args.demo:
        # simulated devices must not end up in the real selection file
        cfg.remember_selection = False
        from .demo import DemoBackend, DemoKernel, DemoSource, DemoWorld, demo_sysfs_battery
        from .demo import DemoUsb
        world = DemoWorld(args.demo_speed)
        source = DemoSource(world)
        usb_source = DemoUsb(world) if cfg.usb else None
        backend: telemetry.RssiBackend = DemoBackend(world)
        kernel_factory = lambda cb: DemoKernel(world, cb)  # noqa: E731
        battery_reader = demo_sysfs_battery
    else:
        source = bluez.BluezSource()
        usb_source = usb.UsbSource(known_file=os.path.join(config.state_dir(), "receivers")) \
            if cfg.usb else None
        kernel_factory = None
        battery_reader = None
        if one_shot:
            backend = telemetry.NullBackend()
        else:
            backend = make_backend(cfg.backend, cfg.sudo, sys.stdin.isatty())

    if one_shot:
        source.start()
        source.wait_ready()
        snap = source.snapshot()
        source.stop()
        usb_devices: list[usb.UsbDevice] = []
        if usb_source is not None:
            usb_source.rescan()
            usb_devices = usb_source.snapshot().devices
            usb_source.stop()
        if args.json:
            json.dump(snapshot_dict(snap, usb_devices), sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(list_report(snap, usb_devices))
        return 0 if snap.status == bluez.STATUS_OK or usb_devices else 1

    events = EventLog(path=cfg.event_file if cfg.log_events and not args.demo else None)
    engine_kwargs = {}
    if battery_reader is not None:
        engine_kwargs["battery_reader"] = battery_reader
    if args.demo:
        engine_kwargs["bt_inputs"] = lambda _address: []
        engine_kwargs["activity_factory"] = world.activity_reader
    engine = Engine(source, backend, cfg, events, follow_kernel=cfg.follow_kernel,
                    kernel_factory=kernel_factory, usb_source=usb_source, **engine_kwargs)
    recorder = None
    if args.record:
        recorder = export.CsvRecorder(args.record)
        engine.recorder = recorder

    # SIGTERM and SIGHUP end the program the same way q does, so the helper,
    # journalctl and the probe threads are always cleaned up
    def _terminate(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGHUP, _terminate)

    engine.start()
    try:
        if args.headless is not None:
            return run_headless(engine, monitor, args.headless, args.export)
        from .tui import run
        preselect = monitor or engine.load_selection()
        run(engine, cfg, preselect, start_monitoring=bool(monitor))
    except KeyboardInterrupt:
        pass
    finally:
        engine.save_selection()
        engine.stop()
        if recorder is not None:
            recorder.close()
    return 0


def normalize_id(value: str) -> str | None:
    """A Bluetooth address (any case, optional bt: prefix) or a usb:<id>; None if neither."""
    value = value.strip()
    if value.lower().startswith("usb:") and len(value) > 4:
        return "usb:" + value[4:]
    if value.lower().startswith("bt:"):
        value = value[3:]
    value = value.upper()
    return value if mgmt.valid_address(value) else None


def resolve_targets(engine: Engine, ids: list[str]) -> list[Target]:
    """Targets for the ids that exist right now (Bluetooth address or usb:<physical id>)."""
    snap = engine.snapshot()
    usb_devices = engine.usb_snapshot().devices
    targets = []
    for ident in ids:
        ident = ident[3:] if ident.startswith("bt:") else ident
        if ident.startswith("usb:"):
            for dev in usb_devices:
                if dev.physical_id == ident or f"usb:{dev.port}" == ident:
                    targets.append(usb_target(dev))
                    break
            continue
        for dev in snap.devices:
            adapter = snap.adapter_by_path(dev.adapter_path)
            if dev.address == ident and adapter is not None:
                targets.append(bt_target(adapter.identity, dev))
                break
    return targets


def run_headless(engine: Engine, ids: list[str], seconds: float, export_path: str | None) -> int:
    engine.source.wait_ready()
    deadline = time.time() + 3.0
    chosen: list[Target] = []
    while time.time() < deadline:
        chosen = resolve_targets(engine, ids)
        if len(chosen) == len(ids):
            break
        time.sleep(0.2)
    found = {tg.address for tg in chosen} | {tg.key for tg in chosen}
    for ident in ids:
        if ident not in found and not any(tg.address.endswith(ident[4:]) for tg in chosen
                                          if ident.startswith("usb:")):
            sys.stderr.write(t("cli.headless.missing", address=ident) + "\n")
    if not chosen:
        return 1

    printed: dict[str, int] = {}

    def show(track, sample) -> None:
        unit = "dB" if track.rssi_kind == "dB" else "dBm"
        value = "—" if sample.rssi is None else f"{sample.rssi} {unit}"
        if track.kind == "usb":
            value = "RSSI n/a"
        if sample.activity is not None:
            value += f"  {sample.activity} ev"
        extra = f"  ({sample.detail})" if sample.detail else ""
        sys.stdout.write(f"{time.strftime('%H:%M:%S', time.localtime(sample.ts))}  "
                         f"{track.name[:28]:<28} {track.address}  {sample.state:<12} {value:>9}{extra}\n")
        sys.stdout.flush()
        printed[track.key] = printed.get(track.key, 0) + 1

    previous = engine.recorder

    def both(track, sample) -> None:
        show(track, sample)
        if previous is not None:
            previous(track, sample)

    engine.recorder = both
    engine.set_monitored(chosen)
    end = time.time() + seconds
    try:
        while time.time() < end:
            time.sleep(0.2)
    finally:
        # no more per-sample lines once the summary starts
        engine.recorder = previous
        engine.set_paused(True)
    tracks = [engine.tracks[k] for k in engine.monitored]
    for track in tracks:
        s = track.stats
        sys.stdout.write(f"{track.name} {track.address}: min {s.min} max {s.max} "
                         f"avg {'—' if s.avg is None else f'{s.avg:.1f}'} "
                         f"sd {'—' if s.stdev is None else f'{s.stdev:.1f}'} "
                         f"samples {track.total_samples} rssi {track.rssi_samples} "
                         f"drops {track.disconnects}\n")
    for line in [e for e in engine.events.events() if e.level != "info"]:
        sys.stdout.write(f"! {time.strftime('%H:%M:%S', time.localtime(line.ts))} {line.text}\n")
    if export_path:
        if export_path.lower().endswith(".json"):
            count = export.export_json(tracks, engine.events, export_path,
                                       {"interval_s": engine.interval,
                                        "rssi_backend": engine.backend.name})
        else:
            count = export.export_csv(tracks, export_path)
        sys.stdout.write(t("cli.exported", path=export_path, count=count) + "\n")
    return 0


# --------------------------------------------------------------------------
# --list and --json
# --------------------------------------------------------------------------

def _yn(value) -> str:
    return "?" if value is None else ("yes" if value else "no")


def usb_dict(d: usb.UsbDevice) -> dict:
    return {
        "id": d.physical_id, "port": d.port, "usb_id": d.usb_id, "name": d.display_name,
        "manufacturer": d.manufacturer, "product": d.product_name, "transport": d.transport,
        "profile": d.profile.id if d.profile else None, "mode": d.mode,
        "receiver_confirmed": d.is_receiver, "battery": d.battery[0] if d.battery else None,
        "rssi": None,
        "interfaces": [{
            "number": i.number, "class": i.cls, "driver": i.driver, "role": i.role,
            "hidraw": [{"node": h.name, "hid_name": h.hid_name, "hid_phys": h.hid_phys} for h in i.hidraw],
            "inputs": [{"node": n.name, "name": n.input_name, "joystick": n.joystick} for n in i.inputs],
        } for i in d.interfaces],
    }


def list_report(snap: bluez.Snapshot, usb_devices: list | None = None) -> str:
    out = [f"bc250-wcm {__version__} — Wireless Controller Monitor — BlueZ: {t('bluez.' + snap.status)}"
           + (f" ({snap.error})" if snap.error else ""), ""]
    out.append(t("cli.list.adapters"))
    if not snap.adapters:
        out.append("  " + t("cli.list.none"))
    for a in snap.adapters:
        out.append(f"  {a.name:<6} {a.address}  powered={_yn(a.powered)}"
                   f"  usb={a.usb_id or '?'} port={a.usb_port or '?'}"
                   f"  {a.usb_product or ''}  identity={a.identity}")
    out.append("")
    out.append(t("cli.list.devices"))
    if not snap.devices:
        out.append("  " + t("cli.list.none"))
    out.append(f"  {'#':>2} {'Name':<28} {'MAC':<17} {'Type':<14} {'Adapter':<7} "
               f"{'Conn':<4} {'Pair':<4} {'Bond':<4} {'Trust':<5} {'RSSI':>5} {'Batt':>5}")
    by_path = {a.path: a for a in snap.adapters}
    devices = sorted(snap.devices, key=lambda d: (not d.connected, d.display_name.lower()))
    for i, d in enumerate(devices, 1):
        adapter = by_path.get(d.adapter_path)
        out.append(f"  {i:>2} {d.display_name[:28]:<28} {d.address:<17} "
                   f"{(d.kind + ' ' + d.bearer)[:14]:<14} {(adapter.name if adapter else '?'):<7} "
                   f"{_yn(d.connected):<4} {_yn(d.paired):<4} {_yn(d.bonded):<4} {_yn(d.trusted):<5} "
                   f"{'' if d.rssi is None else d.rssi:>5} "
                   f"{'' if d.battery is None else str(d.battery) + '%':>5}")
    out.append("")
    out.append(t("cli.list.usb"))
    if not usb_devices:
        out.append("  " + t("cli.list.none"))
    for d in usb_devices or []:
        battery = f"{d.battery[0]}%" if d.battery else "n/a"
        mode = t("usb.mode." + (d.mode or "unknown"))
        out.append(f"  {d.physical_id:<16} {d.usb_id}  {d.display_name}")
        out.append(f"  {'':<16} {t('cli.list.transport.' + d.transport)} · {mode} · "
                   f"drivers: {', '.join(d.drivers) or '-'} · battery {battery} · RSSI n/a")
        for iface, node in d.hidraw_nodes():
            out.append(f"  {'':<16} if{iface.number} {node.name} {node.hid_name or ''}")
    out.append("")
    return "\n".join(out)


def snapshot_dict(snap: bluez.Snapshot, usb_devices: list | None = None) -> dict:
    return {
        "tool": f"bc250-wcm {__version__}",
        "usb": [usb_dict(d) for d in usb_devices or []],
        "bluez": {"status": snap.status, "error": snap.error},
        "adapters": [{
            "name": a.name, "index": a.index, "path": a.path, "address": a.address,
            "address_type": a.address_type, "alias": a.alias, "powered": a.powered,
            "power_state": a.power_state, "discovering": a.discovering, "usb_id": a.usb_id,
            "usb_port": a.usb_port, "usb_product": a.usb_product, "driver": a.driver,
            "identity": a.identity,
        } for a in snap.adapters],
        "devices": [{
            "name": d.name, "alias": d.alias, "address": d.address,
            "address_type": d.address_type, "adapter": d.adapter_path, "kind": d.kind,
            "bearer": d.bearer, "connected_bearer": d.connected_bearer, "icon": d.icon,
            "class": d.device_class, "appearance": d.appearance, "connected": d.connected,
            "paired": d.paired, "bonded": d.bonded, "trusted": d.trusted, "blocked": d.blocked,
            "services_resolved": d.services_resolved, "rssi_advertising": d.rssi,
            "tx_power_advertised": d.tx_power, "battery": d.battery,
            "battery_source": d.battery_source, "modalias": d.modalias, "uuids": d.uuids,
            "gatt_services": [u for u, _ in d.gatt_services],
        } for d in snap.devices],
    }


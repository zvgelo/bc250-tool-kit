"""Findings for the Diagnostics strip: what is wrong, and what can be done about it.

Each finding is (level, text). Advice never runs anything — resetting an
adapter or restarting bluetoothd is left to the user.
"""

from __future__ import annotations

import os
import time

from . import bluez, telemetry
from .i18n import t

ERROR = "error"
WARN = "warn"
INFO = "info"


def diagnose(engine, now: float | None = None) -> list[tuple[str, str]]:
    now = time.time() if now is None else now
    out: list[tuple[str, str]] = []
    snap = engine.snapshot()

    if snap.status == bluez.STATUS_NO_BUS:
        out.append((ERROR, t("diag.nobus", error=snap.error or "?")))
        return out
    if snap.status == bluez.STATUS_NO_BLUEZ:
        out.append((ERROR, t("diag.nobluez")))
        return out
    if snap.status == bluez.STATUS_ERROR:
        out.append((ERROR, t("diag.bluez.error", error=snap.error or "?")))
    if snap.status == bluez.STATUS_OK and not snap.adapters:
        out.append((ERROR, t("diag.noadapter")))

    addresses: dict[str, int] = {}
    for adapter in snap.adapters:
        addresses[adapter.address] = addresses.get(adapter.address, 0) + 1
        if adapter.power_state == "off-blocked":
            out.append((ERROR, t("diag.rfkill", name=adapter.name)))
        elif adapter.powered is False:
            out.append((WARN, t("diag.poweroff", name=adapter.name)))
        if adapter.discovering:
            out.append((WARN, t("diag.discovering", name=adapter.name)))
    for address, count in addresses.items():
        if count > 1:
            out.append((WARN, t("diag.dupaddr", address=address, count=count)))

    for identity, record in engine.adapter_records.items():
        if record.renames:
            when, old, new = record.renames[-1]
            out.append((WARN, t("diag.remap", old=old, new=new, address=record.address,
                                when=time.strftime("%H:%M:%S", time.localtime(when)),
                                count=len(record.renames))))
        if not record.present:
            out.append((ERROR, t("diag.adapter.missing", name=record.name, address=record.address)))

    for name, counters in sorted(engine.kernel.items()):
        if counters.cmd_timeouts or counters.usb_resets or counters.hw_errors:
            out.append((ERROR, t("diag.kernel", name=name, timeouts=counters.cmd_timeouts,
                                 resets=counters.usb_resets, hw=counters.hw_errors)))
        if counters.scan_timeouts:
            out.append((WARN, t("diag.scan.timeouts", name=name)))

    backend = engine.backend
    if not backend.available:
        out.append((WARN, t("diag.nobackend", reason=backend.problem or t("diag.nobackend.off"))))
    statuses = set(engine.backend_status.values())
    if telemetry.PERMISSION in statuses:
        out.append((ERROR, t("diag.permission")))
    if telemetry.INVALID_INDEX in statuses:
        out.append((WARN, t("diag.invalid.index")))

    for key in engine.monitored:
        track = engine.tracks.get(key)
        if track is None or track.kind != "usb":
            continue
        dev = engine.resolve_usb(track)
        if dev is not None and dev.transport == "usb-2.4?":
            out.append((INFO, t("diag.usb.ambiguous", name=track.name)))
        if track.activity_status == "permission":
            out.append((WARN, t("diag.input.denied", node=track.input_node or "?")))
        if track.connected and track.last_input is not None and time.time() - track.last_input > 60:
            out.append((INFO, t("diag.usb.quiet", name=track.name)))
    follower = engine.kernel_follower
    if follower is not None and follower.problem:
        out.append((INFO, t("diag.kernlog", reason=follower.problem)))

    bredr = False
    tx_zero = False
    for key in engine.monitored:
        track = engine.tracks.get(key)
        if track is None:
            continue
        if track.timeouts:
            out.append((WARN, t("diag.timeouts", name=track.name, count=track.timeouts)))
        availability = track.availability()
        if availability is not None and track.connected_samples >= 10 and availability < 90:
            out.append((INFO, t("diag.availability", name=track.name, value=f"{availability:.0f}")))
        if track.rssi_kind == "dB":
            bredr = True
        if track.last_tx_power == 0 and track.last_max_tx_power == 0:
            tx_zero = True
    if bredr:
        out.append((INFO, t("diag.bredr")))
    if tx_zero:
        out.append((INFO, t("diag.txzero")))
    if os.geteuid() == 0:
        out.append((INFO, t("diag.root")))
    if engine.events.write_error:
        out.append((INFO, t("diag.logfile", error=engine.events.write_error)))
    return out


def level_rank(level: str) -> int:
    return {ERROR: 0, WARN: 1, INFO: 2}.get(level, 3)


def sorted_findings(findings: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return sorted(findings, key=lambda f: level_rank(f[0]))


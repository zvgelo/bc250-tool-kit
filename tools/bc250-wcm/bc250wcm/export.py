"""Export of measurements: CSV (one row per sample) and JSON (samples, stats, events)."""

from __future__ import annotations

import csv
import json
import os
import threading
import time

from . import __version__
from .history import EventLog, Sample, Track

CSV_FIELDS = ["time", "epoch", "device", "address", "adapter", "state", "rssi", "rssi_unit",
              "rssi_source", "tx_power_dbm", "max_tx_power_dbm", "battery_percent",
              "query_seconds", "detail"]


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)) + f".{int((ts % 1) * 1000):03d}"


def sample_row(track: Track, sample: Sample) -> dict:
    return {
        "time": _iso(sample.ts),
        "epoch": f"{sample.ts:.3f}",
        "device": track.name,
        "address": track.address,
        "adapter": track.adapter_name or "",
        "state": sample.state,
        "rssi": "" if sample.rssi is None else sample.rssi,
        "rssi_unit": "" if sample.rssi is None else _unit(track, sample),
        "rssi_source": sample.source or "",
        "tx_power_dbm": "" if sample.tx_power is None else sample.tx_power,
        "max_tx_power_dbm": "" if sample.max_tx_power is None else sample.max_tx_power,
        "battery_percent": "" if sample.battery is None else sample.battery,
        "query_seconds": "" if sample.latency is None else f"{sample.latency:.3f}",
        "detail": sample.detail,
    }


def _unit(track: Track, sample: Sample) -> str:
    # BR/EDR "RSSI" from HCI_Read_RSSI is relative to the golden receive range
    if sample.source == "conn" and track.rssi_kind == "dB":
        return "dB (relative, BR/EDR)"
    return "dBm"


def default_name(ext: str) -> str:
    return time.strftime(f"bc250-wcm-%Y%m%d-%H%M%S.{ext}")


def export_csv(tracks: list[Track], path: str) -> int:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    rows = []
    for track in tracks:
        for sample in track.snapshot_samples():
            rows.append(sample_row(track, sample))
    rows.sort(key=lambda r: (float(r["epoch"]), r["address"]))
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def track_summary(track: Track, now: float) -> dict:
    stats = track.stats
    return {
        "device": track.name,
        "address": track.address,
        "adapter_identity": track.adapter_identity,
        "adapter": track.adapter_name,
        "rssi_unit": "dB (relative, BR/EDR)" if track.rssi_kind == "dB" else "dBm",
        "rssi_min": stats.min,
        "rssi_max": stats.max,
        "rssi_avg": None if stats.avg is None else round(stats.avg, 2),
        "rssi_stdev": None if stats.stdev is None else round(stats.stdev, 2),
        "samples_total": track.total_samples,
        "samples_connected": track.connected_samples,
        "samples_with_rssi": track.rssi_samples,
        "rssi_availability_percent": _round(track.availability()),
        "connected_share_percent": _round(track.connected_share()),
        "query_timeouts": track.timeouts,
        "disconnects": track.disconnects,
        "reconnects": track.reconnects,
        "connected_seconds": round(track.connected_time(now), 1),
        "downtime_seconds": round(track.downtime(now), 1),
        "current_connection_seconds": _round(track.uptime(now)),
        "last_connect": _iso(track.last_connect) if track.last_connect else None,
        "last_disconnect": _iso(track.last_disconnect) if track.last_disconnect else None,
        "last_disconnect_reason": track.last_disconnect_reason,
        "tx_power_dbm_reported": track.last_tx_power,
        "max_tx_power_dbm_reported": track.last_max_tx_power,
        "monitoring_since": _iso(track.started),
    }


def _round(value):
    return None if value is None else round(value, 1)


def export_json(tracks: list[Track], events: EventLog, path: str, meta: dict | None = None) -> int:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    now = time.time()
    doc = {
        "tool": f"bc250-wcm {__version__}",
        "exported": _iso(now),
        "notes": [
            "rssi is null where no reading exists (disconnected, query failed) — never 0",
            "LE RSSI is in dBm; BR/EDR RSSI from HCI_Read_RSSI is relative to the golden receive power range",
            "the kernel caches connection info for 1-3 s (conn_info_min_age/max_age), so consecutive samples may repeat",
            "tx_power is what the controller reports for the link; it is not verified",
        ],
        **(meta or {}),
        "devices": [],
        "events": [],
    }
    count = 0
    for track in tracks:
        samples = track.snapshot_samples()
        count += len(samples)
        doc["devices"].append({
            "summary": track_summary(track, now),
            "changes": [{"time": _iso(c.ts), "field": c.field, "old": c.old, "new": c.new}
                        for c in list(track.changes)],
            "samples": [{k: v for k, v in sample_row(track, s).items()
                         if k not in ("device", "address")} for s in samples],
        })
    for event in events.events():
        doc["events"].append({"time": _iso(event.ts), "level": event.level,
                              "category": event.category, "device": event.device,
                              "text": event.text})
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=2, ensure_ascii=False, default=str)
        handle.write("\n")
    return count


class CsvRecorder:
    """Appends each sample to a CSV file as it is taken (--record)."""

    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        self._handle = open(path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._handle, fieldnames=CSV_FIELDS)
        # probes of different devices write from their own threads
        self._lock = threading.Lock()
        if new:
            self._writer.writeheader()

    def __call__(self, track: Track, sample: Sample) -> None:
        row = sample_row(track, sample)
        with self._lock:
            self._writer.writerow(row)
            self._handle.flush()

    def close(self) -> None:
        try:
            self._handle.close()
        except OSError:
            pass

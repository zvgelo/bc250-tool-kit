"""The curses interface: device picker, dashboard, details, event log, help.

Drawing follows bc250-mon: erase, draw into the virtual screen, then one
doupdate() — curses sends only the cells that changed, so nothing flickers.
The screen is redrawn when the engine reports new data, on a key, or twice a
second for the clocks; never in a busy loop.
"""

from __future__ import annotations

import curses
from dataclasses import dataclass
import math
import os
import time

from . import export, history, widgets
from .config import Config
from .diagnostics import diagnose, sorted_findings
from .engine import MAX_MONITORED, Engine, Target, device_key
from .i18n import t, t_or
from .mgmt import ADDR_TYPE_NAMES
from .model import AdapterInfo, DeviceInfo, parse_modalias, service_name, short_uuid
from .widgets import (Glyphs, Theme, activity_cells, addstr, block_bar, draw_box, graph_cells, graph_scale,
                      rssi_fraction, rssi_level, sparkline, truncate, unicode_ok)

V_PICKER = "picker"
V_DASH = "dash"
V_DETAIL = "detail"
V_LOG = "log"

FILTERS = ("all", "connected", "paired", "discovered", "bluetooth", "usb")
SORTS = ("name", "rssi", "state", "address", "adapter")
LOG_FILTERS = ("all", history.CAT_DEVICE, history.CAT_ADAPTER, history.CAT_USB, history.CAT_KERNEL,
               history.CAT_BACKEND)

MIN_W, MIN_H = 40, 10
REDRAW_EVERY = 0.5
DIGIT_WINDOW = 0.7

HELP = [
    ("help.section.picker", None),
    ("↑ ↓ PgUp PgDn", "help.picker.move"),
    ("Space", "help.picker.toggle"),
    ("1..9", "help.picker.number"),
    ("Enter", "help.picker.start"),
    ("/", "help.picker.search"),
    ("f / o", "help.picker.filter"),
    ("d", "help.picker.discovery"),
    ("c", "help.picker.clear"),
    ("help.section.dash", None),
    ("Tab ← → ↑ ↓ 1..4", "help.dash.focus"),
    ("Enter", "help.dash.detail"),
    ("s", "help.dash.select"),
    ("p", "help.dash.pause"),
    ("l", "help.dash.log"),
    ("e / j", "help.dash.export"),
    ("r", "help.dash.reset"),
    ("+ / -", "help.dash.interval"),
    ("c", "help.dash.compact"),
    ("g", "help.dash.graph"),
    ("help.section.common", None),
    ("Esc", "help.back"),
    ("h / ?", "help.help"),
    ("q", "help.quit"),
]


@dataclass
class Row:
    """One line of the device list, whatever the transport."""
    key: str
    kind: str                       # bt / usb
    name: str
    ident: str                      # MAC, or the USB physical place
    type_label: str
    transport: str                  # LE, BR/EDR, dual, USB 2.4 GHz, …
    via: str                        # hci1 / USB 1-3
    connected: bool | None
    paired: bool | None
    bonded: bool | None
    trusted: bool | None
    rssi: int | None
    rssi_link: bool
    battery: int | None
    dev: DeviceInfo | None = None
    adapter: AdapterInfo | None = None
    identity: str = ""
    usbdev: object = None
    usb_id: str | None = None
    profile_id: str | None = None
    activity: int | None = None

    def matches(self, wanted: str) -> bool:
        """A selection entry: bt:ADDRESS, a bare address, or usb:<physical id>."""
        if wanted.startswith("usb:"):
            return self.kind == "usb" and (self.key == wanted or f"usb:{self.ident}" == wanted)
        address = wanted[3:] if wanted.startswith("bt:") else wanted
        return self.kind == "bt" and self.ident == address.upper()

    def target(self) -> Target:
        if self.kind == "usb":
            return Target(self.key, self.name, "usb", self.key, profile_id=self.profile_id)
        return Target(self.key, self.name, "bt", self.ident, self.identity)


class App:
    def __init__(self, engine: Engine, cfg: Config, preselect: list[str] | None = None,
                 start_monitoring: bool = False) -> None:
        self.engine = engine
        self.cfg = cfg
        self.view = V_PICKER
        self.prev_view = V_PICKER
        self.show_help = False
        self.selected: list[str] = []
        self.preselect = [a.upper() for a in (preselect or [])]
        self.start_monitoring = start_monitoring
        self.cursor = 0
        self.scroll = 0
        self.filter = "all"
        self.sort = "name"
        self.search = ""
        self.searching = False
        self.focus = 0
        self.detail_key: str | None = None
        self.detail_scroll = 0
        self.log_scroll = 0
        self.log_filter = "all"
        self.compact = False
        self.braille = cfg.graph != "ascii"
        self.flash_text = ""
        self.flash_attr = 0
        self.flash_until = 0.0
        self._digits = ""
        self._digit_time = 0.0
        self._rows: list[tuple[str, DeviceInfo, AdapterInfo | None]] = []
        self._preselected = False
        self._t0 = time.monotonic()
        self.paused_at: float | None = None

    # -- main loop -------------------------------------------------------------

    def run(self, stdscr) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        stdscr.keypad(True)
        stdscr.timeout(100)
        self.theme = Theme(self.cfg.colors)
        uni = unicode_ok() and not self.cfg.ascii
        widgets.ASCII_ONLY = not uni
        self.g = Glyphs(uni)
        if not uni:
            self.braille = False
        if self.cfg.graph == "braille" and uni:
            self.braille = True
        last_draw = 0.0
        dirty = True
        while True:
            now = time.monotonic()
            if self.engine.changed.is_set():
                self.engine.changed.clear()
                dirty = True
            if dirty or now - last_draw >= REDRAW_EVERY:
                self._maybe_autostart()
                stdscr.erase()
                self._draw(stdscr)
                stdscr.noutrefresh()
                curses.doupdate()
                if os.environ.get("WCM_DUMP"):
                    _dump(stdscr, os.environ["WCM_DUMP"])
                last_draw = now
                dirty = False
            key = stdscr.getch()
            if key == -1:
                if self._digits and time.monotonic() - self._digit_time > DIGIT_WINDOW:
                    self._commit_digits()
                    dirty = True
                continue
            dirty = True
            if not self._handle_key(key, stdscr):
                return

    def _maybe_autostart(self) -> None:
        """Pre-selection from --monitor or the last session, once devices are known."""
        if self._preselected:
            return
        snap = self.engine.snapshot()
        if snap.status == "starting":
            return
        rows = self._all_rows(snap)
        if not rows and time.monotonic() - self._t0 < 2:
            return
        self._preselected = True
        for wanted in self.preselect:
            for row in rows:
                if row.matches(wanted) and row.key not in self.selected \
                        and len(self.selected) < MAX_MONITORED:
                    self.selected.append(row.key)
        if self.start_monitoring and self.selected:
            self._start_monitoring()

    # -- keys ---------------------------------------------------------------------

    def _handle_key(self, key: int, stdscr) -> bool:
        if key == curses.KEY_RESIZE:
            stdscr.erase()
            return True
        if self.searching:
            return self._search_key(key)
        if self.show_help:
            if key in (ord("q"), 27, ord("h"), ord("?"), 10, 13, curses.KEY_ENTER):
                self.show_help = False
            return True
        if key in (ord("h"), ord("H"), ord("?")):
            self.show_help = True
            return True
        if key in (ord("q"), ord("Q")):
            return False
        handler = {V_PICKER: self._picker_key, V_DASH: self._dash_key,
                   V_DETAIL: self._detail_key, V_LOG: self._log_key}[self.view]
        return handler(key)

    def _search_key(self, key: int) -> bool:
        if key in (10, 13, curses.KEY_ENTER):
            self.searching = False
        elif key == 27:
            self.searching = False
            self.search = ""
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            self.search = self.search[:-1]
        elif 32 <= key < 127:
            self.search += chr(key)
        self.cursor = 0
        self.scroll = 0
        return True

    def _picker_key(self, key: int) -> bool:
        rows = self._rows
        if key in (curses.KEY_DOWN, ord("j")):
            self.cursor = min(len(rows) - 1, self.cursor + 1) if rows else 0
        elif key in (curses.KEY_UP, ord("k")):
            self.cursor = max(0, self.cursor - 1)
        elif key == curses.KEY_NPAGE:
            self.cursor = min(len(rows) - 1, self.cursor + 10) if rows else 0
        elif key == curses.KEY_PPAGE:
            self.cursor = max(0, self.cursor - 10)
        elif key == curses.KEY_HOME:
            self.cursor = 0
        elif key == curses.KEY_END:
            self.cursor = max(0, len(rows) - 1)
        elif key == ord(" "):
            if rows:
                self._toggle(rows[self.cursor].key)
        elif ord("0") <= key <= ord("9"):
            self._digit(chr(key))
        elif key in (10, 13, curses.KEY_ENTER):
            if not self.selected and rows:
                self._toggle(rows[self.cursor].key)
            if self.selected:
                self._start_monitoring()
            else:
                self._flash(t("flash.select.none"), self.theme.warn)
        elif key == ord("/"):
            self.searching = True
        elif key == ord("f"):
            self.filter = FILTERS[(FILTERS.index(self.filter) + 1) % len(FILTERS)]
            self.cursor = self.scroll = 0
        elif key == ord("o"):
            self.sort = SORTS[(SORTS.index(self.sort) + 1) % len(SORTS)]
        elif key == ord("c"):
            self.selected = []
        elif key == ord("d"):
            self._toggle_discovery()
        elif key == 27:
            if self.search:
                self.search = ""
            elif self.engine.monitored:
                self.view = V_DASH
        return True

    def _digit(self, char: str) -> None:
        now = time.monotonic()
        if now - self._digit_time > DIGIT_WINDOW:
            self._digits = ""
        self._digit_time = now
        self._digits += char
        # with ten or more rows another digit may follow ("1" then "2" = row 12);
        # wait for it briefly, otherwise act at once
        if int(self._digits) * 10 <= len(self._rows) and int(self._digits) > 0:
            return
        self._commit_digits()

    def _commit_digits(self) -> None:
        number = int(self._digits or "0")
        self._digits = ""
        if 1 <= number <= len(self._rows):
            self.cursor = number - 1
            self._toggle(self._rows[number - 1].key)

    def _toggle(self, key: str) -> None:
        if key in self.selected:
            self.selected.remove(key)
        elif len(self.selected) >= MAX_MONITORED:
            self._flash(t("flash.select.max", count=MAX_MONITORED), self.theme.warn)
        else:
            self.selected.append(key)

    def _start_monitoring(self) -> None:
        lookup = {row.key: row for row in self._all_rows(self.engine.snapshot())}
        chosen = []
        for key in self.selected:
            if key in lookup:
                chosen.append(lookup[key].target())
            elif key in self.engine.tracks:
                # a monitored device that is momentarily gone stays monitored
                track = self.engine.tracks[key]
                chosen.append(Target(key, track.name, track.kind, track.address,
                                     track.adapter_identity, track.profile_id))
        if not chosen:
            return
        self.engine.set_monitored(chosen)
        self.engine.save_selection()
        self.focus = 0
        self.view = V_DASH

    def _toggle_discovery(self) -> None:
        snap = self.engine.snapshot()
        adapter = None
        if self._rows and self.cursor < len(self._rows):
            adapter = self._rows[self.cursor].adapter
        if adapter is None and snap.adapters:
            adapter = snap.adapters[0]
        if adapter is None:
            self._flash(t("flash.noadapter"), self.theme.warn)
            return
        enable = not adapter.discovering
        error = self.engine.set_discovery(adapter, enable)
        if error:
            self._flash(t("flash.discovery.failed", error=error), self.theme.crit, 4)
        elif enable:
            self._flash(t("flash.discovery.on", name=adapter.name), self.theme.warn, 6)
        else:
            self._flash(t("flash.discovery.off", name=adapter.name), self.theme.ok)

    def _dash_key(self, key: int) -> bool:
        count = len(self.engine.monitored)
        if key in (ord("\t"), curses.KEY_RIGHT, curses.KEY_DOWN) and count:
            self.focus = (self.focus + 1) % count
        elif key in (curses.KEY_BTAB, curses.KEY_LEFT, curses.KEY_UP) and count:
            self.focus = (self.focus - 1) % count
        elif ord("1") <= key <= ord("4"):
            if key - ord("1") < count:
                self.focus = key - ord("1")
        elif key in (10, 13, curses.KEY_ENTER) and count:
            self.detail_key = self.engine.monitored[self.focus]
            self.detail_scroll = 0
            self.view = V_DETAIL
        elif key in (ord("s"), ord("S")):
            self.selected = list(self.engine.monitored)
            self.view = V_PICKER
        elif key in (ord("p"), ord("P"), ord(" ")):
            self._toggle_pause()
        elif key in (ord("l"), ord("L")):
            self.prev_view = V_DASH
            self.view = V_LOG
            self.log_scroll = 0
        elif key in (ord("e"), ord("E")):
            self._export("csv")
        elif key in (ord("j"), ord("J")):
            self._export("json")
        elif key in (ord("r"), ord("R")):
            self.engine.reset_stats()
            self._flash(t("flash.reset"), self.theme.ok)
        elif key in (ord("+"), ord("=")):
            self.engine.set_interval(round(self.engine.interval + (0.25 if self.engine.interval < 2 else 1), 2))
        elif key in (ord("-"), ord("_")):
            self.engine.set_interval(round(self.engine.interval - (0.25 if self.engine.interval <= 2 else 1), 2))
        elif key in (ord("c"), ord("C")):
            self.compact = not self.compact
        elif key in (ord("g"), ord("G")):
            if self.g.unicode:
                self.braille = not self.braille
        return True

    def _toggle_pause(self) -> None:
        paused = not self.engine.paused
        self.paused_at = time.time() if paused else None
        self.engine.set_paused(paused)

    def _detail_key(self, key: int) -> bool:
        if key in (27, curses.KEY_LEFT, curses.KEY_BACKSPACE, 127, 10, 13, curses.KEY_ENTER):
            self.view = V_DASH
        elif key in (curses.KEY_DOWN, ord("j")):
            self.detail_scroll += 1
        elif key in (curses.KEY_UP, ord("k")):
            self.detail_scroll = max(0, self.detail_scroll - 1)
        elif key == curses.KEY_NPAGE:
            self.detail_scroll += 10
        elif key == curses.KEY_PPAGE:
            self.detail_scroll = max(0, self.detail_scroll - 10)
        elif key == curses.KEY_HOME:
            self.detail_scroll = 0
        elif key in (ord("\t"),) and self.engine.monitored:
            self.focus = (self.focus + 1) % len(self.engine.monitored)
            self.detail_key = self.engine.monitored[self.focus]
            self.detail_scroll = 0
        elif key in (ord("l"), ord("L")):
            self.prev_view = V_DETAIL
            self.view = V_LOG
        elif key in (ord("e"), ord("E")):
            self._export("csv")
        elif key in (ord("j"), ord("J")):
            self._export("json")
        elif key in (ord("p"), ord("P")):
            self._toggle_pause()
        return True

    def _log_key(self, key: int) -> bool:
        if key in (27, ord("l"), ord("L")):
            self.view = self.prev_view if self.engine.monitored or self.prev_view == V_PICKER else V_PICKER
        elif key in (curses.KEY_UP, ord("k")):
            self.log_scroll += 1
        elif key in (curses.KEY_DOWN, ord("j")):
            self.log_scroll = max(0, self.log_scroll - 1)
        elif key == curses.KEY_PPAGE:
            self.log_scroll += 10
        elif key == curses.KEY_NPAGE:
            self.log_scroll = max(0, self.log_scroll - 10)
        elif key == curses.KEY_END:
            self.log_scroll = 0
        elif key == curses.KEY_HOME:
            self.log_scroll = 10 ** 6
        elif key == ord("f"):
            self.log_filter = LOG_FILTERS[(LOG_FILTERS.index(self.log_filter) + 1) % len(LOG_FILTERS)]
            self.log_scroll = 0
        elif key in (ord("e"), ord("E")):
            self._export("csv")
        elif key in (ord("j"), ord("J")):
            self._export("json")
        return True

    def _export(self, kind: str) -> None:
        tracks = [self.engine.tracks[k] for k in self.engine.tracks]
        if not tracks:
            self._flash(t("flash.export.nothing"), self.theme.warn)
            return
        path = os.path.join(self.cfg.export_dir, export.default_name(kind))
        try:
            if kind == "csv":
                count = export.export_csv(tracks, path)
            else:
                count = export.export_json(tracks, self.engine.events, path,
                                           {"interval_s": self.engine.interval,
                                            "rssi_backend": self.engine.backend.name})
        except OSError as exc:
            self._flash(t("flash.export.failed", error=str(exc)), self.theme.crit, 5)
            return
        self.engine.events.add(history.LEVEL_INFO, history.CAT_APP,
                               t("ev.exported", path=path, count=count))
        self._flash(t("flash.exported", path=path, count=count), self.theme.ok, 6)

    def _flash(self, text: str, attr: int, seconds: float = 3.0) -> None:
        self.flash_text = text
        self.flash_attr = attr
        self.flash_until = time.monotonic() + seconds

    # -- data helpers ------------------------------------------------------------------

    def _all_rows(self, snap) -> list["Row"]:
        """Bluetooth devices and USB controllers in one list."""
        by_path = {a.path: a for a in snap.adapters}
        rows = []
        for dev in snap.devices:
            adapter = by_path.get(dev.adapter_path)
            identity = adapter.identity if adapter else dev.adapter_path
            rssi, link = self._bt_rssi(device_key(identity, dev.address), dev)
            battery, _src = self._battery(dev)
            rows.append(Row(
                key=device_key(identity, dev.address), kind="bt", name=dev.display_name,
                ident=dev.address, type_label=t_or("kind." + dev.kind, dev.kind) if dev.kind != "?" else "",
                transport=dev.bearer, via=adapter.name if adapter else "?", connected=dev.connected,
                paired=dev.paired, bonded=dev.bonded, trusted=dev.trusted, rssi=rssi, rssi_link=link,
                battery=battery, dev=dev, adapter=adapter, identity=identity))
        for udev in self.engine.usb_snapshot().devices:
            track = self.engine.tracks.get(udev.physical_id)
            rows.append(Row(
                key=udev.physical_id, kind="usb", name=udev.display_name,
                ident=udev.physical_id[4:], type_label=t("kind.gamepad"),
                transport=t("transport." + udev.transport), via=f"USB {udev.port}",
                connected=udev.mode != "idle", paired=None, bonded=None, trusted=None,
                rssi=None, rssi_link=False, battery=udev.battery[0] if udev.battery else None,
                usbdev=udev, usb_id=udev.usb_id,
                profile_id=udev.profile.id if udev.profile else None,
                activity=None if track is None or track.last_sample is None else track.last_sample.activity))
        return rows

    def _picker_rows(self, snap) -> list["Row"]:
        rows = self._all_rows(snap)
        if self.filter == "connected":
            rows = [r for r in rows if r.connected]
        elif self.filter == "paired":
            rows = [r for r in rows if r.paired]
        elif self.filter == "discovered":
            rows = [r for r in rows if r.kind == "bt" and not r.paired]
        elif self.filter == "bluetooth":
            rows = [r for r in rows if r.kind == "bt"]
        elif self.filter == "usb":
            rows = [r for r in rows if r.kind == "usb"]
        if self.search:
            needle = self.search.lower()
            rows = [r for r in rows if needle in r.name.lower() or needle in r.ident.lower()
                    or needle in r.type_label.lower() or needle in r.transport.lower()
                    or needle in (r.usb_id or "")]
        if self.sort == "name":
            rows.sort(key=lambda r: (r.name.lower(), r.ident))
        elif self.sort == "rssi":
            rows.sort(key=lambda r: -(r.rssi if r.rssi is not None else 999))
        elif self.sort == "state":
            rows.sort(key=lambda r: (not r.connected, not r.paired, r.name.lower()))
        elif self.sort == "address":
            rows.sort(key=lambda r: (r.kind, r.ident))
        elif self.sort == "adapter":
            rows.sort(key=lambda r: (r.via, r.name.lower()))
        return rows

    def _bt_rssi(self, key: str, dev: DeviceInfo) -> tuple[int | None, bool]:
        """(value, is_link_rssi): the link RSSI if monitored and fresh, else advertising."""
        track = self.engine.tracks.get(key)
        if track is not None and track.last_sample is not None \
                and track.last_sample.rssi is not None \
                and time.time() - track.last_sample.ts < max(5.0, 3 * self.engine.interval):
            return track.last_sample.rssi, track.last_sample.source == history.SRC_CONN
        return dev.rssi, False

    def _battery(self, dev: DeviceInfo) -> tuple[int | None, str | None]:
        if dev.battery is not None:
            return dev.battery, dev.battery_source or "BlueZ"
        found = self.engine.battery_reader(dev.address)
        if found:
            return found[0], t("battery.source.kernel")
        return None, None

    # -- drawing: skeleton -------------------------------------------------------------

    def _draw(self, win) -> None:
        height, width = win.getmaxyx()
        if height < MIN_H or width < MIN_W:
            addstr(win, 0, 0, t("err.small", w=MIN_W, h=MIN_H), self.theme.warn)
            return
        snap = self.engine.snapshot()
        self._draw_header(win, width, snap)
        body_top, body_h = 1, height - 2
        if self.view == V_PICKER:
            self._draw_picker(win, body_top, body_h, width, snap)
        elif self.view == V_DASH:
            self._draw_dashboard(win, body_top, body_h, width, snap)
        elif self.view == V_DETAIL:
            self._draw_detail(win, body_top, body_h, width, snap)
        else:
            self._draw_log(win, body_top, body_h, width)
        self._draw_footer(win, height - 1, width)
        if self.show_help:
            self._draw_help(win, height, width)

    def _draw_header(self, win, width: int, snap) -> None:
        th, g = self.theme, self.g
        addstr(win, 0, 0, " " * width, th.header)
        title = " WIRELESS CONTROLLER MONITOR " if width >= 120 else " WCM "
        addstr(win, 0, 0, title, th.header | curses.A_BOLD)
        x = len(title) + 1
        findings = diagnose(self.engine)
        levels = {lvl for lvl, _ in findings}
        status, status_attr = ("OK", th.ok)
        if "error" in levels:
            status, status_attr = ("ERROR", th.crit)
        elif "warn" in levels:
            status, status_attr = ("WARN", th.warn)
        usb_count = len(self.engine.usb_snapshot().devices)
        parts = [(t("header.devices", count=len(self.engine.monitored)), th.header)]
        if snap.status != "ok":
            parts.append((f"BT: {t('bluez.' + snap.status)}",
                          th.crit if snap.status != "starting" else th.header))
        elif not snap.adapters:
            parts.append((f"BT: {t('header.noadapter')}", th.crit))
        else:
            for adapter in snap.adapters:
                label = f"BT: {adapter.name}"
                if width >= 140:
                    label += f" {adapter.address}"
                if adapter.discovering:
                    label += f" {t('header.scanning')}"
                ok = adapter.powered
                parts.append((label + f" {g.dot}", th.header if ok else th.warn | curses.A_REVERSE))
        parts.append((f"USB: {usb_count}", th.header))
        for text, attr in parts:
            if x + len(text) + 3 > width - 40:
                break
            addstr(win, 0, x, text, attr)
            x += len(text) + 3
        right = [(f"Status: {status}", status_attr | curses.A_REVERSE)]
        backend = self.engine.backend
        right.append((f"RSSI: {backend.name if backend.available else t('header.nolink')}",
                      th.header if backend.available else th.header | curses.A_BOLD))
        right.append((f"{self.engine.interval:g} s", th.header))
        if self.engine.paused:
            right.append((t("state.paused"), th.warn | curses.A_REVERSE))
        right.append((time.strftime("%H:%M:%S"), th.header))
        while right and sum(len(r[0]) + 3 for r in right) > width - x - 1:
            right.pop(1 if len(right) > 2 else 0)
        rx = width - sum(len(r[0]) + 3 for r in right) - 1 + 3
        for i, (text, attr) in enumerate(right):
            if i:
                addstr(win, 0, rx - 2, "│" if g.unicode else "|", th.header)
            addstr(win, 0, rx, text, attr)
            rx += len(text) + 3

    def _draw_footer(self, win, y: int, width: int) -> None:
        th = self.theme
        if self.flash_text and time.monotonic() < self.flash_until:
            addstr(win, y, 0, " " + truncate(self.flash_text, width - 2, self.g.ellipsis), self.flash_attr)
            return
        if self.searching:
            addstr(win, y, 0, f" / {self.search}_", th.accent)
            addstr(win, y, len(self.search) + 6, t("footer.search"), th.dim)
            return
        keys = {V_PICKER: "footer.picker", V_DASH: "footer.dash",
                V_DETAIL: "footer.detail", V_LOG: "footer.log"}[self.view]
        addstr(win, y, 0, " " + truncate(t(keys), width - 2, self.g.ellipsis), th.dim)

    # -- picker --------------------------------------------------------------------------

    def _draw_picker(self, win, top: int, height: int, width: int, snap) -> None:
        th, g = self.theme, self.g
        rows = self._picker_rows(snap)
        self._rows = rows
        if rows:
            self.cursor = max(0, min(self.cursor, len(rows) - 1))
        subtitle = t("picker.subtitle", filter=t("filter." + self.filter),
                     sort=t("sort." + self.sort), count=len(rows))
        if self.search:
            subtitle = f"/{self.search}  " + subtitle
        counts = (sum(1 for r in rows if r.kind == "bt"), sum(1 for r in rows if r.kind == "usb"))
        draw_box(win, top, 0, height, width, th, g,
                 t("picker.title", selected=len(self.selected), max=MAX_MONITORED,
                   bt=counts[0], usb=counts[1]), subtitle)
        inner_x, inner_w = 2, width - 4
        y = top + 1

        cols = self._picker_columns(inner_w)
        header = ""
        for name, w, align in cols:
            label = t("col." + name)
            header += (label.rjust(w) if align == "r" else label.ljust(w))[:w] + " "
        addstr(win, y, inner_x, header, th.label | curses.A_BOLD, inner_x + inner_w)
        y += 1

        if not rows:
            msg = t("picker.empty") if snap.status == "ok" or self.engine.usb_snapshot().devices \
                else t("picker.nobluez")
            addstr(win, y + 1, inner_x, truncate(msg, inner_w), th.dim)
            if snap.status == "ok" and snap.adapters:
                addstr(win, y + 2, inner_x, truncate(t("picker.empty.hint"), inner_w), th.dim)
            return

        list_h = height - 4
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        elif self.cursor >= self.scroll + list_h:
            self.scroll = self.cursor - list_h + 1
        for i in range(self.scroll, min(len(rows), self.scroll + list_h)):
            self._draw_picker_row(win, y, inner_x, inner_w, cols, i, rows[i])
            y += 1
        if len(rows) > list_h:
            info = f" {self.scroll + 1}-{min(len(rows), self.scroll + list_h)}/{len(rows)} "
            addstr(win, top + height - 1, width - len(info) - 2, info, th.dim)
        legend = t("picker.legend")
        addstr(win, top + height - 1, 2, f" {truncate(legend, width - 20)} ", th.faint)

    def _picker_columns(self, width: int) -> list[tuple[str, int, str]]:
        cols = [("num", 3, "r"), ("sel", 3, "l"), ("name", 0, "l"), ("mac", 17, "l"),
                ("type", 16, "l"), ("adapter", 8, "l"), ("conn", 4, "l"), ("paired", 4, "l"),
                ("bonded", 4, "l"), ("trusted", 5, "l"), ("rssi", 6, "r"), ("battery", 5, "r")]
        drop_order = ["bonded", "trusted", "adapter", "type", "battery", "paired"]
        while True:
            fixed = sum(w + 1 for n, w, _ in cols if n != "name")
            name_w = width - fixed - 1
            if name_w >= 14 or not drop_order:
                break
            drop = drop_order.pop(0)
            cols = [c for c in cols if c[0] != drop]
        name_w = max(8, min(44, name_w))
        return [(n, name_w if n == "name" else w, a) for n, w, a in cols]

    def _flag(self, value: bool | None) -> tuple[str, int]:
        th = self.theme
        if value is None:
            return "?", th.faint
        return (self.g.check, th.ok) if value else (self.g.gap, th.faint)

    def _draw_picker_row(self, win, y, x, width, cols, i, row: "Row") -> None:
        th = self.theme
        is_cursor = i == self.cursor
        selected = row.key in self.selected
        base = th.select if is_cursor else 0
        if is_cursor:
            addstr(win, y, x - 1, " " * (width + 2), th.select)
        cx = x
        for name, w, align in cols:
            attr = th.text
            if name == "num":
                text = str(i + 1)
                attr = th.dim
            elif name == "sel":
                order = self.selected.index(row.key) + 1 if selected else 0
                text = f"[{order}]" if selected else "[ ]"
                attr = th.accent if selected else th.faint
            elif name == "name":
                text = row.name
                attr = th.bright if row.connected else th.text
            elif name == "mac":
                text = row.usb_id + " " + row.ident if row.kind == "usb" else row.ident
                attr = th.dim
            elif name == "type":
                text = " ".join(p for p in (row.type_label, row.transport) if p) if w >= 14 \
                    else (row.transport or row.type_label)
                attr = th.label if row.kind == "usb" else th.dim
            elif name == "adapter":
                text = row.via
                attr = th.dim
            elif name in ("conn", "paired", "bonded", "trusted"):
                value = {"conn": row.connected, "paired": row.paired,
                         "bonded": row.bonded, "trusted": row.trusted}[name]
                if row.kind == "usb" and name != "conn":
                    text, attr = "—", th.faint          # Bluetooth notions, not applicable
                else:
                    text, attr = self._flag(value)
                    if name == "conn" and value:
                        text, attr = self.g.dot, th.ok
            elif name == "rssi":
                if row.kind == "usb":
                    text, attr = "n/a", th.faint
                elif row.rssi is None:
                    text, attr = "—", th.faint
                else:
                    text = f"{row.rssi}" if row.rssi_link else f"~{row.rssi}"
                    attr = th.level_attr(rssi_level(row.rssi, self.cfg.good, self.cfg.fair, self.cfg.weak))
                    if not row.rssi_link:
                        attr |= curses.A_DIM
            elif name == "battery":
                text, attr = ("—", th.faint) if row.battery is None else (f"{row.battery}%", th.text)
            else:
                text = ""
            text = truncate(text, w, self.g.ellipsis)
            text = text.rjust(w) if align == "r" else text.ljust(w)
            addstr(win, y, cx, text, (attr | base) if is_cursor else attr, x + width)
            cx += w + 1

    # -- dashboard -------------------------------------------------------------------------

    def _draw_dashboard(self, win, top: int, height: int, width: int, snap) -> None:
        keys = self.engine.monitored
        if not keys:
            self.view = V_PICKER
            return
        findings = sorted_findings(diagnose(self.engine))
        diag_h = 0
        if findings:
            diag_h = min(len(findings), 3 if height >= 30 else (2 if height >= 18 else 1)) + 2
        panels_h = height - diag_h
        # a free cell in the grid (one or three devices) shows the event feed, as btop fills its grid
        cells = len(keys) + (1 if len(keys) in (1, 3) and width >= 90 and panels_h >= 14 else 0)
        layout = grid_layout(cells, width, panels_h, self.compact)
        self.focus = min(self.focus, len(keys) - 1)
        for i, key in enumerate(keys):
            y, x, h, w = layout[i]
            if self.engine.tracks[key].kind == "usb":
                self._draw_usb_panel(win, top + y, x, h, w, key, focus=(i == self.focus), number=i + 1)
            else:
                self._draw_panel(win, top + y, x, h, w, key, snap, focus=(i == self.focus), number=i + 1)
        if cells > len(keys):
            y, x, h, w = layout[-1]
            self._draw_events(win, top + y, x, h, w)
        if diag_h:
            self._draw_diag(win, top + panels_h, 0, diag_h, width, findings)

    def _draw_events(self, win, y: int, x: int, h: int, w: int) -> None:
        th, g = self.theme, self.g
        draw_box(win, y, x, h, w, th, g, t("panel.events"), t("panel.events.more"))
        wanted = (history.CAT_DEVICE, history.CAT_ADAPTER, history.CAT_USB, history.CAT_KERNEL,
                  history.CAT_BACKEND)
        events = sorted((e for e in self.engine.events.events() if e.category in wanted),
                        key=lambda e: e.ts)[-(h - 2):]
        for i, event in enumerate(events):
            attr = {"error": th.crit, "warn": th.warn}.get(event.level, th.text)
            addstr(win, y + 1 + i, x + 2, time.strftime("%H:%M:%S", time.localtime(event.ts)), th.dim)
            addstr(win, y + 1 + i, x + 11, truncate(event.text, w - 13, g.ellipsis), attr, x + w - 1)
        if not events:
            addstr(win, y + 1, x + 2, t("log.empty"), th.faint)

    def _usb_state(self, track) -> tuple[str, int, str, object]:
        th, g = self.theme, self.g
        dev = self.engine.resolve_usb(track)
        now = time.time()
        if dev is None:
            down = now - track.disconnected_since if track.disconnected_since else None
            return f"{'○' if g.unicode else 'o'} {t('state.usbgone')} {fmt_duration(down)}", th.crit, "gone", None
        if dev.mode == "idle":
            down = now - track.disconnected_since if track.disconnected_since else None
            return f"{'○' if g.unicode else 'o'} {t('state.idle')} {fmt_duration(down)}", th.warn, "idle", dev
        return f"{g.dot} {t('state.linked')} {fmt_duration(track.uptime(now))}", th.ok, "linked", dev

    def _draw_usb_panel(self, win, y: int, x: int, h: int, w: int, key: str, focus: bool,
                        number: int) -> None:
        th, g = self.theme, self.g
        track = self.engine.tracks[key]
        label, attr, state, dev = self._usb_state(track)
        draw_box(win, y, x, h, w, th, g, f"{number} {track.name}", label, focus=focus, subtitle_attr=attr)
        ix, iw = x + 2, w - 4
        if h < 3 or iw < 10:
            return
        row, bottom = y + 1, y + h - 1
        if dev is not None:
            ident = f"{dev.usb_id} · USB {dev.port} · {', '.join(dev.drivers) or '—'} · " \
                    f"{t('transport.' + dev.transport)}"
        else:
            ident = f"{track.address} · {t('state.usbgone')}"
        addstr(win, row, ix, truncate(ident, iw, g.ellipsis), th.dim)
        row += 1
        end = self.paused_at or time.time()
        last = track.last_sample
        activity_ok = track.activity_status == "ok" and last is not None and last.activity is not None
        rate = (last.activity / max(0.2, self.engine.interval)) if activity_ok else None
        if row < bottom:
            link = {"linked": t("usb.link.linked"), "idle": t("usb.link.idle"),
                    "gone": t("usb.link.gone")}[state]
            addstr(win, row, ix, f"{t('panel.link')}  ", th.label)
            addstr(win, row, ix + len(t("panel.link")) + 2, truncate(link, iw - 8, g.ellipsis), attr)
            row += 1
        if row < bottom:
            battery = dev.battery[0] if dev is not None and dev.battery else None
            text = f"RSSI: {t('panel.rssi.unsupported')}   {t('panel.battery')}: "
            addstr(win, row, ix, truncate(text, iw), th.text)
            bx = ix + len(text)
            if bx < ix + iw - 4:
                addstr(win, row, bx, f"{battery}%" if battery is not None else t("panel.battery.usb.na"),
                       th.ok if battery is not None else th.faint, ix + iw)
            row += 1
        compact = self.compact or h < 10
        if compact:
            if row < bottom:
                text = f"{t('panel.input')}: " + (f"{rate:.0f}/s" if rate is not None else self._activity_why(track))
                addstr(win, row, ix, truncate(text, iw // 2), th.text)
                sx = ix + min(iw // 2, len(text) + 2)
                sw = ix + iw - sx
                if sw >= 6 and activity_ok:
                    bins = track.binned_activity(sw, self.engine.interval, end)
                    hi = max([b[0] for b in bins if b[0] is not None] + [10.0])
                    for i, (char, val) in enumerate(sparkline(bins, sw, 0, hi, g.unicode)):
                        if char != " ":
                            addstr(win, row, sx + i, char,
                                   th.crit if isinstance(val, float) and math.isnan(val) else th.label)
            return
        graph_h = bottom - row - 2
        if graph_h >= 2:
            addstr(win, row, ix, truncate(t("panel.activity.title"), iw, g.ellipsis), th.faint)
            row += 1
            graph_h -= 1
            if activity_ok or track.activity_samples:
                self._draw_activity(win, row, ix, iw, graph_h, track, end)
            else:
                addstr(win, row + graph_h // 2, ix + 2, truncate(self._activity_why(track), iw - 4, g.ellipsis),
                       th.warn)
            row += graph_h
        if row < bottom:
            ago = None if track.last_input is None else time.time() - track.last_input
            parts = [(t("panel.input"), "—" if rate is None else f"{rate:.0f}/s"),
                     (t("stat.total"), str(track.activity_total)),
                     (t("stat.lastinput"), "—" if ago is None else fmt_duration(ago))]
            self._kv_line(win, row, ix, iw, parts)
            row += 1
        if row < bottom:
            changes = len(self.engine.usb_records.get(track.address, []))
            parts = [(t("stat.drops"), str(track.disconnects)), (t("stat.reconn"), str(track.reconnects)),
                     (t("stat.down"), fmt_duration(track.downtime(time.time())) if track.disconnects else "0"),
                     (t("stat.pidchanges"), str(changes))]
            self._kv_line(win, row, ix, iw, parts, warn_keys={t("stat.drops")})

    def _activity_why(self, track) -> str:
        status = track.activity_status or "no-node"
        return t("activity." + status, node=track.input_node or "?")

    def _draw_activity(self, win, row, ix, iw, height, track, end) -> None:
        th = self.theme
        axis_w = 5
        gw = iw - axis_w
        if gw < 4:
            return
        per_col = 2 if self.braille else 1
        bins = track.binned_activity(gw * per_col, self.engine.interval, end)
        hi = max([b[0] for b in bins if b[0] is not None] + [10.0])
        cells = activity_cells(bins, gw, height, hi, self.braille)
        for cy in range(height):
            label = f"{hi:>4.0f}" if cy == 0 else ("   0" if cy == height - 1 else "")
            addstr(win, row + cy, ix, label.rjust(4), th.faint)
            addstr(win, row + cy, ix + 4, self.g.axis, th.border)
            frac = 1.0 - cy / max(1, height - 1)
            for cx, (char, val) in enumerate(cells[cy]):
                if char == " ":
                    continue
                attr = th.crit if isinstance(val, float) and math.isnan(val) else th.ramp(0.1 + 0.5 * frac)
                addstr(win, row + cy, ix + axis_w + cx, char, attr, ix + iw)

    def _draw_diag(self, win, y: int, x: int, h: int, w: int, findings) -> None:
        th, g = self.theme, self.g
        errors = sum(1 for lvl, _ in findings if lvl == "error")
        draw_box(win, y, x, h, w, th, g, t("panel.diag"),
                 t("diag.count", count=len(findings)) + (f" · {t('diag.more')}" if len(findings) > h - 2 else ""),
                 title_attr=th.crit if errors else th.title)
        for i, (level, text) in enumerate(findings[:h - 2]):
            attr = {"error": th.crit, "warn": th.warn}.get(level, th.dim)
            mark = {"error": "✖" if g.unicode else "X", "warn": "▲" if g.unicode else "!"}.get(level, "i")
            addstr(win, y + 1 + i, x + 2, mark + " ", attr)
            addstr(win, y + 1 + i, x + 4, truncate(text, w - 6, g.ellipsis),
                   th.text if level == "info" else attr, x + w - 1)

    def _state_of(self, track, snap) -> tuple[str, int, str]:
        """(label, attr, short) for the panel subtitle."""
        th = self.theme
        adapter, dev = self.engine.resolve(track, snap)
        now = time.time()
        if adapter is None:
            return t("state.noadapter"), th.crit, "noadapter"
        if dev is None:
            return t("state.absent"), th.crit, "absent"
        if dev.connected:
            up = track.uptime(now)
            return f"{self.g.dot} {t('state.connected')} {fmt_duration(up)}", th.ok, "connected"
        down = now - track.disconnected_since if track.disconnected_since else None
        return (f"○ {t('state.disconnected')} {fmt_duration(down)}" if self.g.unicode
                else f"o {t('state.disconnected')} {fmt_duration(down)}"), th.crit, "disconnected"

    def _draw_panel(self, win, y: int, x: int, h: int, w: int, key: str, snap,
                    focus: bool, number: int) -> None:
        th, g = self.theme, self.g
        track = self.engine.tracks[key]
        adapter, dev = self.engine.resolve(track, snap)
        state_label, state_attr, state = self._state_of(track, snap)
        title = f"{number} {track.name}"
        draw_box(win, y, x, h, w, th, g, title, state_label, focus=focus, subtitle_attr=state_attr)
        ix, iw = x + 2, w - 4
        if h < 3 or iw < 10:
            return
        row = y + 1
        bottom = y + h - 1
        last = track.last_sample
        rssi = last.rssi if last and last.rssi is not None and state == "connected" else None
        unit = "dB" if track.rssi_kind == "dB" else "dBm"
        level = rssi_level(rssi, self.cfg.good, self.cfg.fair, self.cfg.weak)

        # identity line
        kind = dev.kind if dev else "?"
        bearer = dev.bearer if dev else "?"
        addr_type = (dev.address_type or "?") if dev else "?"
        ident = f"{track.address} · {bearer} {addr_type} · {adapter.name if adapter else '—'}"
        if iw > len(ident) + 12:
            ident += f" · {t_or('kind.' + kind, kind)}"
        addstr(win, row, ix, truncate(ident, iw, g.ellipsis), th.dim)
        row += 1
        if row >= bottom:
            return

        compact = self.compact or h < 11
        end = self.paused_at or time.time()
        if compact:
            self._panel_compact(win, row, ix, iw, bottom, track, rssi, unit, level, dev, last, end, state)
            return

        # RSSI line: value, bar, descriptive band
        self._rssi_line(win, row, ix, iw, rssi, unit, level, last, state)
        row += 1
        # TX power and battery
        tx = track.last_tx_power
        tx_text = t("panel.tx.na") if tx is None else t("panel.tx", value=tx)
        battery, source = self._battery(dev) if dev else (None, None)
        bat_text = t("panel.battery.na") if battery is None else f"{battery}%"
        line = f"{t('panel.txpower')}: {tx_text}"
        addstr(win, row, ix, truncate(line, iw // 2 + 6), th.text)
        bx = ix + max(len(line) + 3, iw // 2 + 2)
        if bx < ix + iw - 8:
            addstr(win, row, bx, f"{t('panel.battery')}: ", th.label)
            batt_attr = th.text if battery is None else (th.crit if battery < 15 else th.warn if battery < 30 else th.ok)
            addstr(win, row, bx + len(t('panel.battery')) + 2, bat_text, batt_attr if battery is not None else th.faint,
                   ix + iw)
        row += 1

        if track.activity_status == "ok" and last is not None and last.activity is not None and row < bottom - 3:
            rate = last.activity / max(0.2, self.engine.interval)
            addstr(win, row, ix, f"{t('panel.input')}: ", th.label)
            addstr(win, row, ix + len(t("panel.input")) + 2, f"{rate:.0f}/s", th.text)
            row += 1
        stats_rows = 2
        graph_h = bottom - row - stats_rows
        if graph_h >= 2:
            self._draw_graph(win, row, ix, iw, graph_h, track, end)
            row += graph_h
        self._stats_lines(win, row, ix, iw, bottom, track, unit)

    def _rssi_line(self, win, row, ix, iw, rssi, unit, level, last, state) -> None:
        th, g = self.theme, self.g
        addstr(win, row, ix, "RSSI ", th.label)
        if rssi is not None:
            value = f"{rssi:>4} {unit}"
            attr = th.level_attr(level) | curses.A_BOLD
        elif state != "connected":
            value, attr = t("panel.rssi.down"), th.crit
        else:
            reason = last.detail if last and last.detail else t("panel.rssi.none")
            value, attr = truncate(reason, iw - 6, g.ellipsis), th.warn
        addstr(win, row, ix + 5, value, attr, ix + iw)
        if rssi is None:
            return
        bar_x = ix + 5 + len(value) + 2
        band = t("band." + level) if level else ""
        bar_w = min(30, iw - (bar_x - ix) - len(band) - 2)
        if bar_w >= 6:
            bar = block_bar(rssi_fraction(rssi), bar_w, g.unicode)
            filled = len(bar.rstrip())
            addstr(win, row, bar_x, bar[:filled], th.level_attr(level))
            addstr(win, row, bar_x + filled, ("░" if g.unicode else ".") * (bar_w - filled), th.faint)
            addstr(win, row, bar_x + bar_w + 1, band, th.dim, ix + iw)

    def _draw_graph(self, win, row, ix, iw, graph_h, track, end) -> None:
        th = self.theme
        axis_w = 5
        gw = iw - axis_w
        if gw < 4:
            return
        per_col = 2 if self.braille else 1
        bins = track.binned(gw * per_col, self.engine.interval, end)
        lo, hi = graph_scale([b[0] for b in bins])
        cells = graph_cells(bins, gw, graph_h, lo, hi, self.braille)
        for cy in range(graph_h):
            value = hi - (hi - lo) * (cy / max(1, graph_h - 1)) if graph_h > 1 else hi
            label = ""
            if cy == 0:
                label = f"{hi:>4}"
            elif cy == graph_h - 1:
                label = f"{lo:>4}"
            elif graph_h >= 5 and cy == graph_h // 2:
                label = f"{int(round(value)):>4}"
            addstr(win, row + cy, ix, label.rjust(4), th.faint)
            addstr(win, row + cy, ix + 4, self.g.axis, th.border)
            for cx, (char, val) in enumerate(cells[cy]):
                if char == " ":
                    continue
                if val is not None and isinstance(val, float) and math.isnan(val):
                    attr = th.crit
                else:
                    attr = th.level_attr(rssi_level(val, self.cfg.good, self.cfg.fair, self.cfg.weak))
                addstr(win, row + cy, ix + axis_w + cx, char, attr, ix + iw)

    def _stats_lines(self, win, row, ix, iw, bottom, track, unit) -> None:
        th = self.theme
        stats = track.stats
        now = time.time()
        if row < bottom:
            parts = [
                ("min", _num(stats.min)), ("max", _num(stats.max)),
                ("avg", _num(stats.avg, 1)), ("σ" if self.g.unicode else "sd", _num(stats.stdev, 1)),
            ]
            self._kv_line(win, row, ix, iw, parts)
            span = self.engine.interval * (iw - 5) * (2 if self.braille else 1)
            span_text = f"{fmt_duration(span)}"
            addstr(win, row, ix + iw - len(span_text), span_text, th.faint)
            row += 1
        if row < bottom:
            avail = track.availability()
            parts = [
                (t("stat.avail"), "—" if avail is None else f"{avail:.0f}%"),
                (t("stat.drops"), str(track.disconnects)),
                (t("stat.reconn"), str(track.reconnects)),
                (t("stat.down"), fmt_duration(track.downtime(now)) if track.disconnects else "0"),
                (t("stat.timeouts"), str(track.timeouts)),
            ]
            self._kv_line(win, row, ix, iw, parts, warn_keys={t("stat.drops"), t("stat.timeouts")})

    def _kv_line(self, win, row, ix, iw, parts, warn_keys=frozenset()) -> None:
        th = self.theme
        x = ix
        for label, value in parts:
            chunk_len = len(label) + 1 + len(value) + 2
            if x + chunk_len > ix + iw - 6:
                break
            addstr(win, row, x, label + " ", th.label)
            x += len(label) + 1
            attr = th.warn if label in warn_keys and value not in ("0", "—") else th.bright
            addstr(win, row, x, value, attr)
            x += len(value) + 2

    def _panel_compact(self, win, row, ix, iw, bottom, track, rssi, unit, level, dev, last, end, state) -> None:
        th, g = self.theme, self.g
        # value + sparkline on one row
        if rssi is not None:
            value = f"{rssi:>4} {unit}"
            attr = th.level_attr(level) | curses.A_BOLD
        elif state != "connected":
            value, attr = t("panel.rssi.down"), th.crit
        else:
            value, attr = t("panel.rssi.none"), th.warn
        addstr(win, row, ix, value, attr)
        sx = ix + len(value) + 1
        sw = iw - (sx - ix)
        if sw >= 6:
            bins = track.binned(sw, self.engine.interval, end)
            lo, hi = graph_scale([b[0] for b in bins])
            for i, (char, val) in enumerate(sparkline(bins, sw, lo, hi, g.unicode)):
                if char == " ":
                    continue
                if isinstance(val, float) and math.isnan(val):
                    a = th.crit
                else:
                    a = th.level_attr(rssi_level(val, self.cfg.good, self.cfg.fair, self.cfg.weak))
                addstr(win, row, sx + i, char, a)
        row += 1
        if row < bottom:
            stats = track.stats
            battery, _ = self._battery(dev) if dev else (None, None)
            avail = track.availability()
            text = (f"min {_num(stats.min)} max {_num(stats.max)} avg {_num(stats.avg, 1)}"
                    f" · {t('stat.avail')} {'—' if avail is None else f'{avail:.0f}%'}"
                    f" · {t('stat.drops')} {track.disconnects}"
                    f" · {t('panel.battery')} {'—' if battery is None else f'{battery}%'}")
            addstr(win, row, ix, truncate(text, iw, g.ellipsis), th.dim)
            row += 1
        if row < bottom and bottom - row >= 2:
            # remaining height: a graph after all
            self._draw_graph(win, row, ix, iw, bottom - row, track, end)

    # -- details --------------------------------------------------------------------------

    def _detail_lines(self, snap) -> list[tuple[str, str, int]]:
        th = self.theme
        key = self.detail_key
        track = self.engine.tracks.get(key) if key else None
        if track is None:
            return [("", t("detail.none"), th.dim)]
        if track.kind == "usb":
            return self._detail_usb_lines(track)
        adapter, dev = self.engine.resolve(track, snap)
        na = t("na.notexposed")
        lines: list[tuple[str, str, int]] = []

        def add(label, value, attr=None):
            if value is None or value == "":
                lines.append((label, na, th.faint))
            else:
                lines.append((label, str(value), attr if attr is not None else th.text))

        def section(title):
            lines.append(("", "", 0))
            lines.append(("§", title, th.title))

        def flag(value):
            return None if value is None else (t("yes") if value else t("no"))

        now = time.time()
        section(t("detail.identity"))
        add(t("detail.name"), dev.name if dev else track.name)
        add(t("detail.alias"), dev.alias if dev else None)
        add(t("detail.address"), track.address)
        if dev:
            add(t("detail.addrtype"), f"{dev.address_type or '?'} · {t('detail.bearer')}: {dev.bearer}"
                + (f" · {t('detail.link')}: {dev.connected_bearer}" if dev.connected_bearer else ""))
            add(t("detail.kind"), f"{dev.kind}" + (f" (icon {dev.icon})" if dev.icon else ""))
            add(t("detail.appearance"), None if dev.appearance is None else f"0x{dev.appearance:04X}")
            add(t("detail.class"), None if dev.device_class is None else f"0x{dev.device_class:06X}")
            add("Modalias", dev.modalias)
            mod = parse_modalias(dev.modalias)
            if mod:
                add(t("detail.vidpid"), f"{mod['source']} {mod['vendor']:04X}:{mod['product']:04X} "
                    f"rev {mod['version']:04X}")
        if adapter:
            extra = " · ".join(p for p in (adapter.address, adapter.usb_id,
                                           f"USB {adapter.usb_port}" if adapter.usb_port else None,
                                           adapter.usb_product, adapter.driver) if p)
            add(t("detail.adapter"), f"{adapter.name} · {extra}")
        else:
            add(t("detail.adapter"), t("state.noadapter"), th.crit)

        section(t("detail.state"))
        if dev:
            add(t("detail.connected"), flag(dev.connected), th.ok if dev.connected else th.crit)
            add(t("detail.paired"), flag(dev.paired))
            add(t("detail.bonded"), flag(dev.bonded))
            add(t("detail.trusted"), flag(dev.trusted))
            add(t("detail.blocked"), flag(dev.blocked))
            add(t("detail.resolved"), flag(dev.services_resolved))
            add(t("detail.legacy"), flag(dev.legacy_pairing))
        else:
            add(t("detail.connected"), t("state.absent"), th.crit)
        add(t("detail.since"), fmt_duration(track.uptime(now)) if track.connected else "—")
        add(t("detail.lastconnect"), fmt_time(track.last_connect) or t("detail.never"))
        add(t("detail.lastdisconnect"), t("detail.never") if track.last_disconnect is None else
            fmt_time(track.last_disconnect) + (f" — {track.last_disconnect_reason}"
                                               if track.last_disconnect_reason else ""))

        section(t("detail.link.section"))
        last = track.last_sample
        unit = t("unit.relative") if track.rssi_kind == "dB" else "dBm"
        if last and last.rssi is not None:
            src = t("detail.src." + (last.source or "conn"))
            add(t("detail.rssi.link"), f"{last.rssi} {unit} · {src} · {fmt_time(last.ts)}")
        else:
            add(t("detail.rssi.link"), last.detail if last and last.detail else None)
        if track.rssi_kind == "dB":
            lines.append(("", t("detail.bredr.note"), th.dim))
        add(t("detail.rssi.adv"), None if not dev or dev.rssi is None else f"{dev.rssi} dBm")
        add(t("detail.tx.link"), None if track.last_tx_power is None
            else t("detail.tx.reported", value=track.last_tx_power))
        add(t("detail.tx.max"), None if track.last_max_tx_power is None
            else t("detail.tx.reported", value=track.last_max_tx_power))
        add(t("detail.tx.adv"), None if not dev or dev.tx_power is None else f"{dev.tx_power} dBm")
        if track.known_addr_type is not None:
            add(t("detail.conntype"), ADDR_TYPE_NAMES.get(track.known_addr_type, "?"))
        else:
            add(t("detail.conntype"), None)
        add(t("detail.query"), None if not last or last.latency is None
            else f"{last.latency * 1000:.0f} ms · {t('detail.query.note')}")
        lines.append(("", t("detail.cache.note"), th.dim))

        section(t("detail.battery"))
        if dev and dev.battery is not None:
            add("Battery1", f"{dev.battery}% · {dev.battery_source or 'BlueZ'}")
        else:
            add("Battery1", None)
        kernel_bat = self.engine.battery_reader(track.address)
        add(t("detail.battery.kernel"), None if not kernel_bat else f"{kernel_bat[0]}% · {kernel_bat[1]}")

        section(t("detail.stats"))
        s = track.stats
        add(t("detail.minmax"), None if s.min is None else
            f"min {s.min} · max {s.max} · avg {_num(s.avg, 1)} · σ {_num(s.stdev, 2)} {unit}")
        add(t("detail.samples"), t("detail.samples.value", total=track.total_samples,
                                   connected=track.connected_samples, rssi=track.rssi_samples))
        avail = track.availability()
        add(t("detail.avail"), None if avail is None else f"{avail:.1f}%")
        share = track.connected_share()
        add(t("detail.share"), None if share is None else f"{share:.1f}%")
        add(t("detail.drops"), t("detail.drops.value", drops=track.disconnects,
                                 reconnects=track.reconnects,
                                 down=fmt_duration(track.downtime(now))))
        add(t("detail.timeouts"), str(track.timeouts))
        add(t("detail.connected.total"), fmt_duration(track.connected_time(now)))
        add(t("detail.monitoring"), fmt_time(track.started))

        if dev:
            section(t("detail.uuids", count=len(dev.uuids)))
            if not dev.uuids:
                lines.append(("", na, th.faint))
            for uuid in dev.uuids:
                name = service_name(uuid)
                lines.append(("", f"{short_uuid(uuid):<38} {name or ''}", th.text))
            section(t("detail.gatt", count=len(dev.gatt_services)))
            if not dev.gatt_services:
                lines.append(("", t("detail.gatt.none"), th.faint))
            for uuid, primary in dev.gatt_services:
                name = service_name(uuid) or ""
                lines.append(("", f"{short_uuid(uuid):<38} {name}{'' if primary else ' (secondary)'}",
                              th.text))

        section(t("detail.changes"))
        changes = list(track.changes)[-15:]
        if not changes:
            lines.append(("", t("detail.changes.none"), th.faint))
        for change in changes:
            lines.append((fmt_time(change.ts), f"{change.field}: {_fmt(change.old)} → {_fmt(change.new)}",
                          th.text))

        section(t("detail.events"))
        events = self.engine.events.events(track.key)[-20:]
        if not events:
            lines.append(("", t("detail.events.none"), th.faint))
        for event in events:
            attr = {"error": th.crit, "warn": th.warn}.get(event.level, th.text)
            lines.append((fmt_time(event.ts), event.text, attr))
        return lines

    def _detail_usb_lines(self, track) -> list[tuple[str, str, int]]:
        th = self.theme
        na = t("na.notexposed")
        lines: list[tuple[str, str, int]] = []

        def add(label, value, attr=None):
            lines.append((label, na if value in (None, "") else str(value),
                          th.faint if value in (None, "") else (attr if attr is not None else th.text)))

        def section(title):
            lines.append(("", "", 0))
            lines.append(("§", title, th.title))

        dev = self.engine.resolve_usb(track)
        now = time.time()
        section(t("detail.identity"))
        add(t("detail.name"), dev.display_name if dev else track.name)
        add(t("detail.usb.place"), f"{track.address}  (sysfs {dev.sysfs})" if dev else track.address)
        if dev:
            add("USB ID", f"{dev.usb_id} · bcdDevice {dev.bcd_device or '?'}")
            add(t("detail.usb.strings"), " / ".join(p for p in (dev.manufacturer, dev.product_name) if p))
            add(t("detail.usb.serial"), dev.serial)
            add(t("detail.usb.speed"), f"{dev.speed} Mbit/s" if dev.speed else None)
            add(t("detail.usb.profile"), dev.profile.id if dev.profile else t("detail.usb.noprofile"))
            add(t("detail.usb.transport"), t("transport." + dev.transport))
            if dev.transport == "usb-2.4?":
                lines.append(("", t("detail.usb.ambiguous"), th.warn))
        section(t("detail.state"))
        label, attr, state, _ = self._usb_state(track)
        add(t("panel.link"), label, attr)
        add(t("detail.usb.mode"), None if not dev else t("usb.mode." + (dev.mode or "unknown")))
        add(t("detail.since"), fmt_duration(track.uptime(now)) if track.connected else "—")
        add(t("detail.lastconnect"), fmt_time(track.last_connect) or t("detail.never"))
        add(t("detail.lastdisconnect"), t("detail.never") if track.last_disconnect is None else
            fmt_time(track.last_disconnect) + (f" — {track.last_disconnect_reason}"
                                               if track.last_disconnect_reason else ""))
        for stamp, old, new in self.engine.usb_records.get(track.address, [])[-6:]:
            lines.append((fmt_time(stamp), f"{old} → {new}", th.text))
        section(t("detail.usb.metrics"))
        add("RSSI", t("detail.usb.rssi"), th.dim)
        add(t("detail.battery"), f"{dev.battery[0]}% · {dev.battery[1]}" if dev and dev.battery
            else t("detail.usb.battery"), th.dim if not (dev and dev.battery) else None)
        add(t("panel.input"), t("detail.usb.input", total=track.activity_total, node=track.input_node or "—")
            if track.activity_status == "ok" else self._activity_why(track))
        add(t("stat.lastinput"), None if track.last_input is None else
            f"{fmt_time(track.last_input)} ({fmt_duration(now - track.last_input)})")
        lines.append(("", t("detail.usb.inactive.note"), th.dim))
        add(t("detail.drops"), t("detail.drops.value", drops=track.disconnects, reconnects=track.reconnects,
                                 down=fmt_duration(track.downtime(now))))
        if dev:
            section(t("detail.usb.interfaces", count=len(dev.interfaces)))
            for iface in dev.interfaces:
                cls = f"0x{iface.cls:02x}" if iface.cls is not None else "?"
                lines.append((f"if{iface.number}", f"class {cls} · {iface.driver or t('detail.usb.nodriver')}"
                              f" · {iface.role}", th.text))
                for node in iface.hidraw:
                    lines.append(("", f"{node.name}  {node.hid_name or ''}  {node.hid_phys or ''}", th.dim))
                for node in iface.inputs:
                    lines.append(("", f"{node.name}  {node.input_name or ''}"
                                      f"{'  (joystick)' if node.joystick else ''}", th.dim))
            lines.append(("", t("detail.usb.hidhint", sel=f"{dev.usb_id}:2"), th.dim))
        section(t("detail.changes"))
        changes = list(track.changes)[-15:]
        if not changes:
            lines.append(("", t("detail.changes.none"), th.faint))
        for change in changes:
            lines.append((fmt_time(change.ts), f"{change.field}: {_fmt(change.old)} → {_fmt(change.new)}", th.text))
        section(t("detail.events"))
        events = self.engine.events.events(track.key)[-20:]
        if not events:
            lines.append(("", t("detail.events.none"), th.faint))
        for event in events:
            lines.append((fmt_time(event.ts), event.text,
                          {"error": th.crit, "warn": th.warn}.get(event.level, th.text)))
        return lines

    def _draw_detail(self, win, top: int, height: int, width: int, snap) -> None:
        th, g = self.theme, self.g
        track = self.engine.tracks.get(self.detail_key or "")
        title = t("detail.title", name=track.name if track else "?")
        draw_box(win, top, 0, height, width, th, g, title, t("detail.subtitle"), focus=True)
        lines = self._detail_lines(snap)
        inner_h = height - 2
        self.detail_scroll = max(0, min(self.detail_scroll, max(0, len(lines) - inner_h)))
        label_w = min(30, max(14, width // 4))
        for i, (label, value, attr) in enumerate(lines[self.detail_scroll:self.detail_scroll + inner_h]):
            y = top + 1 + i
            if label == "§":
                addstr(win, y, 2, f"{value}", attr | curses.A_BOLD)
                addstr(win, y, 3 + len(value), (g.h * (width - 6 - len(value))), th.border)
                continue
            if label:
                addstr(win, y, 3, truncate(label, label_w - 1, g.ellipsis), th.label)
            addstr(win, y, 3 + label_w, truncate(value, width - label_w - 6, g.ellipsis), attr, width - 2)
        if len(lines) > inner_h:
            info = f" {self.detail_scroll + 1}-{min(len(lines), self.detail_scroll + inner_h)}/{len(lines)} "
            addstr(win, top + height - 1, width - len(info) - 2, info, th.dim)

    # -- log -------------------------------------------------------------------------------

    def _draw_log(self, win, top: int, height: int, width: int) -> None:
        th, g = self.theme, self.g
        # kernel lines from earlier in the boot arrive after start-up; show them in time order
        events = sorted(self.engine.events.events(), key=lambda e: e.ts)
        if self.log_filter != "all":
            events = [e for e in events if e.category == self.log_filter]
        draw_box(win, top, 0, height, width, th, g, t("log.title"),
                 t("log.subtitle", filter=t("logfilter." + self.log_filter), count=len(events)),
                 focus=True)
        inner_h = height - 2
        self.log_scroll = max(0, min(self.log_scroll, max(0, len(events) - inner_h)))
        end = len(events) - self.log_scroll
        start = max(0, end - inner_h)
        cat_w = 8
        for i, event in enumerate(events[start:end]):
            y = top + 1 + i
            attr = {"error": th.crit, "warn": th.warn}.get(event.level, th.text)
            stamp = time.strftime("%H:%M:%S", time.localtime(event.ts))
            addstr(win, y, 2, stamp, th.dim)
            addstr(win, y, 11, t("cat." + event.category)[:cat_w].ljust(cat_w), th.label)
            addstr(win, y, 12 + cat_w, truncate(event.text, width - 14 - cat_w, g.ellipsis), attr, width - 2)
        if not events:
            addstr(win, top + 1, 2, t("log.empty"), th.faint)

    # -- help --------------------------------------------------------------------------------

    def _draw_help(self, win, height: int, width: int) -> None:
        th, g = self.theme, self.g
        w = min(72, width - 4)
        h = min(len(HELP) + 4, height - 2)
        y0, x0 = (height - h) // 2, (width - w) // 2
        for row in range(h):
            addstr(win, y0 + row, x0, " " * w, th.text)
        draw_box(win, y0, x0, h, w, th, g, t("help.title"), "Esc", focus=True)
        y = y0 + 1
        for key, desc in HELP:
            if y >= y0 + h - 2:
                break
            if desc is None:
                addstr(win, y, x0 + 2, t(key), th.title)
            else:
                addstr(win, y, x0 + 4, key.ljust(18), th.accent)
                addstr(win, y, x0 + 23, truncate(t(desc), w - 25, g.ellipsis), th.text)
            y += 1
        addstr(win, y0 + h - 2, x0 + 2, truncate(t("help.note"), w - 4, g.ellipsis), th.dim)


# --------------------------------------------------------------------------
# Layout and formatting helpers
# --------------------------------------------------------------------------

def grid_layout(count: int, width: int, height: int, compact: bool = False,
                min_w: int = 44, min_h: int = 11) -> list[tuple[int, int, int, int]]:
    """Panel rectangles (y, x, h, w) for 1–4 devices.

    Picks the arrangement whose panels are the largest while staying at least
    min_w × min_h; with four devices that is 2×2 when the window allows it.
    When nothing fits, panels are stacked and shown in compact form.
    """
    if count <= 0:
        return []
    options = []
    for cols in range(1, count + 1):
        rows = math.ceil(count / cols)
        w = width // cols
        h = height // rows
        fits = w >= min_w and h >= (5 if compact else min_h)
        # prefer fitting, then square-ish panels; when nothing fits, stack them
        # full width (compact panels read better wide than narrow)
        score = (fits, min(w / 2.2, h) if fits else -cols)
        options.append((score, cols, rows))
    options.sort(key=lambda o: o[0], reverse=True)
    _, cols, rows = options[0]
    out = []
    for i in range(count):
        r, c = divmod(i, cols)
        # the last row may hold fewer panels: let them share its full width
        in_row = cols if r < rows - 1 else count - cols * (rows - 1)
        w = width // in_row
        x = c * w
        if c == in_row - 1:
            w = width - x
        h = height // rows
        y = r * h
        if r == rows - 1:
            h = height - y
        out.append((y, x, h, w))
    return out


def _dump(win, path: str) -> None:
    """Development aid: the screen as curses believes it to be (see dev/tuishot.py)."""
    height, width = win.getmaxyx()
    lines = []
    for y in range(height):
        try:
            # instr counts bytes, and a cell may take up to four of them
            lines.append(win.instr(y, 0, width * 4).decode("utf-8", "replace").rstrip())
        except curses.error:
            lines.append("")
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def fmt_time(ts: float | None) -> str | None:
    if ts is None:
        return None
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _num(value, digits: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}" if digits else f"{value:.0f}"


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return t("yes") if value else t("no")
    return str(value)


def run(engine: Engine, cfg: Config, preselect: list[str] | None = None,
        start_monitoring: bool = False) -> None:
    app = App(engine, cfg, preselect, start_monitoring)
    curses.wrapper(app.run)


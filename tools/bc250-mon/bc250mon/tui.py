"""The terminal interface: a btop-style dashboard and a HWiNFO-style sensor table."""

from __future__ import annotations

import curses
import re
import time

from . import collector as coll
from . import sysfs
from .i18n import t
from .sysfs import Channel
from .collector import Collector, Snapshot, format_bytes, format_value
from .widgets import Theme, addstr, block_bar, draw_box, draw_gauge, draw_graph, truncate

VIEW_DASH = 0
VIEW_SENSORS = 1

# A graph stretched across half the screen adds nothing and looks bad — instead
# of one huge one we draw several smaller ones, each of a bounded height.
GRAPH_MIN_ROWS = 3
GRAPH_MAX_ROWS = 16
GRAPH_BLOCK_MIN = GRAPH_MIN_ROWS + 1     # wykres + wiersz podpisu

# The condensed sensor table below the panels appears only when there is room
# for it and it does not take that room from the panels.
STRIP_MIN_GRID = 45
STRIP_MAX_ROWS = 20

# Order of sensor chips in the table view
CHIP_ORDER = ("amdgpu", "k10temp", "nct6686", "nct6687", "nct6683", "nvme", "obliczone")

KIND_KEYS = {
    "temp": "kind.temp", "fan": "kind.fan", "volt": "kind.volt",
    "power": "kind.power", "pwm": "kind.pwm", "curr": "kind.curr",
    "freq": "kind.freq", "usage": "kind.usage",
}

HELP_KEYS = [
    ("q / Esc", "help.quit"),
    ("Tab / 1 / 2", "help.switch"),
    ("↑ ↓ PgUp PgDn Home End", "help.scroll"),
    ("p", "help.pause"),
    ("r", "help.reset"),
    ("+ / -", "help.interval"),
    ("d", "help.diagnostics"),
    ("h / ?", "help.help"),
]


class Monitor:
    def __init__(self, collector: Collector, interval: float = 1.0,
                 show_notes: bool = True) -> None:
        self.collector = collector
        self.interval = interval
        self.show_notes = show_notes
        self.view = VIEW_DASH
        self.paused = False
        self.show_help = False
        self.scroll = 0
        self.snap: Snapshot | None = None
        self._last_poll = 0.0

    # -- main loop ------------------------------------------------------

    def run(self, stdscr) -> None:
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.timeout(80)
        self.theme = Theme()
        self.snap = self.collector.collect()
        self._last_poll = time.monotonic()

        while True:
            now = time.monotonic()
            if not self.paused and now - self._last_poll >= self.interval:
                self.snap = self.collector.collect()
                self._last_poll = now

            stdscr.erase()
            self._draw(stdscr)
            stdscr.noutrefresh()
            curses.doupdate()

            key = stdscr.getch()
            if key != -1 and not self._handle_key(key, stdscr):
                return

    def _handle_key(self, key: int, stdscr) -> bool:
        if key in (ord("q"), ord("Q"), 27):
            if self.show_help:
                self.show_help = False
                return True
            return False
        if key in (ord("h"), ord("H"), ord("?")):
            self.show_help = not self.show_help
        elif key == ord("\t") or key in (ord("2"),):
            self.view = VIEW_SENSORS if self.view == VIEW_DASH else VIEW_DASH
            self.scroll = 0
        elif key == ord("1"):
            self.view = VIEW_DASH
        elif key in (ord("p"), ord("P"), ord(" ")):
            self.paused = not self.paused
        elif key in (ord("r"), ord("R")):
            self.collector.reset_stats()
        elif key in (ord("d"), ord("D")):
            self.show_notes = not self.show_notes
        elif key in (ord("+"), ord("=")):
            self.interval = min(10.0, round(self.interval + 0.25, 2))
        elif key in (ord("-"), ord("_")):
            self.interval = max(0.25, round(self.interval - 0.25, 2))
        elif key == curses.KEY_DOWN:
            self.scroll += 1
        elif key == curses.KEY_UP:
            self.scroll = max(0, self.scroll - 1)
        elif key == curses.KEY_NPAGE:
            self.scroll += 10
        elif key == curses.KEY_PPAGE:
            self.scroll = max(0, self.scroll - 10)
        elif key == curses.KEY_HOME:
            self.scroll = 0
        elif key == curses.KEY_END:
            self.scroll = 10 ** 6
        elif key == curses.KEY_RESIZE:
            stdscr.erase()
        return True

    # -- screen skeleton ---------------------------------------------------

    def _draw(self, win) -> None:
        height, width = win.getmaxyx()
        if height < 8 or width < 40:
            addstr(win, 0, 0, t("err.window.small"), self.theme.warn)
            return

        self._draw_header(win, width)
        body_top, body_h = 2, height - 3
        if self.view == VIEW_DASH:
            self._draw_dashboard(win, body_top, body_h, width)
        else:
            self._draw_sensors(win, body_top, body_h, width)
        self._draw_footer(win, height - 1, width)

        if self.show_help:
            self._draw_help(win, height, width)

    def _draw_header(self, win, width: int) -> None:
        snap = self.snap
        theme = self.theme
        assert snap is not None

        badge = "BC-250" if snap.is_bc250 else t("mode.generic")
        title = f" bc250-mon · {badge} "
        addstr(win, 0, 0, " " * width, theme.header)
        addstr(win, 0, 0, title, theme.header)

        gov_name, gov_state = snap.governor_state
        right = f"{t('state.paused') + ' · ' if self.paused else ''}{self.interval:.2f}s "
        addstr(win, 0, max(0, width - len(right)), right, theme.header)

        module = snap.superio_module or t("header.none")
        system_name = snap.system.os_name
        if system_name.lower().startswith(snap.system.hostname.lower()):
            system_name = ""          # "bazzite · Bazzite" says it twice
        info = (f"{snap.system.hostname}{' · ' + system_name if system_name else ''}"
                f" · {t('header.kernel')} {snap.system.kernel}"
                f" · {t('header.uptime')} {sysfs.uptime_str(snap.system.uptime)}"
                f" · SuperIO: {module} · {gov_name.replace('cyan-skillfish-', '')}: {gov_state}")
        if snap.governor:
            low, high = snap.governor.effective_range
            if low and high:
                info += f" {low}–{high} MHz"
        state_attr = theme.ok if gov_state == "active" else theme.dim
        addstr(win, 1, 1, truncate(info, width - 2), state_attr)

    def _draw_footer(self, win, y: int, width: int) -> None:
        theme = self.theme
        view_name = t("footer.view.dash") if self.view == VIEW_DASH else t("footer.view.sensors")
        addstr(win, y, 1, truncate(t("footer.keys"), width - 22), theme.faint)
        right = f"[{view_name}] {t('footer.samples')}: {self.collector.samples}"
        addstr(win, y, max(0, width - len(right) - 1), right, theme.dim)

    # -- dashboard ------------------------------------------------------------

    def _draw_dashboard(self, win, top: int, height: int, width: int) -> None:
        snap = self.snap
        assert snap is not None

        notes_h = 0
        if self.show_notes and snap.notes and height >= 14:
            notes_h = min(len(snap.notes) + 2, 5)
        grid_h = height - notes_h

        if width >= 100:
            left_w = width * 55 // 100
            right_w = width - left_w

            mid_h = self._mid_row_height(grid_h, left_w)
            strip_h = self._strip_row_height(grid_h, mid_h, right_w, width)
            top_h = grid_h - mid_h - strip_h

            self._panel_gpu(win, top, 0, top_h, left_w)
            self._panel_cpu(win, top, left_w, top_h, right_w)
            if mid_h:
                self._panel_board(win, top + top_h, 0, mid_h, left_w)
                self._panel_memory(win, top + top_h, left_w, mid_h, right_w)
            if strip_h:
                self._panel_sensor_strip(win, top + top_h + mid_h, 0, strip_h, width)
        else:
            gpu_h = min(12, grid_h)
            self._panel_gpu(win, top, 0, gpu_h, width)
            if grid_h - gpu_h >= 6:
                self._panel_cpu(win, top + gpu_h, 0, grid_h - gpu_h, width)

        if notes_h:
            self._panel_notes(win, top + grid_h, 0, notes_h, width)

    def _top_row_max(self, right_w: int) -> int:
        """Top row: panel content plus at most three graphs per panel."""
        snap = self.snap
        assert snap is not None
        threads = len(snap.cpu.usage) or 1
        per_row = _core_columns(right_w - 4)
        gpu_rows = 8                                   # gauges, voltages, VRAM, DPM
        cpu_rows = 5 + (threads + per_row - 1) // per_row
        return max(gpu_rows, cpu_rows) + 2 + 3 * (GRAPH_MAX_ROWS + 1)

    def _strip_row_height(self, grid_h: int, mid_h: int, right_w: int, width: int) -> int:
        """How many rows the condensed sensor table gets.

        It takes only what it will actually fill, and never so much that the top
        panels drop below two graphs each.
        """
        if grid_h < STRIP_MIN_GRID:
            return 0

        row_count = len(self._sensor_rows())
        columns = self._strip_columns(row_count, width - 4, grid_h)
        needed = -(-row_count // columns) + 2

        threads = (len(self.snap.cpu.usage) if self.snap else 0) or 1
        cpu_rows = 5 + -(-threads // _core_columns(right_w - 4))
        min_top = max(8, cpu_rows) + 2 + 2 * GRAPH_BLOCK_MIN

        return max(0, min(needed, STRIP_MAX_ROWS, grid_h - mid_h - min_top))

    def _mid_row_height(self, grid_h: int, left_w: int) -> int:
        """The bottom row gets as much as the board panel needs."""
        if grid_h < 18:
            return 0
        snap = self.snap
        assert snap is not None

        board = 2
        channels = list(snap.board_chip.channels) if snap.board_chip else [
            ch for other in snap.other_chips for ch in other.channels]
        if channels:
            inner_w = left_w - 4
            per_row = max(1, inner_w // 24)
            for kind in ("temp", "fan", "pwm", "volt"):
                if kind == "fan":
                    count = sum(1 for c in channels
                                if c.kind == "fan" and self._fan_ever_spun(c))
                else:
                    count = sum(1 for c in channels if c.kind == kind and c.value)
                if count:
                    board += 1 + (count + per_row - 1) // per_row
        else:
            board += 1

        # the memory panel needs only its content and the smallest graph —
        # it will fill any remaining space with a stack of graphs anyway
        memory = 3
        memory += 1 if snap.mem.swap_total else 0
        memory += 1 if snap.gpu.gtt_total else 0
        memory += len(snap.nvme_temps)
        memory += GRAPH_BLOCK_MIN

        return max(6, min(max(board, memory), grid_h // 3))

    def _draw_graph_stack(self, win, x: int, y_top: int, y_bottom: int, width: int,
                          specs: list[tuple[str, str, float, float | None]]) -> None:
        """Draws as many graphs from `specs` as fit in the available height.

        Each gets at most GRAPH_MAX_ROWS rows — so on a tall terminal more graphs
        appear instead of one bloated one.
        """
        theme = self.theme
        available = y_bottom - y_top
        if available < GRAPH_BLOCK_MIN:
            return

        count = max(1, min(len(specs), available // GRAPH_BLOCK_MIN))
        block = min(GRAPH_MAX_ROWS + 1, available // count)

        y = y_top
        for label, key, vmin, vmax in specs[:count]:
            if y + block > y_bottom:
                break
            history = self.collector.history(key)
            top_v = vmax
            if top_v is None:
                top_v = max(history) * 1.1 if history and max(history) > 0 else None
            addstr(win, y, x, truncate(label, width), theme.faint)
            draw_graph(win, y + 1, x, width, block - 1, theme, history, vmin, top_v)
            y += block

    def _panel_gpu(self, win, y: int, x: int, h: int, w: int) -> None:
        snap, theme = self.snap, self.theme
        assert snap is not None
        gpu = snap.gpu

        name = "Cyan Skillfish (gfx1013)" if gpu.is_cyan_skillfish else "amdgpu"
        pci = (f"{gpu.pci_slot or '—'} · 0x{gpu.pci_id:04X}" if gpu.pci_id
               else t("panel.gpu.nocard.short"))
        draw_box(win, y, x, h, w, theme, f"GPU · {name}", pci)

        inner_x, inner_w = x + 2, w - 4
        row = y + 1
        bottom = y + h - 1

        if not gpu.device_path:
            addstr(win, row, inner_x,
                   truncate(t("panel.gpu.nocard"), inner_w),
                   theme.warn)
            return

        warn, crit = coll.THRESHOLDS["gpu"]
        temp = snap.gpu_temp
        if row < bottom:
            draw_gauge(win, row, inner_x, inner_w, theme, t("gauge.temperature"), temp,
                       format_value(temp, "°C"), _frac(temp, 30, 95),
                       theme.level(temp, warn, crit))
            row += 1

        power = snap.gpu_power
        if row < bottom:
            draw_gauge(win, row, inner_x, inner_w, theme, t("gauge.power"), power,
                       format_value(power, "W"), _frac(power, 0, 235))
            row += 1

        busy = gpu.busy_percent
        if row < bottom:
            text = format_value(busy, "%") if busy is not None else t("value.unavailable")
            draw_gauge(win, row, inner_x, inner_w, theme, t("gauge.load"), busy,
                       text, _frac(busy, 0, 100),
                       None if busy is not None else theme.dim)
            row += 1

        clock = snap.gpu_clock_mhz
        if row < bottom:
            text = (format_value(_f(clock), "MHz") if clock is not None
                    else t("value.unavailable"))
            label = (t("gauge.gpu.clock.avg") if snap.gpu_clock_is_average
                     else t("gauge.gpu.clock"))
            # scale the bar to the governor's own range when it is known
            scale = snap.governor.effective_range if snap.governor else (None, None)
            low = float(scale[0] or 800)
            high = float(scale[1] or 2230)
            draw_gauge(win, row, inner_x, inner_w, theme, label,
                       _f(clock), text, _frac(_f(clock), low, high),
                       None if clock is not None else theme.dim)
            row += 1

        if row < bottom:
            parts = [f"vddgfx {format_value(snap.gpu_vddgfx, 'V')}"]
            # the governor curve turns that voltage back into a frequency, which
            # is the only view of the running clock the firmware does not break
            estimate = snap.estimated_gpu_clock
            if estimate:
                low, high = estimate
                shown = f"{low} MHz" if low == high else f"{low}–{high} MHz"
                parts.append(t("gpu.clock.from.curve", clock=shown))
            parts.append(f"vddnb {format_value(snap.gpu_vddnb, 'V')}")
            mclk = snap.gpu_mem_clock_mhz
            if mclk:
                parts.append(f"mclk {mclk} MHz")
            addstr(win, row, inner_x, truncate(" · ".join(parts), inner_w), theme.text)
            row += 1

        if row < bottom and gpu.vram_total:
            frac = (gpu.vram_used or 0) / gpu.vram_total
            text = f"{format_bytes(gpu.vram_used)} / {format_bytes(gpu.vram_total)}"
            # with a small split this bar sits near full by design, so it must not
            # be painted as an alarm
            label = (t("gauge.vram.carveout") if snap.small_vram_split else "VRAM")
            attr = theme.label if snap.small_vram_split else None
            draw_gauge(win, row, inner_x, inner_w, theme, label, frac * 100,
                       text, frac, attr)
            row += 1

        if row < bottom and gpu.sclk:
            marks = " ".join(
                (f"[{lvl.mhz * (100 if gpu.sclk_suspect else 1)}]" if lvl.active
                 else str(lvl.mhz * (100 if gpu.sclk_suspect else 1)))
                for lvl in gpu.sclk)
            level = gpu.perf_level or "?"
            addstr(win, row, inner_x,
                   truncate(f"DPM {marks}  ·  {t('gpu.level')}: {level}", inner_w),
                   theme.dim)
            row += 1

        self._draw_graph_stack(win, inner_x, row, bottom, inner_w, [
            (t("graph.gpu.temp"), "gpu.temp", 30.0, 95.0),
            (t("graph.gpu.power"), "gpu.power", 0.0, None),
            (t("graph.gpu.clock.avg") if snap.gpu_clock_is_average
             else t("graph.gpu.clock"), "gpu.clock", 0.0, None),
        ])

    def _panel_cpu(self, win, y: int, x: int, h: int, w: int) -> None:
        snap, theme = self.snap, self.theme
        assert snap is not None
        cpu = snap.cpu

        draw_box(win, y, x, h, w, theme,
                 f"CPU · {truncate(cpu.model, max(10, w - 26))}", _core_summary(cpu))

        inner_x, inner_w = x + 2, w - 4
        row, bottom = y + 1, y + h - 1

        warn, crit = coll.THRESHOLDS["cpu"]
        temp = snap.cpu_temp
        if row < bottom:
            draw_gauge(win, row, inner_x, inner_w, theme, snap.cpu_temp_label, temp,
                       format_value(temp, "°C"), _frac(temp, 30, 95),
                       theme.level(temp, warn, crit))
            row += 1
        if row < bottom:
            draw_gauge(win, row, inner_x, inner_w, theme, t("gauge.load"), cpu.total_usage,
                       format_value(cpu.total_usage, "%"), _frac(cpu.total_usage, 0, 100))
            row += 1
        avg_freq = sum(cpu.freqs_mhz) / len(cpu.freqs_mhz) if cpu.freqs_mhz else None
        if row < bottom:
            draw_gauge(win, row, inner_x, inner_w, theme, t("gauge.clock.avg"), avg_freq,
                       format_value(avg_freq, "MHz"), _frac(avg_freq, 800, 3600))
            row += 1

        if row < bottom:
            gov = cpu.governor or t("cpu.nocpufreq")
            drv = f" ({cpu.driver})" if cpu.driver else ""
            load = " ".join(f"{v:.2f}" for v in cpu.loadavg)
            addstr(win, row, inner_x, truncate(f"governor: {gov}{drv}  ·  load {load}", inner_w),
                   theme.dim)
            row += 1

        # thread grid — as many columns as the panel width allows
        if cpu.usage and row < bottom - 1:
            per_row = _core_columns(inner_w)
            needed = -(-len(cpu.usage) // per_row)
            available = max(0, bottom - row - 1)          # excluding the heading row
            # when short of room, keep a row for the truncation notice so the
            # list does not end silently
            truncated = needed > available
            visible_rows = max(0, available - 1) if truncated else needed
            shown = min(len(cpu.usage), visible_rows * per_row)

            addstr(win, row, inner_x,
                   truncate(t("cpu.threads", count=len(cpu.usage)), inner_w), theme.faint)
            row += 1
            cell_w = inner_w // per_row
            for idx, usage in enumerate(cpu.usage[:shown]):
                line = row + idx // per_row
                col = inner_x + (idx % per_row) * cell_w
                freq = cpu.freqs_mhz[idx] if idx < len(cpu.freqs_mhz) else None
                freq_text = f"{freq / 1000:.1f}G" if freq else "  — "
                bar_w = max(4, cell_w - 16)
                addstr(win, line, col, f"{idx:>2} ", theme.faint)
                addstr(win, line, col + 3, block_bar(usage / 100.0, bar_w),
                       theme.ramp(usage / 100.0))
                addstr(win, line, col + 3 + bar_w + 1, f"{usage:3.0f}% {freq_text}", theme.text,
                       inner_x + inner_w)
            row += visible_rows
            if truncated and row < bottom:
                addstr(win, row, inner_x,
                       truncate(t("cpu.threads.more", count=len(cpu.usage) - shown),
                                inner_w), theme.warn)
                row += 1

        self._draw_graph_stack(win, inner_x, row, bottom, inner_w, [
            (t("graph.cpu.load"), "cpu.usage", 0.0, 100.0),
            (t("graph.cpu.temp"), "cpu.temp", 30.0, 100.0),
            (t("graph.cpu.clock"), "cpu.clock", 0.0, None),
        ])

    def _panel_board(self, win, y: int, x: int, h: int, w: int) -> None:
        snap, theme = self.snap, self.theme
        assert snap is not None
        chip = snap.board_chip
        channels = list(chip.channels) if chip else []
        if chip:
            title = t("panel.board.superio")
            subtitle = f"{chip.name} · {snap.superio_module or t('panel.board.notloaded')}"
        else:
            title = t("panel.board.other")
            others = snap.other_chips
            subtitle = t("panel.board.chips", count=len(others))
            for other in others:
                # without a prefix several chips would all read just "Temp 1"
                short = other.name.split("_")[0][:8]
                channels.extend(
                    Channel(key=ch.key, label=f"{short} {ch.label}", kind=ch.kind,
                            value=ch.value, unit=ch.unit, limit=ch.limit, crit=ch.crit)
                    for ch in other.channels)
        draw_box(win, y, x, h, w, theme, title, subtitle)

        inner_x, inner_w = x + 2, w - 4
        row, bottom = y + 1, y + h - 1

        if not channels:
            addstr(win, row, inner_x,
                   truncate(t("panel.board.nosuperio"), inner_w),
                   theme.warn)
            return

        warn, crit = coll.THRESHOLDS["board"]

        # A fan header with nothing plugged in reads 0 RPM forever and only adds
        # noise to the dashboard; the sensor table still lists every channel.
        fans = [c for c in channels if c.kind == "fan" and self._fan_ever_spun(c)]
        hidden_fans = {_channel_index(c) for c in channels
                       if c.kind == "fan" and not self._fan_ever_spun(c)}
        pwms = {_channel_index(c): c for c in channels if c.kind == "pwm" and c.value}

        # A fan's duty cycle says more than the raw RPM, and on the NCT6686
        # pwmN drives fanN, so the two are shown together.
        overrides: dict[str, str] = {}
        paired: set[str] = set()
        for fan in fans:
            text = format_value(fan.value, fan.unit)
            pwm = pwms.get(_channel_index(fan))
            if pwm is not None and pwm.value is not None:
                paired.add(_channel_index(fan))
                text += f" · {pwm.value:.0f}%"
            overrides[fan.key] = text

        groups = (
            ("temp", [c for c in channels if c.kind == "temp" and c.value], 24),
            ("fan", fans, 34),
            # a duty cycle for a header with no fan on it says nothing
            ("pwm", [c for c in channels if c.kind == "pwm" and c.value
                     and _channel_index(c) not in paired
                     and _channel_index(c) not in hidden_fans], 24),
            ("volt", [c for c in channels if c.kind == "volt" and c.value], 24),
        )
        for kind, items, min_cell in groups:
            if row >= bottom or not items:
                continue
            addstr(win, row, inner_x, t(KIND_KEYS.get(kind, kind)), theme.faint)
            row += 1
            per_row = max(1, inner_w // min_cell)
            for idx, ch in enumerate(items):
                line = row + idx // per_row
                if line >= bottom:
                    break
                col = inner_x + (idx % per_row) * (inner_w // per_row)
                cell_w = inner_w // per_row - 2
                text = overrides.get(ch.key) or format_value(ch.value, ch.unit)
                attr = theme.level(ch.value, warn, crit) if kind == "temp" else theme.bright
                name = truncate(ch.label, max(6, cell_w - len(text) - 1))
                addstr(win, line, col, name, theme.label, inner_x + inner_w)
                addstr(win, line, col + cell_w - len(text), text, attr, inner_x + inner_w)
            row += (len(items) + per_row - 1) // per_row

    def _fan_ever_spun(self, channel) -> bool:
        """Whether a fan has shown any movement at all during this session."""
        stat = self.collector.stats.get(channel.key)
        if stat is None or stat.maximum is None:
            return bool(channel.value)
        return stat.maximum > 0

    def _panel_memory(self, win, y: int, x: int, h: int, w: int) -> None:
        snap, theme = self.snap, self.theme
        assert snap is not None
        draw_box(win, y, x, h, w, theme, t("panel.memory"), "")

        inner_x, inner_w = x + 2, w - 4
        row, bottom = y + 1, y + h - 1
        mem = snap.mem

        if row < bottom and mem.total:
            frac = mem.used / mem.total
            draw_gauge(win, row, inner_x, inner_w, theme, "RAM", frac * 100,
                       f"{format_bytes(mem.used)} / {format_bytes(mem.total)}", frac)
            row += 1
        if row < bottom and mem.swap_total:
            frac = mem.swap_used / mem.swap_total
            draw_gauge(win, row, inner_x, inner_w, theme, "Swap", frac * 100,
                       f"{format_bytes(mem.swap_used)} / {format_bytes(mem.swap_total)}", frac)
            row += 1
        if row < bottom and snap.gpu.gtt_total:
            frac = (snap.gpu.gtt_used or 0) / snap.gpu.gtt_total
            draw_gauge(win, row, inner_x, inner_w, theme, "GTT", frac * 100,
                       f"{format_bytes(snap.gpu.gtt_used)} / {format_bytes(snap.gpu.gtt_total)}",
                       frac)
            row += 1
        # the split alone says nothing about how much GPU memory is really in use
        used, total = snap.gpu_memory_total
        if row < bottom and total:
            frac = used / total
            draw_gauge(win, row, inner_x, inner_w, theme, t("gauge.gpu.memory"),
                       frac * 100,
                       f"{format_bytes(used)} / {format_bytes(total)}", frac)
            row += 1

        warn, crit = coll.THRESHOLDS["nvme"]
        for name, temp in snap.nvme_temps:
            if row >= bottom:
                break
            draw_gauge(win, row, inner_x, inner_w, theme, f"NVMe {name}", temp,
                       format_value(temp, "°C"), _frac(temp, 25, 85),
                       theme.level(temp, warn, crit))
            row += 1

        self._draw_graph_stack(win, inner_x, row, bottom, inner_w, [
            (t("graph.ram"), "mem.used", 0.0, 100.0),
            (t("graph.fan"), "fan.rpm", 0.0, None),
        ])

    def _panel_notes(self, win, y: int, x: int, h: int, w: int) -> None:
        snap, theme = self.snap, self.theme
        assert snap is not None
        draw_box(win, y, x, h, w, theme, t("panel.notes"), f"{len(snap.notes)}")
        for idx, note in enumerate(snap.notes[:h - 2]):
            addstr(win, y + 1 + idx, x + 2, truncate(f"• {note}", w - 4), theme.warn)

    # -- sensors view (HWiNFO) -----------------------------------------

    # Numeric columns of the sensor table: (header key, attribute to read)
    SENSOR_FIELDS_FULL = (
        ("table.now", "value"), ("table.min", "minimum"),
        ("table.max", "maximum"), ("table.avg", "average"),
    )
    SENSOR_FIELDS_SHORT = (("table.now", "value"), ("table.max", "maximum"))

    def _render_sensor_rows(self, win, y: int, x: int, width: int, height: int,
                            rows: list[tuple], fields=SENSOR_FIELDS_FULL,
                            header: bool = False) -> None:
        """Draws sensor table rows inside the given rectangle."""
        theme = self.theme
        col_w = 12
        numeric_w = col_w * len(fields)
        name_w = max(10, width - numeric_w)

        line = y
        if header:
            text = (f"{t('table.sensor'):<{name_w}}"
                    + "".join(f"{t(h):>{col_w}}" for h, _ in fields))
            addstr(win, line, x, truncate(text, width), theme.header)
            line += 1

        for entry in rows:
            if line >= y + height:
                break
            if entry[0] == "group":
                addstr(win, line, x, truncate(entry[1], width), theme.title)
                line += 1
                continue

            _, stat, attr = entry
            addstr(win, line, x, truncate(f"  {stat.label}", name_w).ljust(name_w),
                   theme.text, x + width)
            for idx, (_, field) in enumerate(fields):
                text = format_value(getattr(stat, field), stat.unit)
                addstr(win, line, x + name_w + idx * col_w, text.rjust(col_w),
                       attr if field == "value" else theme.dim, x + width)
            line += 1

    def _draw_sensors(self, win, top: int, height: int, width: int) -> None:
        rows = self._sensor_rows()
        inner_w = width - 2
        view_h = height - 2
        columns = self._strip_columns(len(rows), inner_w, view_h)

        if columns > 1:
            # on a wide window the whole table fits in columns — no scrolling
            self.scroll = 0
            per_column = max(1, -(-len(rows) // columns))
            col_w = inner_w // columns
            fields = (self.SENSOR_FIELDS_FULL if col_w - 2 >= 74
                      else self.SENSOR_FIELDS_SHORT)
            for index in range(columns):
                chunk = rows[index * per_column:(index + 1) * per_column]
                if not chunk:
                    break
                self._render_sensor_rows(win, top, 1 + index * col_w, col_w - 2,
                                         height - 1, chunk, fields=fields, header=True)
            return

        table_w = min(inner_w, 96)
        max_scroll = max(0, len(rows) - view_h)
        self.scroll = min(self.scroll, max_scroll)

        self._render_sensor_rows(win, top, 1, table_w, height - 1,
                                 rows[self.scroll:self.scroll + view_h], header=True)

        if max_scroll:
            pos = f"{self.scroll + 1}-{min(len(rows), self.scroll + view_h)}/{len(rows)}"
            addstr(win, top + height - 1, max(0, width - len(pos) - 1), pos, self.theme.faint)

    @staticmethod
    def _strip_columns(row_count: int, inner_w: int, inner_h: int) -> int:
        """The fewest columns in which the rows fit, without spreading them across
        half the screen — a column wider than ~110 characters is mostly void."""
        fitting = max(1, -(-row_count // max(1, inner_h)))
        widest = max(1, inner_w // 85)
        return max(1, min(inner_w // 46 or 1, max(fitting, widest)))

    def _panel_sensor_strip(self, win, y: int, x: int, h: int, w: int) -> None:
        """The condensed sensor table — fills the space left at the bottom."""
        rows = self._sensor_rows()
        draw_box(win, y, x, h, w, self.theme, t("panel.sensors"),
                 t("panel.sensors.hint"))

        inner_x, inner_w = x + 2, w - 4
        inner_y, inner_h = y + 1, h - 2
        if inner_h < 1 or inner_w < 30:
            return

        columns = self._strip_columns(len(rows), inner_w, inner_h)
        per_column = max(1, -(-len(rows) // columns))
        col_w = inner_w // columns
        # a wide column fits the full statistics rather than the short form
        fields = (self.SENSOR_FIELDS_FULL if col_w - 2 >= 74
                  else self.SENSOR_FIELDS_SHORT)

        for index in range(columns):
            chunk = rows[index * per_column:(index + 1) * per_column]
            if not chunk:
                break
            self._render_sensor_rows(win, inner_y, inner_x + index * col_w, col_w - 2,
                                     inner_h, chunk, fields=fields)

    def _sensor_rows(self) -> list[tuple]:
        theme = self.theme
        by_chip: dict[str, list] = {}
        for stat in self.collector.stats.values():
            by_chip.setdefault(stat.chip, []).append(stat)

        def chip_key(name: str) -> tuple[int, str]:
            for idx, prefix in enumerate(CHIP_ORDER):
                if name.startswith(prefix):
                    return (idx, name)
            return (len(CHIP_ORDER), name)

        kind_order = {k: i for i, k in enumerate(
            ("temp", "usage", "freq", "power", "fan", "pwm", "volt", "curr"))}

        rows: list[tuple] = []
        for chip_name in sorted(by_chip, key=chip_key):
            stats = sorted(by_chip[chip_name],
                           key=lambda s: (kind_order.get(s.kind, 99), s.label))
            rows.append(("group", f"▌ {chip_name}"))
            for stat in stats:
                if stat.kind == "temp":
                    warn, crit = coll.THRESHOLDS[coll.temp_kind(chip_name, stat.label)]
                    attr = theme.level(stat.value, warn, crit)
                elif stat.kind == "usage":
                    attr = theme.ramp((stat.value or 0) / 100.0)
                else:
                    attr = theme.bright
                rows.append(("stat", stat, attr))
        return rows

    # -- help -------------------------------------------------------------

    def _draw_help(self, win, height: int, width: int) -> None:
        theme = self.theme
        box_w = min(64, width - 4)
        box_h = min(len(HELP_KEYS) + 4, height - 4)
        y = (height - box_h) // 2
        x = (width - box_w) // 2
        for row in range(box_h):
            addstr(win, y + row, x, " " * box_w, theme.text)
        draw_box(win, y, x, box_h, box_w, theme, t("help.title"), t("help.close"))
        for idx, (key, desc_key) in enumerate(HELP_KEYS):
            line = y + 1 + idx
            if line >= y + box_h - 1:
                break
            addstr(win, line, x + 2, key.ljust(24), theme.accent, x + box_w - 2)
            addstr(win, line, x + 26, truncate(t(desc_key), box_w - 28), theme.text, x + box_w - 2)


def _channel_index(channel) -> str:
    """Trailing digits of an hwmon channel key, so fanN can be paired with pwmN."""
    match = re.search(r"(\d+)$", channel.key)
    return match.group(1) if match else ""


def _core_columns(inner_w: int) -> int:
    """How many threads side by side — a cell below 24 characters stops being readable."""
    return max(1, min(6, inner_w // 24))


def _core_summary(cpu) -> str:
    """For example "8C/16T" — the logical CPU count alone misleads after an unlock."""
    threads = cpu.cores_online or len(cpu.usage)
    if cpu.cores_physical and threads:
        summary = f"{cpu.cores_physical}C/{threads}T"
    elif threads:
        summary = t("cpu.threads.plain", count=threads)
    else:
        summary = "?"
    if cpu.cores_present and cpu.cores_online and cpu.cores_present != cpu.cores_online:
        summary += " · " + t("cpu.cores.present", count=cpu.cores_present)
    return summary


def _frac(value: float | None, lo: float, hi: float) -> float:
    if value is None or hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def _f(value: int | None) -> float | None:
    return None if value is None else float(value)


def run(collector: Collector, interval: float, show_notes: bool = True) -> None:
    curses.wrapper(Monitor(collector, interval, show_notes).run)

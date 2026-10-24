"""Drawing primitives for curses: palette, boxes, bars, RSSI graphs.

Adapted from bc250-mon (each tool in the kit is self-contained). Graphs here
are line graphs with gaps: a bin without an RSSI reading is drawn empty, and a
bin in which the device was disconnected gets a red marker on the baseline.
"""

from __future__ import annotations

import curses
import locale
import math
import os
import unicodedata

RAMP_256 = [39, 45, 51, 86, 84, 82, 118, 154, 190, 226, 220, 214, 208, 202, 196]
RAMP_8 = [curses.COLOR_BLUE, curses.COLOR_CYAN, curses.COLOR_GREEN,
          curses.COLOR_YELLOW, curses.COLOR_RED]

BLOCKS = " ▏▎▍▌▋▊▉█"
SPARK = "▁▂▃▄▅▆▇█"

# Braille bit map: BRAILLE[column][row], row 0 = top
BRAILLE = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))

# states of a graph bin that mean "the link was down", not "no reading"
DOWN_STATES = ("disconnected", "absent", "no-adapter", "link-idle", "usb-gone")


class Glyphs:
    def __init__(self, unicode: bool) -> None:
        self.unicode = unicode
        if unicode:
            self.tl, self.tr, self.bl, self.br, self.h, self.v = "╭", "╮", "╰", "╯", "─", "│"
            self.tee_l, self.tee_r = "├", "┤"
            self.dot, self.check, self.bullet, self.gap = "●", "✓", "•", "·"
            self.ellipsis, self.arrow = "…", "→"
            self.axis = "┤"
        else:
            self.tl = self.tr = self.bl = self.br = "+"
            self.h, self.v = "-", "|"
            self.tee_l = self.tee_r = "+"
            self.dot, self.check, self.bullet, self.gap = "*", "x", "*", "."
            self.ellipsis, self.arrow = "~", "->"
            self.axis = "|"


def unicode_ok() -> bool:
    encoding = (locale.getpreferredencoding(False) or "").lower().replace("-", "")
    return encoding in ("utf8", "utf_8")


class Theme:
    """Semantic names for curses colour pairs; degrades to 8 colours or none."""

    def __init__(self, mode: str = "auto") -> None:
        self._pairs: dict[tuple[int, int], int] = {}
        self._next = 1
        self.colors = 0
        if mode != "none" and not os.environ.get("NO_COLOR"):
            try:
                curses.start_color()
                curses.use_default_colors()
                self.colors = curses.COLORS
            except curses.error:
                self.colors = 0
        if mode == "8" and self.colors > 8:
            self.colors = 8

        rich = self.colors >= 256
        self.rich = rich
        self.text = self._attr(252 if rich else curses.COLOR_WHITE)
        self.bright = self._attr(255 if rich else curses.COLOR_WHITE) | curses.A_BOLD
        self.dim = self._attr(245 if rich else curses.COLOR_WHITE)
        self.faint = self._attr(240 if rich else curses.COLOR_WHITE) | curses.A_DIM
        self.border = self._attr(238 if rich else curses.COLOR_BLUE)
        self.border_focus = self._attr(75 if rich else curses.COLOR_CYAN) | curses.A_BOLD
        self.accent = self._attr(208 if rich else curses.COLOR_YELLOW) | curses.A_BOLD
        self.title = self._attr(81 if rich else curses.COLOR_CYAN) | curses.A_BOLD
        self.ok = self._attr(82 if rich else curses.COLOR_GREEN)
        self.warn = self._attr(214 if rich else curses.COLOR_YELLOW) | curses.A_BOLD
        self.crit = self._attr(196 if rich else curses.COLOR_RED) | curses.A_BOLD
        self.label = self._attr(110 if rich else curses.COLOR_CYAN)
        self.header = self._attr(255 if rich else curses.COLOR_WHITE) | curses.A_REVERSE
        self.select = curses.A_REVERSE
        self.rssi = {
            "good": self._attr(82 if rich else curses.COLOR_GREEN),
            "fair": self._attr(190 if rich else curses.COLOR_GREEN),
            "weak": self._attr(214 if rich else curses.COLOR_YELLOW),
            "poor": self._attr(196 if rich else curses.COLOR_RED),
        }
        if not self.colors:
            # monochrome: keep warnings readable through attributes alone
            self.warn = curses.A_BOLD
            self.crit = curses.A_BOLD | curses.A_UNDERLINE
            self.border_focus = curses.A_BOLD
            self.title = curses.A_BOLD

    def _attr(self, fg: int, bg: int = -1) -> int:
        if not self.colors:
            return curses.A_NORMAL
        key = (fg, bg)
        if key not in self._pairs:
            if self._next >= curses.COLOR_PAIRS:
                return curses.A_NORMAL
            try:
                curses.init_pair(self._next, fg, bg)
            except curses.error:
                return curses.A_NORMAL
            self._pairs[key] = self._next
            self._next += 1
        return curses.color_pair(self._pairs[key])

    def ramp(self, frac: float) -> int:
        frac = max(0.0, min(1.0, frac))
        table = RAMP_256 if self.colors >= 256 else RAMP_8
        return self._attr(table[min(len(table) - 1, int(frac * len(table)))])

    def level_attr(self, level: str | None) -> int:
        return self.rssi.get(level, self.dim) if level else self.dim


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------

# --ascii or a non-UTF-8 locale: every string goes through this table, so the
# translations and labels need no ASCII variants of their own
ASCII_ONLY = False
_ASCII_MAP = str.maketrans({
    "·": "-", "—": "-", "–": "-", "≈": "~", "→": "->", "…": "~", "σ": "sd", "×": "x",
    "▁": "_", "░": ".", "●": "*", "○": "o", "✓": "x", "✖": "X", "▲": "!", "│": "|",
    "┤": "|", "ł": "l", "Ł": "L", "„": '"', "”": '"', "“": '"',
})


def to_ascii(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.translate(_ASCII_MAP))
    return text.encode("ascii", "ignore").decode("ascii")


def addstr(win, y: int, x: int, text: str, attr: int = 0, max_x: int | None = None) -> None:
    """An addstr that never raises when clipped at the window edge."""
    if ASCII_ONLY:
        text = to_ascii(text)
    height, width = win.getmaxyx()
    if y < 0 or y >= height or x >= width:
        return
    limit = width if max_x is None else min(width, max_x)
    if x < 0:
        text = text[-x:]
        x = 0
    space = limit - x
    if space <= 0:
        return
    text = text[:space]
    if not text:
        return
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


def truncate(text: str, width: int, ellipsis: str = "…") -> str:
    if width <= 0:
        return ""
    return text if len(text) <= width else text[:max(0, width - 1)] + ellipsis


def draw_box(win, y: int, x: int, h: int, w: int, theme: Theme, glyphs: Glyphs,
             title: str = "", subtitle: str = "", focus: bool = False,
             title_attr: int | None = None, subtitle_attr: int | None = None) -> None:
    if h < 2 or w < 4:
        return
    border = theme.border_focus if focus else theme.border
    addstr(win, y, x, glyphs.tl + glyphs.h * (w - 2) + glyphs.tr, border)
    addstr(win, y + h - 1, x, glyphs.bl + glyphs.h * (w - 2) + glyphs.br, border)
    for row in range(1, h - 1):
        addstr(win, y + row, x, glyphs.v, border)
        addstr(win, y + row, x + w - 1, glyphs.v, border)
    used = 0
    if title:
        text = f" {truncate(title, max(1, w - 6), glyphs.ellipsis)} "
        addstr(win, y, x + 2, text, title_attr if title_attr is not None else theme.title)
        used = len(text)
    if subtitle:
        text = f" {subtitle} "
        start = x + w - 2 - len(text)
        if start > x + 2 + used:
            addstr(win, y, start, text, subtitle_attr if subtitle_attr is not None else theme.dim)


def block_bar(fraction: float, width: int, unicode: bool = True) -> str:
    if width <= 0:
        return ""
    fraction = max(0.0, min(1.0, fraction))
    if not unicode:
        full = int(round(fraction * width))
        return ("#" * full).ljust(width)
    eighths = int(round(fraction * width * 8))
    full, rest = divmod(eighths, 8)
    full = min(full, width)
    bar = "█" * full
    if full < width and rest:
        bar += BLOCKS[rest]
    return bar.ljust(width)


# --------------------------------------------------------------------------
# RSSI scale and graphs
# --------------------------------------------------------------------------

RSSI_BAR_LO = -100
RSSI_BAR_HI = -30


def rssi_fraction(rssi: float | None) -> float:
    if rssi is None:
        return 0.0
    return max(0.0, min(1.0, (rssi - RSSI_BAR_LO) / (RSSI_BAR_HI - RSSI_BAR_LO)))


def graph_scale(values: list[float], min_span: int = 15) -> tuple[int, int]:
    """A dynamic dBm range for the graph: the data plus a margin, in steps of 5."""
    real = [v for v in values if v is not None]
    if not real:
        return -90, -40
    lo = math.floor((min(real) - 2) / 5.0) * 5
    hi = math.ceil((max(real) + 2) / 5.0) * 5
    grow_low = True
    while hi - lo < min_span:
        if grow_low:
            lo -= 5
        else:
            hi += 5
        grow_low = not grow_low
    return max(-130, lo), min(20, hi)


def graph_cells(bins: list[tuple[int | None, str | None]], width: int, height: int,
                lo: float, hi: float, braille: bool = True) -> list[list[tuple[str, float | None]]]:
    """Cells of a line graph: [row][col] = (char, dBm at that row or None).

    With Braille each character holds two bins and four dot rows; consecutive
    readings are joined by a vertical run of dots so the trace reads as a line.
    A bin without a reading breaks the line. The bottom row carries a marker
    under bins in which the link was down.
    """
    cells = [[(" ", None) for _ in range(width)] for _ in range(height)]
    if width <= 0 or height <= 0:
        return cells
    per_col = 2 if braille else 1
    sub_rows = 4 if braille else 1
    dots_h = height * sub_rows
    data = bins[-width * per_col:]
    pad = width * per_col - len(data)
    data = [(None, None)] * pad + list(data)
    span = (hi - lo) or 1.0

    def dot_row(value: float) -> int:
        frac = (hi - value) / span
        return max(0, min(dots_h - 1, int(round(frac * (dots_h - 1)))))

    grid = [[False] * (width * per_col) for _ in range(dots_h)]
    prev: int | None = None
    for i, (value, _state) in enumerate(data):
        if value is None:
            prev = None
            continue
        row = dot_row(value)
        if prev is None or prev == row:
            grid[row][i] = True
        else:
            step = 1 if row > prev else -1
            for r in range(prev + step, row + step, step):
                grid[r][i] = True
        prev = row

    for cy in range(height):
        row_value = hi - span * ((cy + 0.5) / height)
        for cx in range(width):
            if braille:
                bits = 0
                for dx in range(2):
                    for dy in range(4):
                        if grid[cy * 4 + dy][cx * 2 + dx]:
                            bits |= BRAILLE[dx][dy]
                if bits:
                    cells[cy][cx] = (chr(0x2800 + bits), row_value)
            else:
                if grid[cy][cx]:
                    cells[cy][cx] = ("*", row_value)
    # down markers on the baseline
    for cx in range(width):
        group = data[cx * per_col:(cx + 1) * per_col]
        if all(v is None for v, _ in group) and any(s in DOWN_STATES for _, s in group):
            # NaN marks "link down" for the renderer
            cells[height - 1][cx] = ("▁" if braille else "_", float("nan"))
    return cells


def sparkline(bins: list[tuple[int | None, str | None]], width: int, lo: float, hi: float,
              unicode: bool = True) -> list[tuple[str, float | None]]:
    """One row of block characters; gaps stay blank, down bins get a dot."""
    data = bins[-width:]
    data = [(None, None)] * (width - len(data)) + list(data)
    chars = SPARK if unicode else "_.-=+*#@"
    span = (hi - lo) or 1.0
    out = []
    for value, state in data:
        if value is None:
            out.append(("·" if unicode else ".", float("nan")) if state in DOWN_STATES else (" ", None))
            continue
        frac = max(0.0, min(1.0, (value - lo) / span))
        out.append((chars[min(len(chars) - 1, int(frac * len(chars)))], float(value)))
    return out


def rssi_level(value: float | None, good: int, fair: int, weak: int) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if value >= good:
        return "good"
    if value >= fair:
        return "fair"
    if value >= weak:
        return "weak"
    return "poor"


def activity_cells(bins: list[tuple[float | None, str | None]], width: int, height: int,
                   hi: float, braille: bool = True) -> list[list[tuple[str, float | None]]]:
    """A filled bar graph of input events per second (0 at the bottom).

    Unmeasured bins stay empty; bins in which the link was down get the same
    red baseline marker as the RSSI graph.
    """
    cells = [[(" ", None) for _ in range(width)] for _ in range(height)]
    if width <= 0 or height <= 0:
        return cells
    per_col = 2 if braille else 1
    sub_rows = 4 if braille else 1
    dots_h = height * sub_rows
    data = bins[-width * per_col:]
    data = [(None, None)] * (width * per_col - len(data)) + list(data)
    hi = hi or 1.0
    grid = [[False] * (width * per_col) for _ in range(dots_h)]
    for i, (value, _state) in enumerate(data):
        if value is None or value <= 0:
            continue
        filled = max(1, int(round(min(1.0, value / hi) * dots_h)))
        for r in range(dots_h - filled, dots_h):
            grid[r][i] = True
    for cy in range(height):
        for cx in range(width):
            if braille:
                bits = 0
                for dx in range(2):
                    for dy in range(4):
                        if grid[cy * 4 + dy][cx * 2 + dx]:
                            bits |= BRAILLE[dx][dy]
                if bits:
                    cells[cy][cx] = (chr(0x2800 + bits), 1.0)
            elif grid[cy][cx]:
                cells[cy][cx] = ("#", 1.0)
    for cx in range(width):
        group = data[cx * per_col:(cx + 1) * per_col]
        if all(v is None for v, _ in group) and any(s in DOWN_STATES + ("link-idle", "usb-gone")
                                                      for _, s in group):
            cells[height - 1][cx] = ("▁" if braille else "_", float("nan"))
    return cells

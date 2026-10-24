"""Drawing primitives for curses: boxes, bars, Braille graphs, palette."""

from __future__ import annotations

import curses

# Cold -> hot gradient (256-colour palette)
RAMP_256 = [39, 45, 51, 86, 84, 82, 118, 154, 190, 226, 220, 214, 208, 202, 196]
RAMP_8 = [curses.COLOR_BLUE, curses.COLOR_CYAN, curses.COLOR_GREEN,
          curses.COLOR_YELLOW, curses.COLOR_RED]

# Block characters for progress bars (1/8 .. 8/8)
BLOCKS = " ▏▎▍▌▋▊▉█"

# Maximum gauge track length, in characters
GAUGE_MAX_BAR = 56

# Width of the label column in draw_gauge; a label needs to fit in one less
GAUGE_LABEL_WIDTH = 12

# Braille bit map: BRAILLE[column][row], row 0 = top
BRAILLE = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))

BOX = {
    "tl": "╭", "tr": "╮", "bl": "╰", "br": "╯",
    "h": "─", "v": "│",
}


class Theme:
    """Gives semantic names to curses colour pairs."""

    def __init__(self) -> None:
        self._pairs: dict[tuple[int, int], int] = {}
        self._next = 1
        self.colors = 0
        try:
            curses.start_color()
            curses.use_default_colors()
            self.colors = curses.COLORS
        except curses.error:
            self.colors = 0

        rich = self.colors >= 256
        self.text = self._attr(252 if rich else curses.COLOR_WHITE)
        self.bright = self._attr(255 if rich else curses.COLOR_WHITE) | curses.A_BOLD
        self.dim = self._attr(245 if rich else curses.COLOR_WHITE)
        self.faint = self._attr(240 if rich else curses.COLOR_WHITE) | curses.A_DIM
        self.border = self._attr(238 if rich else curses.COLOR_BLUE)
        self.accent = self._attr(208 if rich else curses.COLOR_YELLOW) | curses.A_BOLD
        self.title = self._attr(81 if rich else curses.COLOR_CYAN) | curses.A_BOLD
        self.ok = self._attr(82 if rich else curses.COLOR_GREEN)
        self.warn = self._attr(214 if rich else curses.COLOR_YELLOW) | curses.A_BOLD
        self.crit = self._attr(196 if rich else curses.COLOR_RED) | curses.A_BOLD
        self.label = self._attr(110 if rich else curses.COLOR_CYAN)
        self.header = self._attr(255 if rich else curses.COLOR_WHITE) | curses.A_REVERSE

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
        """A gradient colour for a fraction of 0..1."""
        frac = max(0.0, min(1.0, frac))
        table = RAMP_256 if self.colors >= 256 else RAMP_8
        return self._attr(table[min(len(table) - 1, int(frac * len(table)))])

    def level(self, value: float | None, warn: float, crit: float) -> int:
        """A threshold colour: green / yellow / red."""
        if value is None:
            return self.dim
        if value >= crit:
            return self.crit
        if value >= warn:
            return self.warn
        return self.ok


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------

def addstr(win, y: int, x: int, text: str, attr: int = 0, max_x: int | None = None) -> None:
    """An addstr that never raises when clipped at the window edge."""
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
        # the terminal's last cell always raises — that is normal
        pass


def draw_box(win, y: int, x: int, h: int, w: int, theme: Theme,
             title: str = "", subtitle: str = "") -> None:
    if h < 2 or w < 4:
        return
    top = BOX["tl"] + BOX["h"] * (w - 2) + BOX["tr"]
    bottom = BOX["bl"] + BOX["h"] * (w - 2) + BOX["br"]
    addstr(win, y, x, top, theme.border)
    addstr(win, y + h - 1, x, bottom, theme.border)
    for row in range(1, h - 1):
        addstr(win, y + row, x, BOX["v"], theme.border)
        addstr(win, y + row, x + w - 1, BOX["v"], theme.border)

    if title:
        text = f" {title} "
        if len(text) < w - 4:
            addstr(win, y, x + 2, text, theme.title)
    if subtitle:
        text = f" {subtitle} "
        start = x + w - 2 - len(text)
        if start > x + 2 + len(title) + 3:
            addstr(win, y, start, text, theme.dim)


def block_bar(fraction: float, width: int) -> str:
    """A block bar with one-eighth-character resolution."""
    if width <= 0:
        return ""
    fraction = max(0.0, min(1.0, fraction))
    eighths = int(round(fraction * width * 8))
    full, rest = divmod(eighths, 8)
    full = min(full, width)
    bar = "█" * full
    if full < width and rest:
        bar += BLOCKS[rest]
    return bar.ljust(width)


def draw_gauge(win, y: int, x: int, width: int, theme: Theme, label: str,
               value: float | None, text: str, fraction: float,
               attr: int | None = None) -> None:
    """One row: label, bar, value."""
    label_w = GAUGE_LABEL_WIDTH
    value_w = max(9, len(text))
    # on a very wide panel a bar stretched across 200 characters adds nothing,
    # and the value runs off to the far side of the screen
    bar_w = min(width - label_w - value_w - 2, GAUGE_MAX_BAR)
    if bar_w < 4:
        # too tight for a bar — the value alone
        addstr(win, y, x, f"{label[:width - len(text) - 1]:<{max(0, width - len(text) - 1)}}{text}",
               attr or theme.text, x + width)
        return

    addstr(win, y, x, label[:label_w - 1].ljust(label_w), theme.label, x + width)
    bar = block_bar(fraction, bar_w)
    filled = len(bar.rstrip())
    color = attr if attr is not None else theme.ramp(fraction)
    addstr(win, y, x + label_w, bar[:filled], color, x + width)
    addstr(win, y, x + label_w + filled, bar[filled:], theme.faint, x + width)
    addstr(win, y, x + label_w + bar_w + 1, text.rjust(value_w),
           attr if attr is not None else theme.bright, x + width)


def braille_rows(values: list[float], width: int, height: int,
                 vmin: float | None = None, vmax: float | None = None) -> list[str]:
    """A filled Braille graph — `height` lines of `width` characters."""
    rows_out = [" " * width for _ in range(height)]
    if width <= 0 or height <= 0 or not values:
        return rows_out

    dot_cols = width * 2
    dot_rows = height * 4
    sample = values[-dot_cols:]

    lo = min(sample) if vmin is None else vmin
    hi = max(sample) if vmax is None else vmax
    if hi - lo < 1e-9:
        hi = lo + 1.0

    grid = [[0] * dot_cols for _ in range(dot_rows)]
    offset = dot_cols - len(sample)   # fresh samples pushed to the right
    for i, value in enumerate(sample):
        frac = (value - lo) / (hi - lo)
        frac = max(0.0, min(1.0, frac))
        top = dot_rows - 1 - int(round(frac * (dot_rows - 1)))
        for row in range(top, dot_rows):
            grid[row][offset + i] = 1

    out = []
    for cell_row in range(height):
        line = []
        for cell_col in range(width):
            bits = 0
            for dx in range(2):
                for dy in range(4):
                    if grid[cell_row * 4 + dy][cell_col * 2 + dx]:
                        bits |= BRAILLE[dx][dy]
            line.append(chr(0x2800 + bits))
        out.append("".join(line))
    return out


def draw_graph(win, y: int, x: int, width: int, height: int, theme: Theme,
               values: list[float], vmin: float | None = None,
               vmax: float | None = None) -> None:
    """A vertically coloured graph — upper rows warmer, as in btop."""
    rows = braille_rows(values, width, height, vmin, vmax)
    for i, row in enumerate(rows):
        frac = 1.0 - (i / max(1, height - 1)) if height > 1 else 0.6
        addstr(win, y + i, x, row, theme.ramp(0.15 + 0.85 * frac), x + width)


def truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""
    return text if len(text) <= width else text[:max(0, width - 1)] + "…"

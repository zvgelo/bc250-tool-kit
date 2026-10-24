#!/usr/bin/env python3
"""Generates the README header of bc250-nosuspend (English and Polish).

    python3 docs/img/source/make_img.py      # writes docs/img/header*.svg

The look is shared by the whole kit: black terminal panels with the title set
into the border, cyan headings, green for what works. The panel shows the real status output after start.
"""
import os
from xml.sax.saxutils import escape

OUT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
NAME = "bc250-nosuspend"

BG = "#0b0e12"
PANEL = "#11161c"
BORDER = "#2a3f6e"
FG = "#e6e9ee"
MUTED = "#8a94a3"
CYAN = "#46c6d8"
GREEN = "#5cc96a"
AMBER = "#e0a040"
RED = "#e05a5a"

MONO = "ui-monospace, SFMono-Regular, 'DejaVu Sans Mono', Menlo, Consolas, monospace"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', 'DejaVu Sans', sans-serif"

# real output of `bc250-nosuspend start` + `status`, run against the stubbed
# systemctl and busctl of tests/test-bc250-nosuspend.sh
TEXT = {
    "en": {
        "h_title": "bc250-nosuspend: no suspend, no hibernation",
        "h_desc": "bc250-nosuspend blocks suspend and hibernation at the systemd level; next to "
                  "it, its status output after bc250-nosuspend start.",
        "h_line1": "Suspend and hibernation",
        "h_line2": "blocked at the systemd level",
        "h_line3": "KDE · Gaming Mode · headless",
        "h_meta": "Bash + systemd · undo: bc250-nosuspend stop",
        "h_panel": "after bc250-nosuspend start",
        "h_lines": [
            ("$ bc250-nosuspend status", FG),
            ("State: SUSPEND BLOCKED", GREEN),
            ("", FG),
            ("systemd targets", CYAN),
            ("  sleep.target            masked", FG),
            ("  suspend.target          masked", FG),
            ("  hibernate.target        masked", FG),
            ("  hybrid-sleep.target     masked", FG),
            ("logind", CYAN),
            ("  IdleAction              ignore", FG),
        ],
    },
    "pl": {
        "h_title": "bc250-nosuspend: bez usypiania i hibernacji",
        "h_desc": "bc250-nosuspend blokuje usypianie i hibernację na poziomie systemd; obok wynik "
                  "status po bc250-nosuspend start.",
        "h_line1": "Usypianie i hibernacja",
        "h_line2": "zablokowane w systemd",
        "h_line3": "KDE · Gaming Mode · bez ekranu",
        "h_meta": "Bash + systemd · cofnięcie: bc250-nosuspend stop",
        "h_panel": "po bc250-nosuspend start",
        "h_lines": [
            ("$ bc250-nosuspend status", FG),
            ("Stan: SUSPEND ZABLOKOWANY", GREEN),
            ("", FG),
            ("Cele systemd", CYAN),
            ("  sleep.target            zamaskowany", FG),
            ("  suspend.target          zamaskowany", FG),
            ("  hibernate.target        zamaskowany", FG),
            ("  hybrid-sleep.target     zamaskowany", FG),
            ("logind", CYAN),
            ("  IdleAction              ignore", FG),
        ],
    },
}


def text(x, y, s, size, fill, family=MONO, weight="normal", anchor="start"):
    return (f'<text x="{x}" y="{y}" xml:space="preserve" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{escape(s)}</text>')


def panel(x, y, w, h, title, title_color=CYAN, border=BORDER, title_size=20):
    """A bc250-mon style panel: a thin frame with its title set into the top edge."""
    tw = len(title) * title_size * 0.62 + 20
    return "\n".join([
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{PANEL}" '
        f'stroke="{border}" stroke-width="2"/>',
        f'<rect x="{x + 18}" y="{y - 14}" width="{tw:.0f}" height="28" fill="{BG}"/>',
        text(x + 28, y + 7, title, title_size, title_color, weight="bold"),
    ])


def svg(w, h, title, desc, body):
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img" aria-labelledby="title desc">',
        f'<title id="title">{escape(title)}</title>',
        f'<desc id="desc">{escape(desc)}</desc>',
        f'<rect width="{w}" height="{h}" rx="18" fill="{BG}"/>',
        body,
        "</svg>",
        "",
    ])


def header(t):
    parts = [
        text(56, 76, "bc250-tool-kit", 20, MUTED),
        text(56, 136, NAME, 56, FG, weight="bold"),
        text(56, 190, t["h_line1"], 26, FG, family=SANS),
        text(56, 226, t["h_line2"], 26, FG, family=SANS),
        text(56, 262, t["h_line3"], 26, CYAN, family=SANS),
        text(56, 312, t["h_meta"], 18, MUTED),
        panel(616, 48, 536, 300, t["h_panel"]),
    ]
    y = 92
    for line, color in t["h_lines"]:
        if line:
            parts.append(text(638, y, line, 19, color))
        y += 26
    return svg(1200, 380, t["h_title"], t["h_desc"], "\n".join(parts))


def main():
    for lang, t in TEXT.items():
        suffix = "" if lang == "en" else ".pl"
        path = os.path.join(OUT, f"header{suffix}.svg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(header(t))
        print(path)


if __name__ == "__main__":
    main()

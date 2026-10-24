#!/usr/bin/env python3
"""Generates the README header of bc250-mon (English and Polish).

    python3 docs/img/source/make_img.py      # writes docs/img/header*.svg

The look is shared by the whole kit: black terminal panels with the title set
into the border, cyan headings, green for what works. The panel shows readings and Diagnostics notes from a real BC-250.
"""
import os
from xml.sax.saxutils import escape

OUT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
NAME = "bc250-mon"

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

# the readings and both notes are copied from the dashboard screenshot
# (docs/img/dashboard.jpg) taken on the reference BC-250
TEXT = {
    "en": {
        "h_title": "bc250-mon: sensor and hardware monitor for the AMD BC-250",
        "h_desc": "bc250-mon, a terminal sensor and hardware monitor for the AMD BC-250; next to "
                  "it, readings and two Diagnostics notes from a real BC-250 under load.",
        "h_line1": "Sensor and hardware monitor",
        "h_line2": "for the AMD BC-250",
        "h_line3": "btop graphs · HWiNFO table",
        "h_meta": "reads sysfs, hwmon, procfs · writes nothing",
        "h_panel": "BC-250 · under load",
        "h_lines": [
            ("GPU   67.0 °C   106.89 W   926 MHz", FG),
            ("CPU   72.5 °C   8C/16T    3478 MHz", FG),
            ("Pump Fan 2531 RPM · governor-smu", FG),
            ("", FG),
            ("Diagnostics", CYAN),
            ("• pp_dpm_sclk: 98 MHz in a 1000–2000", AMBER),
            ("  range — the 8-core unlock; the clock", MUTED),
            ("  is taken from gpu_metrics instead", MUTED),
            ("• VRAM split 512 MiB — a full bar is", AMBER),
            ("  normal, the rest comes from GTT", MUTED),
        ],
    },
    "pl": {
        "h_title": "bc250-mon: monitor czujników i sprzętu dla AMD BC-250",
        "h_desc": "bc250-mon, terminalowy monitor czujników i sprzętu dla AMD BC-250; obok odczyty "
                  "i dwie uwagi Diagnostyki z prawdziwej BC-250 pod obciążeniem.",
        "h_line1": "Monitor czujników i sprzętu",
        "h_line2": "dla AMD BC-250",
        "h_line3": "wykresy jak btop · tabela jak HWiNFO",
        "h_meta": "czyta sysfs, hwmon, procfs · nic nie zapisuje",
        "h_panel": "BC-250 · pod obciążeniem",
        "h_lines": [
            ("GPU   67.0 °C   106.89 W   926 MHz", FG),
            ("CPU   72.5 °C   8C/16T    3478 MHz", FG),
            ("Pump Fan 2531 RPM · governor-smu", FG),
            ("", FG),
            ("Diagnostyka", CYAN),
            ("• pp_dpm_sclk: 98 MHz przy 1000–2000", AMBER),
            ("  — efekt odblokowania 8 rdzeni; zegar", MUTED),
            ("  GPU biorę z gpu_metrics", MUTED),
            ("• split VRAM 512 MiB — pełny pasek", AMBER),
            ("  jest normalny, reszta idzie przez GTT", MUTED),
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

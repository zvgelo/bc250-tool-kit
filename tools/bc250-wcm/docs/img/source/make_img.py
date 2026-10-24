#!/usr/bin/env python3
"""Generates the README header of bc250-wcm (English and Polish).

    python3 docs/img/source/make_img.py      # writes docs/img/header*.svg

The look is shared by the whole kit: black terminal panels with the title set
into the border, cyan headings, green for what works. The panel shows an excerpt of the demo mode.
"""
import os
from xml.sax.saxutils import escape

OUT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
NAME = "bc250-wcm"

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

# copied from `bc250-wcm --demo` (dev/tuishot.py): a pad panel and the events
# of the simulated adapter reset
TEXT = {
    "en": {
        "h_title": "bc250-wcm: Wireless Controller Monitor",
        "h_desc": "bc250-wcm monitors Bluetooth pads and USB 2.4 GHz receivers; next to it, a pad "
                  "panel and the events of an adapter reset from its demo mode.",
        "b_label": "tested with",
        "b_value": "8BitDo Ultimate 2C Wireless · 2.4 GHz receiver",
        "h_line1": "Wireless Controller Monitor",
        "h_line2": "Bluetooth pads, 2.4 GHz receivers",
        "h_line3": "RSSI where it exists · drops",
        "h_meta": "read-only · never writes to a receiver",
        "h_panel": "bc250-wcm --demo",
        "h_lines": [
            ("8BitDo Ultimate 2C  ● connected", GREEN),
            ("RSSI  -59 dBm  ≈strong*", FG),
            ("min -72  max -58  avg -63.5  σ 4.3", MUTED),
            ("drops 0  reconn 0  timeouts 0", MUTED),
            ("", FG),
            ("Device events", CYAN),
            ("hci0: command 0x2042 tx timeout", AMBER),
            ("hci0: Resetting usb device.", AMBER),
            ("Adapter changed index hci0 → hci1", AMBER),
            ("monitoring resumed automatically", GREEN),
        ],
    },
    "pl": {
        "h_title": "bc250-wcm: monitor kontrolerów bezprzewodowych",
        "h_desc": "bc250-wcm monitoruje pady Bluetooth i odbiorniki USB 2,4 GHz; obok panel pada "
                  "i zdarzenia resetu adaptera z trybu demo.",
        "b_label": "sprawdzone z",
        "b_value": "8BitDo Ultimate 2C Wireless · odbiornik 2,4 GHz",
        "h_line1": "Monitor kontrolerów",
        "h_line2": "pady Bluetooth, odbiorniki 2,4 GHz",
        "h_line3": "RSSI tam, gdzie istnieje · zerwania",
        "h_meta": "tylko odczyt · nic nie pisze do odbiornika",
        "h_panel": "bc250-wcm --demo",
        "h_lines": [
            ("8BitDo Ultimate 2C  ● połączony", GREEN),
            ("RSSI  -59 dBm  ≈silny*", FG),
            ("min -72  max -58  avg -63.5  σ 4.3", MUTED),
            ("rozł. 0  pon. 0  timeouty 0", MUTED),
            ("", FG),
            ("Zdarzenia urządzeń", CYAN),
            ("hci0: command 0x2042 tx timeout", AMBER),
            ("hci0: Resetting usb device.", AMBER),
            ("Adapter zmienił indeks hci0 → hci1", AMBER),
            ("monitorowanie wznowione automatycznie", GREEN),
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


def badge(t):
    """The hardware tag under the README title: a two-part pill."""
    label, value = t["b_label"], t["b_value"]
    lw, vw = len(label) * 9 + 24, round(len(value) * 9.3) + 30
    w, h = lw + vw, 28
    body = "\n".join([
        f'<rect x="{lw}" y="1" width="{vw - 1}" height="{h - 2}" rx="13" fill="{PANEL}" '
        f'stroke="{AMBER}" stroke-width="2"/>',
        f'<rect x="1" y="1" width="{lw + 12}" height="{h - 2}" rx="13" fill="{BORDER}"/>',
        text(12, 19, label, 15, FG),
        text(lw + 16, 19, value, 15, AMBER, weight="bold"),
    ])
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="{escape(label)}: {escape(value)}">',
        f'<title>{escape(label)}: {escape(value)}</title>',
        body,
        "</svg>",
        "",
    ])


def main():
    for lang, t in TEXT.items():
        suffix = "" if lang == "en" else ".pl"
        for name, build in (("header", header), ("hardware", badge)):
            path = os.path.join(OUT, f"{name}{suffix}.svg")
            with open(path, "w", encoding="utf-8") as f:
                f.write(build(t))
            print(path)


if __name__ == "__main__":
    main()

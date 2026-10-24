#!/usr/bin/env python3
"""Generates the images of the kit's main README (English and Polish).

    python3 assets/readme/source/make_img.py      # writes assets/readme/*.svg

Same look as the tools' own images (bc250-mon's palette): black terminal panels
with the title set into the border, cyan headings, green for what works.
"""
import os
from xml.sax.saxutils import escape

OUT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))

BG = "#0b0e12"
PANEL = "#11161c"
BORDER = "#2a3f6e"
FG = "#e6e9ee"
MUTED = "#8a94a3"
CYAN = "#46c6d8"
GREEN = "#5cc96a"
AMBER = "#e0a040"

MONO = "ui-monospace, SFMono-Regular, 'DejaVu Sans Mono', Menlo, Consolas, monospace"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', 'DejaVu Sans', sans-serif"

# the four tools: name, then per language (what it is, how, hardware tag, command)
TOOLS = [
    ("bc250-mon", {
        "en": ("sensor and hardware monitor", "btop-style dashboard, HWiNFO-style table",
               "AMD BC-250", "bc250-mon"),
        "pl": ("monitor czujników i sprzętu", "pulpit jak btop, tabela jak HWiNFO",
               "AMD BC-250", "bc250-mon"),
    }),
    ("bc250-nosuspend", {
        "en": ("no suspend, no hibernation", "systemd-level, Gaming Mode and headless",
               "any systemd system", "bc250-nosuspend start"),
        "pl": ("bez usypiania i hibernacji", "na poziomie systemd, też w Gaming Mode",
               "każdy system z systemd", "bc250-nosuspend start"),
    }),
    ("bc250-paddles", {
        "en": ("L4/R4 as Steam back buttons", "SDL mapping, native and Flatpak Steam",
               "8BitDo Ultimate 2C Wireless", "bc250-paddles on"),
        "pl": ("L4/R4 jako tylne przyciski Steam", "mapowanie SDL, Steam natywny i Flatpak",
               "8BitDo Ultimate 2C Wireless", "bc250-paddles on"),
    }),
    ("bc250-wcm", {
        "en": ("wireless controller monitor", "Bluetooth RSSI, 2.4 GHz link state, drops",
               "BT pads · 8BitDo Ultimate 2C", "bc250-wcm"),
        "pl": ("monitor kontrolerów bezprzewodowych", "RSSI Bluetooth, stan łącza 2,4 GHz, zerwania",
               "pady BT · 8BitDo Ultimate 2C", "bc250-wcm"),
    }),
]

TEXT = {
    "en": {
        "h_title": "bc250-tool-kit",
        "h_desc": "bc250-tool-kit: terminal tools for the AMD BC-250 board on Bazzite; next to it, "
                  "the kit's numbered tool menu as ./run.sh --menu prints it.",
        "h_kicker": "AMD BC-250 · Cyan Skillfish · Bazzite",
        "h_line1": "Terminal tools for the BC-250",
        "h_line2": "sensors, suspend, controllers",
        "h_meta": "Python stdlib + Bash · runs without installing · EN / PL",
        "h_run": "$ ./run.sh --menu",
        "h_prompt": "Tool number (q to quit): 1",
        "t_title": "Four self-contained tools",
        "t_desc": "The four tools of the kit, each with what it does, the hardware it is for and "
                  "its main command.",
        "t_for": "for",
    },
    "pl": {
        "h_title": "bc250-tool-kit",
        "h_desc": "bc250-tool-kit: narzędzia terminalowe dla płyty AMD BC-250 pod Bazzite; obok "
                  "numerowane menu narzędzi wypisane przez ./run.sh --menu.",
        "h_kicker": "AMD BC-250 · Cyan Skillfish · Bazzite",
        "h_line1": "Narzędzia terminalowe dla BC-250",
        "h_line2": "czujniki, usypianie, kontrolery",
        "h_meta": "Python stdlib + Bash · działa bez instalacji · PL / EN",
        "h_run": "$ ./run.sh --menu",
        "h_prompt": "Tool number (q to quit): 1",
        "t_title": "Cztery niezależne narzędzia",
        "t_desc": "Cztery narzędzia zestawu: co robi każde, dla jakiego sprzętu jest i jego główne "
                  "polecenie.",
        "t_for": "dla",
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


def tag(x, y, label, color=AMBER, size=18):
    """A hardware tag: a rounded outline pill."""
    w = len(label) * size * 0.6 + 28
    return "\n".join([
        f'<rect x="{x}" y="{y}" width="{w:.0f}" height="32" rx="16" fill="none" '
        f'stroke="{color}" stroke-width="2"/>',
        text(x + 14, y + 22, label, size, color),
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


def hero(lang, t):
    parts = [
        text(56, 76, t["h_kicker"], 20, MUTED),
        text(56, 140, "bc250-tool-kit", 60, FG, weight="bold"),
        text(56, 196, t["h_line1"], 28, FG, family=SANS),
        text(56, 236, t["h_line2"], 28, CYAN, family=SANS),
        text(56, 300, t["h_meta"], 18, MUTED),
        panel(664, 48, 488, 292, "BC-250 · terminal"),
        text(686, 98, t["h_run"], 20, FG),
        text(686, 138, "bc250-tool-kit", 20, MUTED),
    ]
    y = 180
    for i, (name, _) in enumerate(TOOLS, 1):
        parts.append(text(686, y, f"{i:>3})", 20, MUTED))
        parts.append(text(746, y, name, 20, GREEN, weight="bold"))
        y += 30
    parts.append(text(686, y + 14, t["h_prompt"], 20, FG))
    return svg(1200, 380, t["h_title"], t["h_desc"], "\n".join(parts))


def tools(lang, t):
    parts = [text(56, 78, t["t_title"], 40, FG, family=SANS, weight="bold")]
    w, h, gap = 540, 228, 24
    for i, (name, langs) in enumerate(TOOLS):
        what, how, hw, cmd = langs[lang]
        x = 48 + (i % 2) * (w + gap) + (i % 2) * 16
        y = 132 + (i // 2) * (h + 40)
        parts.append(panel(x, y, w, h, name, title_size=24))
        parts.append(text(x + 26, y + 58, what, 24, FG, family=SANS, weight="bold"))
        parts.append(text(x + 26, y + 92, how, 20, MUTED, family=SANS))
        parts.append(text(x + 26, y + 142, t["t_for"], 18, MUTED, family=SANS))
        parts.append(tag(x + 26 + len(t["t_for"]) * 11 + 12, y + 120, hw))
        parts.append(text(x + 26, y + 198, "$ " + cmd, 20, GREEN))
    return svg(1200, 680, t["t_title"], t["t_desc"], "\n".join(parts))


def main():
    for lang, t in TEXT.items():
        suffix = "" if lang == "en" else ".pl"
        for name, build in (("hero", hero), ("tools", tools)):
            path = os.path.join(OUT, f"{name}{suffix}.svg")
            with open(path, "w", encoding="utf-8") as f:
                f.write(build(lang, t))
            print(path)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generates the README images of bc250-paddles (English and Polish).

    python3 docs/img/source/make_img.py      # writes docs/img/*.svg

The look follows bc250-mon: black terminal panels with the title set into the
border, cyan headings, green for what works, amber/red for what does not.
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
RED = "#e05a5a"

MONO = "ui-monospace, SFMono-Regular, 'DejaVu Sans Mono', Menlo, Consolas, monospace"
SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', 'DejaVu Sans', sans-serif"

TEXT = {
    "en": {
        "h_title": "bc250-paddles: L4/R4 as Steam back buttons",
        "h_desc": "bc250-paddles turns on the SDL mapping that lets Steam Input see L4 and R4 of the "
                  "8BitDo Ultimate 2C over the 2.4 GHz receiver; next to it, its real status output "
                  "from a BC-250.",
        "h_kit": "bc250-tool-kit",
        "b_label": "hardware",
        "b_value": "8BitDo Ultimate 2C Wireless · 2.4 GHz",
        "h_line1": "L4/R4 of the 8BitDo Ultimate 2C",
        "h_line2": "as Steam Input back buttons",
        "h_line3": "over the 2.4 GHz receiver",
        "h_meta": "native + Flatpak Steam · Bazzite · no root",
        "h_panel": "BC-250 · Gaming Mode",
        # a real status from the BC-250; the two serial numbers are made up
        "h_status": [
            ("$ bc250-paddles status", FG),
            ("State: MAPPING ON", GREEN),
            ("", FG),
            ("Mapping used by Steam", CYAN),
            ("native: 22:02:50 — with paddles", GREEN),
            ("", FG),
            ("8BitDo pads", CYAN),
            ("event2  2dc8:301c DInput  A1B2C3D4E5", GREEN),
            ("event9  2dc8:301c DInput  F6E5D4C3B2", GREEN),
            ("2 pads — one mapping covers them all", MUTED),
        ],
        "m_title": "What the 2.4 GHz receiver sends",
        "m_desc": "Four ways the pad can show up: receiver idle, XInput, DInput and Bluetooth, "
                  "with what each gives for L4/R4, rumble and Steam.",
        "m_cards": [
            ("IDLE", "no pad linked", MUTED,
             [("USB", "2dc8:301c"), ("input device", "none"), ("L4 / R4", "—"),
              ("rumble", "—"), ("Steam back buttons", "—")],
             [MUTED, MUTED, MUTED, MUTED, MUTED]),
            ("XInput", "Home + X", AMBER,
             [("USB", "2dc8:310a"), ("driver", "xpad"), ("L4 / R4", "✗ not sent"),
              ("rumble", "✓"), ("Steam back buttons", "✗ impossible")],
             [FG, FG, RED, GREEN, RED]),
            ("DInput", "Home + B", GREEN,
             [("USB", "2dc8:301c"), ("driver", "hid-generic"), ("L4 / R4", "✓ BTN_C / BTN_Z"),
              ("rumble", "✗"), ("Steam back buttons", "✓ bc250-paddles on")],
             [FG, FG, GREEN, RED, GREEN]),
            ("Bluetooth", "reference", CYAN,
             [("USB", "— (BT 2dc8:301b)"), ("driver", "hid-generic"), ("L4 / R4", "✓ BTN_C / BTN_Z"),
              ("rumble", "✓"), ("Steam back buttons", "✓ built in")],
             [FG, FG, GREEN, GREEN, GREEN]),
        ],
        "m_target": "this tool",
        "m_foot1": "DInput needs pad 1.06 + receiver 1.00 — on 1.09 + 1.03 the switch does not happen.",
        "m_foot2": "301c without an input device is IDLE; bc250-paddles status tells them apart.",
        "f_title": "How L4/R4 reach Steam Input",
        "f_desc": "L4 and R4 travel from the pad through the receiver in DInput mode, the kernel and "
                  "SDL inside Steam to Steam Input; Steam ignores config.vdf for this pad and only the "
                  "SDL_GAMECONTROLLERCONFIG variable set by bc250-paddles adds the paddles.",
        "f_steps": [
            ("pad", ["L4", "R4"]),
            ("receiver", ["DInput", "2dc8:301c"]),
            ("kernel", ["BTN_C", "BTN_Z"]),
            ("SDL in Steam", ["b2 → paddle2", "b5 → paddle1"]),
            ("Steam Input", ["L5", "R5"]),
        ],
        "f_mapping": "where Steam takes the mapping from",
        "f_bad": ["config.vdf  SDL_GamepadBind", "ignored, overwritten on exit"],
        "f_good": ["SDL_GAMECONTROLLERCONFIG", "set by bc250-paddles on"],
    },
    "pl": {
        "h_title": "bc250-paddles: L4/R4 jako tylne przyciski w Steam",
        "h_desc": "bc250-paddles włącza mapowanie SDL, dzięki któremu Steam Input widzi L4 i R4 pada "
                  "8BitDo Ultimate 2C przez odbiornik 2,4 GHz; obok prawdziwy wynik status z BC-250.",
        "h_kit": "bc250-tool-kit",
        "b_label": "sprzęt",
        "b_value": "8BitDo Ultimate 2C Wireless · 2,4 GHz",
        "h_line1": "L4/R4 pada 8BitDo Ultimate 2C",
        "h_line2": "jako tylne przyciski Steam Input",
        "h_line3": "przez odbiornik 2,4 GHz",
        "h_meta": "Steam natywny + Flatpak · Bazzite · bez roota",
        "h_panel": "BC-250 · Gaming Mode",
        "h_status": [
            ("$ bc250-paddles status", FG),
            ("Stan: MAPOWANIE WŁĄCZONE", GREEN),
            ("", FG),
            ("Mapowanie użyte przez Steam", CYAN),
            ("natywny: 22:02:50 — z paddle", GREEN),
            ("", FG),
            ("Pady 8BitDo", CYAN),
            ("event2  2dc8:301c DInput  A1B2C3D4E5", GREEN),
            ("event9  2dc8:301c DInput  F6E5D4C3B2", GREEN),
            ("padów: 2 — jedno mapowanie dla wszystkich", MUTED),
        ],
        "m_title": "Co wysyła odbiornik 2,4 GHz",
        "m_desc": "Cztery postacie pada: odbiornik bez pada, XInput, DInput i Bluetooth, z tym, "
                  "co każda daje dla L4/R4, wibracji i Steama.",
        "m_cards": [
            ("IDLE", "bez pada", MUTED,
             [("USB", "2dc8:301c"), ("urządzenie wejściowe", "brak"), ("L4 / R4", "—"),
              ("wibracje", "—"), ("tylne przyciski w Steam", "—")],
             [MUTED, MUTED, MUTED, MUTED, MUTED]),
            ("XInput", "Home + X", AMBER,
             [("USB", "2dc8:310a"), ("sterownik", "xpad"), ("L4 / R4", "✗ nie są wysyłane"),
              ("wibracje", "✓"), ("tylne przyciski w Steam", "✗ niemożliwe")],
             [FG, FG, RED, GREEN, RED]),
            ("DInput", "Home + B", GREEN,
             [("USB", "2dc8:301c"), ("sterownik", "hid-generic"), ("L4 / R4", "✓ BTN_C / BTN_Z"),
              ("wibracje", "✗"), ("tylne przyciski w Steam", "✓ bc250-paddles on")],
             [FG, FG, GREEN, RED, GREEN]),
            ("Bluetooth", "odniesienie", CYAN,
             [("USB", "— (BT 2dc8:301b)"), ("sterownik", "hid-generic"), ("L4 / R4", "✓ BTN_C / BTN_Z"),
              ("wibracje", "✓"), ("tylne przyciski w Steam", "✓ wbudowane")],
             [FG, FG, GREEN, GREEN, GREEN]),
        ],
        "m_target": "to narzędzie",
        "m_foot1": "DInput wymaga pada 1.06 + odbiornika 1.00 — na 1.09 + 1.03 przełączenie nie działa.",
        "m_foot2": "301c bez urządzenia wejściowego to IDLE; bc250-paddles status je rozróżnia.",
        "f_title": "Jak L4/R4 docierają do Steam Input",
        "f_desc": "L4 i R4 przechodzą z pada przez odbiornik w trybie DInput, kernel i SDL w Steamie do "
                  "Steam Input; Steam ignoruje config.vdf dla tego pada, paddle dodaje dopiero zmienna "
                  "SDL_GAMECONTROLLERCONFIG ustawiana przez bc250-paddles.",
        "f_steps": [
            ("pad", ["L4", "R4"]),
            ("odbiornik", ["DInput", "2dc8:301c"]),
            ("kernel", ["BTN_C", "BTN_Z"]),
            ("SDL w Steamie", ["b2 → paddle2", "b5 → paddle1"]),
            ("Steam Input", ["L5", "R5"]),
        ],
        "f_mapping": "skąd Steam bierze mapowanie",
        "f_bad": ["config.vdf  SDL_GamepadBind", "ignorowany, nadpisywany"],
        "f_good": ["SDL_GAMECONTROLLERCONFIG", "ustawia bc250-paddles on"],
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
        text(56, 76, t["h_kit"], 20, MUTED),
        text(56, 136, "bc250-paddles", 56, FG, weight="bold"),
        text(56, 190, t["h_line1"], 26, FG, family=SANS),
        text(56, 226, t["h_line2"], 26, FG, family=SANS),
        text(56, 262, t["h_line3"], 26, CYAN, family=SANS),
        text(56, 312, t["h_meta"], 18, MUTED),
        panel(616, 48, 536, 300, t["h_panel"]),
    ]
    y = 92
    for line, color in t["h_status"]:
        if line:
            parts.append(text(638, y, line, 19, color))
        y += 26
    return svg(1200, 380, t["h_title"], t["h_desc"], "\n".join(parts))


def modes(t):
    parts = [text(56, 78, t["m_title"], 40, FG, family=SANS, weight="bold")]
    x0, y0, w, h, gap = 48, 130, 264, 404, 16
    for i, (name, how, color, rows, colors) in enumerate(t["m_cards"]):
        x = x0 + i * (w + gap)
        border = GREEN if name == "DInput" else BORDER
        parts.append(panel(x, y0, w, h, name, title_color=color, border=border, title_size=24))
        parts.append(text(x + 22, y0 + 46, how, 20, MUTED))
        y = y0 + 96
        for (label, value), vcolor in zip(rows, colors):
            parts.append(text(x + 22, y, label, 18, MUTED, family=SANS))
            parts.append(text(x + 22, y + 28, value, 20, vcolor))
            y += 62
        if name == "DInput":
            pw = len(t["m_target"]) * 10 + 28
            parts.append(f'<rect x="{x + w - pw - 12}" y="{y0 - 15}" width="{pw}" height="30" rx="15" '
                         f'fill="{GREEN}" stroke="{BG}" stroke-width="4"/>')
            parts.append(text(x + w - 12 - pw / 2, y0 + 6, t["m_target"], 18, BG, family=SANS,
                              weight="bold", anchor="middle"))
    parts.append(text(56, 584, t["m_foot1"], 20, AMBER, family=SANS))
    parts.append(text(56, 618, t["m_foot2"], 20, MUTED, family=SANS))
    return svg(1200, 650, t["m_title"], t["m_desc"], "\n".join(parts))


def flow(t):
    parts = [text(56, 78, t["f_title"], 40, FG, family=SANS, weight="bold")]
    x0, y0, w, h, gap = 48, 128, 196, 150, 31
    centers = []
    for i, (label, lines) in enumerate(t["f_steps"]):
        x = x0 + i * (w + gap)
        last = i == len(t["f_steps"]) - 1
        parts.append(panel(x, y0, w, h, label, title_color=GREEN if last else CYAN,
                           border=GREEN if last else BORDER, title_size=20))
        size = 20 if max(len(s) for s in lines) > 9 else 28
        for j, line in enumerate(lines):
            parts.append(text(x + w / 2, y0 + 66 + j * 44, line, size, FG, weight="bold",
                              anchor="middle"))
        if not last:
            ax = x + w + 4
            parts.append(f'<path d="M{ax} {y0 + h / 2} h{gap - 12}" stroke="{MUTED}" stroke-width="3"/>')
            parts.append(f'<path d="M{ax + gap - 8} {y0 + h / 2} l-10 -7 v14 z" fill="{MUTED}"/>')
        centers.append(x + w / 2)

    # the mapping source sits under the SDL step
    sdl = centers[3]
    parts.append(text(56, 344, t["f_mapping"], 20, MUTED, family=SANS))
    for k, (lines, color, mark) in enumerate([(t["f_bad"], RED, "✗"), (t["f_good"], GREEN, "✓")]):
        y = 368 + k * 92
        parts.append(f'<rect x="56" y="{y}" width="590" height="76" rx="8" fill="{PANEL}" '
                     f'stroke="{color}" stroke-width="2"/>')
        parts.append(text(80, y + 49, mark, 32, color, weight="bold"))
        parts.append(text(124, y + 32, lines[0], 20, FG, weight="bold"))
        parts.append(text(124, y + 60, lines[1], 18, color, family=SANS))
    # elbow from the working source up to the SDL box
    parts.append(f'<path d="M646 {460 + 38} H{sdl} V{y0 + h + 10}" fill="none" stroke="{GREEN}" '
                 f'stroke-width="3"/>')
    parts.append(f'<path d="M{sdl} {y0 + h + 2} l-8 12 h16 z" fill="{GREEN}"/>')
    parts.append(f'<path d="M646 {368 + 38} H{sdl - 40}" fill="none" stroke="{RED}" stroke-width="3" '
                 f'stroke-dasharray="8 8"/>')
    parts.append(text(sdl - 28, 368 + 46, "✗", 26, RED, weight="bold", anchor="middle"))
    return svg(1200, 580, t["f_title"], t["f_desc"], "\n".join(parts))


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
        for name, build in (("header", header), ("receiver-modes", modes), ("how-it-reaches-steam", flow),
                            ("hardware", badge)):
            path = os.path.join(OUT, f"{name}{suffix}.svg")
            with open(path, "w", encoding="utf-8") as f:
                f.write(build(t))
            print(path)


if __name__ == "__main__":
    main()

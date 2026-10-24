"""Interface translations — English and Polish.

Language selection: the --lang argument, the BC250_LANG variable, then the
system locale. An unknown setting falls back to English.
"""

from __future__ import annotations

import os

LANGUAGES = ("pl", "en")
FALLBACK = "en"

# key: (Polish, English)
_MESSAGES: dict[str, tuple[str, str]] = {
    # --- header and footer -------------------------------------------------
    "mode.generic": ("tryb ogólny", "generic mode"),
    "state.paused": ("PAUZA", "PAUSED"),
    "header.kernel": ("jądro", "kernel"),
    "header.uptime": ("uptime", "uptime"),
    "header.none": ("brak", "none"),
    "value.unavailable": ("n/d", "n/a"),
    "footer.keys": (
        "q wyjście · Tab widok · p pauza · r zeruj · +/- interwał · h pomoc",
        "q quit · Tab view · p pause · r reset · +/- interval · h help",
    ),
    "footer.view.dash": ("pulpit", "dashboard"),
    "footer.view.sensors": ("czujniki", "sensors"),
    "footer.samples": ("próbek", "samples"),
    "err.window.small": (
        "Okno terminala za małe (min. 40x8)",
        "Terminal window too small (min. 40x8)",
    ),

    # --- panels ------------------------------------------------------------
    "panel.gpu.nocard": (
        "Nie znaleziono karty amdgpu — brak danych GPU.",
        "No amdgpu card found — no GPU data.",
    ),
    "panel.gpu.nocard.short": ("brak karty", "no card"),
    "panel.board.superio": ("Płyta · SuperIO", "Board · SuperIO"),
    "panel.board.other": ("Płyta · pozostałe czujniki", "Board · other sensors"),
    "panel.board.nosuperio": (
        "Brak czujników płyty. sudo modprobe nct6687 force=true",
        "No board sensors. sudo modprobe nct6687 force=true",
    ),
    "panel.board.chips": ("{count} układów", "{count} chips"),
    "panel.board.notloaded": ("nie załadowany", "not loaded"),
    "panel.memory": ("Pamięć i dyski", "Memory and drives"),
    "panel.notes": ("Diagnostyka", "Diagnostics"),
    "panel.sensors": ("Czujniki", "Sensors"),
    "panel.sensors.hint": (
        "pełna tabela z min/średnią: Tab",
        "full table with min/avg: Tab",
    ),

    # --- gauges ---------------------------------------------------------
    "gauge.temperature": ("Temperatura", "Temperature"),
    "gauge.power": ("Moc (PPT)", "Power (PPT)"),
    "gauge.load": ("Obciążenie", "Load"),
    "gauge.gpu.clock": ("Zegar GPU", "GPU clock"),
    "gauge.gpu.clock.avg": ("Zegar (śr.)", "Clock (avg)"),
    "gauge.clock.avg": ("Zegar (śr.)", "Clock (avg)"),
    "gauge.vram.carveout": ("VRAM (min)", "VRAM (min)"),
    "gauge.gpu.memory": ("Pamięć GPU", "GPU memory"),
    "gpu.level": ("poziom", "level"),
    "gpu.clock.from.curve": ("z krzywej ≈{clock}", "from curve ≈{clock}"),
    "note.governor.range.overridden": (
        "Zakres governora został zmieniony w locie na {live_min}–{live_max} MHz; "
        "config nadal mówi {config_min}–{config_max} MHz.",
        "The governor range was changed at runtime to {live_min}–{live_max} MHz; "
        "the configuration file still says {config_min}–{config_max} MHz.",
    ),

    # --- CPU ---------------------------------------------------------------
    "cpu.threads": ("Wątki ({count})", "Threads ({count})"),
    "cpu.threads.more": (
        "… jeszcze {count} wątków — komplet w widoku czujników (Tab)",
        "… {count} more threads — full list in the sensors view (Tab)",
    ),
    "cpu.threads.plain": ("{count} wątków", "{count} threads"),
    "cpu.cores.present": ("{count} obecnych", "{count} present"),
    "cpu.nocpufreq": ("brak cpufreq", "no cpufreq"),

    # --- graphs -----------------------------------------------------------
    "graph.gpu.temp": ("temperatura GPU (°C)", "GPU temperature (°C)"),
    "graph.gpu.power": ("moc GPU — PPT (W)", "GPU power — PPT (W)"),
    "graph.gpu.clock": ("zegar GPU (MHz)", "GPU clock (MHz)"),
    "graph.gpu.clock.avg": ("zegar GPU — średni (MHz)", "GPU clock — average (MHz)"),
    "graph.cpu.load": ("obciążenie CPU (%)", "CPU load (%)"),
    "graph.cpu.temp": ("temperatura CPU (°C)", "CPU temperature (°C)"),
    "graph.cpu.clock": ("zegar CPU (MHz)", "CPU clock (MHz)"),
    "graph.ram": ("użycie RAM (%)", "RAM usage (%)"),
    "graph.fan": ("obroty wentylatora (RPM)", "fan speed (RPM)"),

    # --- sensor kinds -------------------------------------------------
    "kind.temp": ("Temperatury", "Temperatures"),
    "kind.fan": ("Wentylatory", "Fans"),
    "kind.volt": ("Napięcia", "Voltages"),
    "kind.power": ("Moc", "Power"),
    "kind.pwm": ("PWM", "PWM"),
    "kind.curr": ("Prąd", "Current"),
    "kind.freq": ("Zegary", "Clocks"),
    "kind.usage": ("Obciążenie", "Load"),

    # --- sensor table --------------------------------------------------
    "table.sensor": ("Czujnik", "Sensor"),
    "table.now": ("Teraz", "Now"),
    "table.min": ("Min", "Min"),
    "table.max": ("Maks", "Max"),
    "table.avg": ("Średnia", "Average"),
    "chip.computed": ("obliczone", "computed"),

    # --- help -------------------------------------------------------------
    "help.title": ("Skróty klawiszowe", "Keyboard shortcuts"),
    "help.close": ("h zamyka", "h closes"),
    "help.quit": ("wyjście", "quit"),
    "help.switch": ("przełącz pulpit ↔ czujniki", "switch dashboard ↔ sensors"),
    "help.scroll": ("przewijanie tabeli czujników", "scroll the sensors table"),
    "help.pause": ("pauza odświeżania", "pause refreshing"),
    "help.reset": ("wyzeruj min/maks/średnie", "reset min/max/averages"),
    "help.interval": ("zmień interwał odświeżania", "change the refresh interval"),
    "help.help": ("ta pomoc", "this help"),
    "help.diagnostics": ("pokaż lub ukryj Diagnostykę", "show or hide Diagnostics"),

    # --- computed values ------------------------------------------------
    "calc.gpu.clock": ("GPU zegar rdzenia", "GPU core clock"),
    "calc.gpu.clock.avg": ("GPU zegar rdzenia (śr.)", "GPU core clock (avg)"),
    "calc.gpu.mclk": ("GPU zegar pamięci", "GPU memory clock"),
    "calc.gpu.busy": ("GPU obciążenie", "GPU load"),
    "calc.cpu.usage": ("CPU obciążenie", "CPU load"),
    "calc.cpu.clock": ("CPU zegar (śr.)", "CPU clock (avg)"),
    "calc.mem.used": ("RAM użycie", "RAM usage"),
    "calc.vram.used": ("VRAM użycie", "VRAM usage"),
    "calc.vram.carveout.used": ("VRAM (min) użycie", "VRAM (min) usage"),
    "calc.gpu.memory.used": ("Pamięć GPU użycie", "GPU memory usage"),
    "label.cpu.package": ("CPU pakiet", "CPU package"),
    "label.temperature": ("Temperatura", "Temperature"),

    # --- diagnostics -------------------------------------------------------
    "note.generic": (
        "Nie wykryto BC-250 — działam w trybie ogólnym.",
        "BC-250 not detected — running in generic mode.",
    ),
    "note.superio.missing": (
        "Brak modułu SuperIO — czujniki płyty niewidoczne. "
        "Napraw: sudo modprobe nct6687 force=true",
        "SuperIO module not loaded — board sensors unavailable. "
        "Fix: sudo modprobe nct6687 force=true",
    ),
    "note.superio.readonly": (
        "Załadowany nct6683 (tylko odczyt) — sterowanie PWM wymaga nct6687.",
        "nct6683 loaded (read-only) — PWM control requires nct6687.",
    ),
    "note.gpu.missing": (
        "Nie znaleziono karty amdgpu.",
        "No amdgpu card found.",
    ),
    "note.gpu.busy": (
        "Obciążenie GPU niedostępne: {reason}.",
        "GPU load unavailable: {reason}.",
    ),
    "note.gpu.sclk": (
        "pp_dpm_sclk zwraca zaniżone wartości (znany efekt odblokowania "
        "8 rdzeni) — zegar GPU przeskalowany x100.",
        "pp_dpm_sclk reports values an order of magnitude too low (a known "
        "effect of the 8-core unlock) — GPU clock rescaled by 100.",
    ),
    "note.gpu.sclk.unreliable": (
        "Telemetria zegara GPU jest zepsuta: pp_dpm_sclk podaje {active} MHz przy "
        "zakresie {low}–{high} MHz. Uśrednione pole z gpu_metrics pochodzi z tego "
        "samego licznika firmware'u, więc też mu nie ufam. Znany efekt odblokowania "
        "8 rdzeni — sama wydajność jest w porządku.",
        "GPU clock telemetry is broken: pp_dpm_sclk reports {active} MHz against a "
        "{low}–{high} MHz range. The averaged field in gpu_metrics comes from the "
        "same firmware counter, so it is not trusted either. A known effect of the "
        "8-core unlock — performance itself is unaffected.",
    ),
    "note.gpu.sclk.from.metrics": (
        "pp_dpm_sclk podaje bzdurny zegar ({active} MHz przy zakresie {low}–{high} MHz) "
        "— znany efekt odblokowania 8 rdzeni. Zegar GPU biorę z gpu_metrics, "
        "które sterownik wypełnia z innego pola SMU.",
        "pp_dpm_sclk reports a nonsensical clock ({active} MHz against a "
        "{low}–{high} MHz range) — a known effect of the 8-core unlock. The GPU "
        "clock is taken from gpu_metrics, which the driver fills from a different "
        "SMU field.",
    ),
    "note.vram.small.split": (
        "Split VRAM to {size} — pełny pasek VRAM jest normalny, bo to tylko "
        "gwarantowane minimum. Reszta pamięci GPU idzie przez GTT, a praktyczny "
        "sufit to około {ceiling}.",
        "The VRAM split is {size} — a full VRAM bar is normal, because that is only "
        "the guaranteed minimum. The rest of GPU memory comes from GTT, and the "
        "practical ceiling is about {ceiling}.",
    ),
    "note.gpu.locked": (
        "GPU zablokowane na 1500 MHz — brak działającego governora "
        "({unit}: {state}).",
        "GPU locked at 1500 MHz — no governor running ({unit}: {state}).",
    ),
    "note.temp.crit": (
        "{label} {value:.0f}°C — powyżej progu krytycznego {limit:.0f}°C!",
        "{label} {value:.0f}°C — above the critical threshold of {limit:.0f}°C!",
    ),
    "note.temp.warn": (
        "{label} {value:.0f}°C — powyżej progu ostrzegawczego {limit:.0f}°C.",
        "{label} {value:.0f}°C — above the warning threshold of {limit:.0f}°C.",
    ),
    "note.fans.zero": (
        "Wszystkie wentylatory raportują 0 RPM — sprawdź podłączenie "
        "do złącza J1/J4003 lub tryb w BIOS.",
        "All fans report 0 RPM — check the J1/J4003 header connection or the "
        "fan mode in BIOS.",
    ),

    # --- reasons a reading is missing ----------------------------------------------
    "reason.metrics.uninitialised": (
        "amdgpu nie wypełnia pola aktywności (brak governora smu)",
        "amdgpu leaves the activity field uninitialised (no smu governor)",
    ),
    "reason.metrics.invalid": (
        "gpu_metrics zwraca nieprawidłową wartość ({raw})",
        "gpu_metrics returns an invalid value ({raw})",
    ),
    "reason.metrics.absent": (
        "brak gpu_busy_percent i gpu_metrics",
        "neither gpu_busy_percent nor gpu_metrics is available",
    ),

    # --- command line ----------------------------------------------------
    "cli.description": (
        "Monitor czujników i sprzętu AMD BC-250 (Cyan Skillfish).",
        "Sensor and hardware monitor for the AMD BC-250 (Cyan Skillfish).",
    ),
    "cli.interval": (
        "interwał odświeżania (domyślnie 1.0)",
        "refresh interval (default 1.0)",
    ),
    "cli.interval.metavar": ("SEK", "SEC"),
    "cli.once": (
        "wypisz jednorazowy raport tekstowy i zakończ",
        "print a one-shot text report and exit",
    ),
    "cli.json": (
        "wypisz migawkę w formacie JSON i zakończ",
        "print a snapshot as JSON and exit",
    ),
    "cli.demo": (
        "symulowana BC-250 (do podglądu bez fizycznej płyty)",
        "simulated BC-250 (preview without the physical board)",
    ),
    "cli.nodiag": (
        "nie pokazuj panelu Diagnostyka",
        "do not show the Diagnostics panel",
    ),
    "cli.lang": (
        "język interfejsu: pl, en (domyślnie z locale)",
        "interface language: pl, en (defaults to the locale)",
    ),
    "cli.notty": (
        "bc250-mon: brak terminala — użyj --once albo --json",
        "bc250-mon: no terminal — use --once or --json",
    ),

    # --- text report ---------------------------------------------------
    "report.detected": ("BC-250 wykryta", "BC-250 detected"),
    "report.host": ("host", "host"),
    "report.kernel": ("jądro", "kernel"),
    "report.board": ("płyta", "board"),
    "report.superio": ("SuperIO", "SuperIO"),
    "report.superio.none": ("brak modułu", "module not loaded"),
    "report.governor": ("governor", "governor"),
    "report.chip": ("układ", "chip"),
    "report.temperature": ("temperatura", "temperature"),
    "report.power": ("moc (PPT)", "power (PPT)"),
    "report.load": ("obciążenie", "load"),
    "report.core.clock": ("zegar rdz.", "core clock"),
    "report.mem.clock": ("zegar pam.", "memory clock"),
    "report.model": ("model", "model"),
    "report.cores": ("rdzenie", "cores"),
    "report.cores.value": (
        "{physical} fizycznych / {online} logicznych / {present} obecnych",
        "{physical} physical / {online} logical / {present} present",
    ),
    "report.clocks": ("zegary", "clocks"),
    "report.memory": ("Pamięć", "Memory"),
    "report.swap": ("swap", "swap"),
    "report.hwmon": ("hwmon", "hwmon"),
    "report.nogpu": ("nie znaleziono karty amdgpu", "no amdgpu card found"),
}

_current = FALLBACK


def detect_language(explicit: str | None = None) -> str:
    """Order: the argument, BC250_LANG, the system locale, then English."""
    candidates = (
        explicit,
        os.environ.get("BC250_LANG"),
        os.environ.get("LC_ALL"),
        os.environ.get("LC_MESSAGES"),
        os.environ.get("LANG"),
    )
    for candidate in candidates:
        if not candidate:
            continue
        code = candidate.split(".")[0].split("_")[0].strip().lower()
        if code in LANGUAGES:
            return code
    return FALLBACK


def set_language(name: str | None) -> str:
    global _current
    _current = detect_language(name)
    return _current


def language() -> str:
    return _current


def t(key: str, **kwargs) -> str:
    """Text in the current language; an unknown key returns the key itself."""
    entry = _MESSAGES.get(key)
    if entry is None:
        return key
    text = entry[0] if _current == "pl" else entry[1]
    return text.format(**kwargs) if kwargs else text

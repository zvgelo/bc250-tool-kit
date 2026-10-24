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
    "yes": ("tak", "yes"),
    "no": ("nie", "no"),

    # --- command line -------------------------------------------------------
    "cli.description": (
        "Wireless Controller Monitor (WCM) dla BC-250: kontrolery Bluetooth i odbiorniki "
        "USB 2,4 GHz — stan łącza, RSSI tam, gdzie istnieje, rozłączenia, resety adaptera.",
        "Wireless Controller Monitor (WCM) for the BC-250: Bluetooth controllers and "
        "USB 2.4 GHz receivers — link state, RSSI where it exists, disconnects, adapter resets.",
    ),
    "cli.interval": ("interwał próbkowania w sekundach (domyślnie 1)",
                     "sampling interval in seconds (default 1)"),
    "cli.interval.metavar": ("SEK", "SEC"),
    "cli.monitor": ("monitoruj od razu te urządzenia (1–4: adres MAC albo usb:<id> z --list)",
                    "start monitoring these devices right away (1–4: a MAC address or usb:<id> from --list)"),
    "cli.list": ("wypisz adaptery i urządzenia, potem zakończ",
                 "print adapters and devices, then exit"),
    "cli.json": ("adaptery i urządzenia jako JSON, potem zakończ",
                 "adapters and devices as JSON, then exit"),
    "cli.headless": ("bez interfejsu: próbkuj --monitor przez SEK sekund i wypisuj wyniki",
                     "no interface: sample --monitor for SEC seconds and print the readings"),
    "cli.export": ("plik eksportu po trybie --headless (.csv albo .json)",
                   "export file written after --headless (.csv or .json)"),
    "cli.record": ("dopisuj każdą próbkę do pliku CSV na bieżąco",
                   "append every sample to a CSV file as it is taken"),
    "cli.backend": ("źródło RSSI połączenia (domyślnie z konfiguracji: auto)",
                    "link RSSI source (default from the configuration: auto)"),
    "cli.nosudo": ("nigdy nie pytaj o sudo (bez RSSI połączenia, o ile helper nie jest zainstalowany)",
                   "never ask for sudo (no link RSSI unless the helper is installed)"),
    "cli.nokernel": ("nie czytaj dziennika jądra", "do not follow the kernel log"),
    "cli.config": ("ścieżka pliku konfiguracji", "configuration file path"),
    "cli.printconfig": ("wypisz przykładową konfigurację i zakończ",
                        "print an example configuration and exit"),
    "cli.demo": ("symulacja: dwa pady, reset adaptera hci0 → hci1",
                 "simulation: two pads, an hci0 → hci1 adapter reset"),
    "cli.demospeed": ("przyspieszenie osi czasu symulacji", "speed-up of the simulation timeline"),
    "cli.lang": ("język interfejsu", "interface language"),
    "cli.ascii": ("tylko znaki ASCII (bez Braille'a i ramek Unicode)",
                  "ASCII characters only (no Braille, no Unicode boxes)"),
    "cli.nocolor": ("bez kolorów", "no colours"),
    "cli.notty": ("To nie jest terminal — użyj --list, --json albo --headless.",
                  "Not a terminal — use --list, --json or --headless."),
    "cli.monitor.max": ("Najwyżej {count} urządzenia naraz.", "At most {count} devices at once."),
    "cli.monitor.invalid": ("Niepoprawny adres Bluetooth: {address}", "Invalid Bluetooth address: {address}"),
    "cli.headless.needs": ("--headless wymaga --monitor ADRES…", "--headless needs --monitor ADDRESS…"),
    "cli.headless.missing": ("Nie znaleziono urządzenia {address} — pomijam.",
                             "Device {address} not found — skipping."),
    "cli.migrated": ("migracja z bc250-btmon: {what}", "migrated from bc250-btmon: {what}"),
    "cli.config.warning": ("konfiguracja: {text}", "configuration: {text}"),
    "cli.exported": ("Zapisano {count} próbek: {path}", "Wrote {count} samples: {path}"),
    "cli.sudo.why": (
        "RSSI aktywnego połączenia wymaga CAP_NET_ADMIN (mgmt Get Connection Information).\n"
        "Uruchomię przez sudo tylko mały helper tylko-do-odczytu ({path});\n"
        "interfejs działa dalej bez uprawnień. Ctrl-C pomija — monitor pokaże wtedy\n"
        "stan, baterię i zdarzenia, ale bez RSSI połączenia.",
        "The RSSI of a live link needs CAP_NET_ADMIN (mgmt Get Connection Information).\n"
        "Only a small read-only helper ({path}) runs through sudo; the interface\n"
        "itself stays unprivileged. Ctrl-C skips it — the monitor then shows state,\n"
        "battery and events, but no link RSSI.",
    ),
    "cli.sudo.prompt": ("[bc250-wcm] hasło sudo dla %u (helper RSSI, Ctrl-C pomija): ",
                        "[bc250-wcm] sudo password for %u (RSSI helper, Ctrl-C skips): "),
    "cli.helper.failed": ("Helper RSSI nie wystartował: {reason}", "The RSSI helper did not start: {reason}"),
    "cli.list.adapters": ("Adaptery", "Adapters"),
    "cli.list.devices": ("Urządzenia", "Devices"),
    "cli.list.usb": ("Kontrolery USB i odbiorniki 2,4 GHz", "USB controllers and 2.4 GHz receivers"),
    "cli.list.transport.usb-2.4": ("odbiornik USB 2,4 GHz", "USB 2.4 GHz receiver"),
    "cli.list.transport.usb-2.4?": ("USB: odbiornik 2,4 GHz albo kabel (nie do odróżnienia pasywnie)",
                                    "USB: 2.4 GHz receiver or cable (not distinguishable passively)"),
    "cli.list.transport.usb": ("USB", "USB"),
    "cli.nousb": ("pomiń kontrolery USB i odbiorniki 2,4 GHz", "skip USB controllers and 2.4 GHz receivers"),
    "cli.noactivity": ("nie licz zdarzeń wejściowych z evdev", "do not count input events from evdev"),
    "cli.epilog": ("Badanie interfejsów HID: bc250-wcm hid --help",
                   "HID interface research: bc250-wcm hid --help"),
    "cli.list.none": ("(brak)", "(none)"),

    # --- hid subcommand --------------------------------------------------------
    "hid.description": ("Pasywne badanie interfejsów HID (tylko odczyt).",
                        "Passive research of HID interfaces (read-only)."),
    "hid.list": ("lista węzłów hidraw", "list hidraw nodes"),
    "hid.descriptor": ("deskryptor raportów, surowy i zdekodowany", "report descriptor, raw and decoded"),
    "hid.capture": ("pasywny zapis raportów wejściowych", "passive capture of input reports"),
    "hid.capture.seconds": ("czas w sekundach (0 = do Ctrl-C)", "seconds (0 = until Ctrl-C)"),
    "hid.capture.output": ("zapisz zrzut do pliku JSONL", "save the capture as JSON lines"),
    "hid.capture.marks": ("czytaj znaczniki ze stdin: wpisz opis czynności i Enter",
                          "read marks from stdin: type what you did and press Enter"),
    "hid.capture.quiet": ("nie wypisuj raportów", "do not print reports"),
    "hid.analyze": ("analiza zrzutu: zmienne bajty, porównanie, znaczniki",
                    "analyse a capture: changing bytes, comparison, marks"),
    "hid.analyze.window": ("okno wokół znacznika w sekundach", "window around a mark in seconds"),
    "hid.notfound": ("Nie znaleziono węzła HID „{selector}” (bc250-wcm hid list).",
                     "No HID node '{selector}' (see bc250-wcm hid list)."),
    "hid.none": ("Brak węzłów hidraw.", "No hidraw nodes."),
    "hid.pages": ("strony użycia", "usage pages"),
    "hid.vendor": ("interfejs producenta", "vendor-defined"),
    "hid.reports": ("raporty", "reports"),
    "hid.access": ("dostęp", "access"),
    "hid.access.read": ("odczyt możliwy", "readable"),
    "hid.access.denied": ("brak (patrz docs/permissions.md)", "denied (see docs/permissions.md)"),
    "hid.access.missing": ("brak węzła w /dev", "no node in /dev"),
    "hid.access.?": ("?", "?"),
    "hid.nodescriptor": ("Deskryptor nieczytelny.", "Descriptor not readable."),
    "hid.collection": ("kolekcja", "collection"),
    "hid.noid": ("bez ID", "no ID"),
    "hid.size": ("{payload} B danych, {total} B z hidraw", "{payload} B payload, {total} B from hidraw"),
    "hid.vendor.note": (
        "Strona producenta: deskryptor podaje rozmiary, nie znaczenie bajtów. Nie wysyłaj tu raportów — "
        "u 8BitDo to także kanał aktualizacji firmware'u.",
        "Vendor-defined page: the descriptor gives sizes, not what the bytes mean. Do not send reports "
        "here — on 8BitDo receivers this is also the firmware-update channel.",
    ),
    "hid.denied": (
        "Brak prawa odczytu {path}. Reguła Steama (60-steam-input.rules) albo "
        "install.sh --udev daje dostęp zalogowanemu użytkownikowi (uaccess).",
        "No read access to {path}. Steam's rule (60-steam-input.rules) or "
        "install.sh --udev grants it to the logged-in user (uaccess).",
    ),
    "hid.capturing": ("Pasywny odczyt {path} ({name}) — Ctrl-C kończy.",
                      "Reading {path} ({name}) passively — Ctrl-C stops."),
    "hid.marks.howto": ("Znaczniki: wpisz np. „A wciśnięty” i Enter w chwili działania.",
                        "Marks: type e.g. 'pressed A' and Enter as you do it."),
    "hid.badsize": ("(rozmiar {got} zamiast {want})", "(size {got}, expected {want})"),
    "hid.captured": ("Odebrano {reports} raportów, {marks} znaczników.",
                     "Received {reports} reports, {marks} marks."),
    "hid.nothing": (
        "Brak raportów. Interfejs może odpowiadać wyłącznie na zapytania — tego narzędzie celowo nie robi.",
        "No reports. The interface may only answer requests — which this tool deliberately never sends.",
    ),
    "hid.saved": ("Zapisano: {path}", "Saved: {path}"),
    "hid.analysis": ("{path}: {reports} raportów, {marks} znaczników", "{path}: {reports} reports, {marks} marks"),
    "hid.report.header": ("Raport {rid}: {count} szt., {rate}/s", "Report {rid}: {count} received, {rate}/s"),
    "hid.malformed": ("{count} raportów o złej długości (oczekiwano {want} B) — pominięte",
                      "{count} reports of the wrong length (expected {want} B) — skipped"),
    "hid.constant": ("bajty stałe: {count}", "constant bytes: {count}"),
    "hid.values": ("wart.", "values"),
    "hid.compare": ("różne zbiory wartości względem {path} na pozycjach: {offsets}",
                    "value sets differ from {path} at offsets: {offsets}"),
    "hid.mark": ("znacznik „{label}”: zmienione bajty {offsets}", "mark '{label}': bytes changed {offsets}"),
    "hid.mark.none": ("brak (albo brak raportów wokół)", "none (or no reports around it)"),

    "backend.disabled": ("wyłączone (--rssi-backend none)", "disabled (--rssi-backend none)"),
    "backend.direct.notroot": ("backend direct wymaga roota", "the direct backend needs root"),
    "backend.sudo.declined": ("pominięto sudo", "sudo skipped"),
    "backend.needs.sudo": (
        "wymaga uprawnień — zainstaluj helper (install.sh --rssi-helper) albo pozwól na sudo",
        "needs privileges — install the helper (install.sh --rssi-helper) or allow sudo",
    ),

    # --- statuses -------------------------------------------------------------
    "bluez.starting": ("łączenie z BlueZ…", "connecting to BlueZ…"),
    "bluez.no-bus": ("brak D-Bus", "no D-Bus"),
    "bluez.no-bluez": ("bluetoothd nie działa", "bluetoothd not running"),
    "bluez.error": ("błąd BlueZ", "BlueZ error"),
    "bluez.ok": ("OK", "OK"),
    "status.ok": ("OK", "OK"),
    "status.not-connected": ("brak połączenia w jądrze", "not connected in the kernel"),
    "status.permission": ("brak uprawnień", "permission denied"),
    "status.timeout": ("timeout", "timeout"),
    "status.invalid-index": ("niepoprawny indeks adaptera", "invalid adapter index"),
    "status.not-powered": ("adapter wyłączony", "adapter powered off"),
    "status.unsupported": ("nieobsługiwane", "not supported"),
    "status.invalid-params": ("niepoprawne parametry", "invalid parameters"),
    "status.unavailable": ("niedostępne", "unavailable"),
    "status.error": ("błąd", "error"),

    # --- events ----------------------------------------------------------------
    "ev.started": ("Start monitora", "Monitor started"),
    "ev.bluez.back": ("BlueZ znów dostępny", "BlueZ is back"),
    "ev.bluez.gone": ("bluetoothd zniknął z magistrali D-Bus", "bluetoothd left the D-Bus"),
    "ev.bus.gone": ("Brak połączenia z systemowym D-Bus: {error}",
                    "No connection to the system D-Bus: {error}"),
    "ev.bluez.error": ("Błąd BlueZ: {error}", "BlueZ error: {error}"),
    "ev.adapter.found": ("Adapter {name} {address} (USB {usb})", "Adapter {name} {address} (USB {usb})"),
    "ev.adapter.added": ("Nowy adapter {name} {address}", "New adapter {name} {address}"),
    "ev.adapter.removed": ("Adapter {name} {address} zniknął", "Adapter {name} {address} disappeared"),
    "ev.adapter.back": ("Adapter {name} {address} wrócił", "Adapter {name} {address} came back"),
    "ev.adapter.remap": ("Adapter {address} zarejestrował się ponownie: {old} → {new}",
                         "Adapter {address} re-registered: {old} → {new}"),
    "ev.adapter.newaddr": ("Adapter {name} wrócił z innym adresem: {old} → {new} (ten sam port USB)",
                           "Adapter {name} came back with a different address: {old} → {new} "
                           "(same USB port)"),
    "ev.adapter.prop": ("{name}: {field} {old} → {new}", "{name}: {field} {old} → {new}"),
    "ev.device.added": ("Urządzenie {name} {address} pojawiło się", "Device {name} {address} appeared"),
    "ev.device.removed": ("Urządzenie {name} {address} zniknęło", "Device {name} {address} disappeared"),
    "ev.device.prop": ("{name}: {field} {old} → {new}", "{name}: {field} {old} → {new}"),
    "ev.device.connected": ("{name} połączony ({adapter})", "{name} connected ({adapter})"),
    "ev.device.disconnected": ("{name} rozłączony", "{name} disconnected"),
    "reason.adapter.gone": ("adapter zniknął", "adapter gone"),
    "reason.device.gone": ("urządzenie zniknęło z BlueZ", "device gone from BlueZ"),
    "ev.monitoring": ("Monitorowane: {names}", "Monitoring: {names}"),
    "ev.reset": ("Statystyki wyzerowane", "Statistics reset"),
    "ev.discovery.on": ("Wyszukiwanie włączone na {name}", "Discovery started on {name}"),
    "ev.discovery.off": ("Wyszukiwanie wyłączone na {name}", "Discovery stopped on {name}"),
    "ev.discovery.failed": ("Wyszukiwanie na {name} nie powiodło się: {error}",
                            "Discovery on {name} failed: {error}"),
    "ev.rssi.back": ("{name}: RSSI połączenia znów dostępne", "{name}: link RSSI available again"),
    "ev.rssi.problem": ("{name}: RSSI połączenia — {status} ({adapter}) {detail}",
                        "{name}: link RSSI — {status} ({adapter}) {detail}"),
    "ev.mgmt.disconnected": ("{adapter}: jądro zgłasza rozłączenie {address} — {reason}",
                             "{adapter}: kernel reports {address} disconnected — {reason}"),
    "ev.mgmt.ctrlerror": ("{adapter}: błąd kontrolera, kod {code}", "{adapter}: controller error, code {code}"),
    "ev.mgmt.index.removed": ("{adapter}: indeks usunięty z jądra", "{adapter}: index removed by the kernel"),
    "ev.mgmt.index.added": ("{adapter}: indeks dodany w jądrze", "{adapter}: index added by the kernel"),
    "ev.kernel.earlier": ("[wcześniej w tym rozruchu]", "[earlier this boot]"),
    "ev.usb.found": ("{name} — {usb}, port USB {port} ({mode})", "{name} — {usb}, USB port {port} ({mode})"),
    "ev.usb.added": ("Podłączono {name} — {usb}, port USB {port} ({mode})",
                     "Plugged in: {name} — {usb}, USB port {port} ({mode})"),
    "ev.usb.removed": ("Odłączono z USB: {name} — {usb}, port {port}",
                       "Removed from USB: {name} — {usb}, port {port}"),
    "ev.usb.mode": ("{name} ({where}): {old} → {new} — {mode}", "{name} ({where}): {old} → {new} — {mode}"),
    "ev.usb.moved": ("{name}: odbiornik przeniesiony {old} → {new}", "{name}: receiver moved {old} → {new}"),
    "ev.input.denied": ("{name}: brak prawa odczytu {node} — aktywność wejścia niedostępna",
                        "{name}: no read access to {node} — input activity unavailable"),
    "reason.usb.gone": ("urządzenie zniknęło z USB", "the device left the USB bus"),
    "reason.usb.idle": ("odbiornik bez sparowanego pada ({usb})", "receiver has no pad linked ({usb})"),
    "usb.mode.active": ("pad połączony", "pad linked"),
    "usb.mode.idle": ("bez pada (IDLE)", "no pad linked (IDLE)"),
    "usb.mode.unknown": ("stan łącza nieznany", "link state unknown"),
    "ev.exported": ("Eksport: {count} próbek → {path}", "Export: {count} samples → {path}"),

    "detail.adv": ("RSSI z reklam (wyszukiwanie), nie z połączenia",
                   "advertising RSSI (discovery), not the link"),
    "detail.nobackend": ("brak źródła RSSI połączenia", "no link RSSI source"),
    "detail.rssi.invalid": ("kontroler nie podał RSSI (127)", "the controller gave no RSSI (127)"),

    # --- diagnostics -------------------------------------------------------------
    "diag.nobus": ("Brak systemowego D-Bus ({error}).", "No system D-Bus ({error})."),
    "diag.nobluez": (
        "bluetoothd nie działa — sprawdź: systemctl status bluetooth",
        "bluetoothd is not running — check: systemctl status bluetooth",
    ),
    "diag.bluez.error": ("BlueZ zwrócił błąd: {error}", "BlueZ returned an error: {error}"),
    "diag.noadapter": (
        "Brak adaptera Bluetooth. Sprawdź lsusb, rfkill list i dmesg (btusb, firmware rtl_bt).",
        "No Bluetooth adapter. Check lsusb, rfkill list and the kernel log (btusb, rtl_bt firmware).",
    ),
    "diag.rfkill": ("{name} zablokowany przez rfkill — rfkill unblock bluetooth",
                    "{name} is blocked by rfkill — rfkill unblock bluetooth"),
    "diag.poweroff": ("{name} jest wyłączony — bluetoothctl power on",
                      "{name} is powered off — bluetoothctl power on"),
    "diag.discovering": (
        "{name}: trwa wyszukiwanie. Skanowanie LE obciąża kontroler; na RTL8761BU "
        "timeouty 0x2042 (LE Set Extended Scan Enable) kończyły się resetem USB.",
        "{name}: discovery is running. LE scanning loads the controller; on RTL8761BU "
        "0x2042 (LE Set Extended Scan Enable) timeouts ended in a USB reset.",
    ),
    "diag.dupaddr": ("{count} adaptery mają ten sam adres {address} (klony Realteka?) — "
                     "rozróżniam je po porcie USB.",
                     "{count} adapters share the address {address} (Realtek clones?) — "
                     "telling them apart by USB port."),
    "diag.remap": ("Adapter {address} zmienił indeks {old} → {new} o {when} (razy: {count}); "
                   "monitorowanie wznowione automatycznie.",
                   "Adapter {address} changed index {old} → {new} at {when} ({count}×); "
                   "monitoring resumed automatically."),
    "diag.adapter.missing": ("Adapter {name} {address} zniknął — czekam na powrót.",
                             "Adapter {name} {address} is gone — waiting for it to return."),
    "diag.kernel": ("{name}: w dzienniku jądra {timeouts}× timeout komendy HCI, {resets}× reset USB, "
                    "{hw}× błąd sprzętowy.",
                    "{name}: kernel log shows {timeouts}× HCI command timeout, {resets}× USB reset, "
                    "{hw}× hardware error."),
    "diag.scan.timeouts": (
        "{name}: timeouty dotyczyły komend skanowania LE — unikaj wyszukiwania przy podłączonych padach.",
        "{name}: the timeouts hit LE scan commands — avoid discovery while pads are connected.",
    ),
    "diag.nobackend": ("Brak RSSI połączenia: {reason}", "No link RSSI: {reason}"),
    "diag.nobackend.off": ("wyłączone", "disabled"),
    "diag.permission": (
        "Odmowa dostępu do mgmt (0x14). Potrzebny helper przez sudo — patrz docs/permissions.md.",
        "mgmt access denied (0x14). The helper must run through sudo — see docs/permissions.md.",
    ),
    "diag.invalid.index": (
        "Jądro nie zna indeksu adaptera (0x11) — adapter właśnie się przerejestrowuje?",
        "The kernel does not know the adapter index (0x11) — is the adapter re-registering?",
    ),
    "diag.kernlog": ("Bez zdarzeń jądra: {reason}", "No kernel events: {reason}"),
    "diag.timeouts": ("{name}: {count}× zapytanie o RSSI przekroczyło limit czasu.",
                      "{name}: {count}× the RSSI query timed out."),
    "diag.availability": ("{name}: RSSI tylko w {value}% próbek przy połączeniu.",
                          "{name}: RSSI in only {value}% of samples while connected."),
    "diag.bredr": (
        "BR/EDR: HCI_Read_RSSI podaje różnicę względem „złotego zakresu”, nie dBm.",
        "BR/EDR: HCI_Read_RSSI reports a difference from the golden receive range, not dBm.",
    ),
    "diag.txzero": (
        "Kontroler zgłasza moc TX 0 / max 0 dBm — wartość pokazana jako zgłoszona, niezweryfikowana.",
        "The controller reports TX power 0 / max 0 dBm — shown as reported, not verified.",
    ),
    "diag.root": ("Interfejs działa jako root — wystarczy helper przez sudo.",
                  "The interface runs as root — the sudo helper would suffice."),
    "diag.logfile": ("Nie mogę zapisać dziennika zdarzeń: {error}", "Cannot write the event log: {error}"),
    "diag.usb.ambiguous": ("{name}: 2dc8:310a bez wcześniejszego IDLE — odbiornik albo kabel, nie wiadomo.",
                           "{name}: 2dc8:310a without an earlier IDLE — receiver or cable, cannot tell."),
    "diag.input.denied": ("Brak odczytu {node}: aktywność wejścia niedostępna (patrz docs/permissions.md).",
                          "Cannot read {node}: input activity unavailable (see docs/permissions.md)."),
    "diag.usb.quiet": ("{name}: brak wejścia od ponad minuty — pad bezczynny; to nie oznacza zerwania łącza.",
                       "{name}: no input for over a minute — the pad is idle; that is not a link failure."),
    "diag.count": ("{count}", "{count}"),
    "diag.more": ("więcej: l", "more: l"),

    # --- header and footer --------------------------------------------------------
    "err.small": ("Okno terminala za małe (min. {w}x{h})", "Terminal window too small (min. {w}x{h})"),
    "header.noadapter": ("brak adaptera", "no adapter"),
    "header.scanning": ("[skan]", "[scan]"),
    "header.nolink": ("brak", "none"),
    "state.paused": ("PAUZA", "PAUSED"),
    "footer.picker": (
        "Spacja/1-9 wybór · Enter monitoruj · / szukaj · f filtr · o sortuj · d wyszukiwanie · c wyczyść · h pomoc · q wyjście",
        "Space/1-9 select · Enter monitor · / search · f filter · o sort · d discovery · c clear · h help · q quit",
    ),
    "footer.dash": (
        "Tab panel · Enter szczegóły · s wybór · p pauza · l dziennik · e CSV · j JSON · r zeruj · +/- interwał · c kompakt · h pomoc · q wyjście",
        "Tab panel · Enter details · s select · p pause · l log · e CSV · j JSON · r reset · +/- interval · c compact · h help · q quit",
    ),
    "footer.detail": ("↑↓ przewiń · Tab następne · Esc wróć · l dziennik · e CSV · j JSON · q wyjście",
                      "↑↓ scroll · Tab next · Esc back · l log · e CSV · j JSON · q quit"),
    "footer.log": ("↑↓ PgUp PgDn przewiń · f filtr · Esc wróć · e CSV · j JSON · q wyjście",
                   "↑↓ PgUp PgDn scroll · f filter · Esc back · e CSV · j JSON · q quit"),
    "footer.search": ("Enter zatwierdź · Esc anuluj", "Enter accept · Esc cancel"),

    # --- flash messages -------------------------------------------------------------
    "flash.select.none": ("Wybierz 1–4 urządzenia spacją.", "Select 1–4 devices with Space."),
    "flash.select.max": ("Najwyżej {count} urządzenia naraz.", "At most {count} devices at once."),
    "flash.noadapter": ("Brak adaptera.", "No adapter."),
    "flash.discovery.on": (
        "Wyszukiwanie na {name} włączone. Uwaga: skan LE potrafi obciążyć RTL8761BU — wyłącz klawiszem d.",
        "Discovery on {name} started. Note: LE scanning can strain RTL8761BU — stop it with d.",
    ),
    "flash.discovery.off": ("Wyszukiwanie na {name} wyłączone.", "Discovery on {name} stopped."),
    "flash.discovery.failed": ("Wyszukiwanie: {error}", "Discovery: {error}"),
    "flash.reset": ("Statystyki wyzerowane.", "Statistics reset."),
    "flash.export.nothing": ("Nic do eksportu — najpierw monitoruj urządzenie.",
                             "Nothing to export — monitor a device first."),
    "flash.export.failed": ("Eksport nieudany: {error}", "Export failed: {error}"),
    "flash.exported": ("Zapisano {count} próbek: {path}", "Saved {count} samples: {path}"),

    # --- picker ----------------------------------------------------------------------
    "picker.title": ("Kontrolery · Bluetooth {bt} · USB {usb} · wybrane {selected}/{max}",
                     "Controllers · Bluetooth {bt} · USB {usb} · selected {selected}/{max}"),
    "picker.subtitle": ("filtr: {filter} · sort: {sort} · {count}",
                        "filter: {filter} · sort: {sort} · {count}"),
    "picker.empty": ("Brak urządzeń dla tego filtra.", "No devices for this filter."),
    "picker.empty.hint": (
        "Sparuj urządzenie (bluetoothctl) albo włącz wyszukiwanie klawiszem d.",
        "Pair a device (bluetoothctl) or start discovery with d.",
    ),
    "picker.nobluez": ("Brak danych z BlueZ — szczegóły w nagłówku.", "No data from BlueZ — see the header."),
    "picker.legend": ("~RSSI = z reklam (wyszukiwanie), bez ~ = z połączenia",
                      "~RSSI = advertising (discovery), no ~ = live link"),
    "filter.all": ("wszystkie", "all"),
    "filter.connected": ("połączone", "connected"),
    "filter.paired": ("sparowane", "paired"),
    "filter.discovered": ("wykryte", "discovered"),
    "sort.name": ("nazwa", "name"),
    "sort.rssi": ("RSSI", "RSSI"),
    "sort.state": ("stan", "state"),
    "sort.address": ("adres", "address"),
    "sort.adapter": ("adapter", "adapter"),
    "col.num": ("#", "#"),
    "col.sel": ("", ""),
    "col.name": ("Nazwa", "Name"),
    "col.mac": ("MAC / USB", "MAC / USB"),
    "col.type": ("Typ", "Type"),
    "col.adapter": ("Przez", "Via"),
    "col.conn": ("Poł.", "Conn"),
    "col.paired": ("Spar", "Pair"),
    "col.bonded": ("Bond", "Bond"),
    "col.trusted": ("Zauf", "Trust"),
    "col.rssi": ("RSSI", "RSSI"),
    "col.battery": ("Bat.", "Batt"),

    "kind.gamepad": ("gamepad", "gamepad"),
    "kind.joystick": ("joystick", "joystick"),
    "kind.keyboard": ("klawiatura", "keyboard"),
    "kind.mouse": ("mysz", "mouse"),
    "kind.tablet": ("tablet", "tablet"),
    "kind.headset": ("zestaw słuch.", "headset"),
    "kind.headphones": ("słuchawki", "headphones"),
    "kind.audio": ("audio", "audio"),
    "kind.speaker": ("głośnik", "speaker"),
    "kind.phone": ("telefon", "phone"),
    "kind.computer": ("komputer", "computer"),
    "kind.watch": ("zegarek", "watch"),
    "kind.hid": ("HID", "HID"),
    "kind.peripheral": ("peryferium", "peripheral"),
    "kind.?": ("?", "?"),

    # --- WCM: header, USB panels ----------------------------------------------
    "header.devices": ("Urządzenia: {count}", "Devices: {count}"),
    "transport.usb-2.4": ("USB 2,4 GHz", "USB 2.4 GHz"),
    "transport.usb-2.4?": ("USB (2,4 GHz/kabel)", "USB (2.4 GHz/cable)"),
    "transport.usb": ("USB", "USB"),
    "filter.bluetooth": ("Bluetooth", "Bluetooth"),
    "filter.usb": ("USB", "USB"),
    "state.linked": ("pad połączony", "pad linked"),
    "state.idle": ("BEZ PADA", "NO PAD LINKED"),
    "state.usbgone": ("BRAK NA USB", "GONE FROM USB"),
    "panel.link": ("Łącze", "Link"),
    "usb.link.linked": ("pad połączony z odbiornikiem (z ID produktu USB)",
                        "pad linked to the receiver (from the USB product ID)"),
    "usb.link.idle": ("odbiornik bez pada — pad wyłączony albo poza zasięgiem (IDLE)",
                      "receiver without a pad — pad off or out of range (IDLE)"),
    "usb.link.gone": ("urządzenie zniknęło z magistrali USB", "the device left the USB bus"),
    "panel.rssi.unsupported": ("n/d — odbiornik nie podaje", "n/a — not reported by the receiver"),
    "panel.battery.usb.na": ("n/d", "n/a"),
    "panel.input": ("Wejście", "Input"),
    "panel.activity.title": ("aktywność wejścia, zdarzeń/s — użycie pada, nie siła sygnału",
                             "input activity, events/s — pad use, not signal strength"),
    "panel.events": ("Zdarzenia urządzeń", "Device events"),
    "panel.events.more": ("l: pełny dziennik", "l: full log"),
    "stat.total": ("razem", "total"),
    "stat.lastinput": ("ostatnie wejście", "last input"),
    "stat.pidchanges": ("zmiany PID", "PID changes"),
    "activity.ok": ("brak danych", "no data yet"),
    "activity.permission": ("brak odczytu {node} (uprawnienia)", "cannot read {node} (permissions)"),
    "activity.no-node": ("brak węzła wejścia (np. odbiornik bez pada)", "no input node (e.g. receiver without a pad)"),
    "activity.gone": ("węzeł wejścia zniknął", "the input node went away"),
    "activity.error": ("błąd odczytu {node}", "error reading {node}"),

    # --- dashboard ----------------------------------------------------------------------
    "panel.diag": ("Diagnostyka", "Diagnostics"),
    "state.connected": ("połączony", "connected"),
    "state.disconnected": ("ROZŁĄCZONY", "DISCONNECTED"),
    "state.noadapter": ("BRAK ADAPTERA", "ADAPTER GONE"),
    "state.absent": ("BRAK W BLUEZ", "NOT IN BLUEZ"),
    "panel.txpower": ("Moc TX", "TX power"),
    "panel.tx": ("{value} dBm (zgłoszona)", "{value} dBm (reported)"),
    "panel.tx.na": ("n/d", "n/a"),
    "panel.battery": ("Bateria", "Battery"),
    "panel.battery.na": ("n/d", "n/a"),
    "panel.rssi.down": ("— brak połączenia", "— no link"),
    "panel.rssi.none": ("brak odczytu", "no reading"),
    "band.good": ("≈silny*", "≈strong*"),
    "band.fair": ("≈średni*", "≈fair*"),
    "band.weak": ("≈słaby*", "≈weak*"),
    "band.poor": ("≈b. słaby*", "≈very weak*"),
    "stat.avail": ("RSSI", "RSSI"),
    "stat.drops": ("rozł.", "drops"),
    "stat.reconn": ("pon.", "reconn"),
    "stat.down": ("przerwy", "down"),
    "stat.timeouts": ("timeouty", "timeouts"),
    "battery.source.kernel": ("jądro (HID power_supply)", "kernel (HID power_supply)"),

    # --- details ---------------------------------------------------------------------------
    "detail.title": ("Szczegóły · {name}", "Details · {name}"),
    "detail.subtitle": ("Esc wróć", "Esc back"),
    "detail.none": ("Brak urządzenia.", "No device."),
    "na.notexposed": ("nieudostępniane", "not exposed"),
    "detail.identity": ("Tożsamość", "Identity"),
    "detail.name": ("Nazwa", "Name"),
    "detail.alias": ("Alias", "Alias"),
    "detail.address": ("Adres", "Address"),
    "detail.addrtype": ("Typ adresu", "Address type"),
    "detail.bearer": ("nośnik", "bearer"),
    "detail.link": ("łącze", "link"),
    "detail.kind": ("Rodzaj", "Kind"),
    "detail.appearance": ("Appearance (GAP)", "Appearance (GAP)"),
    "detail.class": ("Klasa (BR/EDR)", "Class (BR/EDR)"),
    "detail.vidpid": ("Producent:produkt", "Vendor:product"),
    "detail.adapter": ("Adapter", "Adapter"),
    "detail.state": ("Stan", "State"),
    "detail.connected": ("Połączony", "Connected"),
    "detail.paired": ("Sparowany", "Paired"),
    "detail.bonded": ("Powiązany (bonded)", "Bonded"),
    "detail.trusted": ("Zaufany", "Trusted"),
    "detail.never": ("jeszcze nie w tej sesji", "not yet this session"),
    "detail.blocked": ("Zablokowany", "Blocked"),
    "detail.resolved": ("Usługi rozpoznane", "Services resolved"),
    "detail.legacy": ("Parowanie legacy", "Legacy pairing"),
    "detail.since": ("Połączony od", "Connected for"),
    "detail.lastconnect": ("Ostatnie połączenie", "Last connect"),
    "detail.lastdisconnect": ("Ostatnie rozłączenie", "Last disconnect"),
    "detail.link.section": ("Łącze radiowe", "Radio link"),
    "detail.src.conn": ("z połączenia (mgmt)", "live link (mgmt)"),
    "detail.src.adv": ("z reklam", "advertising"),
    "detail.rssi.link": ("RSSI", "RSSI"),
    "detail.bredr.note": (
        "BR/EDR: wartość względem „złotego zakresu” odbiornika (dB), nie bezwzględne dBm.",
        "BR/EDR: relative to the receiver's golden range (dB), not absolute dBm.",
    ),
    "unit.relative": ("dB (wzgl.)", "dB (rel.)"),
    "detail.rssi.adv": ("RSSI z reklam (Device1)", "Advertising RSSI (Device1)"),
    "detail.tx.link": ("Moc TX łącza", "Link TX power"),
    "detail.tx.max": ("Maks. moc TX łącza", "Link max TX power"),
    "detail.tx.reported": ("{value} dBm (zgłoszona przez kontroler, niezweryfikowana)",
                           "{value} dBm (reported by the controller, not verified)"),
    "detail.tx.adv": ("TxPower z reklam", "Advertised TxPower"),
    "detail.conntype": ("Typ połączenia (mgmt)", "Connection type (mgmt)"),
    "detail.query": ("Czas zapytania", "Query time"),
    "detail.query.note": ("czas odpowiedzi jądra, nie opóźnienie łącza",
                          "kernel round trip, not link latency"),
    "detail.cache.note": (
        "Jądro buforuje informacje o połączeniu 1–3 s, więc kolejne próbki mogą się powtarzać.",
        "The kernel caches connection info for 1–3 s, so consecutive samples may repeat.",
    ),
    "detail.battery": ("Bateria", "Battery"),
    "detail.battery.kernel": ("Jądro (HID)", "Kernel (HID)"),
    "detail.stats": ("Statystyki sesji", "Session statistics"),
    "detail.minmax": ("RSSI", "RSSI"),
    "detail.samples": ("Próbki", "Samples"),
    "detail.samples.value": ("{total} razem · {connected} przy połączeniu · {rssi} z RSSI",
                             "{total} total · {connected} while connected · {rssi} with RSSI"),
    "detail.avail": ("Dostępność RSSI", "RSSI availability"),
    "detail.share": ("Czas w połączeniu", "Connected share"),
    "detail.drops": ("Rozłączenia", "Disconnects"),
    "detail.drops.value": ("{drops} rozłączeń · {reconnects} powrotów · przerwy łącznie {down}",
                           "{drops} drops · {reconnects} reconnects · {down} down in total"),
    "detail.timeouts": ("Timeouty zapytań", "Query timeouts"),
    "detail.connected.total": ("Łącznie połączony", "Connected in total"),
    "detail.monitoring": ("Monitorowany od", "Monitored since"),
    "detail.uuids": ("UUID usług ({count})", "Service UUIDs ({count})"),
    "detail.gatt": ("Usługi GATT ({count})", "GATT services ({count})"),
    "detail.gatt.none": ("brak obiektów GattService1 (BR/EDR albo usługi nierozpoznane)",
                         "no GattService1 objects (BR/EDR, or services not resolved)"),
    "detail.changes": ("Zmiany parametrów", "Property changes"),
    "detail.changes.none": ("brak zmian od początku monitorowania", "no changes since monitoring began"),
    "detail.events": ("Zdarzenia", "Events"),
    "detail.events.none": ("brak zdarzeń", "no events"),

    "detail.usb.place": ("Miejsce na magistrali", "Place on the bus"),
    "detail.usb.strings": ("Producent / produkt", "Manufacturer / product"),
    "detail.usb.serial": ("Numer seryjny", "Serial number"),
    "detail.usb.speed": ("Prędkość USB", "USB speed"),
    "detail.usb.profile": ("Profil odbiornika", "Receiver profile"),
    "detail.usb.noprofile": ("brak — zwykły pad USB", "none — a plain USB gamepad"),
    "detail.usb.transport": ("Technologia", "Technology"),
    "detail.usb.ambiguous": (
        "2dc8:310a to zarówno odbiornik z padem, jak i pad na kablu; pewność daje dopiero stan IDLE (301c) "
        "na tym porcie.",
        "2dc8:310a is both a receiver with a pad and the pad on a cable; only seeing IDLE (301c) on this "
        "port settles it.",
    ),
    "detail.usb.mode": ("Tryb odbiornika", "Receiver mode"),
    "detail.usb.metrics": ("Metryki", "Metrics"),
    "detail.usb.rssi": ("nieobsługiwane — odbiornik 2,4 GHz nie podaje siły sygnału (stock firmware)",
                        "unsupported — the 2.4 GHz receiver reports no signal strength (stock firmware)"),
    "detail.usb.battery": ("niedostępna — brak power_supply; stock firmware 2C nie przesyła baterii przez 2,4 GHz",
                           "not available — no power_supply; stock 2C firmware sends no battery over 2.4 GHz"),
    "detail.usb.input": ("{total} zdarzeń razem · {node}", "{total} events in total · {node}"),
    "detail.usb.inactive.note": (
        "Brak wejścia to nie zerwane łącze: leżący pad jest połączony i milczy.",
        "No input is not a broken link: a pad lying still is connected and silent.",
    ),
    "detail.usb.interfaces": ("Interfejsy USB ({count})", "USB interfaces ({count})"),
    "detail.usb.nodriver": ("bez sterownika", "no driver"),
    "detail.usb.hidhint": ("Badanie interfejsu producenta: bc250-wcm hid descriptor {sel}",
                           "Vendor interface research: bc250-wcm hid descriptor {sel}"),

    # --- log --------------------------------------------------------------------------------
    "log.title": ("Dziennik zdarzeń", "Event log"),
    "log.subtitle": ("{filter} · {count}", "{filter} · {count}"),
    "log.empty": ("Brak zdarzeń.", "No events."),
    "logfilter.all": ("wszystko", "all"),
    "logfilter.device": ("urządzenia", "devices"),
    "logfilter.adapter": ("adaptery", "adapters"),
    "logfilter.kernel": ("jądro", "kernel"),
    "logfilter.backend": ("mgmt", "mgmt"),
    "cat.adapter": ("adapter", "adapter"),
    "cat.device": ("urządz.", "device"),
    "cat.kernel": ("jądro", "kernel"),
    "cat.backend": ("mgmt", "mgmt"),
    "cat.app": ("monitor", "monitor"),
    "cat.usb": ("USB", "USB"),
    "logfilter.usb": ("USB", "USB"),

    # --- help ----------------------------------------------------------------------------------
    "help.title": ("Pomoc", "Help"),
    "help.section.picker": ("Wybór urządzeń", "Device picker"),
    "help.picker.move": ("ruch kursora", "move the cursor"),
    "help.picker.toggle": ("zaznacz / odznacz (najwyżej 4)", "select / deselect (up to 4)"),
    "help.picker.number": ("zaznacz wiersz o tym numerze", "select the row with that number"),
    "help.picker.start": ("monitoruj zaznaczone", "monitor the selection"),
    "help.picker.search": ("szukaj po nazwie, adresie, typie", "search by name, address, type"),
    "help.picker.filter": ("filtr: wszystkie/połączone/sparowane/wykryte/Bluetooth/USB · sortowanie",
                           "filter: all/connected/paired/discovered/Bluetooth/USB · sort order"),
    "help.picker.discovery": ("włącz/wyłącz wyszukiwanie na adapterze", "start/stop discovery on the adapter"),
    "help.picker.clear": ("wyczyść zaznaczenie", "clear the selection"),
    "help.section.dash": ("Pulpit", "Dashboard"),
    "help.dash.focus": ("wybór panelu", "choose a panel"),
    "help.dash.detail": ("szczegóły urządzenia", "device details"),
    "help.dash.select": ("zmień wybór urządzeń", "change the device selection"),
    "help.dash.pause": ("pauza próbkowania", "pause sampling"),
    "help.dash.log": ("dziennik zdarzeń", "event log"),
    "help.dash.export": ("eksport CSV / JSON", "export CSV / JSON"),
    "help.dash.reset": ("zeruj statystyki", "reset statistics"),
    "help.dash.interval": ("zmień interwał próbkowania", "change the sampling interval"),
    "help.dash.compact": ("tryb kompaktowy", "compact mode"),
    "help.dash.graph": ("wykres Braille / ASCII", "Braille / ASCII graph"),
    "help.section.common": ("Wszędzie", "Everywhere"),
    "help.back": ("wróć", "back"),
    "help.help": ("ta pomoc", "this help"),
    "help.quit": ("wyjście", "quit"),
    "help.note": (
        "* pasma ≈ to progi z konfiguracji, a nie pomiar jakości łącza.",
        "* the ≈ bands are thresholds from the configuration, not a link quality measurement.",
    ),
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


def t_or(key: str, default: str) -> str:
    """Like t(), but an unknown key gives `default`."""
    return t(key) if key in _MESSAGES else default


def keys() -> set[str]:
    return set(_MESSAGES)

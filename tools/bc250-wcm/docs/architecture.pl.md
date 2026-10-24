# bc250-wcm (Wireless Controller Monitor) — architektura i źródła danych

[English](architecture.md) · **Polski**

## Moduły

```
bc250wcm/
  dbus.py         minimalny klient D-Bus na protokole (uwierzytelnienie EXTERNAL, wywołania, sygnały)
  bluez.py        pamięć obiektów BlueZ: GetManagedObjects + InterfacesAdded/Removed + PropertiesChanged
  model.py        AdapterInfo / DeviceInfo ze słowników ObjectManagera, tożsamość adaptera, sysfs
  mgmt.py         Management API jądra: kodowanie, klient — i sam uprzywilejowany helper
  telemetry.py    źródła RSSI łącza: helper, direct, btmgmt, none
  engine.py       cykl życia adapterów, różnice migawek -> zdarzenia, wątki próbkujące urządzenia
  history.py      próbki, statystyki Welforda, rozliczanie połączeń, dziennik zdarzeń
  kernlog.py      śledzenie journalctl -k i klasyfikacja (timeouty, resety, firmware)
  diagnostics.py  wnioski dla paska Diagnostyka
  export.py       eksport CSV / JSON, zapis CSV na bieżąco
  config.py       konfiguracja INI w $XDG_CONFIG_HOME
  usb.py          backend USB: skan sysfs, profile odbiorników, tożsamość fizyczna, źródło uevent
  inputs.py       aktywność wejścia: liczenie zdarzeń evdev (tylko odczyt, bez przejmowania)
  hid.py          deskryptory HID, lista hidraw, pasywny zapis i analiza
  hidcli.py       `bc250-wcm hid …`
  fakesys.py      miniaturowe drzewo sysfs z kontrolerami USB (demo i testy)
  tui.py          curses: lista urządzeń, pulpit, szczegóły, dziennik, pomoc
  widgets.py      paleta (256 / 8 / brak), ramki, paski, wykresy liniowe z przerwami
  cli.py          opcje, wybór źródła RSSI (sudo), --list / --json / --headless
  demo.py         symulowany stos, który daje dane w kształcie prawdziwego BlueZ
  i18n.py         teksty angielskie i polskie
```

Podział zgodny z wymaganiami: backend Bluetooth (`dbus`, `bluez`, `mgmt`),
wykrywanie i cykl życia adapterów (`model`, `engine`), wykrywanie urządzeń
(`bluez`, `model`), telemetria połączeń (`telemetry`, `mgmt`), próbkowanie
i historia (`engine`, `history`), statystyki (`history`), TUI (`tui`, `widgets`),
konfiguracja (`config`), eksport (`export`), diagnostyka (`diagnostics`,
`kernlog`).

## Backendy i wspólny model

Interfejs nigdy nie rozmawia z backendem; czyta silnik. Silnik ma jeden
**Track** na monitorowany kontroler, niezależnie od technologii:

| | Backend Bluetooth | Backend USB |
|---|---|---|
| wykrywanie | pamięć obiektów BlueZ (`bluez.py`) | skan sysfs + uevent jądra (`usb.py`) |
| tożsamość | tożsamość adaptera + adres urządzenia | miejsce na magistrali (`usb:3-5`) albo prawdziwy numer seryjny |
| klucz Tracka | `<tożsamość adaptera>/<adres>` | `usb:<id fizyczne>` |
| połączenie | `Device1.Connected` | tryb odbiornika z ID produktu (active / idle) |
| próbki | `ok` / `no-rssi` / `timeout` / `disconnected` / `absent` / `no-adapter` | `linked` / `link-idle` / `usb-gone` |
| RSSI | mgmt *Get Connection Information* | nigdy (`rssi_capable = False`) |
| aktywność | węzeł evdev znaleziony po `uniq` = adres | węzeł evdev interfejsu joysticka |

**Target** (`engine.py`) to to, co lista urządzeń przekazuje silnikowi: klucz,
nazwa, rodzaj, adres i — dla USB — profil odbiornika. Każdy Track ma własny
wątek próbkujący, więc wolne zapytanie Bluetooth nigdy nie opóźnia panelu USB
i odwrotnie. Próbki mają jeden kształt (`history.Sample`): `rssi`, `activity`,
`battery`, `usb_id` — brak wartości to `None`, nigdy 0.

Trzecia technologia dodałaby źródło (wykrywanie, tożsamość, stan), własne stany
próbek i panel; wybór, historia, eksport i dziennik zdarzeń są wspólne.

## Dlaczego bez dbus-python i Textual

Zestaw działa na obrazach atomowych i korzysta tylko z biblioteki standardowej,
żeby niczego nie trzeba było nakładać przez `rpm-ostree` ani budować przez
`pip`. Podzbiór D-Bus potrzebny dla BlueZ — uwierzytelnienie, wywołanie,
subskrypcja sygnałów — to około 400 linii na `socket`. Interfejs używa
`curses`, tak jak `bc250-mon`, więc oba narzędzia wyglądają i zachowują się
podobnie.

## Wątki

| Wątek | Co robi |
|---|---|
| `dbus-reader` | czyta gniazdo magistrali, kończy wywołania, nakłada sygnały BlueZ na pamięć obiektów |
| `bluez` | łączy / łączy ponownie, pełna synchronizacja na starcie, gdy bluetoothd wraca, i co 30 s |
| `engine` | przy każdej zmianie: różnica migawek, zdarzenia, stan połączeń |
| `probe-…` (po jednym na urządzenie) | jedna próbka na interwał; zapytanie, które wisi do limitu czasu, opóźnia tylko to urządzenie |
| `helper-out`, `helper-err` | odpowiedzi i zdarzenia helpera |
| `kernlog` | czyta `journalctl -k -f -o json` |
| główny | curses: przerysowanie po nowych danych, po klawiszu i dwa razy na sekundę |

Helper też obsługuje każde zapytanie w osobnym wątku, więc cztery pady to cztery
niezależne zapytania aż do jądra.

## RSSI łącza: dlaczego nie Device1.RSSI

`org.bluez.Device1.RSSI` pochodzi z **raportów reklam**, a BlueZ odświeża je
tylko podczas wyszukiwania. Połączony gamepad się nie reklamuje, a
wyszukiwanie to właśnie to, czego nie powinno się obok niego uruchamiać (patrz
uwaga o 0x2042 niżej). Monitor pyta więc jądro o RSSI **łącza** poleceniem
Management API *Get Connection Information* (0x0031): jądro wysyła
`HCI_Read_RSSI`, a za pierwszym razem także `HCI_Read_Transmit_Power_Level` na
uchwycie połączenia.

Co znaczy odpowiedź:

- **LE**: RSSI w dBm.
- **BR/EDR**: `HCI_Read_RSSI` podaje różnicę względem *złotego zakresu mocy
  odbieranej* kontrolera w dB — 0 znaczy „w zakresie”. To nie jest poziom
  bezwzględny i monitor opisuje go jako `dB (wzgl.)`.
- **127** to HCI-owe „brak wartości” (`HCI_RSSI_INVALID`,
  `HCI_TX_POWER_INVALID`). Pokazywane jako *n/d*, nigdy jako liczba.
- **Moc TX** to to, co kontroler zgłasza dla łącza. 0 może być prawdziwym
  0 dBm albo kontrolerem, który wpisuje w to pole zero; z hosta nie da się tego
  rozróżnić, więc interfejs pisze *zgłoszona, niezweryfikowana*.
- Jądro **buforuje** odpowiedź na losowe 1–3 s (`conn_info_min_age` /
  `conn_info_max_age` w debugfs). Przy interwale 1 s część kolejnych próbek to
  ten sam odczyt — to normalne.

RSSI z reklam nadal jest używane, wyraźnie opisane, gdy nie ma źródła RSSI
łącza, a adapter akurat wyszukuje.

## Typy adresów

mgmt potrzebuje typu adresu: 0 BR/EDR, 1 LE publiczny, 2 LE losowy.
`AddressType` w BlueZ mówi tylko `public` albo `random`, więc:

1. typ, który już zadziałał dla tego połączenia, jest używany ponownie;
2. BlueZ 5.80+ udostępnia `org.bluez.Bearer.LE1` / `Bearer.BREDR1` z własnym
   `Connected` — to rozstrzyga;
3. `random` to z definicji LE (pady 8BitDo);
4. w przeciwnym razie próbowane są oba, najpierw BR/EDR dla urządzenia z Class,
   najpierw LE publiczny dla urządzenia z Appearance. „Not Connected” przechodzi
   do następnego typu.

Zapamiętany typ jest kasowany przy rozłączeniu — kolejne łącze może użyć
drugiego nośnika.

## Tożsamość adaptera i przemapowanie hci0 → hci1

Adapter jest rozpoznawany po adresie kontrolera, nie po `hciN`. Gdy dwa obecne
adaptery mają ten sam adres (tanie dongle Realteka tak potrafią), dopisywany
jest fizyczny port USB z `/sys/class/bluetooth/hciN/device`:
`00:E0:4C:01:02:03@1-4`.

Monitorowane urządzenie ma klucz *tożsamość adaptera + adres urządzenia*. Przy
każdej próbce silnik ponownie odnajduje adapter: ta sama tożsamość → ten sam
adres, jeśli jest jedyny → ten sam vendor:product USB na tym samym porcie
(adapter, który wrócił z innym adresem). Gdy `hci0` znika, a `hci1` pojawia się
z tym samym kontrolerem, panel urządzenia po prostu trwa dalej: przerwa jest
zapisana jako próbki `no-adapter` / `disconnected`, dziennik mówi
`Adapter … zarejestrował się ponownie: hci0 → hci1`, a liczniki pokazują jedno
rozłączenie i jeden powrót.

Nic nie resetuje adaptera ani nie restartuje usług. To pozostaje decyzją
użytkownika.

## Zdarzenia jądra

`journalctl -k -f -o json -n 400` daje zaległości z bieżącego rozruchu
(oznaczone *wcześniej w tym rozruchu*), a potem linie na żywo. Rozpoznawane:

| Linia | Znaczenie |
|---|---|
| `hciN: command 0xXXXX tx timeout` | kontroler nie odpowiedział na komendę; opcode nazwany (0x2042 = LE Set Extended Scan Enable) |
| `hciN: Opcode 0xXXXX failed: -110` | komenda nieudana; errno nazwane (-110 = ETIMEDOUT) |
| `hciN: Resetting usb device` | btusb resetuje dongla; adapter zaraz zniknie |
| `hciN: Hardware error 0x..` | błąd sprzętowy zgłoszony przez kontroler |
| `hciN: RTL: …`, linie firmware'u | ładowanie firmware'u |
| `usb X-Y: USB disconnect` | tylko gdy X-Y to port znanego adaptera |

Liczniki na `hciN` zasilają pasek Diagnostyka; timeouty komend skanowania
dodają wskazówkę, by nie włączać wyszukiwania. Bez roota czytelne dla członków
`wheel`, `adm` albo `systemd-journal` (domyślny użytkownik Bazzite jest
w `wheel`).

## Próbki i statystyki

Każdy krok próbkowania daje jedną próbkę ze stanem:

| Stan | Znaczenie | RSSI |
|---|---|---|
| `ok` | połączony, RSSI odczytane | wartość |
| `no-rssi` | połączony, bez wartości (brak źródła, odmowa, 127) | brak |
| `timeout` | połączony, zapytanie przekroczyło limit czasu | brak |
| `disconnected` | znany BlueZ, niepołączony | brak |
| `absent` | BlueZ już nie zna urządzenia | brak |
| `no-adapter` | adapter urządzenia zniknął | brak |

Min / max / średnia / odchylenie standardowe (Welford) liczone są wyłącznie
z prawdziwych odczytów. *Dostępność RSSI* = próbki z wartością ÷ próbki przy
połączeniu. *Czas w połączeniu* = próbki przy połączeniu ÷ wszystkie próbki.
Rozłączenie liczone jest przy przejściu z połączonego do niepołączonego, powrót
— przy powrocie po rozłączeniu, a czas przerwy — pomiędzy nimi.

Oś x wykresu to **czas**: przedziały o długości interwału, kończące się teraz.
Przedział bez próbki — pauza, zawieszone zapytanie — jest pusty, a przedział,
w którym łącze leżało, dostaje czerwony znacznik na osi.

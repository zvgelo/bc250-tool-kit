# bc250-wcm — odbiorniki USB 2,4 GHz

[English](usb-receivers.md) · **Polski**

## Obsługiwane odbiorniki

| Profil | Urządzenie | ID USB | Weryfikacja |
|---|---|---|---|
| `8bitdo-ultimate-2c-2g4` | 8BitDo Ultimate 2C Wireless, oryginalny odbiornik 2,4 GHz | `2dc8:310a` pad połączony (XInput), `2dc8:301c` bez pada (IDLE) albo pad w trybie DInput | na wzorcowej BC-250 (Bazzite); znaczenie PID zgodne z badaniami wymienionymi niżej |

Każde inne urządzenie USB z węzłem wejścia joysticka (albo sterownikiem pada,
np. `xpad`) też jest pokazywane — jako zwykły kontroler **USB**: z punktu widzenia
hosta pad na kablu i odbiornik wyglądają tak samo, więc nic bez profilu nie jest
nazywane „2,4 GHz”.

### Odbiornik 8BitDo Ultimate 2C

Z połączonym padem (`2dc8:310a`, bcdDevice 1.14) ma trzy interfejsy:

| Interfejs | Sterownik | Funkcja |
|---|---|---|
| 0 | `xpad` | pad zgodny z Xbox 360 (evdev `eventN`, `uaccess`) |
| 1 | `usbhid` | klawiatura / mysz |
| 2 | `usbhid` | HID producenta, strona `0xFF7A`: raport wejściowy `0x02` (63 + 1 bajty), wyjściowy `0x81` (63 bajty) |

Bez pada (`2dc8:301c`, nazwa produktu `IDLE`, bcdDevice 2.00) to pojedynczy
interfejs HID producenta.

**`2dc8:301c` to także tryb DInput.** Po przełączeniu Home + B (firmware pada
1.06 + odbiornika 1.00; nowszy zostaje w XInput) odbiornik zachowuje `301c`,
ale nazwą produktu jest nazwa kontrolera i pojawia się urządzenie wejściowe
pada (`hid-generic`) — tego trybu potrzebuje [bc250-paddles](../../bc250-paddles/)
dla L4/R4. Samo ID produktu nie odróżnia IDLE od DInput; urządzenie wejściowe
tak, więc WCM liczy `301c` **z** urządzeniem wejściowym pada jako połączony
pad, a `301c` bez niego jako IDLE.

**Ten sam pad na kablu USB to także `2dc8:310a`** z tymi samymi interfejsami.
Nie znaleziono pasywnego sposobu, by je rozróżnić (znany sposób wysyła
zapytanie). WCM opisuje więc urządzenie `310a` jako *„USB (odbiornik 2,4 GHz
albo kabel)”*, dopóki nie zobaczy tego fizycznego urządzenia w stanie IDLE
(`301c`) — kabel nigdy nie przechodzi w IDLE. Tak rozpoznane porty są
zapamiętywane w `~/.local/state/bc250-wcm/receivers`, więc przy kolejnym starcie
opis od razu jest właściwy.

## Co da się zmierzyć — a czego nie

| Metryka | Dostępna | Jak |
|---|---|---|
| obecność odbiornika | tak | sysfs |
| pad połączony z odbiornikiem | tak, wnioskowane | ID produktu `310a` kontra `301c` oraz to, czy `301c` ma urządzenie wejściowe pada (DInput); opisane „z ID produktu USB” |
| wyjęcie / ponowne wyliczenie USB | tak | sysfs + uevent jądra |
| aktywność wejścia | tak, jeśli węzeł event jest czytelny | zdarzenia/s z evdev, tylko odczyt, bez przejmowania |
| RSSI / jakość sygnału | **nie** | odbiornik jej nie udostępnia; `n/d`, nigdy wyliczane z czegoś innego |
| bateria przez 2,4 GHz | **nie** (stock firmware) | `n/d`; jeśli kiedyś pojawi się `power_supply` w jądrze, zostanie użyte |
| wersja firmware'u | nie przez WCM | tylko przez wysłanie zapytania na interfejs 2 — patrz niżej |

**Żadnych zmyślonych metryk.** Częstotliwość raportów interfejsu producenta ani
liczba zdarzeń pada nigdy nie są zamieniane na „sygnał”. Aktywność wejścia jest
pokazywana jako to, czym jest — miarą używania pada — a leżący pad to nie
problem z łączem.

### Stany rozróżniane na panelu odbiornika

| Co się stało | Jak to widać |
|---|---|
| pad wyłączony / poza zasięgiem | odbiornik przechodzi w `301c` bez urządzenia wejściowego: *BEZ PADA*, liczone jako rozłączenie z przyczyną „odbiornik bez sparowanego pada” |
| odbiornik wyjęty | *BRAK NA USB*, rozłączenie z przyczyną „urządzenie zniknęło z USB” |
| pad bezczynny, nikt nie gra | nadal *pad połączony*; wejście 0/s; po minucie informacja, że to nie jest awaria łącza |
| brak prawa do węzła event | stan łącza działa dalej; przy wejściu widać, czemu jest niedostępne |
| odbiornik przełożony do innego portu | ten sam panel podąża za nim (ten sam profil, jedyny), w dzienniku „odbiornik przeniesiony” |

Zdarzenia po stronie Bluetooth (timeouty backendu, resety adaptera, zmiany
`hciN`) są śledzone osobno i nigdy nie dotykają panelu USB.

## Przegląd projektów open source (sprawdzone 2026-10)

| Projekt | Dotyczy | Znaczenie dla odbiornika 2C |
|---|---|---|
| [philipp-schwarz/8bd-u2cw](https://github.com/philipp-schwarz/8bd-u2cw) | **2C**, `2dc8:310a` | sterownik jądra spoza drzewa zastępujący xpad na interfejsie 0 (punkty końcowe XInput); wysyła pakiet inicjujący i wibracje. Bez baterii i danych o łączu. |
| [mhbxyz/8bitdo-ultimate-linux](https://github.com/mhbxyz/8bitdo-ultimate-linux) | 8BitDo po nazwie (2C niewymieniony) | inspekcja evdev/udev, mapowania SDL; bez protokołu hidraw. Jego reguła udev to plik `99-…`, za późno dla `uaccess`. |
| [s8n/ultimatecontroller-rs](https://github.com/s8n/ultimatecontroller-rs) | pierwszy **Ultimate** (`3012`, `6007`, `3011`, `3106`) | konfigurator; odpina xpad, używa raportów `0x81`/`0x02`, odczyt/zapis konfiguracji z CRC. Nie 2C, bez baterii/RSSI. |
| [MiguelSanzBr/8bitdo-ultimate2-linux](https://github.com/MiguelSanzBr/8bitdo-ultimate2-linux) | **Ultimate 2** (`310b`, dongle `6013`) | ta sama ramka raportów; zapytanie o baterię dla dongla *Ultimate 2* (oznaczone jako wnioskowane, ścieżka dongla nieprzetestowana przez autora). Nie 2C. |
| ascendedent/8bitdo-Linux-Software | **2C** | inżynieria wsteczna tylko do odczytu: znaczenie PID, „bezczynny odczyt interfejsu producenta nic nie zwrócił”; zapytania na interfejs 2 zwracają wersję firmware'u odbiornika. |
| jgus-org/8bitdo-2c-re-flake | firmware **2C** | „brak telemetrii baterii przez 2,4 GHz i kabel” na stock firmware; interfejs 2 to kanał flashowania; niektóre polecenia zmieniają adres radiowy i kasują parowanie. |

Ponadto: `xpad.c` w głównej linii jądra zna `2dc8:310a` (XTYPE_XBOX360) i nie ma
kodu baterii; sterownik 8BitDo w SDL nie obsługuje `310a` i nie dokumentuje bajtu
baterii dla 2C. `60-steam-input.rules` Steama daje `uaccess` każdemu węzłowi
hidraw `2dc8`.

**Wniosek.** Nic nie podaje RSSI dla 2C przez 2,4 GHz, a bateria wymaga
zmodyfikowanego firmware'u. Odczyt wersji firmware'u odbiornika jest możliwy
tylko przez zapis zapytania do tego samego interfejsu, który flashuje firmware;
WCM tego nie robi. Jeśli kiedyś wiarygodne źródło potwierdzi bezpieczne zapytanie
tylko-do-odczytu dla 2C, jego miejsce jest w osobnym, jawnie wywoływanym
poleceniu — nigdy w pasywnym monitorowaniu.

## Dodawanie kolejnego odbiornika

1. Zbierz fakty na prawdziwym urządzeniu, niczego nie zapisując:
   ```bash
   bc250-wcm --list
   bc250-wcm hid list
   bc250-wcm hid descriptor <vid:pid:interfejs>
   udevadm info -a -n /dev/hidrawN | head -40
   ```
   Zanotuj ID produktu w każdym stanie (pad włączony, wyłączony, parowanie),
   interfejsy i ich sterowniki oraz to, czy pad istnieje też jako urządzenie
   na kablu.
2. Dodaj `ReceiverProfile` do `PROFILES` w `bc250wcm/usb.py`: `vendor`, `modes`
   (`{pid: "active" | "idle"}`), `name`, `verified` (kto, co i kiedy widział),
   `shared_with_cable` dla ID produktu używanych także przez pad na kablu.
3. Odwzoruj go w `bc250wcm/fakesys.py` (oba tryby) i dodaj testy
   w `tests/test_wcm.py`: wykrycie, zmiana trybu z zachowaniem tożsamości,
   wyjęcie.
4. Na panel trafiają tylko metryki, które urządzenie daje bez zapytań.

## Bezpieczne badanie nieznanego interfejsu HID

Do tego służy podpolecenie `hid`; ono tylko czyta.

1. **Najpierw deskryptor.** `bc250-wcm hid descriptor 2dc8:310a:2` podaje ID
   raportów, kierunki i rozmiary. Nigdy nie mówi, co znaczą bajty.
2. **Punkt odniesienia.** `bc250-wcm hid capture 2dc8:310a:2 -t 30 -o idle.jsonl`
   z nieruszanym padem. Wiele interfejsów producenta niczego nie wysyła samo
   z siebie — interfejs 2 w 2C podobno milczy — i to też jest wynik.
3. **Oznaczone czynności.** `bc250-wcm hid capture … --marks -o actions.jsonl`,
   a potem w chwili każdej czynności wpisz opis i Enter („A wciśnięty”, „pad
   wyłączony”, „pad włączony”).
4. **Analiza.** `bc250-wcm hid analyze actions.jsonl idle.jsonl`: bajty stałe
   i zmienne, zakresy wartości, pozycje różniące się między zrzutami oraz bajty,
   które zmieniły się tuż po każdym znaczniku. Raport o złej długości jest
   liczony i pomijany, nie interpretowany.
5. **Powtórz** każdą czynność kilka razy, zanim uwierzysz, że bajt coś znaczy.
6. **Nigdy** nie wysyłaj raportów wyjściowych ani feature do odbiornika „żeby
   zobaczyć, co się stanie”. W odbiorniku 8BitDo ten interfejs aktualizuje też
   firmware; istnieją udokumentowane polecenia zmieniające adres radiowy
   i kasujące parowanie.

Odczyt wymaga prawa do węzła hidraw: zwykle daje je reguła Steama; w przeciwnym
razie `install.sh --udev` dodaje regułę `uaccess` tylko dla tego odbiornika
([uprawnienia](permissions.pl.md)).

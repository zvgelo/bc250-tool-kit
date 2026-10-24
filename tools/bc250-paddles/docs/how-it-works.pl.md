# Jak działa bc250-paddles

[English](how-it-works.md) · **Polski**

Wszystkie wartości poniżej zmierzono na prawdziwym padzie i odbiorniku.
Pomiary wykonano na Fedorze 44 (kernel 7.2, Steam z Flatpaka) i sprawdzono
z logiem `logs/controller.txt` samego Steama. Ścieżkę przez `environment.d` potwierdzono
potem na BC-250 z Bazzite w Gaming Mode: Steam miał zmienną i zalogował mapowanie z paddle.

## Trzy oblicza odbiornika

Odbiornik 2,4 GHz zmienia identyfikator produktu USB zależnie od tego, co jest
z nim połączone i w jakim trybie:

| ID USB | Urządzenie wejściowe | Znaczenie | L4/R4 |
|---|---|---|---|
| `2dc8:301c`, produkt `IDLE` | brak (tylko vendor HID, strona `0xFFA0`) | odbiornik bez pada | — |
| `2dc8:310a` | `xpad`, 11 przycisków | pad w trybie **XInput** | **nie są wysyłane** |
| `2dc8:301c`, produkt `8BitDo Ultimate 2C Wireless Controller` | `hid-generic`, 15 przycisków | pad w trybie **DInput** | `BTN_C` / `BTN_Z` |
| `2dc8:301b` (Bluetooth) | `hid-generic`, 16 przycisków | pad przez Bluetooth | `BTN_C` / `BTN_Z` |

Samo `301c` nie odróżnia IDLE od DInput. Odróżnia je urządzenie wejściowe:
w IDLE go nie ma, w DInput jest `eventN` z `Product=301c` w
`/proc/bus/input/devices`. Dokładnie to czyta `bc250-paddles status`.

### XInput: nie ma czego mapować

W trybie XInput odbiornik wysyła stały 20-bajtowy raport Xbox 360. Podsłuch
`usbmon` przy naciskaniu L4, R4, L4+A i R4+A pokazuje pakiety tylko dla A.
L4/R4 **nie generują żadnego pakietu USB**, więc żaden sterownik, mapowanie SDL
ani `uinput` ich nie odtworzy.

### DInput: L4/R4 to prawdziwe przyciski

Deskryptor DInput (145 bajtów) deklaruje 15 przycisków, hat, cztery 8-bitowe
osie oraz analogowe Accel/Brake. Zmierzone kody i indeksy SDL (SDL numeruje
klawisze evdev w kolejności rosnących kodów):

| Przycisk | evdev | SDL |
|---|---|---|
| A, B, X, Y | 304, 305, 307, 308 | b0, b1, b3, b4 |
| **L4** | **306 `BTN_C`** | **b2 → `paddle2`** |
| **R4** | **309 `BTN_Z`** | **b5 → `paddle1`** |
| LB, RB | 310, 311 | b6, b7 |
| LT, RT | `ABS_BRAKE`, `ABS_GAS` (+ cyfrowe 312, 313) | a5, a4 |
| Select, Start, Home | 314, 315, 316 | b10, b11, b12 |
| L3, R3 | 317, 318 | b13, b14 |

To ta sama numeracja co przez Bluetooth. Tam baza SDL ma już
`paddle1:b5,paddle2:b2`.

### Firmware

Przełączenie w DInput (Home + B) działa przy **padzie 1.06 + odbiorniku 1.00**.
Przy **padzie 1.09 + odbiorniku 1.03** odbiornik za każdym razem wraca jako
`310a`. To samo zgłaszają issue SDL #15658 i forum Steam Community
(sierpień 2026).

## Dlaczego config.vdf nie wystarcza

Steam trzyma mapowania SDL w `config/config.vdf` pod `SDL_GamepadBind`.
Typowa rada to dopisać tam `paddle1:b5,paddle2:b2`. Dla tego urządzenia to nie
działa. Log kontrolerów Steama pokazuje, co się dzieje:

| Próba | `SDL Mapping for 2dc8/301c` w logu | Paddle |
|---|---|---|
| wpis w config.vdf bez CRC | mapowanie automatyczne, bez paddle; wpis nadpisany przy wyjściu | nie |
| wpis w config.vdf z `crc:6fed` | mapowanie automatyczne, bez paddle; wpis nadpisany przy wyjściu | nie |
| `SDL_GAMECONTROLLERCONFIG` w środowisku Steama | **nasze mapowanie** | **tak** (L5/R5) |
| tylko config.vdf, zmienna znów usunięta | mapowanie automatyczne, bez paddle | nie |

Steam buduje z kodów evdev automatyczne mapowanie SDL, które nie ma miejsca na
`BTN_C`/`BTN_Z`. Przy wyjściu zapisuje je z powrotem do `config.vdf`, dlatego
ręczne poprawki „nie przeżywają restartu”. Mapowanie ze zmiennej
`SDL_GAMECONTROLLERCONFIG` ma wyższy priorytet i jest używane.

`crc:6fed` ma znaczenie. Steam widzi urządzenie pod GUID `0300ed6f…`, gdzie
bajty 2–3 to CRC nazwy urządzenia. Mapowanie bez CRC przegrywa z takim,
które ma pasujące CRC.

## Gdzie trafia zmienna

| Steam | Mechanizm | Kto ją odczyta |
|---|---|---|
| natywny | `~/.config/environment.d/90-bc250-paddles.conf` | menedżer użytkownika systemd po zalogowaniu oraz sesje, które wczytują `environment.d` |
| natywny, od razu | `systemctl --user set-environment` | usługi użytkownika uruchomione po wywołaniu |
| Flatpak | `[Environment]` w `~/.local/share/flatpak/overrides/com.valvesoftware.Steam` | każde uruchomienie Steama z Flatpaka |

Plik `environment.d` ma postać `KLUCZ="wartość"`. Jest ona poprawna zarówno dla
systemd, jak i dla powłoki, która go wczytuje, tak jak robią to sesje gamescope.

**Sprawdzaj, nie zakładaj.** To, czy dana sesja przekazuje zmienną Steamowi,
widać w `bc250-paddles status`. Narzędzie sprawdza dwie rzeczy:

1. **Środowisko działającego Steama** (`/proc/<pid>/environ`). Proces
   w piaskownicy może je ukrywać. Wtedy narzędzie mówi to wprost, zamiast
   zgadywać.
2. **Log Steama** (`logs/controller.txt`). Ostatnia linia `SDL Mapping for
   2dc8/301c` mówi, którego mapowania Steam użył. To jest rozstrzygające.

## Wibracje w DInput

Nie ma ich, i to na dwóch poziomach:

- **Kernel:** urządzenie obsługuje `hid-generic`, który nie implementuje force
  feedback. `event` nie ma `EV_FF`, więc gry ani `fftest` nie mają do czego
  wysyłać efektów. Deskryptor nie ma też strony Physical Interface Device
  (`0x0F`), której potrzebowałaby ogólna obsługa force feedback dla HID.
- **Firmware:** deskryptor deklaruje wyjście w stylu producenta (raport `0x01`,
  usages strony LED `0x43`–`0x46`, 8 bajtów). Bezpośredni zapis przez `hidraw`
  (wszystkie bajty, potem każda para) nie wywołał wibracji. Firmware nie
  steruje silnikami w tym trybie. To samo opisują zgłoszenia w SDL #15658.

Sterownik niczego tu nie zmieni, bo pad ignoruje żądanie.

## Ręczne cofnięcie

```bash
rm ~/.config/environment.d/90-bc250-paddles.conf
systemctl --user unset-environment SDL_GAMECONTROLLERCONFIG
flatpak override --user --show com.valvesoftware.Steam   # potem usuń linię SDL_GAMECONTROLLERCONFIG=
```

`bc250-paddles off` robi dokładnie to i nie rusza innych override'ów.

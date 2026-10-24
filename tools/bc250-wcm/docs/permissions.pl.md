# bc250-wcm — uprawnienia

[English](permissions.md) · **Polski**

## Co wymaga uprawnień, a co nie

| Dane | Interfejs | Uprawnienia |
|---|---|---|
| adaptery, urządzenia, stan, bateria, UUID, GATT | BlueZ na systemowym D-Bus | żadne (polityka D-Bus BlueZ pozwala czytać każdemu) |
| włączanie / wyłączanie wyszukiwania | `Adapter1.StartDiscovery` | żadne przy domyślnej polityce Fedory/Bazzite |
| ID USB i port adaptera | sysfs | żadne |
| komunikaty Bluetooth jądra | `journalctl -k` | członkostwo w `wheel`, `adm` albo `systemd-journal` |
| **RSSI i moc TX aktywnego łącza** | mgmt *Get Connection Information* | **CAP_NET_ADMIN** |
| kontrolery i odbiorniki USB, ich stan | sysfs, uevent jądra | żadne |
| aktywność wejścia pada | `/dev/input/eventN`, tylko odczyt | dla padów żadne: systemd oznacza węzły joysticków `uaccess` |
| `hid capture` na odbiorniku | `/dev/hidrawN`, tylko odczyt | prawo odczytu — reguła Steama albo `install.sh --udev` |

Jądro pozwala nieuprzywilejowanemu gniazdu na kanale sterującym mgmt wysyłać
tylko „niezaufane” polecenia odczytu (wersja, lista indeksów, informacje
o kontrolerze). *Get Connection Information* i *Get Connections* są odrzucane
kodem 0x14, *Permission Denied* — tym samym, który `btmgmt conn-info` zgłasza
bez sudo.

## Założenie: mały helper, a nie interfejs jako root

Uruchomienie całego interfejsu curses jako root byłoby proste i złe. Zamiast
tego `bc250wcm/mgmt.py` jest jednocześnie samodzielnym helperem:

- uruchamiany jest **raz** na sesję monitora, przez sudo;
- rozmawia liniami JSON na stdin/stdout;
- przyjmuje dokładnie cztery operacje — `ping`, `index_list`, `connections`,
  `conn_info` — wszystkie to polecenia mgmt tylko do odczytu;
- każdy argument jest sprawdzany przed użyciem: indeks kontrolera to liczba
  całkowita 0–65534 (`bool` odrzucany), adres musi pasować do
  `^[0-9A-F]{2}(:[0-9A-F]{2}){5}$`, typ adresu to 0, 1 lub 2, linia żądania ma
  najwyżej 4 KiB, w locie najwyżej 16 zapytań;
- nie przyjmuje argumentów wiersza poleceń (odrzuca każdy), nie importuje niczego
  spoza biblioteki standardowej ani ze swojego katalogu (`python3 -I`);
- nie uruchamia programów i nie otwiera plików; jego jedynym zasobem jest
  gniazdo mgmt;
- kończy się, gdy zamknie się jego stdin — czyli gdy monitor kończy pracę albo
  pada, więc nigdy nie zostaje w tle.

Nie może wyłączyć adaptera, usunąć parowania, połączyć, rozłączyć ani niczego
zresetować.

## Wariant 1: jedno pytanie o hasło na uruchomienie (domyślny)

Bez żadnej konfiguracji `bc250-wcm` wyjaśnia, po co mu sudo, i uruchamia
`sudo -v` **zanim** curses przejmie terminal, a potem startuje
`sudo -n -- /usr/bin/python3 -I …/bc250wcm/mgmt.py`. Ctrl-C przy pytaniu
pomija ten krok: monitor działa bez RSSI łącza i mówi o tym w pasku Diagnostyka.

Niedawne `sudo` w tym samym terminalu się liczy — wtedy nie pada żadne pytanie.

`--no-sudo` (albo `sudo = never` w konfiguracji) nigdy nie pyta.

## Wariant 2: bez pytania — `install.sh --rssi-helper`

```bash
./tools/bc250-wcm/install.sh --rssi-helper
```

To polecenie (pytając raz o hasło):

1. kopiuje `mgmt.py` do `/usr/local/libexec/bc250-wcm/rssi-helper`,
   właściciel root, tryb 0755 — `/usr/local` jest na Bazzite zapisywalny
   i trwały;
2. zapisuje `/etc/sudoers.d/bc250-wcm`, wcześniej sprawdzony `visudo -cf`:

   ```
   <ty> ALL=(root) NOPASSWD: /usr/local/libexec/bc250-wcm/rssi-helper ""
   ```

   `""` oznacza *żadnych argumentów*. Reguła dotyczy jednego użytkownika
   i jednego pliku.

Dlaczego kopia, a nie reguła wskazująca do repozytorium: reguła NOPASSWD na
plik, który użytkownik może edytować, to powłoka roota dla każdego, kto dostanie
powłokę użytkownika. Zainstalowana kopia należy do roota w katalogu roota;
monitor używa jej dopiero po sprawdzeniu dokładnie tego (właściciel root, brak
zapisu dla grupy i innych, to samo dla katalogu).

Po `git pull` uruchom `install.sh --rssi-helper` ponownie, żeby odświeżyć kopię
(monitor odrzuca helper z inną wersją protokołu i wraca do pytania o hasło).

Usunięcie: `./tools/bc250-wcm/install.sh --remove-rssi-helper`.

## Wariant 3: btmgmt

`--rssi-backend btmgmt` uruchamia `sudo -n -- btmgmt -i N conn-info -t T ADRES`
przy każdej próbce: lista argumentów (bez powłoki), sprawdzone argumenty, twardy
limit czasu, usuwane kody kolorów. Wymaga zapamiętanego znacznika sudo albo
własnej reguły sudoers dla `btmgmt` i uruchamia proces na urządzenie co
sekundę — to wyjście awaryjne, nie domyślne. Reguła na całe `btmgmt` jest
szersza niż reguła helpera (btmgmt potrafi też wyłączyć adapter i usunąć
parowanie), dlatego zestaw jej nie instaluje.

## Odbiorniki USB i `hid capture`

Monitorowanie odbiornika nie wymaga żadnych uprawnień: wszystko pochodzi
z sysfs, a węzeł event pada jest czytelny dla użytkownika przy stanowisku
(`uaccess` z `70-uaccess.rules` systemd dla `ID_INPUT_JOYSTICK`). Węzeł jest
otwierany tylko do odczytu i nigdy nie jest przejmowany (bez `EVIOCGRAB`), więc
gry, Steam i `xpad` dostają każde zdarzenie; żaden interfejs nie jest odpinany
od sterownika.

Tylko `bc250-wcm hid capture` otwiera węzeł hidraw, również tylko do odczytu.
Fedora tworzy węzły hidraw jako `root:root 0600`, ale `60-steam-input.rules`
Steama (instalowany ze Steamem na Bazzite) dodaje `TAG+="uaccess"` każdemu
węzłowi hidraw `2dc8`. Jeśli go brakuje, `install.sh --udev` instaluje
`/etc/udev/rules.d/70-bc250-wcm.rules`:

```
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="2dc8", ATTRS{idProduct}=="310a|301c", TAG+="uaccess"
```

`uaccess` daje ACL użytkownikowi zalogowanemu przy stanowisku i nikomu więcej —
bez `MODE="0666"`, bez `chmod 666 /dev/hidraw*`. Plik musi być posortowany przed
`73-seat-late.rules`, który stosuje znacznik; reguła `99-…` nie zadziała.
Usunięcie: `install.sh --remove-udev`.

## Wariant 4: root

Uruchomiony jako root monitor otwiera gniazdo mgmt we własnym procesie
(`direct`). Działa, a pasek Diagnostyka przypomina, że helper by wystarczył.

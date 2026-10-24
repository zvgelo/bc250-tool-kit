# Rozwiązywanie problemów — `bc250-mon`

[English](troubleshooting.md) · **Polski**

## Panel „Płyta" jest pusty

Nie jest załadowany moduł SuperIO. Sprawdź:

```bash
lsmod | grep nct
```

Załaduj jeden z dwóch (nigdy oba naraz):

```bash
sudo modprobe nct6687 force=true   # odczyt + sterowanie PWM
sudo modprobe nct6683 force=true   # tylko odczyt
```

Na Bazzite ze stockowym jądrem `nct6687` jest dostępny jako akmod i wystarczy
`modprobe`. Na jądrze niestandardowym (np. `-ogc`) trzeba zbudować moduł
z [Fred78290/nct6687d](https://github.com/Fred78290/nct6687d) i wczytać go przez
`insmod` — `make install` nie zadziała, bo `/usr/lib/modules` jest tylko do odczytu.

## Obciążenie GPU pokazuje `n/d`

To zachowanie oczekiwane bez governora smu. Sterownik amdgpu nie wypełnia pola
aktywności w `gpu_metrics`. Rozwiązanie:

```bash
systemctl status cyan-skillfish-governor-smu
sudo systemctl enable --now cyan-skillfish-governor-smu.service
```

Governor podmienia `gpu_metrics` przez bind-mount i wpisuje próbkowaną wartość.
Sprawdzenie na żywo:

```bash
sudo xxd -s 0x1c -l 2 /sys/class/drm/card0/device/gpu_metrics
# ffff = pole niewypełnione, cokolwiek innego = działa
```

## Zegar GPU wygląda absurdalnie nisko

Jeżeli w Diagnostyce widnieje wpis o `pp_dpm_sclk`, to znany skutek
odblokowania ośmiu rdzeni CPU. Gdy cała tabela jest zaniżona (`15Mhz` zamiast
1500 MHz), program mnoży ją przez 100. Gdy bzdurę pokazuje tylko aktywny poziom
(98 MHz przy zakresie 1000–2000 MHz), zegar GPU jest brany z uśrednionego pola
`gpu_metrics` — średniej obejmującej czas z wyłączonym taktowaniem, więc niższej
niż częstotliwość ustawiana przez governor. Weryfikacja surowego odczytu:

```bash
cat /sys/class/drm/card0/device/pp_dpm_sclk
```

Wydajność GPU nie jest tym dotknięta — psuje się wyłącznie raportowanie.

## Komunikat „GPU zablokowane na 1500 MHz"

Bez governora GPU nie schodzi poniżej ani nie wychodzi powyżej 1500 MHz.

```bash
sudo rpm-ostree install cyan-skillfish-governor-smu   # Bazzite / Fedora Atomic
sudo systemctl enable --now cyan-skillfish-governor-smu.service
```

## Wszystkie wentylatory na 0 RPM

Płyta ma dwa złącza: **J1** i **J4003**. Główny wentylator zwykle raportuje się
jako `Pump Fan` (`fan2`). Zero obrotów przy kręcącym się wentylatorze oznacza
najczęściej wentylator 3-pinowy, rozgałęziacz gubiący sygnał tachometru albo
wpięcie poza monitorowanym złączem. Jeżeli słychać, że wentylator pracuje,
kieruj się temperaturą GPU jako potwierdzeniem skuteczności chłodzenia.

Złącze pokazujące 0 RPM przez całą sesję jest celowo ukrywane na pulpicie —
jeśli chcesz je mimo wszystko zobaczyć, zajrzyj do tabeli czujników (`Tab`).

## Program kończy się z kodem 2

```
bc250-mon: brak terminala — użyj --once albo --json
```

Interfejs curses wymaga terminala interaktywnego. W potoku, w cronie lub w skrypcie
używaj `--once` albo `--json`.

## „Okno terminala za małe"

Interfejs wymaga co najmniej 40×8 znaków. Pełny układ dwukolumnowy włącza się
od 100 kolumn; poniżej program pokazuje kolumnę pojedynczą. Tabela czujników
(`Tab`) działa w każdym rozmiarze i zawsze pokazuje komplet danych.

## Lista wątków jest ucięta

Gdy panel CPU nie mieści wszystkich wątków, wypisuje, ile pominął. Pełna lista
jest zawsze dostępna w widoku czujników (`Tab`) albo w wyższym oknie.

## Temperatura CPU przekracza próg w teście obciążeniowym

Próg krytyczny CPU to 90 °C, zgodnie z dokumentacją BC-250. Syntetyczny stress
CPU+GPU bywa cieplejszy niż realne granie i przekroczenie progu jest wtedy
oczekiwanym ostrzeżeniem, nie fałszywym alarmem. Progi zmienisz w słowniku
`THRESHOLDS` w `bc250mon/collector.py`.

## Interfejs jest w złym języku

Język wybierany jest z `--lang`, potem z `BC250_LANG`, potem z locale systemu,
a w ostateczności jest angielski:

```bash
bc250-mon --lang pl
BC250_LANG=en bc250-mon
```

Strony podręcznika działają tak samo, standardowym mechanizmem:
`man bc250-mon` jest po angielsku, `LANG=pl_PL.UTF-8 man bc250-mon` po polsku.

## Zgłaszanie błędu

Do zgłoszenia dołącz:

```bash
bc250-mon --once > bc250-stan.txt
bc250-mon --json > bc250-stan.json
uname -r; lsmod | grep -E 'nct|amdgpu|k10temp'
```

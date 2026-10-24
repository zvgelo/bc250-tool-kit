# bc250-wcm — test na prawdziwych padach

[English](hardware-testing.md) · **Polski**

Testy automatyczne mockują BlueZ, gniazdo mgmt i `btmgmt`. Ta lista to część,
na którą odpowie tylko prawdziwy sprzęt: dwa pady 8BitDo Ultimate 2C na
MERCUSYS MA530 (RTL8761BU) w BC-250. Nic tutaj nie resetuje adaptera ani nie
restartuje usług, chyba że krok wyraźnie to mówi, a ty zdecydujesz się to zrobić.

Zapisuj wynik każdego kroku (OK / nie OK i wszystko, co dziwne); do zgłoszenia
najbardziej przydają się dziennik zdarzeń
(`~/.local/state/bc250-wcm/events.jsonl`) i eksport JSON (`j`).

## 0. Przygotowanie

```bash
cd ~/bc250-tool-kit && git pull
./run.sh bc250-wcm --version
python3 -m unittest discover -s tools/bc250-wcm/tests      # musi być OK
```

Oba pady sparowane i połączone (widać je w Steamie / `evtest`).

## 1. Wykrywanie bez uprawnień

```bash
./run.sh bc250-wcm --list
```

Oczekiwane: MA530 pod bieżącym `hciN` z `usb=2c4e:0115` i portem (np. `1-4`);
oba pady jako `gamepad LE`, `Conn yes`, `Pair yes`. Zanotuj ich adresy — niżej
to `$PAD1` i `$PAD2`:

```bash
PAD1=E4:17:D8:xx:xx:xx; PAD2=E4:17:D8:yy:yy:yy
```

## 2. RSSI łącza kontra btmgmt

```bash
sudo btmgmt -i <N> conn-info -t 2 $PAD1        # N z kroku 1
./run.sh bc250-wcm --headless 15 -m $PAD1 $PAD2
```

Zaakceptuj pytanie sudo. Oczekiwane: jedna linia na pad na sekundę, stan `ok`,
RSSI w tym samym zakresie, co wypisał `btmgmt` (±5 dB — radio się zmienia).
Podsumowanie na końcu pokazuje min / max / avg i `drops 0`.

## 3. Interfejs

```bash
./run.sh bc250-wcm
```

1. Lista pokazuje oba pady; zaznacz je klawiszami `1` i `2` (albo spacją), Enter.
2. Dwa panele obok siebie; tytuł każdego ma dwa ostatnie bajty MAC (nazwy są
   identyczne). Linia RSSI odświeża się co sekundę.
3. Zanieś jeden pad na drugi koniec pokoju albo zasłoń go ciałem: jego RSSI
   spada i wykres to pokazuje; drugi pad bez zmian.
4. Enter na panelu: szczegóły. Sprawdź: typ adresu `random`, nośnik `LE`, typ
   połączenia `LE Random`, moc TX jako *zgłoszona*, bateria (*nieudostępniane*
   to poprawny wynik — zanotuj który), lista UUID, usługi GATT.
5. `+` / `-` zmieniają interwał; `p` pauzuje (wykres stoi, nagłówek mówi PAUZA).
6. Zmniejsz terminal do ok. 80 × 24: panele przechodzą w tryb kompaktowy, nic
   na siebie nie nachodzi.

## 4. Rozłączenie i ponowne połączenie

1. Wyłącz PAD2. W ciągu kilku sekund jego panel pokazuje *ROZŁĄCZONY*, wykres
   ma przerwę z czerwonym znacznikiem, `rozł. 1`. PAD1 dalej próbkuje — jego
   panel nie może stanąć.
2. Włącz PAD2. Połączy się sam; `pon. 1`, widać czas przerwy, wykres trwa dalej
   po przerwie. Dziennik (`l`) ma oba zdarzenia, z przyczyną od jądra (np.
   *terminated by remote host*).

## 5. Zniknięcie adaptera (ponowne wpięcie USB)

To sytuacja hci0 → hci1, wywołana ręcznie. Wyjmij MA530, odczekaj 5 s, wepnij
z powrotem **w ten sam port**.

Oczekiwane, bez restartu monitora:

- oba panele: *BRAK ADAPTERA*, potem *ROZŁĄCZONY*, potem znów *połączony*, gdy
  pady się połączą;
- nagłówek pokazuje nowy `hciN` (często wyższy numer);
- dziennik: *Adapter … zniknął*, *Adapter … zarejestrował się ponownie:
  hci0 → hci1* (albo *wrócił*, jeśli indeks się nie zmienił), rozłączenia
  i połączenia padów;
- Diagnostyka: przemapowanie i — jeśli jądro je zalogowało — odłączenie USB.

Opcjonalnie wepnij w **inny** port: adapter nadal jest rozpoznany (ten sam adres
kontrolera); `--list` pokazuje nowy port.

## 6. Timeout 0x2042 (tylko jeśli wystąpi)

Pierwotnej awarii nie da się wywołać na żądanie. Jeśli powtórzy się w czasie
pracy monitora, w dzienniku powinno być:

```
jądro   Bluetooth: hci0: command 0x2042 tx timeout  [LE Set Extended Scan Enable]
jądro   Bluetooth: hci0: Opcode 0x2042 failed: -110  [LE Set Extended Scan Enable]  [ETIMEDOUT]
jądro   Bluetooth: hci0: Resetting usb device.
adapter Adapter hci0 … zniknął
adapter Adapter … zarejestrował się ponownie: hci0 → hci1
```

a w Diagnostyce liczniki i rada, by nie włączać wyszukiwania. Jeśli zdarzyło się
to wcześniej w bieżącym rozruchu, te same linie pojawią się na starcie,
oznaczone *wcześniej w tym rozruchu*.

Klawisz `d` włącza wyszukiwanie — czyli dokładnie to, co robi 0x2042. Na BC-250
używaj go tylko wtedy, gdy chcesz spróbować odtworzyć problem.

## 7. Bez uprawnień

```bash
./run.sh bc250-wcm --no-sudo
```

Oczekiwane: stan, bateria i zdarzenia działają; linia RSSI mówi, czemu nie ma
wartości; Diagnostyka: *Brak RSSI połączenia: wymaga uprawnień …*. Nic się nie
wysypuje.

## 8. Zainstalowany helper

```bash
./tools/bc250-wcm/install.sh --rssi-helper
sudo -n /usr/local/libexec/bc250-wcm/rssi-helper </dev/null   # jedna linia "ready", koniec
./run.sh bc250-wcm                                            # bez pytania o hasło
```

Nagłówek: `RSSI: mgmt helper`. Po wyjściu klawiszem `q`:

```bash
pgrep -af rssi-helper || echo "helper gone"     # musi wypisać: helper gone
```

## 9. Odbiornik 2,4 GHz (8BitDo Ultimate 2C)

1. Wepnij odbiornik przy **wyłączonym** padzie. `./run.sh bc250-wcm --list`: w sekcji
   *Kontrolery USB i odbiorniki 2,4 GHz* jeden wpis, `2dc8:301c`, *bez pada
   (IDLE)*, z identyfikatorem `usb:<port>`.
2. Włącz pad (tryb 2,4 GHz). Znów `--list`: **ten sam** identyfikator
   `usb:<port>`, teraz `2dc8:310a`, *pad połączony*, opisany jako *odbiornik USB
   2,4 GHz* (był widziany jako IDLE na tym porcie). Nic nie pojawia się jako drugi
   kontroler.
3. `./run.sh bc250-wcm`, wybierz odbiornik razem z padem Bluetooth. Panel
   odbiornika: *pad połączony*, `RSSI: n/d — odbiornik nie podaje`, bateria `n/d`,
   wykres aktywności wejścia, który rośnie podczas gry i spada do 0, gdy odłożysz
   pad — panel nadal pokazuje *pad połączony*.
4. Wyłącz pad: panel przechodzi w *BEZ PADA* w ciągu ok. 2 s, `rozł. 1`,
   w zdarzeniach `2dc8:310a → 2dc8:301c`. Włącz: *pad połączony*, `pon. 1`.
5. Wyjmij odbiornik: *BRAK NA USB*. Wepnij w ten sam port: panel trwa dalej;
   w Szczegółach nowe numery `hidrawN`/`eventN`.
6. Przez cały czas gra albo `evtest` muszą dostawać wejście pada (WCM nigdy go
   nie przejmuje).
7. HID, tylko odczyt: `./run.sh bc250-wcm hid descriptor 2dc8:310a:2` musi
   zdekodować wejście `0x02` 64 B i wyjście `0x81`, strona `0xFF7A`.
   `./run.sh bc250-wcm hid capture 2dc8:310a:2 -t 20 --marks -o ~/rx.jsonl`
   — naciskaj przyciski, wpisuj znaczniki. Zanotuj, czy przychodzi *jakikolwiek*
   raport (według źródeł interfejs milczy, dopóki się go nie zapyta; „0 raportów”
   to poprawny wynik). Przy braku dostępu patrz [uprawnienia](permissions.pl.md).
8. Do zgłoszenia: wynik `--json`, `~/rx.jsonl`, dziennik zdarzeń.

## 10. Eksport

`e` i `j` na pulpicie. Otwórz CSV: wiersze z czasu, gdy PAD2 był wyłączony, mają
puste `rssi` i stan `disconnected` (nie 0). JSON ma podsumowanie dla każdego
pada i listę zdarzeń.

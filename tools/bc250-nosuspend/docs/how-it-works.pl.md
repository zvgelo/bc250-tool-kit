# Jak działa blokada usypiania

[English](how-it-works.md) · **Polski**

## Warstwy usypiania w Linuksie

Uśpienie może zainicjować kilka niezależnych warstw i wyłączenie tylko jednej
z nich zwykle nie wystarcza:

| Warstwa | Przykład | Czy `bc250-nosuspend` to obejmuje |
|---|---|---|
| Cele systemd | `systemctl suspend`, przycisk zasilania | **tak** — maskowanie celów |
| Bezczynność w logind | `IdleAction` po `IdleActionSec` | **tak** — drop-in `IdleAction=ignore` |
| Ustawienia pulpitu | KDE Powerdevil, Gaming Mode | nie trzeba — żądanie i tak trafia w zamaskowany cel |
| Wygaszanie ekranu | DPMS, wygaszacz | **nie** — osobny mechanizm, patrz niżej |
| Blokada chwilowa | `systemd-inhibit` | nie — to narzędzie systemowe do jednorazowych zadań |

Maskowanie celów systemd jest skuteczne właśnie dlatego, że leży **pod** pulpitem:
KDE i Gaming Mode nie usypiają systemu same, tylko proszą o to logind, a ten
uruchamia `suspend.target`. Zamaskowany cel to dowiązanie do `/dev/null`, więc
żądanie kończy się błędem niezależnie od tego, kto je złożył.

## Pliki, które powstają

| Ścieżka | Rola |
|---|---|
| `/etc/systemd/system/sleep.target` → `/dev/null` | maska celu (analogicznie pozostałe cztery) |
| `/etc/systemd/logind.conf.d/10-bc250-nosuspend.conf` | `IdleAction=ignore` |
| `/var/lib/bc250-nosuspend/previous-mask-state` | które cele były zamaskowane **przed** blokadą |

Plik stanu jest powodem, dla którego `stop` jest bezpieczny: bez niego zdjęcie
blokady odmaskowałoby także cele zamaskowane wcześniej przez kogoś innego.
Zapisywany jest tylko przy pierwszym `start` — ponowne uruchomienie `start`
nie nadpisuje oryginalnego stanu.

## Maskowane cele

```
sleep.target                    cel zbiorczy, pociągany przez pozostałe
suspend.target                  uśpienie do RAM (S3)
hibernate.target                hibernacja na dysk (S4)
hybrid-sleep.target             uśpienie z jednoczesnym zrzutem na dysk
suspend-then-hibernate.target   uśpienie, po czasie hibernacja
```

## Ręczne odwrócenie zmian

Gdyby narzędzie było niedostępne, wszystko cofniesz dwiema komendami:

```bash
sudo systemctl unmask sleep.target suspend.target hibernate.target \
                      hybrid-sleep.target suspend-then-hibernate.target
sudo rm -f /etc/systemd/logind.conf.d/10-bc250-nosuspend.conf
sudo systemctl reload systemd-logind
```

## Dystrybucje atomowe (Bazzite, Silverblue)

Blokada działa na nich normalnie i **nie wymaga warstwowania pakietów**:
`/etc` oraz `/var` są zapisywalne również na obrazie ostree, a maski to zwykłe
dowiązania w `/etc/systemd/system`. Aktualizacja obrazu ich nie usuwa, bo
`rpm-ostree` scala zmiany w `/etc`.

Efekt uboczny: skoro to zmiana w `/etc`, zobaczysz ją w `rpm-ostree status`
jako lokalną modyfikację konfiguracji. To normalne.

## Weryfikacja, że blokada działa

```bash
bc250-nosuspend status          # kod wyjścia 0 = zablokowane
systemctl suspend               # powinno odmówić: Unit suspend.target is masked
```

Druga komenda jest bezpieczna przy założonej blokadzie — właśnie o to chodzi,
żeby się nie powiodła.

## Dlaczego nie wystarczy sam `IdleAction=ignore`

`IdleAction` steruje wyłącznie reakcją na **bezczynność**. Nie powstrzyma
uśpienia wywołanego ręcznie z menu, przyciskiem zasilania ani zamknięciem
klapy. Dlatego narzędzie ustawia oba mechanizmy naraz: maski blokują żądania,
a `IdleAction` wyłącza automatyczny wyzwalacz.

## A wygaszanie ekranu?

To zupełnie inny mechanizm — nie usypia systemu, tylko wygasza wyświetlacz.
Steruje nim środowisko graficzne:

```bash
# KDE Plasma (sesja użytkownika)
kwriteconfig6 --file powermanagementprofilesrc --group AC --group DPMSControl --key idleTime 0

# Gaming Mode: Ustawienia → Zasilanie
```

Świadomie tego nie ruszamy: ustawienia pulpitu żyją w profilu użytkownika,
zmieniają się między wersjami Plasmy i wymagałyby zgadywania, która sesja jest
aktywna. Blokada systemowa jest jednoznaczna i odwracalna jedną komendą.

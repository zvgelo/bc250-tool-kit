# Skąd `bc250-mon` bierze dane

[English](data-sources.md) · **Polski**

Pełna mapa odczytów. Wszystko jest tylko do odczytu — program nie zapisuje
niczego w `sysfs` ani nie wywołuje narzędzi modyfikujących sprzęt.

## GPU (amdgpu)

Karta wyszukiwana jest przez `/sys/class/drm/card*/device`, gdzie dowiązanie
`driver` wskazuje na `amdgpu`. Katalog tej karty nazywamy dalej `$DEV`.

| Ścieżka | Co daje | Uwagi |
|---|---|---|
| `$DEV/device` | PCI ID | `0x13FE` lub `0x143F` → rozpoznanie Cyan Skillfish |
| `$DEV/pp_dpm_sclk` | poziomy DPM rdzenia + aktywny (`*`) | patrz [korekta skali](#zaniżone-zegary-po-odblokowaniu-8-rdzeni) |
| `$DEV/pp_dpm_mclk` | poziomy DPM pamięci | |
| `$DEV/gpu_busy_percent` | obciążenie GPU | na BC-250 zwykle nie działa |
| `$DEV/gpu_metrics` | obciążenie GPU (bajty 0x1C–0x1D) | wartość `0xFFFF` = pole niewypełnione |
| `$DEV/mem_info_vram_total`, `..._used` | wydzielony VRAM | **minimum**, nie sufit — patrz niżej |
| `$DEV/mem_info_gtt_total`, `..._used` | GTT | |
| `$DEV/power_dpm_force_performance_level` | poziom wydajności | `auto`, `manual`, … |
| `$DEV/hwmon/hwmon*/temp1_input` | temperatura `edge` | milistopnie |
| `$DEV/hwmon/hwmon*/power1_average` | PPT | mikrowaty; `power1_input` jako zapas |
| `$DEV/hwmon/hwmon*/in0_input`, `in1_input` | `vddgfx`, `vddnb` | miliwolty |

### VRAM to minimum, nie pojemność

Na tej płycie całe 16 GB jest współdzielone, a split ustawiany przez
[`bc250memcfg`](https://github.com/fanoush/bc250_memcfg) określa jedynie
**najmniejszą** ilość pamięci trzymaną na stałe dla GPU. `mem_info_vram_total`
podaje właśnie tę wydzieloną część. Wszystko ponad nią jest pożyczane dynamicznie
przez GTT, więc przy popularnym splicie 512 MB pasek stoi prawie pełny, gdy tylko
cokolwiek się renderuje — i to jest normalne, a nie objaw braku pamięci.

Dlatego monitor pokazuje dwie liczby: wydzieloną część (podpisaną `VRAM (min)`,
gdy split wynosi 1 GiB lub mniej) oraz `Pamięć GPU`, czyli
`vram_used + gtt_used` wobec `vram_total + gtt_total` — praktyczny sufit. Przy
splicie 512 MB i domyślnym limicie TTM wychodzi to blisko 8 GiB, co zgadza się
z podawanymi w dokumentacji ~8,25 GB. Podnosi się go parametrem jądra
`ttm.pages_limit`, a większy split rozwiązuje osobną awarię, w której nie da się
przypiąć bufora obrazu.

### Odczytanie zegara pracy z krzywej napięcia

Pole uśrednione jest średnią, chwilowe jest zepsute. Żadne z nich nie pokazuje
częstotliwości, z jaką GPU pracuje w tej chwili. Ale governor ustawia
częstotliwość i napięcie **razem**, z krzywej safe-points w swojej konfiguracji:

    /etc/cyan-skillfish-governor-smu/config.toml
    /etc/cyan-skillfish-governor-tt/config.toml

Zmierzone `vddgfx` da się więc odwzorować z powrotem na tę krzywą. Na płycie
referencyjnej pod obciążeniem 912 mV wypada między punktami 1700 MHz / 910 mV
i 1850 MHz / 920 mV, co daje zegar pracy około **1730 MHz** — podczas gdy średnia
z firmware'u pokazywała w tym samym momencie 931 MHz. Obie liczby są zgodne:
mniej więcej połowa czasu pracy przy ~1730 MHz uśrednia się do ~930.

Monitor pokazuje to obok napięcia, z którego wynika, jako
`vddgfx 912 mV · z krzywej ≈1730 MHz`, więc pochodzenie liczby jest jawne.
Krzywa nie jest różnowartościowa — 1500 i 1600 MHz dzielą 900 mV na krzywej
stockowej — więc wspólne napięcie raportujemy jako zakres, a nie zmyśloną
pojedynczą wartość.

### Interfejs D-Bus governora

`cyan-skillfish-governor-smu` wystawia też usługę D-Bus. **Nie ma tam bieżącej
częstotliwości** — introspekcja pokazuje wyłącznie metody sterujące i właściwości
zakresu — więc problemu zegara to nie rozwiązuje. Daje za to zakres faktycznie
obowiązujący, czego plik konfiguracyjny nie potrafi: `SetRange` może go zawęzić
w trakcie pracy.

| Obiekt | Interfejs | Zawartość |
|---|---|---|
| `/com/cyanskillfish/Governor/Range/Current` | `…Governor.Range` | `Min`, `Max` — zakres w mocy |
| `/com/cyanskillfish/Governor/Range/Allowed` | `…Governor.Range` | twarde limity, 500–2000 MHz |
| `/com/cyanskillfish/Governor` | `…PerformanceMode` | `Enabled`, cele obciążenia, progi temperatur i metody `Set*` |

Monitor czyta `Range/Current` przez `busctl get-property`, używa go w nagłówku
i do skalowania wskaźnika zegara, a przy braku D-Bus wraca do `[frequency-range]`
z pliku. Gdy oba się rozjeżdżają, panel Diagnostyka o tym mówi — plik nie opisuje
już wtedy tego, co robi GPU.

### Obciążenie GPU — kolejność prób

1. `gpu_busy_percent`, jeśli mieści się w zakresie 0–100.
2. `gpu_metrics`, uint16 little-endian spod offsetu `0x1C`:
   - `0xFFFF` → pole niewypełnione przez sterownik, zgłaszamy przyczynę zamiast liczby,
   - wartość ≤ 100 → traktowana jako procenty,
   - wartość ≤ 10000 → traktowana jako setne części procenta (dzielona przez 100).
3. Brak obu → w interfejsie `n/d` z wyjaśnieniem.

To właśnie odczyt `0xFFFF` podzielony przez 100 daje słynne „655% GPU" w MangoHud.
Usługa `cyan-skillfish-governor-smu` podmienia `gpu_metrics` przez bind-mount
i wpisuje tam sensowną wartość — wtedy punkt 2 zwraca prawdziwe obciążenie.

### Zaniżone zegary po odblokowaniu 8 rdzeni

Po odblokowaniu ośmiu rdzeni CPU `pp_dpm_sclk` zaczyna kłamać, na dwa sposoby.

**Cała tabela jest zaniżona.** Udokumentowane jako `1: 15Mhz` w miejsce 1500 MHz.
Wykrywane, gdy **najwyższy** poziom DPM jest mniejszy niż 100 MHz; odczyt mnożymy
przez 100 i zgłaszamy korektę w panelu Diagnostyka.

**Bzdurę pokazuje tylko aktywny poziom.** Lektura `cyan_skillfish_ppt.c`
tłumaczy dlaczego. Ta tabela w ogóle nie jest listą DPM: wpisy 0 i 2 to stałe
kompilacji `CYAN_SKILLFISH_SCLK_MIN` (1000) i `CYAN_SKILLFISH_SCLK_MAX` (2000),
a żywy jest tylko wpis 1 — to `metrics->Current.GfxclkFrequency` prosto z SMU.
Gdy to pole SMU się psuje, środkowy wpis idzie za nim, i dokładnie to widać na
płycie referencyjnej: `0: 1000Mhz  1: 15Mhz *  2: 2000Mhz`, z kolejnymi odczytami
2, 15, 46 i 59 MHz. Kanał hwmon `sclk` pokazuje to samo, bo `read_sensor` czerpie
z tego samego pola.

Żaden pojedynczy mnożnik tego nie naprawia (15 × 100 = 1500 się zgadza, ale
46 × 100 = 4600 już nie), więc monitor go nie wymyśla. Zamiast tego sięga po
**`gpu_metrics`**: ten sam sterownik wypełnia `average_gfxclk_frequency` z
`metrics->Average.GfxclkFrequency` — *innego* pola SMU — więc bywa ono zdrowe
tam, gdzie `Current` nie jest. Zegar bierzemy stamtąd, o ile mieści się
w wiarygodnym zakresie 100–3000 MHz, a w przeciwnym razie zgłaszamy jako
niedostępny. Tak czy inaczej panel Diagnostyka mówi, co się stało.

**Czym ta liczba naprawdę jest.** `smu11_driver_if_cyan_skillfish.h` definiuje
tabelę wypełnianą przez firmware tak:

```c
struct SmuMetrics_t {
    SmuMetricsTable_t Current;   // próbka chwilowa
    SmuMetricsTable_t Average;   // uśrednienie po oknie próbkowania SMU
    uint32_t SampleStartTime, SampleStopTime, Accnt;
};
```

Czyli `Average.GfxclkFrequency` to **średnia po czasie z okna próbkowania
firmware'u, razem z przerwami bezczynności** — a nie zegar, z jakim GPU liczy
klatkę. Dlatego monitor podpisuje ją `Zegar GPU (śr.)`. Wartość wyraźnie poniżej
skonfigurowanego minimum governora pod obciążeniem jest dla średniej normalna
i sama w sobie nie dowodzi, że governor nie podbija zegara.

**Co naprawdę raportuje płyta referencyjna.** Zmierzone przez `dev/smu-metrics.py`
w trakcie benchmarku Cyberpunka i sprawdzone krzyżowo przez `umr -gm`:

| Pole | Odczyt |
|---|---|
| `Current.CoreFrequency[0..5]` | 3500 MHz, zgodnie z `cpufreq` ~3490 |
| `Current.MemclkFrequency` | 450 MHz, poprawne — a leży dziesięć bajtów *za* polem GFX |
| `Current.GfxclkFrequency` | 18–100 MHz, **nigdy powyżej dokładnie 100** |
| `Average.GfxclkFrequency` | 505 MHz przy 36% aktywności, 931 przy 73%, 932 przy 56% |
| `Average.SocclkFrequency` / `UclkFrequency` / `DclkFrequency` | 1254 / 450 / 1111 MHz, wszystkie poprawne |

Wynikają z tego dwie rzeczy. Po pierwsze, tabela jest czytana prawidłowo: pola
i przed, i za polem GFX są poprawne, więc to nie problem z offsetami — firmware
po prostu wpisuje do tego jednego pola bzdurę. Po drugie, oba pola GFX zachowują
się inaczej: chwilowe stoi poniżej 100 niezależnie od wszystkiego, a uśrednione
rusza się razem z obciążeniem i sąsiaduje ze zdrowymi wartościami.

Czyli `Average.GfxclkFrequency` **jest** prawdziwym odczytem i monitor go używa.
Jako średnia po oknie firmware'u obejmuje przerwy, w których GPU jest odcięte od
zegara, więc wypada wyraźnie poniżej częstotliwości ustawianej przez governora —
930 MHz średniej odpowiada mniej więcej połowie czasu pracy przy ~1850 MHz.
Dlatego jest podpisane `Zegar (śr.)` i dlatego wartość poniżej skonfigurowanego
minimum governora niczego złego nie dowodzi.

`dev/smu-metrics.py` wypisuje oba pola obok siebie, razem z zegarami rdzeni CPU
z tej samej tabeli `Current`, więc da się to sprawdzić na dowolnej płycie.

### Błąd w sterowniku, o którym warto wiedzieć

`gpu_metrics->average_socket_power` jest typu `uint16`, a `cyan_skillfish_ppt.c`
przypisuje do niego `metrics.Current.CurrentSocketPower` — `uint32` w **mW**.
Wszystko powyżej 65,5 W się zawija, dlatego to pole skacze chaotycznie, podczas
gdy kanał hwmon `power1_average`, który idzie przez `read_sensor` i zachowuje
pełną wartość, pokazuje stabilną liczbę. Moc czytaj z hwmon, nigdy z tego pola.

W obu przypadkach sam zegar GPU jest prawidłowy — to błąd raportowania, nie
wydajności. Poprawny zakres nadal widać w wierszu DPM.

## CPU

| Ścieżka | Co daje |
|---|---|
| `/proc/stat` | obciążenie całkowite i per rdzeń (różnice między odczytami) |
| `/proc/cpuinfo` | model CPU, zegary awaryjne gdy brak `cpufreq` |
| `/proc/loadavg` | średnie obciążenie 1/5/15 min |
| `/sys/devices/system/cpu/online`, `present` | liczba logicznych CPU online i obecnych |
| `/sys/devices/system/cpu/cpu*/topology/core_id` + `physical_package_id` | rdzenie fizyczne — liczone jako unikalne pary pakiet:rdzeń |
| `/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq` | bieżące zegary (kHz) |
| `/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor`, `scaling_driver` | governor i sterownik |
| `hwmon` `k10temp` → `Tctl` | temperatura CPU (zapas: `coretemp` → `Package id 0`) |

Rozróżnienie rdzeni od wątków ma znaczenie po odblokowaniu ośmiu rdzeni: jądro
raportuje wtedy 16 logicznych CPU, więc sama ta liczba wyglądałaby jak 16 rdzeni.
Monitor pokazuje `8C/16T`. Gdy `topology/` jest niedostępne, wartość pochodzi
z pola `cpu cores` w `/proc/cpuinfo`.

Bez SSDT-PST z [bc250-acpi-fix](https://github.com/bc250-collective/bc250-acpi-fix)
katalog `cpufreq` nie istnieje — wtedy zegary pochodzą z `/proc/cpuinfo`,
a pole governora pokazuje „brak cpufreq".

## Płyta główna (SuperIO)

Układ `nct6686-isa-0a20` niezależnie od tego, czy załadowano `nct6683`, czy `nct6687`.
Skanowane są wszystkie kanały układu:

| Wzorzec pliku | Rodzaj | Przelicznik |
|---|---|---|
| `temp*_input` | temperatura | ÷ 1000 → °C |
| `fan*_input` | wentylator | RPM bez zmian |
| `in*_input` | napięcie | ÷ 1000 → V |
| `power*_average`, `power*_input` | moc | ÷ 1 000 000 → W |
| `curr*_input` | prąd | ÷ 1000 → A |
| `freq*_input` | częstotliwość | ÷ 1 000 000 → MHz |
| `pwm*` | PWM | 0–255 → procenty |
| `*_label` | etykieta | gdy brak, nazwa generyczna |
| `*_max`, `*_crit` | progi producenta | |

`nct6687` podaje nazwane etykiety (`CPU Fan`, `Pump Fan`, `VRM MOS`, `+12V`),
`nct6683` — generyczne (`fan2`, `VIN0`, `Thermistor 14`).

### Wentylatory na pulpicie

Panel płyty rządzi się dwiema zasadami:

- **Wentylator, który nigdy się nie ruszył, jest ukrywany.** Nieużywane złącze
  pokazuje 0 RPM w nieskończoność i niczego nie wnosi; BC-250 zwykle korzysta
  tylko z jednego z J1 / J4003. Wentylator, który choć raz się zakręcił, zostaje
  na ekranie także po zatrzymaniu — bo to już informacja warta pokazania. Tabela
  czujników (`Tab`) zawsze wymienia wszystkie kanały, również ukryte.
- **Obroty pokazywane są razem z wypełnieniem PWM**, na przykład `2633 RPM · 95%`.
  W NCT6686 `pwmN` steruje `fanN`, więc są łączone po numerze kanału. Kanał PWM
  bez odpowiadającego wentylatora trafia do osobnej grupy.

## Pamięć, dyski, system

| Ścieżka | Co daje |
|---|---|
| `/proc/meminfo` | RAM i swap |
| `hwmon` `nvme` → `Composite` | temperatura dysku |
| `/proc/modules` | wykrycie `nct6683` / `nct6687` |
| `/sys/class/dmi/id/*` | producent i nazwa płyty, wersja BIOS |
| `/etc/os-release`, `/proc/uptime`, `uname` | system, czas pracy, jądro |
| `systemctl is-active` | stan `cyan-skillfish-governor-smu` i `-tt` (odpytywane co 10 s) |

## Progi temperatur

| Element | Ostrzeżenie | Krytyczne |
|---|---|---|
| GPU (edge) | 85 °C | 90 °C |
| CPU (Tctl) | 80 °C | 90 °C |
| NVMe | 70 °C | 80 °C |
| Płyta (SuperIO) | 70 °C | 85 °C |

Wartości są w `bc250mon/collector.py`, słownik `THRESHOLDS`.

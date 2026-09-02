# De Dietrich MCR3+ → Home Assistant (MQTT Bridge)
🇬🇧 [English version](README_EN.md)

Integracja kotła **De Dietrich MCR3+ 24 kW** / **Remeha Tzerra** (sterownik **PCU-05_P3**) z **Home Assistant** przez ESP8266 i MQTT z autodiscovery.

```
Kocioł MCR3+ ←(RJ10)→ ESP8266 ←(WiFi/TCP)→ Python Bridge ←(MQTT)→ Home Assistant
```

> **Wersja 2.0.0 — zmiana mapowania ramki.** Wcześniejsze wydania używały offsetów z poprzedniej generacji sterowników Remeha. Wszystkie pozycje w ramce Sample Data zostały poprawione dla PCU-05_P3. Jeśli aktualizujesz ze starszej wersji, wartości sensorów zmienią się na prawidłowe, a nieistniejący już sensor ciśnienia zostanie automatycznie usunięty z Home Assistanta (patrz [Migracja](#migracja-ze-starszej-wersji)).

## Funkcje

- Odczyt danych z portu PC (RJ10) kotła przez protokół Remeha PCU-05_P3
- Transmisja bezprzewodowa przez ESP8266 (TCP bridge na porcie 999)
- Składanie ramek ze strumienia TCP z weryfikacją CRC16-Modbus i resynchronizacją po szumie
- Odczyt bieżącej ramki Sample Data (74 bajty) oraz dwóch rekordów liczników (0x1C, 0x1D)
- Ponad 60 encji: sensory, sensory binarne, kody blokad, diagnostyka
- Automatyczne wykrywanie encji w Home Assistant (MQTT autodiscovery) + topic dostępności (LWT)
- Automatyczny reconnect przy utracie połączenia z ESP i brokerem
- Tryb offline: `--self-test`, `--decode-file`, `--decode-hex` do analizy zrzutów bez kotła
- Uruchamianie w Dockerze

## Odczytywane dane

### Sensory

| Sensor | Jednostka | Opis |
|---|---|---|
| Status pieca | — | Czuwanie, Praca CO, Praca CWU, Uruchamianie palnika… |
| Podstatus pieca | — | Szczegółowy stan pracy (przedmuch, zapłon, wybieg pompy…) |
| Blokada trwała | — | Opis kodu lockout (diagnostyczny) |
| Blokada czasowa | — | Opis kodu blocking (diagnostyczny) |
| Temp. zasilania | °C | Temperatura wody na wyjściu z kotła |
| Temp. powrotu | °C | Temperatura wody powracającej |
| Temp. zasobnika CWU | °C | Czujnik zasobnika (calorifier) |
| Temp. dopływu CWU | °C | Czujnik dopływu CWU *(domyślnie wyłączony)* |
| Temp. zewnętrzna | °C | Czujnik temperatury zewnętrznej (jeśli podłączony) |
| Temp. kontrolna kotła | °C | Wewnętrzna temperatura kontrolna |
| Temp. pomieszczenia OpenTherm | °C | Z podłączonego sterownika modulującego |
| Temperatura regulacji | °C | Wartość używana do regulacji (diagnostyczny) |
| Wewnętrzna temp. zadana | °C | Wewnętrzny setpoint sterownika (diagnostyczny) |
| Temperatura solarna | °C | Wejście solarne *(domyślnie wyłączony)* |
| Zadana temp. CO ze sterownika | °C | Setpoint CO |
| Zadana temp. zasilania CWU | °C | Setpoint CWU |
| Zadana temp. pomieszczenia | °C | Setpoint pomieszczenia |
| Zadana CO na HMI | °C | Nastawa z panelu kotła |
| Zadana CWU na HMI | °C | Nastawa z panelu kotła |
| Zadana prędkość wentylatora | rpm | Setpoint wentylatora |
| Prędkość wentylatora | rpm | Rzeczywiste obroty |
| Prędkość wentylatora SU | rpm | Wg jednostki bezpieczeństwa *(domyślnie wyłączony)* |
| Prąd jonizacji | µA | Prąd jonizacji płomienia |
| Aktualna moc kotła | % | Bieżąca moc palnika |
| Dostępna moc | % | Moc dostępna |
| Zadana maksymalna moc | % | Ograniczenie mocy |
| Sterowanie pompą | % | Wysterowanie pompy obiegowej |
| Przepływ CWU | L/min | Przepływ ciepłej wody |
| Położenie zaworu 3-drogowego | — | CO / CWU |

### Sensory binarne

| Sensor | Opis |
|---|---|
| Awaria kotła | Aktywna blokada trwała lub czasowa |
| Palnik aktywny | Stan 2/3/4 |
| Grzanie CO | Stan 3 |
| Grzanie CWU | Stan 4 |
| Pompa aktywna | Pompa kotła pracuje |
| Wykryty płomień | Sygnał jonizacji |
| Zadanie CO ze sterownika modulującego | Żądanie ciepła OpenTherm |
| Zadanie grzania CWU | Żądanie ciepła CWU |
| Tryb CWU Eco | Tryb Eco włączony |

Dodatkowo, domyślnie wyłączone (do włączenia ręcznie w HA): sterownik modulujący podłączony, zadanie CO ON/OFF, ochrona przeciw zamarzaniu, CWU zablokowane, program anty-Legionella, CO/CWU dozwolone, zawór gazowy otwarty, zapłon aktywny, HRU aktywne, programy czasowe CO/CWU.

### Liczniki (encje diagnostyczne)

Odczytywane rzadziej, z osobnych ramek 0x1C i 0x1D (domyślnie co 600 s):

| Sensor | Jednostka |
|---|---|
| Godziny pracy pompy | h |
| Godziny pracy zaworu 3-drogowego | h |
| Godziny pracy CO | h |
| Godziny pracy CWU | h |
| Godziny zasilania | h |
| Starty pompy | — |
| Cykle zaworu 3-drogowego | — |
| Starty palnika CWU | — |
| Starty palnika łącznie | — |
| Nieudane starty palnika | — |
| Utraty płomienia | — |

> **Ciśnienie wody nie jest publikowane.** W wariancie PCU-05_P3 bajt, z którego stara wersja czytała ciśnienie, jest nieużywany. Surowa wartość trafia do JSON-a jako `pressure_raw_unsupported` wyłącznie w celach diagnostycznych.

Pełny JSON w topicu `remeha/state` zawiera też pola nieopublikowane jako encje: surowe bajty flag (`demand_source_raw`, `input_flags_raw`, `valve_flags_raw`, `pump_flags_raw`, `hru_flags_raw`), kody jednostki bezpieczeństwa (`su_state_code`, `su_blocking_code`), `hmi_active_value`, `service_mode`, `rs232_mode` oraz znacznik `received_at`.

## Wymagania sprzętowe

- **ESP8266** (np. Wemos D1 Mini)
- **Kabel RJ10 (4P4C)** — zwykły kabel telefoniczny do słuchawki
- Opcjonalnie: dzielnik napięcia 1kΩ / 2kΩ (5V → 3.3V)

## Schemat podłączenia

### Pinout RJ10 (patrząc na wtyczkę od strony styków)

```
       +---------+
GND 4  ---       +--+
TXD 3  ---          |
RXD 2  ---          |
5V  1  ---       +--+
       +---------+
```

### Podłączenie do ESP8266

```
RJ10 kocioł            ESP8266 (Wemos D1 Mini)
───────────            ──────────────────────────
Pin 4 (GND)    ─────   GND
Pin 3 (TXD)    ─────   D5 (GPIO14) — RX
Pin 2 (RXD)    ─────   D6 (GPIO12) — TX
Pin 1 (5V)     ─────    Vin
```

> **Uwaga:** ESP zasilamy przez USB, nie z kotła. Opcjonalnie można dodać dzielnik napięcia na linii TXD kotła → RX ESP (1kΩ + 2kΩ do GND), ale w praktyce bezpośrednie połączenie działa.

Parametry portu kotła: **9600 baud, 8N1, bez inwersji**.

## Instalacja

### 1. Firmware ESP8266

Wgraj [`esp8266_tcp_bridge.ino`](esp8266_tcp_bridge.ino) przez Arduino IDE. Wymagane: pakiet ESP8266 w Menedżerze płytek. Przed wgraniem ustaw `ssid` i `password`.

**Narzędzia → Płytka:** LOLIN(WEMOS) D1 mini (lub Generic ESP8266 Module)

Po wgraniu w Monitorze portu szeregowego (115200 baud) zobaczysz przydzielony adres IP.

### 2. MQTT Bridge (Docker)

#### Struktura plików

```
dietrich-bridge/
├── docker-compose.yml
├── Dockerfile
└── remeha_mqtt.py
```

#### Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir paho-mqtt==1.6.1

COPY remeha_mqtt.py .

CMD ["python", "-u", "remeha_mqtt.py"]
```

Most działa zarówno z paho-mqtt 1.6, jak i 2.x. Wymaga Pythona 3.10+.

#### docker-compose.yml

```yaml
version: '3.8'

services:
  remeha-bridge:
    build: .
    container_name: remeha-bridge
    restart: unless-stopped
    environment:
      - ESP_HOST=192.168.1.100    # ← zmień na IP swojego ESP8266
      - ESP_PORT=999
      - MQTT_HOST=192.168.1.200   # ← zmień na IP brokera MQTT
      - MQTT_PORT=1883
      - MQTT_USER=your_user       # ← zmień na login MQTT
      - MQTT_PASS=your_password   # ← zmień na hasło MQTT
      - POLL_INTERVAL=5           # co ile sekund odpytywać kocioł
```

#### Zmienne środowiskowe

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `ESP_HOST` | — | **Wymagana.** IP mostka ESP8266 |
| `ESP_PORT` | `999` | Port TCP mostka |
| `MQTT_HOST` | — | **Wymagana.** IP brokera MQTT |
| `MQTT_PORT` | `1883` | Port brokera |
| `MQTT_USER` | *(puste)* | Login MQTT; puste = bez uwierzytelniania |
| `MQTT_PASS` | *(puste)* | Hasło MQTT |
| `POLL_INTERVAL` | `5` | Odstęp między odczytami Sample Data [s] |
| `COUNTER_INTERVAL` | `600` | Odstęp między odczytami liczników [s]; `0` = wyłącz |
| `RESPONSE_TIMEOUT` | `3` | Czas oczekiwania na poprawną ramkę [s] |
| `RECONNECT_DELAY` | `10` | Odstęp przed ponownym połączeniem z ESP [s] |
| `MQTT_TOPIC_PREFIX` | `remeha` | Prefiks topiców stanu i dostępności |
| `HA_DISCOVERY_PREFIX` | `homeassistant` | Prefiks autodiscovery HA |
| `DEVICE_ID` | `dedietrich_mcr3` | Identyfikator urządzenia w HA |
| `DEVICE_NAME` | `De Dietrich MCR3+ 24 kW` | Nazwa urządzenia w HA |
| `MQTT_CLIENT_ID` | `remeha_bridge` | Client ID w brokerze |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

#### Uruchomienie

```bash
cd dietrich-bridge
docker compose up -d

# Sprawdź logi
docker logs -f remeha-bridge
```

Prawidłowe logi wyglądają tak:

```
2026-09-02 22:43:35 [INFO] Laczenie z MQTT 192.168.1.200:1883
2026-09-02 22:43:35 [INFO] Polaczono z MQTT
2026-09-02 22:43:35 [INFO] Opublikowano konfiguracje Home Assistant MQTT Discovery
2026-09-02 22:43:35 [INFO] Laczenie z ESP 192.168.1.100:999
2026-09-02 22:43:35 [INFO] Polaczono z ESP; oczekiwanie na pierwsza poprawna ramke
2026-09-02 22:43:36 [INFO] Stan: Czuwanie | zasilanie: 43.62 °C | powrot: 43.62 °C | CWU: 43.8 °C | moc: 0% | wentylator: 0 rpm
2026-09-02 22:43:36 [INFO] Zaktualizowano liczniki kotla
```

### 3. Home Assistant

Upewnij się, że masz skonfigurowaną integrację MQTT wskazującą na tego samego brokera. Encje pojawią się automatycznie:

**Ustawienia → Urządzenia → De Dietrich MCR3+ 24 kW**

## Topici MQTT

| Topic | Zawartość |
|---|---|
| `remeha/state` | Retained JSON ze wszystkimi zdekodowanymi polami |
| `remeha/status` | `online` / `offline` — dostępność (również jako Last Will) |
| `homeassistant/sensor/dedietrich_mcr3/<klucz>/config` | Retained konfiguracja autodiscovery |
| `homeassistant/binary_sensor/dedietrich_mcr3/<klucz>/config` | Jw. dla sensorów binarnych |

`remeha/status` przechodzi w `offline` nie tylko po zatrzymaniu kontenera, lecz także po utracie połączenia z ESP — encje w HA stają się wtedy niedostępne zamiast pokazywać stare wartości.

## Tryby offline (bez kotła)

Skrypt działa też jako samodzielny dekoder:

```bash
# Test wbudowany: CRC, składanie strumienia, dekodowanie Sample Data i liczników
python remeha_mqtt.py --self-test

# Dekodowanie zrzutu (plik binarny albo tekst/Markdown z blokami hex)
python remeha_mqtt.py --decode-file capture.md

# Jedna ramka na linię jako JSON (do dalszej obróbki)
python remeha_mqtt.py --decode-file capture.md --json-lines

# Dekodowanie pojedynczej ramki z linii poleceń
python remeha_mqtt.py --decode-hex "02 01 FE 06 48 02 01 ..."
```

## Migracja ze starszej wersji

- `DEVICE_ID` pozostał niezmieniony (`dedietrich_mcr3`), więc istniejące urządzenie w HA zostanie zaktualizowane, a nie zduplikowane.
- Most publikuje pustą, retained wiadomość na `homeassistant/sensor/dedietrich_mcr3/water_pressure/config`, co usuwa błędny sensor ciśnienia z poprzedniej wersji.
- Część nazw i kluczy encji się zmieniła (np. `calorifier_temp` zamiast dawnego sensora CWU). Stare, nieużywane encje można usunąć ręcznie w HA.
- Liczniki godzin i startów pochodzą teraz z ramek 0x1C/0x1D i mają inne wartości niż wcześniej — statystyki `total_increasing` mogą wymagać korekty.

## Budowanie obrazu na innym komputerze

Jeśli serwer docelowy (np. ZimaOS) ma ograniczenia z budowaniem:

```bash
# Na PC
cd dietrich-bridge
docker build -t remeha-bridge .
docker save remeha-bridge > remeha-bridge.tar

# Skopiuj na serwer
scp remeha-bridge.tar user@serwer:/tmp/

# Na serwerze
docker load < /tmp/remeha-bridge.tar
docker compose up -d
```

## Protokół Remeha (PCU-05_P3) — mapa ramek

Ramka: `STX (0x02)` … `CRC16-Modbus LE (2 B)` `ETX (0x03)`. Bajt `[4]` to długość: `len(ramka) = [4] + 2`. CRC liczone z bajtów `[1] … [-4]`.

### Zapytania

```
Sample Data:   02 FE 01 05 08 02 01 69 AB 03
Licznik 0x1C:  02 FE 00 05 08 10 1C 98 C2 03
Licznik 0x1D:  02 FE 00 05 08 10 1D 59 02 03
```

### Odpowiedź Sample Data (74 bajty, prefiks `01 FE 06 48 02 01`)

```
Bajt      Opis                                Format
─────     ──────────────────────────────      ──────────────────
[0]       STX                                 0x02
[1-2]     Adres do/od                         01 FE
[3]       Typ wiadomości                      0x06
[4]       Długość danych                      0x48
[5-6]     Identyfikator danych                02 01
[7-8]     Temp. zasilania                     int16 LE /100 °C
[9-10]    Temp. powrotu                       int16 LE /100 °C
[11-12]   Temp. dopływu CWU                   int16 LE /100 °C
[13-14]   Temp. zewnętrzna                    int16 LE /100 °C
[15-16]   Temp. zasobnika CWU                 int16 LE /100 °C
[19-20]   Temp. kontrolna kotła               int16 LE /100 °C
[21-22]   Temp. pomieszczenia (OpenTherm)     int16 LE /100 °C
[23-24]   Zadana temp. CO                     int16 LE /100 °C
[25-26]   Zadana temp. CWU                    int16 LE /100 °C
[27-28]   Zadana temp. pomieszczenia          int16 LE /100 °C
[29-30]   Zadana prędkość wentylatora         uint16 LE rpm
[31-32]   Prędkość wentylatora                uint16 LE rpm
[33]      Prąd jonizacji                      uint8 /10 µA
[34-35]   Wewnętrzna temp. zadana             int16 LE /100 °C
[36]      Dostępna moc                        uint8 %
[37]      Sterowanie pompą                    uint8 %
[39]      Zadana maksymalna moc               uint8 %
[40]      Aktualna moc kotła                  uint8 %
[43]      Flagi żądania ciepła / CWU          bitmapa
[44]      Flagi wejść                         bitmapa
[45]      Flagi zaworów                       bitmapa
[46]      Flagi pomp                          bitmapa
[47]      Kod stanu                           uint8
[48]      Kod blokady trwałej (lockout)       uint8 (0xFF = brak)
[49]      Kod blokady czasowej (blocking)     uint8 (0xFF = brak)
[50]      Kod podstanu                        uint8
[51-52]   Prędkość wentylatora wg SU          uint16 LE rpm
[53]      Kod stanu SU                        uint8
[54]      Kod blokady SU                      uint8
[56]      Nieużywany w tym wariancie          uint8 (dawniej mylnie: ciśnienie)
[57]      Flagi HRU / programów czasowych     bitmapa
[58-59]   Temperatura regulacji               int16 LE /100 °C
[60-61]   Przepływ CWU                        int16 LE /100 L/min
[63-64]   Temperatura solarna                 int16 LE /100 °C
[65-66]   Wartość aktywna HMI                 uint16 LE
[67]      Zadana CO na HMI                    uint8 °C
[68]      Zadana CWU na HMI                   uint8 °C
[69]      Tryb serwisowy                      uint8
[70]      Tryb RS232                          uint8
[71-72]   CRC16-Modbus                        uint16 LE
[73]      ETX                                 0x03
```

Wartości `0x8000`, `0xFFFF`, `0xF380`, `0x80F3` oraz odczyty poza zakresem fizycznym oznaczają czujnik niepodłączony i są publikowane jako `null`.

### Odpowiedzi liczników (26 bajtów, prefiks `00 FE 06 18 10`, bajt `[6]` = numer rekordu)

Liczniki są **big-endian** i mają mnożniki:

```
Rekord 0x1C                                   Rekord 0x1D
[7-8]    Godziny pracy pompy      ×2          [7-8]    Starty palnika łącznie   ×8
[9-10]   Godziny zaworu 3-drog.   ×2          [9-10]   Nieudane starty palnika  ×1
[11-12]  Godziny pracy CO         ×2          [11-12]  Utraty płomienia         ×1
[13-14]  Godziny pracy CWU        ×1
[15-16]  Godziny zasilania        ×2
[17-18]  Starty pompy             ×8
[19-20]  Cykle zaworu 3-drog.     ×8
[21-22]  Starty palnika CWU       ×8
```

## Rozwiązywanie problemów

| Problem | Rozwiązanie |
|---|---|
| ESP nie łączy się z WiFi | Sprawdź SSID i hasło w sketchu |
| Monitor portu — krzaki | Ustaw prędkość monitora na 115200 baud |
| Brak danych z kotła | Odwróć wtyczkę RJ10, zamień piny RX/TX |
| `Brak poprawnej odpowiedzi przez 3.0 s` | Zwiększ `RESPONSE_TIMEOUT`, sprawdź okablowanie RJ10 |
| `Nie odczytano licznikow` | Bramka TCP gubi szybką serię zapytań; zwiększ `COUNTER_INTERVAL` lub `RESPONSE_TIMEOUT` — dane bieżące działają dalej |
| `To nie jest poprawna 74-bajtowa ramka` | Inny wariant sterownika niż PCU-05_P3; zrzuć ruch i sprawdź `--decode-file` |
| `Serial2 not declared` | ESP8266 nie ma Serial2 — użyj SoftwareSerial |
| `WiFi.h not found` | Wybierz płytkę ESP8266 w Narzędzia → Płytka |
| Kontener nie widzi ESP | ESP obsługuje 1 klienta — zatrzymaj inne połączenia |
| Encje nie pojawiają się w HA | Sprawdź czy MQTT broker jest ten sam co w HA |
| Część encji jest niewidoczna | Są wyłączone domyślnie — włącz je w ustawieniach urządzenia w HA |
| Encje pokazują „niedostępne” | `remeha/status` = `offline`; sprawdź połączenie z ESP |
| Timeout przy połączeniu z ESP | Zatrzymaj kontener remeha-bridge przed testami |

## Licencja

MIT

## Podziękowania

- [kakaki/esphome_dietrich](https://github.com/kakaki/esphome_dietrich) — inspiracja i pinout RJ10
- [rjblake/remeha](https://github.com/rjblake/remeha) — mapowanie protokołu Remeha
- [skyboo.net](https://skyboo.net/2017/03/connecting-dedietrich-mcr3-to-pc-via-serial-connection/) — opis połączenia MCR3

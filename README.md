# De Dietrich MCR3 → Home Assistant (MQTT Bridge)

Integracja kotła **De Dietrich MCR3** (Remeha) z **Home Assistant** przez ESP8266 i MQTT z autodiscovery.

```
Kocioł MCR3 ←(RJ10)→ ESP8266 ←(WiFi/TCP)→ Python Bridge ←(MQTT)→ Home Assistant
```

## Funkcje

- Odczyt danych z portu PC (RJ10) kotła MCR3 przez protokół Remeha
- Transmisja bezprzewodowa przez ESP8266 (TCP bridge na porcie 999)
- Parsowanie ramek Remeha i publikacja do MQTT
- Automatyczne wykrywanie sensorów w Home Assistant (MQTT autodiscovery)
- Automatyczny reconnect przy utracie połączenia
- Uruchamianie w Dockerze

## Odczytywane dane

| Sensor | Jednostka | Opis |
|---|---|---|
| Status pieca | — | Czuwanie, Ogrzewanie CO/CWU, Uruchomienie palnika… |
| Podstatus pieca | — | Szczegółowy stan pracy |
| Temp. zasilania | °C | Temperatura wody na wyjściu z kotła |
| Temp. powrotu | °C | Temperatura wody powracającej |
| Temp. CWU | °C | Temperatura ciepłej wody użytkowej |
| Temp. kontrolna kotła | °C | Wewnętrzna temperatura kontrolna |
| Temp. zewnętrzna | °C | Czujnik temperatury zewnętrznej (jeśli podłączony) |
| Zadana temp. CO | °C | Zadana temperatura centralnego ogrzewania |
| Zadana temp. CWU | °C | Zadana temperatura ciepłej wody |
| Zadana temp. pomieszczenia | °C | Zadana temperatura pomieszczenia |
| Ciśnienie wody | bar | Ciśnienie w instalacji |
| Moc aktualna | % | Aktualna moc palnika |
| Moc dostępna | % | Dostępna moc |
| Prędkość pompy | % | Prędkość pompy obiegowej |
| Prędkość wentylatora | rpm | Obroty wentylatora |
| Prąd jonizacji | µA | Prąd jonizacji płomienia |
| Godziny pracy CO | h | Łączny czas pracy ogrzewania |
| Godziny pracy CWU | h | Łączny czas pracy c.w.u. |
| Godziny pracy pompy | h | Łączny czas pracy pompy |
| Starty palnika | — | Łączna ilość startów |
| Nieudane starty | — | Ilość nieudanych startów palnika |
| Utrata płomienia | — | Ilość utrat płomienia |

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
Pin 1 (5V)              nie podłączaj!
```

> **Uwaga:** ESP zasilamy przez USB, nie z kotła. Opcjonalnie można dodać dzielnik napięcia na linii TXD kotła → RX ESP (1kΩ + 2kΩ do GND), ale w praktyce bezpośrednie połączenie działa.

## Instalacja

### 1. Firmware ESP8266

Wgraj poniższy sketch przez Arduino IDE. Wymagane: pakiet ESP8266 w Menedżerze płytek.

**Narzędzia → Płytka:** LOLIN(WEMOS) D1 mini (lub Generic ESP8266 Module)

```cpp
#include <ESP8266WiFi.h>
#include <SoftwareSerial.h>

const char* ssid     = "TWOJA_SIEC";
const char* password  = "TWOJE_HASLO";

WiFiServer server(999);

#define RXD 14  // D5 - podłącz do TXD kotła (pin 3 RJ10)
#define TXD 12  // D6 - podłącz do RXD kotła (pin 2 RJ10)

SoftwareSerial boilerSerial(RXD, TXD, false);  // bez inwersji

void setup() {
  Serial.begin(115200);
  boilerSerial.begin(9600);

  delay(1000);
  Serial.println();
  Serial.print("Lacze z WiFi: ");
  Serial.println(ssid);

  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("Polaczono! IP: ");
  Serial.println(WiFi.localIP());
  Serial.println("Serwer TCP na porcie 999");
  server.begin();
}

void loop() {
  WiFiClient client = server.available();
  if (client) {
    Serial.println("Klient polaczony");
    while (client.connected()) {
      while (client.available()) {
        byte b = client.read();
        boilerSerial.write(b);
      }
      while (boilerSerial.available()) {
        byte b = boilerSerial.read();
        client.write(b);
        if (b < 0x10) Serial.print("0");
        Serial.print(b, HEX);
        Serial.print(" ");
      }
      delay(1);
    }
    client.stop();
    Serial.println();
    Serial.println("Klient rozlaczony");
  }
}
```

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

#### Uruchomienie

```bash
cd dietrich-bridge
docker compose up -d

# Sprawdź logi
docker logs -f remeha-bridge
```

Prawidłowe logi wyglądają tak:

```
2026-04-23 19:43:35 [INFO] Connecting to MQTT: 192.168.1.200:1883
2026-04-23 19:43:35 [INFO] HA autodiscovery published
2026-04-23 19:43:35 [INFO] Connecting to ESP: 192.168.1.100:999
2026-04-23 19:43:35 [INFO] Connected to ESP
2026-04-23 19:43:35 [INFO] Status: Czuwanie | Zasil: 45.82°C | Powrot: 46.55°C | CWU: 25.70°C | Moc: 0% | Cisn: 1.50 bar | Went: 0 rpm | Zewn: ?°C
```

### 3. Home Assistant

Upewnij się, że masz skonfigurowaną integrację MQTT wskazującą na tego samego brokera. Sensory pojawią się automatycznie:

**Ustawienia → Urządzenia → De Dietrich MCR3**

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

## Protokół Remeha — mapa ramki

Dla referencji — struktura 74-bajtowej ramki odpowiedzi "Sample Data":

```
Bajt    Opis                            Format
─────   ──────────────────────────      ──────────
[0]     STX                             0x02
[1-2]   Adres do/od                     
[3]     Typ wiadomości                  
[4]     Długość danych                  
[5]     Status code                     uint8
[6]     Sub-status code                 uint8
[7-8]   Temp. zasilania                 int16 LE /100 °C
[9-10]  Temp. powrotu                   int16 LE /100 °C
[15-16] Temp. kontrolna / zadana CO     int16 LE /100 °C
[19-20] Temp. CWU                       int16 LE /100 °C
[21-22] Zadana CWU                      int16 LE /100 °C
[23-24] Zadana temp. pomieszczenia      int16 LE /100 °C
[25-26] Zadana prędkość wentylatora     uint16 LE rpm
[27-28] Prędkość wentylatora            uint16 LE rpm
[29-30] Prąd jonizacji                  uint16 LE /10 µA
[33]    Moc dostępna                    uint8 %
[34-35] Ciśnienie wody                  uint16 LE /100 bar
[37]    Prędkość pompy                  uint8 %
[39]    Moc aktualna                    uint8 %
[41-42] Godziny pracy pompy             uint16 LE h
[43-44] Godziny pracy zaworu 3-drog.    uint16 LE h
[45-46] Godziny pracy CO                uint16 LE h
[47-48] Godziny pracy CWU              uint16 LE h
[51-52] Starty palnika                  uint16 LE
[53-54] Nieudane starty                 uint16 LE
[55-56] Utrata płomienia                uint16 LE
[58-59] Temp. zewnętrzna                int16 LE /100 °C
[73]    ETX                             0x03
```

Ramka zapytania (Sample Data Request):

```
02 FE 01 05 08 02 01 69 AB 03
```

## Rozwiązywanie problemów

| Problem | Rozwiązanie |
|---|---|
| ESP nie łączy się z WiFi | Sprawdź SSID i hasło w sketchu |
| Monitor portu — krzaki | Ustaw prędkość monitora na 115200 baud |
| Brak danych z kotła | Odwróć wtyczkę RJ10, zamień piny RX/TX |
| `Serial2 not declared` | ESP8266 nie ma Serial2 — użyj SoftwareSerial |
| `WiFi.h not found` | Wybierz płytkę ESP8266 w Narzędzia → Płytka |
| Kontener nie widzi ESP | ESP obsługuje 1 klienta — zatrzymaj inne połączenia |
| Sensory nie pojawiają się w HA | Sprawdź czy MQTT broker jest ten sam co w HA |
| Timeout przy połączeniu z ESP | Zatrzymaj kontener remeha-bridge przed testami |

## Licencja

MIT

## Podziękowania

- [kakaki/esphome_dietrich](https://github.com/kakaki/esphome_dietrich) — inspiracja i pinout RJ10
- [rjblake/remeha](https://github.com/rjblake/remeha) — mapowanie protokołu Remeha
- [skyboo.net](https://skyboo.net/2017/03/connecting-dedietrich-mcr3-to-pc-via-serial-connection/) — opis połączenia MCR3

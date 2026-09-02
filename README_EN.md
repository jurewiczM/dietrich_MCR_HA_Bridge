# De Dietrich MCR3+ → Home Assistant (MQTT Bridge)

🇵🇱 [Wersja polska](README.md)

Integration of the **De Dietrich MCR3+ 24 kW** / **Remeha Tzerra** boiler (**PCU-05_P3** controller) with **Home Assistant** via ESP8266 and MQTT with autodiscovery.

```
Boiler MCR3+ ←(RJ10)→ ESP8266 ←(WiFi/TCP)→ Python Bridge ←(MQTT)→ Home Assistant
```

> **Version 2.0.0 — frame mapping changed.** Earlier releases used offsets taken from the previous generation of Remeha controllers. Every field in the Sample Data frame has been corrected for PCU-05_P3. If you are upgrading, sensor values will change to the correct ones and the no-longer-existing pressure sensor is removed from Home Assistant automatically (see [Migration](#migration-from-an-older-version)).

## Features

- Reading data from the boiler's PC port (RJ10) using the Remeha PCU-05_P3 protocol
- Wireless transmission via ESP8266 (TCP bridge on port 999)
- Frame reassembly from the TCP stream with CRC16-Modbus validation and resync after noise
- Reads the live Sample Data frame (74 bytes) plus two counter records (0x1C, 0x1D)
- Over 60 entities: sensors, binary sensors, lockout/blocking codes, diagnostics
- Automatic entity discovery in Home Assistant (MQTT autodiscovery) + availability topic (LWT)
- Auto-reconnect on loss of the ESP or broker connection
- Offline modes: `--self-test`, `--decode-file`, `--decode-hex` for analysing captures without a boiler
- Runs in Docker

## Available Data

### Sensors

| Sensor | Unit | Description |
|---|---|---|
| Boiler Status | — | Standby, CH Heating, DHW Heating, Burner Start… |
| Boiler Sub-status | — | Detailed operating state (prepurge, ignition, pump overrun…) |
| Lockout | — | Lockout code description (diagnostic) |
| Blocking | — | Blocking code description (diagnostic) |
| Flow Temperature | °C | Water temperature leaving the boiler |
| Return Temperature | °C | Water temperature returning to the boiler |
| Calorifier Temperature | °C | DHW cylinder sensor |
| DHW Inlet Temperature | °C | DHW inlet sensor *(disabled by default)* |
| Outside Temperature | °C | Outside temperature sensor (if connected) |
| Boiler Control Temperature | °C | Internal control temperature |
| OpenTherm Room Temperature | °C | From a connected modulating controller |
| Control Temperature | °C | Value used for regulation (diagnostic) |
| Internal Setpoint | °C | Controller's internal setpoint (diagnostic) |
| Solar Temperature | °C | Solar input *(disabled by default)* |
| CH Setpoint | °C | Central heating setpoint |
| DHW Flow Setpoint | °C | Domestic hot water setpoint |
| Room Setpoint | °C | Room temperature setpoint |
| CH Setpoint (HMI) | °C | Setting from the boiler panel |
| DHW Setpoint (HMI) | °C | Setting from the boiler panel |
| Fan Speed Setpoint | rpm | Fan setpoint |
| Fan Speed | rpm | Actual fan speed |
| SU Fan Speed | rpm | As reported by the safety unit *(disabled by default)* |
| Ionisation Current | µA | Flame ionisation current |
| Actual Power | % | Current burner power |
| Available Power | % | Available power |
| Desired Maximum Power | % | Power limit |
| Pump Control | % | Circulation pump drive level |
| DHW Flow Rate | L/min | Hot water flow |
| 3-Way Valve Position | — | CH / DHW |

### Binary Sensors

| Sensor | Description |
|---|---|
| Boiler Fault | Active lockout or blocking |
| Burner Active | State 2/3/4 |
| CH Heating | State 3 |
| DHW Heating | State 4 |
| Pump Active | Boiler pump running |
| Flame Detected | Ionisation signal |
| Modulating Heat Demand | OpenTherm heat request |
| DHW Heat Demand | Hot water heat request |
| DHW Eco Mode | Eco mode enabled |

Additionally, disabled by default (enable manually in HA): modulating controller connected, ON/OFF heat demand, frost protection, DHW blocked, anti-legionella program, CH/DHW enabled, gas valve open, ignition active, HRU active, CH/DHW timer programs.

### Counters (diagnostic entities)

Read less frequently, from separate 0x1C and 0x1D records (every 600 s by default):

| Sensor | Unit |
|---|---|
| Pump Running Hours | h |
| 3-Way Valve Running Hours | h |
| CH Running Hours | h |
| DHW Running Hours | h |
| Power Supply Hours | h |
| Pump Starts | — |
| 3-Way Valve Cycles | — |
| DHW Burner Starts | — |
| Total Burner Starts | — |
| Failed Burner Starts | — |
| Flame Loss Count | — |

> **Water pressure is not published.** On the PCU-05_P3 variant, the byte the old version read pressure from is unused. The raw value is still exposed in the JSON as `pressure_raw_unsupported` for diagnostics only.

The full JSON on `remeha/state` also carries fields not exposed as entities: raw flag bytes (`demand_source_raw`, `input_flags_raw`, `valve_flags_raw`, `pump_flags_raw`, `hru_flags_raw`), safety unit codes (`su_state_code`, `su_blocking_code`), `hmi_active_value`, `service_mode`, `rs232_mode` and the `received_at` timestamp.

## Hardware Requirements

- **ESP8266** (e.g. Wemos D1 Mini)
- **RJ10 (4P4C) cable** — standard telephone handset cord
- Optional: voltage divider 1kΩ / 2kΩ (5V → 3.3V)

## Wiring

### RJ10 Pinout (looking at the plug from the contact side)

```
       +---------+
GND 4  ---       +--+
TXD 3  ---          |
RXD 2  ---          |
5V  1  ---       +--+
       +---------+
```

### Connecting to ESP8266

```
RJ10 Boiler             ESP8266 (Wemos D1 Mini)
───────────             ──────────────────────────
Pin 4 (GND)    ─────    GND
Pin 3 (TXD)    ─────    D5 (GPIO14) — RX
Pin 2 (RXD)    ─────    D6 (GPIO12) — TX
Pin 1 (5V)     ─────    Vin
```

> **Note:** Power the ESP via USB, not from the boiler. Optionally add a voltage divider on the boiler TXD → ESP RX line (1kΩ + 2kΩ to GND), but in practice a direct connection works fine.

Boiler port settings: **9600 baud, 8N1, no inversion**.

## Installation

### 1. ESP8266 Firmware

Flash [`esp8266_tcp_bridge.ino`](esp8266_tcp_bridge.ino) using Arduino IDE. Required: ESP8266 board package in Board Manager. Set `ssid` and `password` before flashing.

**Tools → Board:** LOLIN(WEMOS) D1 mini (or Generic ESP8266 Module)

After flashing, open Serial Monitor (115200 baud) to see the assigned IP address.

### 2. MQTT Bridge (Docker)

#### File Structure

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

The bridge works with both paho-mqtt 1.6 and 2.x. Requires Python 3.10+.

#### docker-compose.yml

```yaml
version: '3.8'

services:
  remeha-bridge:
    build: .
    container_name: remeha-bridge
    restart: unless-stopped
    environment:
      - ESP_HOST=192.168.1.100    # ← change to your ESP8266 IP
      - ESP_PORT=999
      - MQTT_HOST=192.168.1.200   # ← change to your MQTT broker IP
      - MQTT_PORT=1883
      - MQTT_USER=your_user       # ← change to your MQTT username
      - MQTT_PASS=your_password   # ← change to your MQTT password
      - POLL_INTERVAL=5           # polling interval in seconds
```

#### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `ESP_HOST` | — | **Required.** ESP8266 bridge IP |
| `ESP_PORT` | `999` | Bridge TCP port |
| `MQTT_HOST` | — | **Required.** MQTT broker IP |
| `MQTT_PORT` | `1883` | Broker port |
| `MQTT_USER` | *(empty)* | MQTT username; empty = no authentication |
| `MQTT_PASS` | *(empty)* | MQTT password |
| `POLL_INTERVAL` | `5` | Interval between Sample Data reads [s] |
| `COUNTER_INTERVAL` | `600` | Interval between counter reads [s]; `0` disables them |
| `RESPONSE_TIMEOUT` | `3` | Time to wait for a valid frame [s] |
| `RECONNECT_DELAY` | `10` | Delay before reconnecting to the ESP [s] |
| `MQTT_TOPIC_PREFIX` | `remeha` | Prefix for state and availability topics |
| `HA_DISCOVERY_PREFIX` | `homeassistant` | HA autodiscovery prefix |
| `DEVICE_ID` | `dedietrich_mcr3` | Device identifier in HA |
| `DEVICE_NAME` | `De Dietrich MCR3+ 24 kW` | Device name in HA |
| `MQTT_CLIENT_ID` | `remeha_bridge` | Broker client ID |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

#### Running

```bash
cd dietrich-bridge
docker compose up -d

# Check logs
docker logs -f remeha-bridge
```

Expected log output (bridge messages are in Polish):

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

Make sure you have the MQTT integration configured and pointing to the same broker. Entities appear automatically:

**Settings → Devices → De Dietrich MCR3+ 24 kW**

## MQTT Topics

| Topic | Content |
|---|---|
| `remeha/state` | Retained JSON with every decoded field |
| `remeha/status` | `online` / `offline` — availability (also the Last Will) |
| `homeassistant/sensor/dedietrich_mcr3/<key>/config` | Retained autodiscovery config |
| `homeassistant/binary_sensor/dedietrich_mcr3/<key>/config` | Same for binary sensors |

`remeha/status` goes `offline` not only when the container stops but also when the ESP connection is lost — entities in HA then become unavailable instead of showing stale values.

## Offline Modes (no boiler needed)

The script also works as a standalone decoder:

```bash
# Built-in test: CRC, stream reassembly, Sample Data and counter decoding
python remeha_mqtt.py --self-test

# Decode a capture (binary file, or text/Markdown containing hex blocks)
python remeha_mqtt.py --decode-file capture.md

# One frame per line as JSON (for further processing)
python remeha_mqtt.py --decode-file capture.md --json-lines

# Decode a single frame from the command line
python remeha_mqtt.py --decode-hex "02 01 FE 06 48 02 01 ..."
```

## Migration from an Older Version

- `DEVICE_ID` is unchanged (`dedietrich_mcr3`), so the existing HA device is updated rather than duplicated.
- The bridge publishes an empty retained message to `homeassistant/sensor/dedietrich_mcr3/water_pressure/config`, which removes the incorrect pressure sensor from the previous version.
- Some entity names and keys changed (e.g. `calorifier_temp` replaces the old DHW sensor). Stale entities can be deleted manually in HA.
- Running hours and start counters now come from the 0x1C/0x1D records and hold different values than before — `total_increasing` statistics may need adjusting.

## Building the Image on a Different Machine

If your target server (e.g. ZimaOS) has build restrictions:

```bash
# On your PC
cd dietrich-bridge
docker build -t remeha-bridge .
docker save remeha-bridge > remeha-bridge.tar

# Copy to server
scp remeha-bridge.tar user@server:/tmp/

# On server
docker load < /tmp/remeha-bridge.tar
docker compose up -d
```

## Remeha Protocol (PCU-05_P3) — Frame Map

Frame layout: `STX (0x02)` … `CRC16-Modbus LE (2 B)` `ETX (0x03)`. Byte `[4]` is the length: `len(frame) = [4] + 2`. CRC is computed over bytes `[1] … [-4]`.

### Requests

```
Sample Data:    02 FE 01 05 08 02 01 69 AB 03
Counter 0x1C:   02 FE 00 05 08 10 1C 98 C2 03
Counter 0x1D:   02 FE 00 05 08 10 1D 59 02 03
```

### Sample Data Response (74 bytes, prefix `01 FE 06 48 02 01`)

```
Byte      Description                         Format
─────     ──────────────────────────────      ──────────────────
[0]       STX                                 0x02
[1-2]     To/From address                     01 FE
[3]       Message type                        0x06
[4]       Data length                         0x48
[5-6]     Data identifier                     02 01
[7-8]     Flow temperature                    int16 LE /100 °C
[9-10]    Return temperature                  int16 LE /100 °C
[11-12]   DHW inlet temperature               int16 LE /100 °C
[13-14]   Outside temperature                 int16 LE /100 °C
[15-16]   Calorifier temperature              int16 LE /100 °C
[19-20]   Boiler control temperature          int16 LE /100 °C
[21-22]   Room temperature (OpenTherm)        int16 LE /100 °C
[23-24]   CH setpoint                         int16 LE /100 °C
[25-26]   DHW setpoint                        int16 LE /100 °C
[27-28]   Room temperature setpoint           int16 LE /100 °C
[29-30]   Fan speed setpoint                  uint16 LE rpm
[31-32]   Fan speed actual                    uint16 LE rpm
[33]      Ionisation current                  uint8 /10 µA
[34-35]   Internal setpoint                   int16 LE /100 °C
[36]      Available power                     uint8 %
[37]      Pump control                        uint8 %
[39]      Desired maximum power               uint8 %
[40]      Actual power                        uint8 %
[43]      Heat demand / DHW flags             bitmap
[44]      Input flags                         bitmap
[45]      Valve flags                         bitmap
[46]      Pump flags                          bitmap
[47]      Status code                         uint8
[48]      Lockout code                        uint8 (0xFF = none)
[49]      Blocking code                       uint8 (0xFF = none)
[50]      Sub-status code                     uint8
[51-52]   Fan speed per safety unit           uint16 LE rpm
[53]      SU state code                       uint8
[54]      SU blocking code                    uint8
[56]      Unused on this variant              uint8 (formerly misread as pressure)
[57]      HRU / timer program flags           bitmap
[58-59]   Control temperature                 int16 LE /100 °C
[60-61]   DHW flow rate                       int16 LE /100 L/min
[63-64]   Solar temperature                   int16 LE /100 °C
[65-66]   HMI active value                    uint16 LE
[67]      CH setpoint on HMI                  uint8 °C
[68]      DHW setpoint on HMI                 uint8 °C
[69]      Service mode                        uint8
[70]      RS232 mode                          uint8
[71-72]   CRC16-Modbus                        uint16 LE
[73]      ETX                                 0x03
```

The values `0x8000`, `0xFFFF`, `0xF380`, `0x80F3` and readings outside the physical range mean the sensor is not connected and are published as `null`.

### Counter Responses (26 bytes, prefix `00 FE 06 18 10`, byte `[6]` = record number)

Counters are **big-endian** and carry multipliers:

```
Record 0x1C                                   Record 0x1D
[7-8]    Pump running hours       ×2          [7-8]    Total burner starts      ×8
[9-10]   3-way valve hours        ×2          [9-10]   Failed burner starts     ×1
[11-12]  CH running hours         ×2          [11-12]  Flame loss count         ×1
[13-14]  DHW running hours        ×1
[15-16]  Power supply hours       ×2
[17-18]  Pump starts              ×8
[19-20]  3-way valve cycles       ×8
[21-22]  DHW burner starts        ×8
```

## Troubleshooting

| Problem | Solution |
|---|---|
| ESP won't connect to WiFi | Check SSID and password in the sketch |
| Serial monitor shows garbage | Set monitor baud rate to 115200 |
| No data from boiler | Flip the RJ10 plug, swap RX/TX pins |
| `Brak poprawnej odpowiedzi przez 3.0 s` (response timeout) | Increase `RESPONSE_TIMEOUT`, check RJ10 wiring |
| `Nie odczytano licznikow` (counters not read) | The TCP bridge drops rapid request bursts; raise `COUNTER_INTERVAL` or `RESPONSE_TIMEOUT` — live data keeps working |
| `To nie jest poprawna 74-bajtowa ramka` (bad frame) | Controller variant other than PCU-05_P3; capture the traffic and inspect it with `--decode-file` |
| `Serial2 not declared` | ESP8266 has no Serial2 — use SoftwareSerial |
| `WiFi.h not found` | Select ESP8266 board in Tools → Board |
| Container can't reach ESP | ESP supports 1 client — stop other connections |
| Entities don't appear in HA | Verify the MQTT broker matches the one used by HA |
| Some entities are missing | They are disabled by default — enable them in the device settings in HA |
| Entities show "unavailable" | `remeha/status` is `offline`; check the ESP connection |
| Connection timeout to ESP | Stop the remeha-bridge container before manual testing |

## License

MIT

## Acknowledgments

- [kakaki/esphome_dietrich](https://github.com/kakaki/esphome_dietrich) — inspiration and RJ10 pinout
- [rjblake/remeha](https://github.com/rjblake/remeha) — Remeha protocol mapping
- [skyboo.net](https://skyboo.net/2017/03/connecting-dedietrich-mcr3-to-pc-via-serial-connection/) — MCR3 serial connection guide

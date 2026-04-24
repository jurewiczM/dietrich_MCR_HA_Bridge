# De Dietrich MCR3 → Home Assistant (MQTT Bridge)

🇵🇱 [Wersja polska](README.md)

Integration of **De Dietrich MCR3** (Remeha) boiler with **Home Assistant** via ESP8266 and MQTT with autodiscovery.

```
Boiler MCR3 ←(RJ10)→ ESP8266 ←(WiFi/TCP)→ Python Bridge ←(MQTT)→ Home Assistant
```

## Features

- Reading data from the boiler's PC port (RJ10) using Remeha protocol
- Wireless transmission via ESP8266 (TCP bridge on port 999)
- Remeha frame parsing and MQTT publishing
- Automatic sensor discovery in Home Assistant (MQTT autodiscovery)
- Auto-reconnect on connection loss
- Runs in Docker

## Available Sensors

| Sensor | Unit | Description |
|---|---|---|
| Boiler Status | — | Standby, CH Heating, DHW Heating, Burner Start… |
| Boiler Sub-status | — | Detailed operating state |
| Flow Temperature | °C | Water temperature leaving the boiler |
| Return Temperature | °C | Water temperature returning to boiler |
| DHW Temperature | °C | Domestic hot water temperature |
| Boiler Control Temp | °C | Internal control temperature |
| Outside Temperature | °C | Outside temperature sensor (if connected) |
| CH Setpoint | °C | Central heating setpoint |
| DHW Setpoint | °C | Domestic hot water setpoint |
| Room Setpoint | °C | Room temperature setpoint |
| Water Pressure | bar | System water pressure |
| Actual Power | % | Current burner power |
| Available Power | % | Available power |
| Pump Speed | % | Circulation pump speed |
| Fan Speed | rpm | Fan speed |
| Ionisation Current | µA | Flame ionisation current |
| CH Running Hours | h | Total central heating running time |
| DHW Running Hours | h | Total DHW running time |
| Pump Running Hours | h | Total pump running time |
| Burner Starts | — | Total burner start count |
| Failed Starts | — | Failed burner start count |
| Flame Loss | — | Flame loss count |

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
Pin 1 (5V)               do not connect!
```

> **Note:** Power the ESP via USB, not from the boiler. Optionally add a voltage divider on the boiler TXD → ESP RX line (1kΩ + 2kΩ to GND), but in practice a direct connection works fine.

## Installation

### 1. ESP8266 Firmware

Flash the following sketch using Arduino IDE. Required: ESP8266 board package in Board Manager.

**Tools → Board:** LOLIN(WEMOS) D1 mini (or Generic ESP8266 Module)

```cpp
#include <ESP8266WiFi.h>
#include <SoftwareSerial.h>

const char* ssid     = "YOUR_SSID";
const char* password  = "YOUR_PASSWORD";

WiFiServer server(999);

#define RXD 14  // D5 - connect to boiler TXD (RJ10 pin 3)
#define TXD 12  // D6 - connect to boiler RXD (RJ10 pin 2)

SoftwareSerial boilerSerial(RXD, TXD, false);  // no inversion

void setup() {
  Serial.begin(115200);
  boilerSerial.begin(9600);

  delay(1000);
  Serial.println();
  Serial.print("Connecting to WiFi: ");
  Serial.println(ssid);

  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("Connected! IP: ");
  Serial.println(WiFi.localIP());
  Serial.println("TCP server on port 999");
  server.begin();
}

void loop() {
  WiFiClient client = server.available();
  if (client) {
    Serial.println("Client connected");
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
    Serial.println("Client disconnected");
  }
}
```

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

#### Running

```bash
cd dietrich-bridge
docker compose up -d

# Check logs
docker logs -f remeha-bridge
```

Expected log output:

```
2026-04-23 19:43:35 [INFO] Connecting to MQTT: 192.168.1.200:1883
2026-04-23 19:43:35 [INFO] HA autodiscovery published
2026-04-23 19:43:35 [INFO] Connecting to ESP: 192.168.1.100:999
2026-04-23 19:43:35 [INFO] Connected to ESP
2026-04-23 19:43:35 [INFO] Status: Standby | Flow: 45.82°C | Return: 46.55°C | DHW: 25.70°C | Power: 0% | Press: 1.50 bar | Fan: 0 rpm | Outside: ?°C
```

### 3. Home Assistant

Make sure you have the MQTT integration configured and pointing to the same broker. Sensors will appear automatically:

**Settings → Devices → De Dietrich MCR3**

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

## Remeha Protocol — Frame Map

For reference — structure of the 74-byte "Sample Data" response frame:

```
Byte    Description                     Format
─────   ──────────────────────────      ──────────
[0]     STX                             0x02
[1-2]   To/From address
[3]     Message type
[4]     Data length
[5]     Status code                     uint8
[6]     Sub-status code                 uint8
[7-8]   Flow temperature                int16 LE /100 °C
[9-10]  Return temperature              int16 LE /100 °C
[15-16] Control temp / CH setpoint      int16 LE /100 °C
[19-20] DHW (calorifier) temperature    int16 LE /100 °C
[21-22] DHW setpoint                    int16 LE /100 °C
[23-24] Room temperature setpoint       int16 LE /100 °C
[25-26] Fan speed setpoint              uint16 LE rpm
[27-28] Fan speed actual                uint16 LE rpm
[29-30] Ionisation current              uint16 LE /10 µA
[33]    Available power                 uint8 %
[34-35] Water pressure                  uint16 LE /100 bar
[37]    Pump percentage                 uint8 %
[39]    Actual power                    uint8 %
[41-42] Pump running hours              uint16 LE h
[43-44] 3-way valve running hours       uint16 LE h
[45-46] CH running hours                uint16 LE h
[47-48] DHW running hours               uint16 LE h
[51-52] Total burner starts             uint16 LE
[53-54] Failed burner starts            uint16 LE
[55-56] Flame loss count                uint16 LE
[58-59] Outside temperature             int16 LE /100 °C
[73]    ETX                             0x03
```

Sample Data Request frame:

```
02 FE 01 05 08 02 01 69 AB 03
```

## Troubleshooting

| Problem | Solution |
|---|---|
| ESP won't connect to WiFi | Check SSID and password in sketch |
| Serial monitor shows garbage | Set monitor baud rate to 115200 |
| No data from boiler | Flip the RJ10 plug, swap RX/TX pins |
| `Serial2 not declared` | ESP8266 has no Serial2 — use SoftwareSerial |
| `WiFi.h not found` | Select ESP8266 board in Tools → Board |
| Container can't reach ESP | ESP supports 1 client — stop other connections |
| Sensors don't appear in HA | Verify MQTT broker matches the one used by HA |
| Connection timeout to ESP | Stop remeha-bridge container before manual testing |

## License

MIT

## Acknowledgments

- [kakaki/esphome_dietrich](https://github.com/kakaki/esphome_dietrich) — inspiration and RJ10 pinout
- [rjblake/remeha](https://github.com/rjblake/remeha) — Remeha protocol mapping
- [skyboo.net](https://skyboo.net/2017/03/connecting-dedietrich-mcr3-to-pc-via-serial-connection/) — MCR3 serial connection guide

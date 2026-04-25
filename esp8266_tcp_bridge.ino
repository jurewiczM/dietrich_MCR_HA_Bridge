/*
 * De Dietrich MCR3 - ESP8266 TCP Bridge
 * 
 * Creates a TCP server on port 999 that bridges WiFi to the boiler's
 * PC port (RJ10) using SoftwareSerial.
 * 
 * Wiring (RJ10 → ESP8266):
 *   RJ10 Pin 4 (GND) → GND
 *   RJ10 Pin 3 (TXD) → D5 (GPIO14) — RX
 *   RJ10 Pin 2 (RXD) → D6 (GPIO12) — TX
 *   RJ10 Pin 1 (5V)  → Vin
 * 
 * Boiler communication: 9600 baud, 8N1, no inversion
 * 
 * License: MIT
 */

#include <ESP8266WiFi.h>
#include <SoftwareSerial.h>

// ── WiFi Configuration ────────────────────────────────────────
const char* ssid     = "YOUR_SSID";       // ← change to your WiFi SSID
const char* password = "YOUR_PASSWORD";   // ← change to your WiFi password

// ── TCP Server ────────────────────────────────────────────────
WiFiServer server(999);

// ── Boiler Serial (RJ10 PC port) ─────────────────────────────
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
      // TCP → Boiler
      while (client.available()) {
        byte b = client.read();
        boilerSerial.write(b);
      }
      // Boiler → TCP (+ debug to Serial Monitor)
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

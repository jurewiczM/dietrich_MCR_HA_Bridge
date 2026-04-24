#!/usr/bin/env python3
"""
De Dietrich MCR3 (Remeha) → MQTT Bridge v1.1
Connects to ESP8266 TCP bridge, parses Remeha protocol, publishes to MQTT with HA autodiscovery.
Byte offsets verified against actual MCR3 frame data.
"""

import socket
import time
import json
import logging
import sys
import os
import signal

import paho.mqtt.client as mqtt

# ── Configuration ──────────────────────────────────────────────
ESP_HOST = os.getenv("ESP_HOST", "192.168.1.100")
ESP_PORT = int(os.getenv("ESP_PORT", "999"))

MQTT_HOST = os.getenv("MQTT_HOST", "192.168.1.200")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USER = os.getenv("MQTT_USER", "your_user")
MQTT_PASS = os.getenv("MQTT_PASS", "your_password")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "5"))
MQTT_TOPIC_PREFIX = "remeha"
HA_DISCOVERY_PREFIX = "homeassistant"
DEVICE_NAME = "De Dietrich MCR3"
DEVICE_ID = "dedietrich_mcr3"

# ── Remeha protocol ───────────────────────────────────────────
REMEHA_REQUEST = bytes([0x02, 0xFE, 0x01, 0x05, 0x08, 0x02, 0x01, 0x69, 0xAB, 0x03])

STATUS_CODES = {
    0: "Czuwanie",
    1: "Uruchomienie kotła",
    2: "Uruchomienie palnika",
    3: "Ogrzewanie CO",
    4: "Ogrzewanie CWU",
    5: "Zatrzymanie palnika",
    6: "Zatrzymanie kotła",
    8: "Kontrolowane zatrzymanie",
    9: "Tryb blokowania",
    10: "Locking mode",
    15: "Manual heat demand",
    16: "Zabezpieczenie przed zamarzaniem",
    17: "Odpowietrzanie",
}

SUB_STATUS_CODES = {
    0: "Czuwanie", 1: "Anti-cycling", 2: "Open hydraulic valve",
    3: "Pump start", 4: "Wait for burner start",
    10: "Open external gas valve", 11: "Fan to fluegasvalve speed",
    12: "Open fluegasvalve", 13: "Pre-purge", 14: "Wait for release",
    15: "Burner start", 16: "VPS test", 17: "Pre-ignition",
    18: "Ignition", 19: "Flame check", 20: "Interpurge",
    30: "Normal internal setpoint", 31: "Limited internal setpoint",
    32: "Normal power control", 33: "Gradient control level 1",
    34: "Gradient control level 2", 35: "Gradient control level 3",
    36: "Flame protection", 37: "Stabilisation time", 38: "Cold start",
    39: "Limited power Tfg", 40: "Burner stop", 41: "Post purge",
    42: "Fan to flue gas valve speed", 43: "Close flue gas valve",
    44: "Stop fan", 45: "Close external gas valve",
    60: "Pump post running", 61: "Pump stop",
    62: "Close hydraulic valve", 63: "Start anti-cycle timer",
    255: "Reset wait time",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("remeha")

running = True


def signal_handler(sig, frame):
    global running
    log.info("Shutting down...")
    running = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ── Parsing ───────────────────────────────────────────────────
def parse_temp(low, high):
    """Parse Remeha temperature (2 bytes, little-endian, /100)."""
    value = (high << 8) | low
    if value in (0x8000, 0xFFFF, 0x80F3, 0xF380):
        return None
    if value > 0x7FFF:
        value -= 0x10000
    return round(value / 100.0, 2)


def parse_uint16(low, high):
    """Parse unsigned 16-bit value (little-endian)."""
    value = (high << 8) | low
    if value == 0xFFFF:
        return None
    return value


def parse_remeha_frame(data):
    """
    Parse Remeha Sample Data response frame.
    Byte map verified against actual MCR3 74-byte frames:
    
    [ 0] STX (0x02)
    [ 1] To addr
    [ 2] From addr
    [ 3] Msg type
    [ 4] Data length
    [ 5] Status code
    [ 6] Sub-status / locking
    [7-8] Flow temp (zasilanie)
    [9-10] Return temp (powrot)
    [11-12] (invalid/unused on MCR3)
    [13-14] (invalid/unused on MCR3)
    [15-16] Boiler control temp / CH setpoint
    [17-18] (invalid)
    [19-20] Calorifier temp (CWU)
    [21-22] DHW setpoint
    [23-24] Room temp setpoint
    [25-26] Fan speed setpoint
    [27-28] Fan speed actual
    [29-30] Ionisation current
    [31-32] Internal setpoint
    [33] Available power
    [34-35] Water pressure (/100 = bar)
    [36] Desired max power
    [37] Pump percentage
    [38] (reserved)
    [39] Actual power %
    [40] Demand source
    [41-42] Hours run pump
    [43-44] Hours run 3-way valve
    [45-46] Hours run CH
    [47-48] Hours run DHW
    [49-50] Power supply hours
    [51-52] Total burner starts
    [53-54] Failed burner starts
    [55-56] Number flame loss
    [57] (reserved)
    [58-59] Outside temp (or control temp repeat)
    ...
    [73] ETX (0x03)
    """
    if len(data) < 64:
        log.warning(f"Frame too short: {len(data)} bytes")
        return None

    if data[0] != 0x02 or data[-1] != 0x03:
        log.warning("Invalid frame markers")
        return None

    d = data
    result = {}

    # ── Status ──
    status_code = d[5]
    sub_status_code = d[6]
    result["status_code"] = status_code
    result["status"] = STATUS_CODES.get(status_code, f"Nieznany ({status_code})")
    result["sub_status_code"] = sub_status_code
    result["sub_status"] = SUB_STATUS_CODES.get(sub_status_code, f"Nieznany ({sub_status_code})")

    # ── Temperatures ──
    result["flow_temp"] = parse_temp(d[7], d[8])
    result["return_temp"] = parse_temp(d[9], d[10])
    result["boiler_control_temp"] = parse_temp(d[15], d[16])
    result["calorifier_temp"] = parse_temp(d[19], d[20])

    # ── Setpoints ──
    result["ch_setpoint"] = parse_temp(d[15], d[16])
    result["dhw_setpoint"] = parse_temp(d[21], d[22])
    result["room_setpoint"] = parse_temp(d[23], d[24])

    # ── Fan ──
    result["fan_speed_setpoint"] = parse_uint16(d[25], d[26])
    result["fan_speed"] = parse_uint16(d[27], d[28])

    # ── Ionisation ──
    ion_raw = parse_uint16(d[29], d[30])
    result["ionisation_current"] = round(ion_raw / 10.0, 1) if ion_raw is not None else None

    # ── Internal setpoint ──
    result["internal_setpoint"] = parse_uint16(d[31], d[32])

    # ── Power & pump ──
    result["available_power"] = d[33]
    result["desired_max_power"] = d[36]
    result["pump_percentage"] = d[37]
    result["actual_power"] = d[39]
    result["demand_source"] = d[40]

    # ── Water pressure ── (bajty 34-35, /100 = bar)
    wp = parse_uint16(d[34], d[35])
    result["water_pressure"] = round(wp / 100.0, 2) if wp is not None else None

    # ── Hours & counters ──
    result["hours_run_pump"] = parse_uint16(d[41], d[42])
    result["hours_run_3way"] = parse_uint16(d[43], d[44])
    result["hours_run_ch"] = parse_uint16(d[45], d[46])
    result["hours_run_dhw"] = parse_uint16(d[47], d[48])
    result["power_supply_hours"] = parse_uint16(d[49], d[50])
    result["total_burner_starts"] = parse_uint16(d[51], d[52])
    result["failed_burner_starts"] = parse_uint16(d[53], d[54])
    result["flame_loss_count"] = parse_uint16(d[55], d[56])

    # ── Outside temp ──
    # MCR3 may have outside temp at bytes 58-59
    if len(d) > 59:
        outside = parse_temp(d[58], d[59])
        if outside is not None and -50 < outside < 60:
            result["outside_temp"] = outside

    # Filter out None values
    return {k: v for k, v in result.items() if v is not None}


# ── MQTT ──────────────────────────────────────────────────────
SENSOR_CONFIG = {
    "status": {"name": "Status pieca", "icon": "mdi:fire"},
    "sub_status": {"name": "Podstatus pieca", "icon": "mdi:fire-circle"},
    "flow_temp": {"name": "Temp. zasilania", "unit": "°C", "device_class": "temperature", "icon": "mdi:thermometer"},
    "return_temp": {"name": "Temp. powrotu", "unit": "°C", "device_class": "temperature", "icon": "mdi:thermometer"},
    "boiler_control_temp": {"name": "Temp. kontrolna kotła", "unit": "°C", "device_class": "temperature", "icon": "mdi:thermometer"},
    "calorifier_temp": {"name": "Temp. CWU", "unit": "°C", "device_class": "temperature", "icon": "mdi:thermometer-water"},
    "outside_temp": {"name": "Temp. zewnętrzna", "unit": "°C", "device_class": "temperature", "icon": "mdi:thermometer"},
    "ch_setpoint": {"name": "Zadana temp. CO", "unit": "°C", "device_class": "temperature", "icon": "mdi:thermometer-chevron-up"},
    "dhw_setpoint": {"name": "Zadana temp. CWU", "unit": "°C", "device_class": "temperature", "icon": "mdi:thermometer-chevron-up"},
    "room_setpoint": {"name": "Zadana temp. pomieszczenia", "unit": "°C", "device_class": "temperature", "icon": "mdi:thermometer-chevron-up"},
    "water_pressure": {"name": "Ciśnienie wody", "unit": "bar", "device_class": "pressure", "icon": "mdi:gauge"},
    "actual_power": {"name": "Moc aktualna", "unit": "%", "icon": "mdi:percent"},
    "available_power": {"name": "Moc dostępna", "unit": "%", "icon": "mdi:percent"},
    "desired_max_power": {"name": "Moc max żądana", "unit": "%", "icon": "mdi:percent"},
    "pump_percentage": {"name": "Prędkość pompy", "unit": "%", "icon": "mdi:pump"},
    "fan_speed": {"name": "Prędkość wentylatora", "unit": "rpm", "icon": "mdi:fan"},
    "fan_speed_setpoint": {"name": "Zadana prędkość wentylatora", "unit": "rpm", "icon": "mdi:fan"},
    "ionisation_current": {"name": "Prąd jonizacji", "unit": "µA", "icon": "mdi:flash"},
    "hours_run_pump": {"name": "Godziny pracy pompy", "unit": "h", "icon": "mdi:clock-outline", "entity_category": "diagnostic"},
    "hours_run_3way": {"name": "Godziny pracy zaworu 3-drog.", "unit": "h", "icon": "mdi:clock-outline", "entity_category": "diagnostic"},
    "hours_run_ch": {"name": "Godziny pracy CO", "unit": "h", "icon": "mdi:clock-outline", "entity_category": "diagnostic"},
    "hours_run_dhw": {"name": "Godziny pracy CWU", "unit": "h", "icon": "mdi:clock-outline", "entity_category": "diagnostic"},
    "power_supply_hours": {"name": "Godziny zasilania", "unit": "h", "icon": "mdi:clock-outline", "entity_category": "diagnostic"},
    "total_burner_starts": {"name": "Starty palnika", "icon": "mdi:counter", "entity_category": "diagnostic"},
    "failed_burner_starts": {"name": "Nieudane starty palnika", "icon": "mdi:alert-circle", "entity_category": "diagnostic"},
    "flame_loss_count": {"name": "Utrata płomienia", "icon": "mdi:fire-off", "entity_category": "diagnostic"},
}

DEVICE_INFO = {
    "identifiers": [DEVICE_ID],
    "name": DEVICE_NAME,
    "manufacturer": "De Dietrich",
    "model": "MCR3",
    "sw_version": "1.1",
}


def publish_ha_discovery(mqtt_client):
    """Publish Home Assistant MQTT autodiscovery config for all sensors."""
    for key, cfg in SENSOR_CONFIG.items():
        unique_id = f"{DEVICE_ID}_{key}"
        topic = f"{HA_DISCOVERY_PREFIX}/sensor/{DEVICE_ID}/{key}/config"

        payload = {
            "name": cfg["name"],
            "unique_id": unique_id,
            "state_topic": f"{MQTT_TOPIC_PREFIX}/state",
            "value_template": "{{ value_json." + key + " }}",
            "device": DEVICE_INFO,
            "icon": cfg.get("icon"),
            "availability_topic": f"{MQTT_TOPIC_PREFIX}/status",
        }

        if "unit" in cfg and cfg["unit"]:
            payload["unit_of_measurement"] = cfg["unit"]
        if "device_class" in cfg:
            payload["device_class"] = cfg["device_class"]
            payload["state_class"] = "measurement"
        if cfg.get("entity_category"):
            payload["entity_category"] = cfg["entity_category"]

        mqtt_client.publish(topic, json.dumps(payload), retain=True)

    log.info("HA autodiscovery published")


def publish_state(mqtt_client, data):
    """Publish parsed data to MQTT state topic."""
    mqtt_client.publish(f"{MQTT_TOPIC_PREFIX}/state", json.dumps(data), retain=True)


# ── Main loop ─────────────────────────────────────────────────
def main():
    global running

    log.info(f"Connecting to MQTT: {MQTT_HOST}:{MQTT_PORT}")
    mqttc = mqtt.Client(client_id="remeha_bridge")
    mqttc.username_pw_set(MQTT_USER, MQTT_PASS)
    mqttc.will_set(f"{MQTT_TOPIC_PREFIX}/status", "offline", retain=True)

    try:
        mqttc.connect(MQTT_HOST, MQTT_PORT, 60)
    except Exception as e:
        log.error(f"MQTT connection failed: {e}")
        sys.exit(1)

    mqttc.loop_start()
    publish_ha_discovery(mqttc)
    mqttc.publish(f"{MQTT_TOPIC_PREFIX}/status", "online", retain=True)

    while running:
        sock = None
        try:
            log.info(f"Connecting to ESP: {ESP_HOST}:{ESP_PORT}")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect((ESP_HOST, ESP_PORT))
            log.info("Connected to ESP")

            while running:
                sock.sendall(REMEHA_REQUEST)

                response = bytearray()
                start_time = time.time()
                while time.time() - start_time < 3:
                    try:
                        chunk = sock.recv(1024)
                        if not chunk:
                            break
                        response.extend(chunk)
                        if len(response) > 5 and response[-1] == 0x03:
                            break
                    except socket.timeout:
                        break

                if response:
                    parsed = parse_remeha_frame(response)
                    if parsed:
                        publish_state(mqttc, parsed)
                        log.info(
                            f"Status: {parsed.get('status', '?')} | "
                            f"Zasil: {parsed.get('flow_temp', '?')}°C | "
                            f"Powrot: {parsed.get('return_temp', '?')}°C | "
                            f"CWU: {parsed.get('calorifier_temp', '?')}°C | "
                            f"Moc: {parsed.get('actual_power', '?')}% | "
                            f"Cisn: {parsed.get('water_pressure', '?')} bar | "
                            f"Went: {parsed.get('fan_speed', '?')} rpm | "
                            f"Zewn: {parsed.get('outside_temp', '?')}°C"
                        )
                    else:
                        log.warning("Failed to parse frame")
                else:
                    log.warning("No response from boiler")

                time.sleep(POLL_INTERVAL)

        except (socket.error, ConnectionRefusedError, OSError) as e:
            log.error(f"ESP connection error: {e}")
            mqttc.publish(f"{MQTT_TOPIC_PREFIX}/status", "offline", retain=True)
            if sock:
                sock.close()
            log.info("Reconnecting in 10s...")
            time.sleep(10)
            continue
        finally:
            if sock:
                sock.close()

    mqttc.publish(f"{MQTT_TOPIC_PREFIX}/status", "offline", retain=True)
    mqttc.loop_stop()
    mqttc.disconnect()
    log.info("Stopped")


if __name__ == "__main__":
    main()

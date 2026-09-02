#!/usr/bin/env python3
"""
Interpreter i most MQTT dla De Dietrich MCR3+ 24 kW / Remeha Tzerra.

Obslugiwany wariant protokolu: PCU-05_P3 (Sample Data 0x02/0x01).

Najwazniejsze tryby:
  python remeha_mqtt_mcr3plus.py --self-test
  python remeha_mqtt_mcr3plus.py --decode-file capture.md
  python remeha_mqtt_mcr3plus.py --decode-file capture.md --json-lines
  python remeha_mqtt_mcr3plus.py

Tryb mostu korzysta ze zmiennych srodowiskowych zgodnych z pierwotnym
projektem: ESP_HOST, ESP_PORT, MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS,
POLL_INTERVAL. Opcjonalne: COUNTER_INTERVAL, RESPONSE_TIMEOUT,
RECONNECT_DELAY, MQTT_TOPIC_PREFIX, HA_DISCOVERY_PREFIX, DEVICE_ID,
DEVICE_NAME, MQTT_CLIENT_ID i LOG_LEVEL.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


VERSION = "2.0.0"
LOG = logging.getLogger("remeha_mcr3plus")

# Zapytania maja juz prawidlowy CRC16-Modbus (LSB, MSB).
SAMPLE_REQUEST = bytes.fromhex("02 FE 01 05 08 02 01 69 AB 03")
COUNTER_1_REQUEST = bytes.fromhex("02 FE 00 05 08 10 1C 98 C2 03")
COUNTER_2_REQUEST = bytes.fromhex("02 FE 00 05 08 10 1D 59 02 03")

SAMPLE_RESPONSE_PREFIX = bytes.fromhex("01 FE 06 48 02 01")
COUNTER_RESPONSE_PREFIX = bytes.fromhex("00 FE 06 18 10")
SAMPLE_FRAME_LENGTH = 74
COUNTER_FRAME_LENGTH = 26

# Spotykane oznaczenia wejscia otwartego / czujnika niepodlaczonego.
INVALID_16BIT_VALUES = {0x8000, 0xFFFF, 0xF380, 0x80F3}


STATUS_CODES = {
    0: "Czuwanie",
    1: "Uruchamianie kotla",
    2: "Uruchamianie palnika",
    3: "Praca CO",
    4: "Praca CWU",
    5: "Zatrzymywanie palnika",
    6: "Zatrzymywanie kotla",
    7: "Nieokreslony",
    8: "Kontrolowane zatrzymanie",
    9: "Blokada czasowa",
    10: "Blokada trwala",
    11: "Tryb kominiarski L",
    12: "Tryb kominiarski h",
    13: "Tryb kominiarski H",
    14: "Nieokreslony",
    15: "Reczne zadanie ciepla",
    16: "Ochrona kotla przed zamarzaniem",
    17: "Odpowietrzanie",
    18: "Ochrona temperatury sterownika",
}

SUB_STATUS_CODES = {
    0: "Czuwanie",
    1: "Blokada przeciw taktowaniu",
    2: "Otwieranie zaworu hydraulicznego",
    3: "Start pompy",
    4: "Oczekiwanie na start palnika",
    10: "Otwieranie zewnetrznego zaworu gazu",
    11: "Wentylator do predkosci zaworu spalin",
    12: "Otwieranie zaworu spalin",
    13: "Przedmuch wstepny",
    14: "Oczekiwanie na zezwolenie",
    15: "Start palnika",
    16: "Test VPS",
    17: "Zaplon wstepny",
    18: "Zaplon",
    19: "Kontrola plomienia",
    20: "Przedmuch posredni",
    30: "Normalna wartosc zadana wewnetrzna",
    31: "Ograniczona wartosc zadana wewnetrzna",
    32: "Normalna regulacja mocy",
    33: "Regulacja gradientu, poziom 1",
    34: "Regulacja gradientu, poziom 2",
    35: "Regulacja gradientu, poziom 3",
    36: "Ochrona plomienia",
    37: "Czas stabilizacji",
    38: "Zimny start",
    39: "Ograniczenie mocy temperatura spalin",
    40: "Zatrzymanie palnika",
    41: "Przedmuch koncowy",
    42: "Wentylator do predkosci zaworu spalin",
    43: "Zamykanie zaworu spalin",
    44: "Zatrzymanie wentylatora",
    45: "Zamykanie zewnetrznego zaworu gazu",
    60: "Wybieg pompy",
    61: "Zatrzymanie pompy",
    62: "Zamykanie zaworu hydraulicznego",
    63: "Start licznika przeciw taktowaniu",
    255: "Oczekiwanie na reset",
}

LOCKOUT_CODES = {
    0: "PSU nie jest podlaczone",
    1: "Blad parametru SU",
    2: "Czujnik temperatury zasilania zwarty",
    3: "Czujnik temperatury zasilania otwarty",
    4: "Temperatura zasilania ponizej minimum",
    5: "Temperatura zasilania powyzej maksimum",
    6: "Czujnik temperatury powrotu zwarty",
    7: "Czujnik temperatury powrotu otwarty",
    8: "Temperatura powrotu ponizej minimum",
    9: "Temperatura powrotu powyzej maksimum",
    10: "Roznica temperatur zasilanie-powrot za duza",
    11: "Odwrotna roznica temperatur za duza",
    12: "Zadzialal ogranicznik STB",
    14: "Piec nie uruchomil sie 5 razy",
    15: "Test VPS nieudany 5 razy",
    16: "Falszywy sygnal plomienia",
    17: "Blad sterownika zaworu gazowego SU",
    32: "Czujnik temperatury zasilania zwarty",
    33: "Czujnik temperatury zasilania otwarty",
    34: "Wentylator poza zakresem regulacji",
    35: "Temperatura powrotu wyzsza od zasilania",
    36: "Utrata plomienia 5 razy",
    37: "Blad komunikacji SU",
    38: "Blad komunikacji SCU-S",
    39: "Wejscie BL skonfigurowane jako lockout",
    41: "Za wysoka temperatura elektroniki / airbox",
    42: "Za niskie cisnienie wody",
    43: "Brak wymaganego gradientu temperatury",
    44: "Nieudany test odpowietrzania",
    50: "Timeout zewnetrznego PSU",
    51: "Timeout wbudowanego PSU",
    52: "Blokada GVC",
    255: "Brak blokady trwalej",
}

BLOCKING_CODES = {
    0: "Blad parametru PCU",
    1: "Temperatura zasilania powyzej maksimum",
    2: "Przyrost temperatury zasilania za szybki",
    3: "Temperatura wymiennika powyzej maksimum",
    4: "Przyrost temperatury wymiennika za szybki",
    5: "Roznica wymiennik-powrot za duza",
    6: "Roznica zasilanie-wymiennik za duza",
    7: "Roznica zasilanie-powrot za duza",
    8: "Brak sygnalu zezwolenia",
    9: "Zamienione L-N",
    10: "Sygnal blokady bez ochrony przeciwmrozowej",
    11: "Sygnal blokady z ochrona przeciwmrozowa",
    12: "HMI niepodlaczone",
    13: "Blad komunikacji SCU",
    14: "Minimalne cisnienie wody",
    15: "Minimalne cisnienie gazu",
    16: "Niezgodna identyfikacja SU",
    17: "Blad tablicy identyfikacyjnej dF/dU",
    18: "Niezgodna identyfikacja PSU",
    19: "Wymagana identyfikacja dF/dU",
    20: "Trwa identyfikacja",
    21: "Utrata komunikacji SU",
    22: "Utrata plomienia",
    24: "Nieudany test VPS",
    25: "Wewnetrzny blad SU",
    26: "Blad czujnika zasobnika CWU",
    27: "Blad czujnika doplywu CWU",
    28: "Trwa reset",
    29: "Zmieniono parametr GVC",
    31: "Przekroczona temperatura spalin",
    32: "Blad czujnika temperatury spalin",
    33: "Wewnetrzny blad PCU",
    34: "Za duza roznica czujnikow spalin",
    35: "Temperatura spalin zatrzymala palnik 5 razy",
    36: "Temperatura zasilania zatrzymala palnik 5 razy",
    41: "Nieudane odpowietrzanie: roznica temperatur",
    43: "Za maly gradient przy starcie palnika",
    44: "Za duza roznica zasilanie-powrot",
    45: "Za wysokie cisnienie powietrza",
    255: "Brak blokady czasowej",
}


class FrameError(ValueError):
    """Ramka ma zly format, identyfikator albo CRC."""


class ResponseTimeout(TimeoutError):
    """Nie otrzymano oczekiwanej, poprawnej ramki w wyznaczonym czasie."""


def crc16_modbus(data: bytes) -> int:
    """CRC16: init 0xFFFF, poly 0xA001."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def frame_crc(frame: bytes) -> int:
    """Zwraca CRC zapisany w ramce jako little-endian."""
    if len(frame) < 3:
        raise FrameError("Ramka jest za krotka, aby zawierala CRC")
    return int.from_bytes(frame[-3:-1], "little")


def valid_frame(frame: bytes) -> bool:
    """Sprawdza STX, dlugosc, ETX oraz CRC calej ramki Remeha."""
    if len(frame) < 8 or frame[0] != 0x02 or frame[-1] != 0x03:
        return False
    if frame[4] + 2 != len(frame):
        return False
    return frame_crc(frame) == crc16_modbus(frame[1:-3])


def is_sample_frame(frame: bytes) -> bool:
    return (
        len(frame) == SAMPLE_FRAME_LENGTH
        and frame[1:7] == SAMPLE_RESPONSE_PREFIX
        and valid_frame(frame)
    )


def is_counter_frame(frame: bytes, record: int) -> bool:
    return (
        len(frame) == COUNTER_FRAME_LENGTH
        and frame[1:6] == COUNTER_RESPONSE_PREFIX
        and frame[6] == record
        and valid_frame(frame)
    )


class RemehaFrameStream:
    """Sklada ramki z dowolnie pocietych fragmentow TCP i resynchronizuje szum."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._frames: list[bytes] = []
        self.invalid_frames = 0
        self.discarded_bytes = 0

    def reset(self) -> None:
        self.discarded_bytes += len(self._buffer)
        self._buffer.clear()
        self._frames.clear()

    def feed(self, data: bytes) -> None:
        self._buffer.extend(data)
        while True:
            start = self._buffer.find(b"\x02")
            if start < 0:
                self.discarded_bytes += len(self._buffer)
                self._buffer.clear()
                return
            if start:
                self.discarded_bytes += start
                del self._buffer[:start]
            if len(self._buffer) < 5:
                return

            expected_length = self._buffer[4] + 2
            if expected_length < 8 or expected_length > 257:
                self.discarded_bytes += 1
                del self._buffer[0]
                continue
            if len(self._buffer) < expected_length:
                return

            candidate = bytes(self._buffer[:expected_length])
            if valid_frame(candidate):
                del self._buffer[:expected_length]
                self._frames.append(candidate)
            else:
                self.invalid_frames += 1
                self.discarded_bytes += 1
                del self._buffer[0]

    def pop(self) -> bytes | None:
        return self._frames.pop(0) if self._frames else None


def _u16_le(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little", signed=False)


def _s16_le(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little", signed=True)


def _u16_be(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big", signed=False)


def _temperature(
    data: bytes,
    offset: int,
    minimum: float = -60.0,
    maximum: float = 150.0,
) -> float | None:
    raw = _u16_le(data, offset)
    if raw in INVALID_16BIT_VALUES:
        return None
    value = round(_s16_le(data, offset) / 100.0, 2)
    return value if minimum <= value <= maximum else None


def _scaled_s16(
    data: bytes,
    offset: int,
    scale: float,
    minimum: float,
    maximum: float,
) -> float | None:
    raw = _u16_le(data, offset)
    if raw in INVALID_16BIT_VALUES:
        return None
    value = round(_s16_le(data, offset) * scale, 2)
    return value if minimum <= value <= maximum else None


def _bit(value: int, number: int) -> bool:
    return bool((value >> number) & 1)


def _code_text(mapping: dict[int, str], code: int, kind: str) -> str:
    return mapping.get(code, f"Nieznany {kind} ({code})")


def decode_sample_frame(frame: bytes) -> dict[str, Any]:
    """Dekoduje 74-bajtowa odpowiedz Sample Data PCU-05_P3."""
    if not is_sample_frame(frame):
        raise FrameError("To nie jest poprawna 74-bajtowa ramka Sample Data MCR3+")

    state = frame[47]
    lockout = frame[48]
    blocking = frame[49]
    sub_state = frame[50]
    demand = frame[43]
    inputs = frame[44]
    valves = frame[45]
    pumps = frame[46]
    hru = frame[57]

    return {
        "status_code": state,
        "status": _code_text(STATUS_CODES, state, "stan"),
        "sub_status_code": sub_state,
        "sub_status": _code_text(SUB_STATUS_CODES, sub_state, "podstan"),
        "lockout_code": lockout,
        "lockout": _code_text(LOCKOUT_CODES, lockout, "kod blokady trwalej"),
        "blocking_code": blocking,
        "blocking": _code_text(BLOCKING_CODES, blocking, "kod blokady czasowej"),
        "fault_active": lockout != 0xFF or blocking != 0xFF,
        "burner_active": state in {2, 3, 4},
        "ch_active": state == 3,
        "dhw_active": state == 4,
        "flow_temp": _temperature(frame, 7),
        "return_temp": _temperature(frame, 9),
        "dhw_in_temp": _temperature(frame, 11),
        "outside_temp": _temperature(frame, 13, -60.0, 80.0),
        "calorifier_temp": _temperature(frame, 15),
        "boiler_control_temp": _temperature(frame, 19),
        "room_temp": _temperature(frame, 21, -20.0, 60.0),
        "ch_setpoint": _temperature(frame, 23),
        "dhw_setpoint": _temperature(frame, 25),
        "room_setpoint": _temperature(frame, 27, -20.0, 60.0),
        "fan_speed_setpoint": _u16_le(frame, 29),
        "fan_speed": _u16_le(frame, 31),
        "ionisation_current": round(frame[33] / 10.0, 1),
        "internal_setpoint": _temperature(frame, 34),
        "available_power": frame[36],
        "pump_percentage": frame[37],
        "desired_max_power": frame[39],
        "actual_power": frame[40],
        "demand_source_raw": demand,
        "input_flags_raw": inputs,
        "valve_flags_raw": valves,
        "pump_flags_raw": pumps,
        "modulating_controller_connected": _bit(demand, 0),
        "heat_demand_modulating": _bit(demand, 1),
        "heat_demand_onoff": _bit(demand, 2),
        "frost_protection": _bit(demand, 3),
        "dhw_eco_enabled": not _bit(demand, 4),
        "dhw_blocked": _bit(demand, 5),
        "anti_legionella": _bit(demand, 6),
        "dhw_heat_demand": _bit(demand, 7),
        "shutdown_input_closed": not _bit(inputs, 0),
        "release_input_closed": not _bit(inputs, 1),
        "ionisation_detected": _bit(inputs, 2),
        "dhw_flow_switch_closed": _bit(inputs, 3),
        "min_gas_pressure_input_closed": _bit(inputs, 5),
        "ch_enabled": _bit(inputs, 6),
        "dhw_enabled": _bit(inputs, 7),
        "gas_valve_open": not _bit(valves, 0),
        "ignition_active": _bit(valves, 2),
        "three_way_valve": "CWU" if _bit(valves, 3) else "CO",
        "external_three_way_valve_open": not _bit(valves, 4),
        "external_gas_valve_open": not _bit(valves, 6),
        "pump_active": _bit(pumps, 0),
        "calorifier_pump_active": _bit(pumps, 1),
        "external_ch_pump_active": _bit(pumps, 2),
        "status_report_active": _bit(pumps, 4),
        "opentherm_smartpower": _bit(pumps, 7),
        "su_fan_speed": _u16_le(frame, 51),
        "su_state_code": frame[53],
        "su_blocking_code": frame[54],
        # Bajt 56 w tym wariancie jest oznaczony jako nieuzywany. Zachowujemy
        # surowa wartosc diagnostyczna, ale NIE publikujemy jej jako cisnienie.
        "pressure_raw_unsupported": frame[56],
        "hru_flags_raw": hru,
        "hru_active": _bit(hru, 1),
        "ch_timer_enabled": _bit(hru, 6),
        "dhw_timer_enabled": _bit(hru, 7),
        "control_temp": _temperature(frame, 58),
        "dhw_flow_rate": _scaled_s16(frame, 60, 0.01, 0.0, 50.0),
        "solar_temp": _temperature(frame, 63, -60.0, 150.0),
        "hmi_active_value": _u16_le(frame, 65),
        "ch_setpoint_hmi": frame[67],
        "dhw_setpoint_hmi": frame[68],
        "service_mode": frame[69],
        "rs232_mode": frame[70],
    }


def decode_counter_frames(counter_1: bytes, counter_2: bytes) -> dict[str, int]:
    """Dekoduje dwa 26-bajtowe rekordy licznikow 0x1C i 0x1D."""
    if not is_counter_frame(counter_1, 0x1C):
        raise FrameError("Niepoprawna ramka licznika 0x1C")
    if not is_counter_frame(counter_2, 0x1D):
        raise FrameError("Niepoprawna ramka licznika 0x1D")

    return {
        "hours_run_pump": _u16_be(counter_1, 7) * 2,
        "hours_run_3way": _u16_be(counter_1, 9) * 2,
        "hours_run_ch": _u16_be(counter_1, 11) * 2,
        "hours_run_dhw": _u16_be(counter_1, 13),
        "power_supply_hours": _u16_be(counter_1, 15) * 2,
        "pump_starts": _u16_be(counter_1, 17) * 8,
        "three_way_valve_cycles": _u16_be(counter_1, 19) * 8,
        "burner_starts_dhw": _u16_be(counter_1, 21) * 8,
        "total_burner_starts": _u16_be(counter_2, 7) * 8,
        "failed_burner_starts": _u16_be(counter_2, 9),
        "flame_loss_count": _u16_be(counter_2, 11),
    }


def request_frame(
    sock: socket.socket,
    stream: RemehaFrameStream,
    request: bytes,
    predicate: Callable[[bytes], bool],
    timeout: float,
) -> bytes:
    """Wysyla zapytanie i czeka na pasujaca, kompletna ramke z dobrym CRC."""
    while stream.pop() is not None:
        pass
    sock.sendall(request)
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(1024)
        except socket.timeout:
            continue
        if not chunk:
            raise ConnectionError("ESP zamknal polaczenie TCP")
        stream.feed(chunk)
        while True:
            frame = stream.pop()
            if frame is None:
                break
            if predicate(frame):
                return frame
            LOG.debug("Pominieto nieoczekiwana ramke: %s", frame.hex(" "))

    stream.reset()
    raise ResponseTimeout(f"Brak poprawnej odpowiedzi przez {timeout:.1f} s")


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} musi byc liczba, otrzymano: {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} musi byc >= {minimum}")
    return value


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} musi byc liczba calkowita, otrzymano: {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} musi byc >= {minimum}")
    return value


@dataclass(frozen=True)
class Settings:
    esp_host: str
    esp_port: int
    mqtt_host: str
    mqtt_port: int
    mqtt_user: str
    mqtt_password: str
    poll_interval: float
    counter_interval: float
    response_timeout: float
    reconnect_delay: float
    mqtt_topic_prefix: str
    ha_discovery_prefix: str
    device_id: str
    device_name: str
    mqtt_client_id: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            esp_host=os.getenv("ESP_HOST", "IP_ESP"),
            esp_port=_env_int("ESP_PORT", 999, 1),
            mqtt_host=os.getenv("MQTT_HOST", "IP_Broker"),
            mqtt_port=_env_int("MQTT_PORT", 1883, 1),
            mqtt_user=os.getenv("MQTT_USER", ""),
            mqtt_password=os.getenv("MQTT_PASS", ""),
            poll_interval=_env_float("POLL_INTERVAL", 5.0, 0.2),
            counter_interval=_env_float("COUNTER_INTERVAL", 600.0, 0.0),
            response_timeout=_env_float("RESPONSE_TIMEOUT", 3.0, 0.2),
            reconnect_delay=_env_float("RECONNECT_DELAY", 10.0, 0.0),
            mqtt_topic_prefix=os.getenv("MQTT_TOPIC_PREFIX", "remeha").strip("/"),
            ha_discovery_prefix=os.getenv("HA_DISCOVERY_PREFIX", "homeassistant").strip("/"),
            # Ten sam domyslny identyfikator co w starym projekcie: encje zostana
            # zaktualizowane zamiast utworzenia drugiego urzadzenia w HA.
            device_id=os.getenv("DEVICE_ID", "dedietrich_mcr3"),
            device_name=os.getenv("DEVICE_NAME", "De Dietrich MCR3+ 24 kW"),
            mqtt_client_id=os.getenv("MQTT_CLIENT_ID", "remeha_bridge"),
        )


def _sensor(
    name: str,
    *,
    unit: str | None = None,
    device_class: str | None = None,
    state_class: str | None = None,
    icon: str | None = None,
    entity_category: str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    return {
        "component": "sensor",
        "name": name,
        "unit": unit,
        "device_class": device_class,
        "state_class": state_class,
        "icon": icon,
        "entity_category": entity_category,
        "enabled": enabled,
    }


def _binary(
    name: str,
    *,
    device_class: str | None = None,
    icon: str | None = None,
    entity_category: str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    return {
        "component": "binary_sensor",
        "name": name,
        "device_class": device_class,
        "icon": icon,
        "entity_category": entity_category,
        "enabled": enabled,
    }


SENSOR_CONFIG: dict[str, dict[str, Any]] = {
    "status": _sensor("Status pieca", icon="mdi:fire"),
    "sub_status": _sensor("Podstatus pieca", icon="mdi:fire-circle"),
    "lockout": _sensor("Blokada trwala", icon="mdi:alert-octagon", entity_category="diagnostic"),
    "blocking": _sensor("Blokada czasowa", icon="mdi:alert", entity_category="diagnostic"),
    "status_code": _sensor("Kod stanu", icon="mdi:code-braces", entity_category="diagnostic", enabled=False),
    "sub_status_code": _sensor("Kod podstanu", icon="mdi:code-braces", entity_category="diagnostic", enabled=False),
    "lockout_code": _sensor("Kod blokady trwalej", icon="mdi:code-braces", entity_category="diagnostic", enabled=False),
    "blocking_code": _sensor("Kod blokady czasowej", icon="mdi:code-braces", entity_category="diagnostic", enabled=False),
    "flow_temp": _sensor("Temp. zasilania", unit="°C", device_class="temperature", state_class="measurement"),
    "return_temp": _sensor("Temp. powrotu", unit="°C", device_class="temperature", state_class="measurement"),
    "dhw_in_temp": _sensor("Temp. doplywu CWU", unit="°C", device_class="temperature", state_class="measurement", enabled=False),
    "outside_temp": _sensor("Temp. zewnetrzna", unit="°C", device_class="temperature", state_class="measurement"),
    "calorifier_temp": _sensor("Temp. zasobnika CWU", unit="°C", device_class="temperature", state_class="measurement"),
    "boiler_control_temp": _sensor("Temp. kontrolna kotla", unit="°C", device_class="temperature", state_class="measurement"),
    "room_temp": _sensor("Temp. pomieszczenia OpenTherm", unit="°C", device_class="temperature", state_class="measurement"),
    "ch_setpoint": _sensor("Zadana temp. CO ze sterownika", unit="°C", device_class="temperature", state_class="measurement"),
    "dhw_setpoint": _sensor("Zadana temp. zasilania CWU", unit="°C", device_class="temperature", state_class="measurement"),
    "room_setpoint": _sensor("Zadana temp. pomieszczenia", unit="°C", device_class="temperature", state_class="measurement"),
    "internal_setpoint": _sensor("Wewnetrzna temp. zadana", unit="°C", device_class="temperature", state_class="measurement", entity_category="diagnostic"),
    "control_temp": _sensor("Temperatura regulacji", unit="°C", device_class="temperature", state_class="measurement", entity_category="diagnostic"),
    "solar_temp": _sensor("Temperatura solarna", unit="°C", device_class="temperature", state_class="measurement", enabled=False),
    "ch_setpoint_hmi": _sensor("Zadana CO na HMI", unit="°C", device_class="temperature", state_class="measurement"),
    "dhw_setpoint_hmi": _sensor("Zadana CWU na HMI", unit="°C", device_class="temperature", state_class="measurement"),
    "fan_speed_setpoint": _sensor("Zadana predkosc wentylatora", unit="rpm", state_class="measurement", icon="mdi:fan"),
    "fan_speed": _sensor("Predkosc wentylatora", unit="rpm", state_class="measurement", icon="mdi:fan"),
    "su_fan_speed": _sensor("Predkosc wentylatora SU", unit="rpm", state_class="measurement", icon="mdi:fan", enabled=False),
    "ionisation_current": _sensor("Prad jonizacji", unit="µA", state_class="measurement", icon="mdi:flash"),
    "available_power": _sensor("Dostepna moc", unit="%", state_class="measurement", icon="mdi:percent"),
    "pump_percentage": _sensor("Sterowanie pompa", unit="%", state_class="measurement", icon="mdi:pump"),
    "desired_max_power": _sensor("Zadana maksymalna moc", unit="%", state_class="measurement", icon="mdi:percent"),
    "actual_power": _sensor("Aktualna moc kotla", unit="%", state_class="measurement", icon="mdi:percent"),
    "dhw_flow_rate": _sensor("Przeplyw CWU", unit="L/min", state_class="measurement", icon="mdi:waves-arrow-right"),
    "three_way_valve": _sensor("Polozenie zaworu 3-drogowego", icon="mdi:valve"),
    "fault_active": _binary("Awaria kotla", device_class="problem"),
    "burner_active": _binary("Palnik aktywny", device_class="heat", icon="mdi:fire"),
    "ch_active": _binary("Grzanie CO", device_class="heat", icon="mdi:radiator"),
    "dhw_active": _binary("Grzanie CWU", device_class="heat", icon="mdi:water-boiler"),
    "pump_active": _binary("Pompa aktywna", icon="mdi:pump"),
    "ionisation_detected": _binary("Wykryty plomien", device_class="heat", icon="mdi:fire"),
    "modulating_controller_connected": _binary("Sterownik modulujacy podlaczony", device_class="connectivity", enabled=False),
    "heat_demand_modulating": _binary("Zadanie CO ze sterownika modulujacego", icon="mdi:radiator"),
    "heat_demand_onoff": _binary("Zadanie CO ze sterownika ON/OFF", icon="mdi:radiator", enabled=False),
    "frost_protection": _binary("Ochrona przeciw zamarzaniu", device_class="cold", enabled=False),
    "dhw_eco_enabled": _binary("Tryb CWU Eco", icon="mdi:leaf"),
    "dhw_blocked": _binary("CWU zablokowane", device_class="problem", enabled=False),
    "anti_legionella": _binary("Program anty-Legionella", icon="mdi:bacteria", enabled=False),
    "dhw_heat_demand": _binary("Zadanie grzania CWU", icon="mdi:water-boiler"),
    "ch_enabled": _binary("CO dozwolone", icon="mdi:radiator", enabled=False),
    "dhw_enabled": _binary("CWU dozwolone", icon="mdi:water-boiler", enabled=False),
    "gas_valve_open": _binary("Zawor gazowy otwarty", icon="mdi:valve", enabled=False),
    "ignition_active": _binary("Zaplon aktywny", icon="mdi:lightning-bolt", enabled=False),
    "hru_active": _binary("HRU aktywne", enabled=False),
    "ch_timer_enabled": _binary("Program czasowy CO", enabled=False),
    "dhw_timer_enabled": _binary("Program czasowy CWU", enabled=False),
    "hours_run_pump": _sensor("Godziny pracy pompy", unit="h", state_class="total_increasing", icon="mdi:clock-outline", entity_category="diagnostic"),
    "hours_run_3way": _sensor("Godziny pracy zaworu 3-drogowego", unit="h", state_class="total_increasing", icon="mdi:clock-outline", entity_category="diagnostic"),
    "hours_run_ch": _sensor("Godziny pracy CO", unit="h", state_class="total_increasing", icon="mdi:clock-outline", entity_category="diagnostic"),
    "hours_run_dhw": _sensor("Godziny pracy CWU", unit="h", state_class="total_increasing", icon="mdi:clock-outline", entity_category="diagnostic"),
    "power_supply_hours": _sensor("Godziny zasilania", unit="h", state_class="total_increasing", icon="mdi:clock-outline", entity_category="diagnostic"),
    "pump_starts": _sensor("Starty pompy", state_class="total_increasing", icon="mdi:counter", entity_category="diagnostic"),
    "three_way_valve_cycles": _sensor("Cykle zaworu 3-drogowego", state_class="total_increasing", icon="mdi:counter", entity_category="diagnostic"),
    "burner_starts_dhw": _sensor("Starty palnika CWU", state_class="total_increasing", icon="mdi:counter", entity_category="diagnostic"),
    "total_burner_starts": _sensor("Starty palnika lacznie", state_class="total_increasing", icon="mdi:counter", entity_category="diagnostic"),
    "failed_burner_starts": _sensor("Nieudane starty palnika", state_class="total_increasing", icon="mdi:alert-circle", entity_category="diagnostic"),
    "flame_loss_count": _sensor("Utraty plomienia", state_class="total_increasing", icon="mdi:fire-off", entity_category="diagnostic"),
}


class MqttPublisher:
    def __init__(self, settings: Settings) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise RuntimeError(
                "Brak paho-mqtt. Zainstaluj: pip install paho-mqtt==1.6.1"
            ) from exc

        self.settings = settings
        self.mqtt = mqtt
        # Konstruktor pozostaje zgodny zarowno z paho-mqtt 1.6, jak i 2.x.
        self.client = mqtt.Client(client_id=settings.mqtt_client_id)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.connected = threading.Event()
        self.esp_online = False
        self.status_topic = f"{settings.mqtt_topic_prefix}/status"
        self.state_topic = f"{settings.mqtt_topic_prefix}/state"

        if settings.mqtt_user:
            self.client.username_pw_set(settings.mqtt_user, settings.mqtt_password)
        self.client.will_set(self.status_topic, "offline", qos=1, retain=True)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

    @staticmethod
    def _rc_value(rc: Any) -> int:
        try:
            return int(rc)
        except (TypeError, ValueError):
            return int(getattr(rc, "value", -1))

    def _on_connect(
        self,
        client: Any,
        userdata: Any,
        flags: Any,
        rc: Any,
        properties: Any = None,
    ) -> None:
        code = self._rc_value(rc)
        if code != 0:
            LOG.error("Broker MQTT odrzucil polaczenie, rc=%s", code)
            return
        self.connected.set()
        LOG.info("Polaczono z MQTT")
        self.publish_discovery()
        self.publish_availability(self.esp_online)

    def _on_disconnect(
        self,
        client: Any,
        userdata: Any,
        rc: Any,
        properties: Any = None,
    ) -> None:
        self.connected.clear()
        code = self._rc_value(rc)
        if code:
            LOG.warning("Utracono polaczenie MQTT, rc=%s; paho sprobuje ponownie", code)

    def start(self) -> None:
        LOG.info(
            "Laczenie z MQTT %s:%s",
            self.settings.mqtt_host,
            self.settings.mqtt_port,
        )
        self.client.connect(self.settings.mqtt_host, self.settings.mqtt_port, 60)
        self.client.loop_start()
        if not self.connected.wait(10):
            self.client.loop_stop()
            raise ConnectionError("Broker MQTT nie potwierdzil polaczenia w 10 s")

    def _publish_json(self, topic: str, value: dict[str, Any], retain: bool) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        info = self.client.publish(topic, payload, qos=0, retain=retain)
        if info.rc != self.mqtt.MQTT_ERR_SUCCESS:
            LOG.warning("Nie udalo sie zakolejkowac publikacji MQTT %s: rc=%s", topic, info.rc)

    def publish_discovery(self) -> None:
        s = self.settings
        device = {
            "identifiers": [s.device_id],
            "name": s.device_name,
            "manufacturer": "De Dietrich / Remeha",
            "model": "MCR3+ 24 kW (PCU-05_P3)",
            "sw_version": VERSION,
        }

        for key, cfg in SENSOR_CONFIG.items():
            component = cfg["component"]
            topic = f"{s.ha_discovery_prefix}/{component}/{s.device_id}/{key}/config"
            payload: dict[str, Any] = {
                "name": cfg["name"],
                "unique_id": f"{s.device_id}_{key}",
                "state_topic": self.state_topic,
                "availability_topic": self.status_topic,
                "device": device,
                "enabled_by_default": cfg.get("enabled", True),
            }
            if component == "binary_sensor":
                payload["value_template"] = (
                    "{{ 'ON' if value_json." + key + " else 'OFF' }}"
                )
                payload["payload_on"] = "ON"
                payload["payload_off"] = "OFF"
            else:
                payload["value_template"] = "{{ value_json." + key + " }}"

            for source, target in (
                ("unit", "unit_of_measurement"),
                ("device_class", "device_class"),
                ("state_class", "state_class"),
                ("icon", "icon"),
                ("entity_category", "entity_category"),
            ):
                if cfg.get(source) is not None:
                    payload[target] = cfg[source]
            self._publish_json(topic, payload, retain=True)

        # Poprzedni interpreter tworzyl ten sensor z blednego offsetu. Pusta,
        # retained wiadomosc usuwa tylko jego stara konfiguracje discovery.
        obsolete = (
            f"{s.ha_discovery_prefix}/sensor/{s.device_id}/water_pressure/config"
        )
        self.client.publish(obsolete, "", qos=0, retain=True)
        LOG.info("Opublikowano konfiguracje Home Assistant MQTT Discovery")

    def publish_availability(self, online: bool) -> None:
        self.client.publish(
            self.status_topic,
            "online" if online else "offline",
            qos=1,
            retain=True,
        )

    def set_esp_online(self, online: bool) -> None:
        changed = online != self.esp_online
        self.esp_online = online
        if self.connected.is_set() and (changed or not online):
            self.publish_availability(online)

    def publish_state(self, data: dict[str, Any]) -> None:
        self._publish_json(self.state_topic, data, retain=True)

    def stop(self) -> None:
        if self.connected.is_set():
            info = self.client.publish(self.status_topic, "offline", qos=1, retain=True)
            try:
                try:
                    info.wait_for_publish(timeout=2)
                except TypeError:
                    # paho-mqtt 1.6 nie przyjmuje jeszcze argumentu timeout.
                    info.wait_for_publish()
            except (RuntimeError, ValueError):
                pass
        self.client.disconnect()
        self.client.loop_stop()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_bridge(settings: Settings, stop_event: threading.Event) -> None:
    if settings.esp_host in {"", "IP_ESP"}:
        raise ValueError("Ustaw ESP_HOST na adres mostka ESP8266")
    if settings.mqtt_host in {"", "IP_Broker"}:
        raise ValueError("Ustaw MQTT_HOST na adres brokera MQTT")

    publisher = MqttPublisher(settings)
    publisher.start()
    state_cache: dict[str, Any] = {}

    try:
        while not stop_event.is_set():
            sock: socket.socket | None = None
            publisher.set_esp_online(False)
            try:
                LOG.info("Laczenie z ESP %s:%s", settings.esp_host, settings.esp_port)
                sock = socket.create_connection(
                    (settings.esp_host, settings.esp_port), timeout=10
                )
                sock.settimeout(0.5)
                stream = RemehaFrameStream()
                next_counter_read = time.monotonic()
                LOG.info("Polaczono z ESP; oczekiwanie na pierwsza poprawna ramke")

                while not stop_event.is_set():
                    frame = request_frame(
                        sock,
                        stream,
                        SAMPLE_REQUEST,
                        is_sample_frame,
                        settings.response_timeout,
                    )
                    sample = decode_sample_frame(frame)
                    state_cache.update(sample)
                    state_cache["received_at"] = _utc_now()
                    publisher.set_esp_online(True)
                    publisher.publish_state(state_cache)

                    LOG.info(
                        "Stan: %s | zasilanie: %s °C | powrot: %s °C | "
                        "CWU: %s °C | moc: %s%% | wentylator: %s rpm",
                        sample["status"],
                        sample["flow_temp"],
                        sample["return_temp"],
                        sample["calorifier_temp"],
                        sample["actual_power"],
                        sample["fan_speed"],
                    )

                    now = time.monotonic()
                    if settings.counter_interval > 0 and now >= next_counter_read:
                        try:
                            c1 = request_frame(
                                sock,
                                stream,
                                COUNTER_1_REQUEST,
                                lambda value: is_counter_frame(value, 0x1C),
                                settings.response_timeout,
                            )
                            c2 = request_frame(
                                sock,
                                stream,
                                COUNTER_2_REQUEST,
                                lambda value: is_counter_frame(value, 0x1D),
                                settings.response_timeout,
                            )
                            state_cache.update(decode_counter_frames(c1, c2))
                            state_cache["received_at"] = _utc_now()
                            publisher.publish_state(state_cache)
                            LOG.info("Zaktualizowano liczniki kotla")
                        except ResponseTimeout as exc:
                            # Niektore bramki TCP moga odrzucac szybsza serie zapytan.
                            # Dane biezace nadal sa poprawne, wiec nie zrywamy polaczenia.
                            LOG.warning("Nie odczytano licznikow: %s", exc)
                        next_counter_read = time.monotonic() + settings.counter_interval

                    stop_event.wait(settings.poll_interval)

            except (OSError, ConnectionError, ResponseTimeout, FrameError) as exc:
                publisher.set_esp_online(False)
                if not stop_event.is_set():
                    LOG.error("Blad polaczenia lub ramki ESP: %s", exc)
                    LOG.info("Ponowna proba za %.1f s", settings.reconnect_delay)
                    stop_event.wait(settings.reconnect_delay)
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
    finally:
        publisher.set_esp_online(False)
        publisher.stop()


def _capture_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    # Najpierw sprawdzamy, czy plik sam w sobie jest strumieniem binarnym.
    binary_probe = RemehaFrameStream()
    binary_probe.feed(raw)
    if binary_probe.pop() is not None:
        return raw

    text = raw.decode("utf-8", errors="replace")
    blocks = re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.DOTALL)
    candidates = blocks if blocks else [text]
    source = max(
        candidates,
        key=lambda value: len(re.findall(r"(?i)\b[0-9a-f]{2}\b", value)),
    )
    tokens = re.findall(r"(?i)\b[0-9a-f]{2}\b", source)
    if tokens:
        return bytes(int(token, 16) for token in tokens)

    # Obsluga zrzutu bez spacji, np. 0201FE0648...
    compact = re.sub(r"(?i)0x", "", source)
    compact = re.sub(r"[\s,;:|_-]+", "", compact)
    if re.fullmatch(r"(?i)[0-9a-f]+", compact or "") and len(compact) % 2 == 0:
        return bytes.fromhex(compact)
    raise ValueError(f"Nie znaleziono bajtow hex w pliku {path}")


def _decode_capture(raw: bytes) -> tuple[list[dict[str, Any]], RemehaFrameStream]:
    stream = RemehaFrameStream()
    stream.feed(raw)
    decoded: list[dict[str, Any]] = []
    while True:
        frame = stream.pop()
        if frame is None:
            break
        if is_sample_frame(frame):
            decoded.append(decode_sample_frame(frame))
    return decoded, stream


def print_capture(raw: bytes, json_lines: bool) -> int:
    decoded, stream = _decode_capture(raw)
    if not decoded:
        print(
            "Nie znaleziono poprawnych ramek Sample Data MCR3+ "
            f"(bledne kandydaty: {stream.invalid_frames}).",
            file=sys.stderr,
        )
        return 2

    if json_lines:
        for item in decoded:
            print(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
    else:
        print(f"Poprawne ramki Sample Data: {len(decoded)}")
        print(f"Kandydaty odrzucone przez format/CRC: {stream.invalid_frames}")
        print("Pierwsza ramka:")
        print(json.dumps(decoded[0], ensure_ascii=False, indent=2))
        if len(decoded) > 1:
            print("Ostatnia ramka:")
            print(json.dumps(decoded[-1], ensure_ascii=False, indent=2))
    return 0


SAMPLE_TEST_FRAME = bytes.fromhex(
    "02 01 FE 06 48 02 01 0A 11 0A 11 80 F3 D0 BC 1C 11 00 80 "
    "D2 0F 30 09 D0 07 94 11 08 07 00 00 00 00 00 BC 02 00 00 "
    "00 64 00 00 00 11 C2 03 10 00 FF FF 00 00 00 00 FF FF 00 "
    "C0 BC 02 00 00 00 00 80 47 03 32 2D 00 00 34 3C 03"
)

COUNTER_1_TEST_FRAME = bytes.fromhex(
    "02 00 FE 06 18 10 1C 0A 32 1F 97 06 B8 01 99 28 D1 0D 7E "
    "0B B3 08 56 AE C6 03"
)

COUNTER_2_TEST_FRAME = bytes.fromhex(
    "02 00 FE 06 18 10 1D 11 8B 00 0D 00 03 00 00 00 00 00 00 "
    "00 5D 5D 37 90 2B 03"
)


def self_test() -> int:
    assert valid_frame(SAMPLE_REQUEST)
    assert valid_frame(COUNTER_1_REQUEST)
    assert valid_frame(COUNTER_2_REQUEST)
    assert is_sample_frame(SAMPLE_TEST_FRAME)
    assert frame_crc(SAMPLE_TEST_FRAME) == 0x3C34

    sample = decode_sample_frame(SAMPLE_TEST_FRAME)
    expected_sample = {
        "flow_temp": 43.62,
        "return_temp": 43.62,
        "dhw_in_temp": None,
        "outside_temp": None,
        "calorifier_temp": 43.8,
        "boiler_control_temp": 40.5,
        "room_temp": 23.52,
        "ch_setpoint": 20.0,
        "dhw_setpoint": 45.0,
        "room_setpoint": 18.0,
        "fan_speed": 0,
        "internal_setpoint": 7.0,
        "desired_max_power": 100,
        "actual_power": 0,
        "status_code": 0,
        "sub_status_code": 0,
        "lockout_code": 255,
        "blocking_code": 255,
        "ch_setpoint_hmi": 50,
        "dhw_setpoint_hmi": 45,
    }
    for key, expected in expected_sample.items():
        assert sample[key] == expected, (key, sample[key], expected)

    counters = decode_counter_frames(COUNTER_1_TEST_FRAME, COUNTER_2_TEST_FRAME)
    expected_counters = {
        "hours_run_pump": 5220,
        "hours_run_3way": 16174,
        "hours_run_ch": 3440,
        "hours_run_dhw": 409,
        "power_supply_hours": 20898,
        "pump_starts": 27632,
        "three_way_valve_cycles": 23960,
        "burner_starts_dhw": 17072,
        "total_burner_starts": 35928,
        "failed_burner_starts": 13,
        "flame_loss_count": 3,
    }
    assert counters == expected_counters, (counters, expected_counters)

    stream = RemehaFrameStream()
    stream.feed(b"\x99\x00" + SAMPLE_TEST_FRAME[:19])
    assert stream.pop() is None
    stream.feed(SAMPLE_TEST_FRAME[19:])
    assert stream.pop() == SAMPLE_TEST_FRAME

    corrupted = bytearray(SAMPLE_TEST_FRAME)
    corrupted[20] ^= 0x01
    bad_stream = RemehaFrameStream()
    bad_stream.feed(bytes(corrupted))
    assert bad_stream.pop() is None
    assert bad_stream.invalid_frames >= 1

    print(
        "SELF-TEST OK: CRC, skladanie strumienia, Sample Data i liczniki "
        "MCR3+ PCU-05_P3."
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Interpreter De Dietrich MCR3+ 24 kW / PCU-05_P3"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--self-test", action="store_true", help="uruchom test wbudowany")
    modes.add_argument("--decode-file", type=Path, help="zdekoduj tekstowy lub binarny zrzut")
    modes.add_argument("--decode-hex", help="zdekoduj ciag bajtow szesnastkowych")
    parser.add_argument(
        "--json-lines",
        action="store_true",
        help="wypisz kazda znaleziona ramke jako osobny JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )

    try:
        if args.self_test:
            return self_test()
        if args.decode_file:
            return print_capture(_capture_bytes(args.decode_file), args.json_lines)
        if args.decode_hex:
            tokens = re.findall(r"(?i)\b[0-9a-f]{2}\b", args.decode_hex)
            if tokens:
                raw = bytes(int(token, 16) for token in tokens)
            else:
                compact = re.sub(r"(?i)0x", "", args.decode_hex)
                compact = re.sub(r"[\s,;:|_-]+", "", compact)
                if not re.fullmatch(r"(?i)[0-9a-f]+", compact or "") or len(compact) % 2:
                    raise ValueError("Nie znaleziono poprawnych bajtow hex w --decode-hex")
                raw = bytes.fromhex(compact)
            return print_capture(raw, args.json_lines)

        settings = Settings.from_env()
        stop_event = threading.Event()

        def stop_handler(signum: int, frame: Any) -> None:
            LOG.info("Otrzymano sygnal %s, zatrzymywanie", signum)
            stop_event.set()

        signal.signal(signal.SIGINT, stop_handler)
        signal.signal(signal.SIGTERM, stop_handler)
        run_bridge(settings, stop_event)
        return 0
    except (OSError, RuntimeError, ValueError, FrameError) as exc:
        LOG.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

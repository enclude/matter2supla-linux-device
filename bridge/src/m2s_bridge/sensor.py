# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Czujniki Matter -> kanały SUPLA (kanały `...Parsed` w supla-device).

Typy urządzeń w devices.yaml i ich kanały SUPLA:

  leak     czujnik zalania (BooleanState, typ urządzenia 0x0043)   BinaryParsed
           stan `{"state": 1|0}` - 1 = wykryto wodę
  climate  temperatura i wilgotność (TemperatureMeasurement 0x0402 +   ThermHygroMeterParsed
           RelativeHumidityMeasurement 0x0405, mogą być na osobnych
           endpointach - TIMMERFLOTTE: temperatura na 1, wilgotność na 2)
           stan `{"temperature": °C, "humidity": %}`

Stan to JSON z dokładnie tymi kluczami, które są zapisane dla urządzenia, albo pusty komunikat,
gdy urządzenie jest niedostępne lub nie podaje któregoś z pomiarów (tak samo jak dla licznika:
supla-device odrzuca cały odczyt, gdy brakuje zmapowanego klucza). Poziom baterii (`battery`,
0-100) dochodzi do stanu tylko wtedy, gdy urządzenie go podaje - decyzja zapada przy dodaniu
kanału i jest zapisana w `measurements`.
"""

from __future__ import annotations

from typing import Any

BOOLEAN_STATE_CLUSTER = 0x0045
TEMPERATURE_CLUSTER = 0x0402  # MeasuredValue: 0,01 °C, null = brak pomiaru
HUMIDITY_CLUSTER = 0x0405  # MeasuredValue: 0,01 %
POWER_SOURCE_CLUSTER = 0x002F
BATTERY_ATTRIBUTE = 0x000C  # BatPercentRemaining: 0-200, jednostka 0,5 %
LEAK_DEVICE_TYPE = 0x0043  # Water Leak Detector

SENSOR_TYPES = ("leak", "climate")
BATTERY = "battery"


def slot_of(dev_type: str) -> str:
    """Miejsce urządzenia na endpoincie: licznik i czujniki mogą dzielić endpoint z innymi urządzeniami."""
    return dev_type if dev_type == "meter" or dev_type in SENSOR_TYPES else ""


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _device_types(attributes: dict[str, Any], endpoint: int) -> set[int]:
    listed = attributes.get(f"{endpoint}/29/0")
    types: set[int] = set()
    for item in listed if isinstance(listed, list) else []:
        if isinstance(item, dict) and isinstance(item.get("0"), int):
            types.add(item["0"])
    return types


def _endpoints(attributes: dict[str, Any]) -> list[int]:
    return sorted({int(path.split("/")[0]) for path in attributes if path.count("/") == 2})


def slots(attributes: dict[str, Any]) -> list[tuple[str, int]]:
    """Czujniki, które udostępnia węzeł, jako pary (typ, endpoint) - w stałej kolejności."""
    found: list[tuple[str, int]] = []
    for endpoint in _endpoints(attributes):
        if endpoint == 0:
            continue
        flag = attributes.get(f"{endpoint}/{BOOLEAN_STATE_CLUSTER}/0")
        if LEAK_DEVICE_TYPE in _device_types(attributes, endpoint) and isinstance(flag, bool):
            found.append(("leak", endpoint))
    temperature = _first_endpoint(attributes, TEMPERATURE_CLUSTER)
    if temperature is not None and _first_endpoint(attributes, HUMIDITY_CLUSTER) is not None:
        found.append(("climate", temperature))
    return found


def _first_endpoint(attributes: dict[str, Any], cluster: int) -> int | None:
    for endpoint in _endpoints(attributes):
        if endpoint != 0 and f"{endpoint}/{cluster}/0" in attributes:
            return endpoint
    return None


def humidity_endpoint(attributes: dict[str, Any], endpoint: int) -> int | None:
    """Endpoint z wilgotnością dla czujnika `climate` o temperaturze na `endpoint`."""
    if f"{endpoint}/{HUMIDITY_CLUSTER}/0" in attributes:
        return endpoint
    return _first_endpoint(attributes, HUMIDITY_CLUSTER)


def is_sensor_endpoint(attributes: dict[str, Any], endpoint: int) -> bool:
    return any(e == endpoint for _, e in slots(attributes))


def battery_percent(attributes: dict[str, Any]) -> int | None:
    """Poziom baterii węzła w procentach (PowerSource z dowolnego endpointu) albo None."""
    for endpoint in _endpoints(attributes):
        raw = _number(attributes.get(f"{endpoint}/{POWER_SOURCE_CLUSTER}/{BATTERY_ATTRIBUTE}"))
        if raw is not None:
            return max(0, min(100, round(raw / 2)))
    return None


def detect(attributes: dict[str, Any], dev_type: str, endpoint: int) -> tuple[str, ...]:
    """Pomiary dodatkowe urządzenia (dziś tylko bateria) - do zapisania przy tworzeniu kanału."""
    return (BATTERY,) if battery_percent(attributes) is not None else ()


def _main_values(attributes: dict[str, Any], dev_type: str, endpoint: int) -> dict[str, float | int] | None:
    if dev_type == "leak":
        flag = attributes.get(f"{endpoint}/{BOOLEAN_STATE_CLUSTER}/0")
        return {"state": int(flag)} if isinstance(flag, bool) else None
    if dev_type == "climate":
        humidity_at = humidity_endpoint(attributes, endpoint)
        temperature = _number(attributes.get(f"{endpoint}/{TEMPERATURE_CLUSTER}/0"))
        humidity = _number(attributes.get(f"{humidity_at}/{HUMIDITY_CLUSTER}/0")) if humidity_at is not None else None
        if temperature is None or humidity is None or not -10000 <= temperature <= 20000 or not 0 <= humidity <= 10000:
            return None
        return {"temperature": round(temperature / 100, 2), "humidity": round(humidity / 100, 2)}
    return None


def values(
    attributes: dict[str, Any], dev_type: str, endpoint: int, measurements: tuple[str, ...]
) -> dict[str, float | int] | None:
    """Komplet wartości czujnika albo None, gdy brakuje któregokolwiek."""
    result = _main_values(attributes, dev_type, endpoint)
    if result is None:
        return None
    if BATTERY in measurements:
        level = battery_percent(attributes)
        if level is None:
            return None
        result[BATTERY] = level
    return result

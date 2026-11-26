# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Licznik energii: klastry Matter ElectricalPowerMeasurement / ElectricalEnergyMeasurement.

Jednostki według specyfikacji Matter (klastry 0x0090 i 0x0091) -> jednostki kanału
`ElectricityMeterParsed` w supla-device:

  pomiar      atrybut Matter                                   Matter   SUPLA
  voltage     0x0090/0x000B RMSVoltage, a gdy go brak 0x0004   mV       V
  current     0x0090/0x000C RMSCurrent, a gdy go brak 0x0005   mA       A
  power       0x0090/0x0008 ActivePower                        mW       W
  frequency   0x0090/0x000E Frequency                          mHz      Hz
  energy      0x0091/0x0001 CumulativeEnergyImported,
              pole 0 (Energy) struktury EnergyMeasurementStruct  mWh    kWh

Pole 4 tej struktury to EndSystime (milisekundy od startu urządzenia), nie energia.

supla-device odrzuca cały odczyt, gdy brakuje choć jednej zmapowanej wartości, dlatego
lista pomiarów urządzenia jest ustalana raz, przy dodaniu kanału, i zapisywana
w devices.yaml (`measurements`). Stan to JSON z dokładnie tymi kluczami albo pusty
komunikat, gdy urządzenie jest niedostępne lub nie podaje któregoś z pomiarów.
"""

from __future__ import annotations

import re
from typing import Any

POWER_CLUSTER = 0x0090
ENERGY_CLUSTER = 0x0091

# Kolejność = kolejność kluczy w komunikacie stanu i w devices.yaml.
MEASUREMENTS = ("voltage", "current", "power", "energy", "frequency")

# pomiar -> atrybuty klastra 0x0090 w kolejności pierwszeństwa
_POWER_ATTRIBUTES = {
    "voltage": (0x000B, 0x0004),
    "current": (0x000C, 0x0005),
    "power": (0x0008,),
    "frequency": (0x000E,),
}
_ENERGY_ATTRIBUTE = 0x0001  # CumulativeEnergyImported
_ENERGY_FIELD = "0"  # EnergyMeasurementStruct.Energy

# Częstotliwość sieci poza tym zakresem oznacza, że urządzenie nie trzyma się jednostki
# ze specyfikacji (GRILLPLATS 1.4.6 zgłasza `50`, czyli formalnie 0,05 Hz) — takiego
# pomiaru nie dodajemy do kanału, zamiast zgadywać jednostkę.
_FREQUENCY_RANGE_HZ = (40.0, 70.0)

_METER_PATH = re.compile(rf"^(\d+)/(?:{POWER_CLUSTER}/8|{ENERGY_CLUSTER}/{_ENERGY_ATTRIBUTE})$")


def endpoints(attributes: dict[str, Any]) -> list[int]:
    """Endpointy z pomiarem mocy czynnej lub energii narastająco."""
    return sorted({int(m.group(1)) for path in attributes if (m := _METER_PATH.match(path))})


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _power_path(attributes: dict[str, Any], endpoint: int, measurement: str) -> str | None:
    for attribute in _POWER_ATTRIBUTES[measurement]:
        path = f"{endpoint}/{POWER_CLUSTER}/{attribute}"
        if path in attributes:
            return path
    return None


def read(attributes: dict[str, Any], endpoint: int, measurement: str) -> float | None:
    """Wartość pomiaru w jednostkach SUPLA albo None, gdy urządzenie jej nie podaje."""
    if measurement == "energy":
        struct = attributes.get(f"{endpoint}/{ENERGY_CLUSTER}/{_ENERGY_ATTRIBUTE}")
        raw = _number(struct.get(_ENERGY_FIELD)) if isinstance(struct, dict) else None
        if raw is None or raw < 0:
            return None
        return round(raw / 1_000_000, 6)  # mWh -> kWh
    path = _power_path(attributes, endpoint, measurement)
    raw = _number(attributes.get(path)) if path else None
    if raw is None:
        return None
    if measurement == "current":
        raw = abs(raw)  # SUPLA przyjmuje natężenie bez znaku; kierunek widać w znaku mocy
    return round(raw / 1000, 3)  # mV, mA, mW, mHz -> V, A, W, Hz


def detect(attributes: dict[str, Any], endpoint: int) -> tuple[str, ...]:
    """Pomiary, które endpoint udostępnia — do zapisania przy tworzeniu kanału."""
    found: list[str] = []
    for measurement in MEASUREMENTS:
        if measurement == "energy":
            present = f"{endpoint}/{ENERGY_CLUSTER}/{_ENERGY_ATTRIBUTE}" in attributes
        else:
            present = _power_path(attributes, endpoint, measurement) is not None
        if measurement == "frequency" and present:
            value = read(attributes, endpoint, measurement)
            present = value is not None and _FREQUENCY_RANGE_HZ[0] <= value <= _FREQUENCY_RANGE_HZ[1]
        if present:
            found.append(measurement)
    return tuple(found)


def values(
    attributes: dict[str, Any], endpoint: int, measurements: tuple[str, ...]
) -> dict[str, float] | None:
    """Komplet pomiarów albo None, gdy brakuje któregokolwiek (kanał ma wtedy nie pokazywać nic)."""
    result: dict[str, float] = {}
    for measurement in measurements:
        value = read(attributes, endpoint, measurement)
        if value is None:
            return None
        result[measurement] = value
    return result or None

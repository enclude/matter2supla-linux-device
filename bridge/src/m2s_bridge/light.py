"""Przeliczenia dla świateł: Matter (poziom 1–254, miredy) <-> skala SUPLA (0–100).

Temperatura bieli w SUPLA: 0 = ciepła, 100 = zimna. W Matter miredy rosną w stronę ciepłej.
"""

from __future__ import annotations

from typing import Any

LEVEL_CLUSTER = 0x0008
COLOR_CLUSTER = 0x0300
MAX_LEVEL = 254
DEFAULT_MIREDS = (153, 500)  # zimna, ciepła — gdy urządzenie nie poda własnego zakresu
TRANSITION = 3  # dziesiąte części sekundy: łagodna zmiana zamiast skoku
EXECUTE_IF_OFF = 1  # bit opcji ColorControl: przyjmij zmianę bieli także przy zgaszonym świetle


def level_path(endpoint: int) -> str:
    return f"{endpoint}/{LEVEL_CLUSTER}/0"


def mireds_path(endpoint: int) -> str:
    return f"{endpoint}/{COLOR_CLUSTER}/7"


def mireds_range(attributes: dict[str, Any], endpoint: int) -> tuple[int, int]:
    low = attributes.get(f"{endpoint}/{COLOR_CLUSTER}/16395")
    high = attributes.get(f"{endpoint}/{COLOR_CLUSTER}/16396")
    if isinstance(low, int) and isinstance(high, int) and 0 < low < high:
        return low, high
    return DEFAULT_MIREDS


def level_to_percent(level: int) -> int:
    return max(1, min(100, round(level * 100 / MAX_LEVEL)))


def percent_to_level(percent: int) -> int:
    return max(1, min(MAX_LEVEL, round(percent * MAX_LEVEL / 100)))


def mireds_to_temperature(mireds: int, low: int, high: int) -> int:
    return max(0, min(100, round((high - mireds) * 100 / (high - low))))


def temperature_to_mireds(temperature: int, low: int, high: int) -> int:
    return max(low, min(high, round(high - temperature * (high - low) / 100)))

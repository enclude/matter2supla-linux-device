# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Przeliczenia dla świateł: Matter (poziom 1–254, miredy) <-> skala SUPLA (0–100).

Temperatura bieli w SUPLA: 0 = ciepła, 100 = zimna. W Matter miredy rosną w stronę ciepłej.
"""

from __future__ import annotations

import colorsys
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


# --- światła kolorowe (HS) --------------------------------------------------

COLOR_MODE_HS, COLOR_MODE_XY, COLOR_MODE_CT = 0, 1, 2  # ColorControl.ColorMode (0x0008)
HS_CAPABLE = 1  # bit 0 ColorCapabilities (0x400A): światło umie ustawić barwę i nasycenie
MAX_HUE = 254  # Matter: barwa i nasycenie 0-254 (barwa: 0-360 stopni)
DEFAULT_RGB = (255, 255, 255)
MODE_WHITE, MODE_COLOR = 1, 2  # tryb światła kolorowego w tematach MQTT


def hue_path(endpoint: int) -> str:
    return f"{endpoint}/{COLOR_CLUSTER}/0"


def saturation_path(endpoint: int) -> str:
    return f"{endpoint}/{COLOR_CLUSTER}/1"


def color_mode_path(endpoint: int) -> str:
    return f"{endpoint}/{COLOR_CLUSTER}/8"


def is_color_capable(attributes: dict[str, Any], endpoint: int) -> bool:
    """Czy endpoint to światło z kolorem (barwa + nasycenie) i regulacją bieli."""
    capabilities = attributes.get(f"{endpoint}/{COLOR_CLUSTER}/16394")
    return (
        isinstance(capabilities, int)
        and bool(capabilities & HS_CAPABLE)
        and hue_path(endpoint) in attributes
        and saturation_path(endpoint) in attributes
    )


def hs_to_rgb(hue: int, saturation: int) -> tuple[int, int, int]:
    """Barwa i nasycenie Matter -> kolor RGB o pełnej jasności (jasność jest osobnym parametrem)."""
    h = max(0, min(MAX_HUE, hue)) / MAX_HUE
    s = max(0, min(MAX_HUE, saturation)) / MAX_HUE
    r, g, b = colorsys.hsv_to_rgb(h, s, 1.0)
    return round(r * 255), round(g * 255), round(b * 255)


def rgb_to_hs(red: int, green: int, blue: int) -> tuple[int, int]:
    """Kolor RGB -> barwa i nasycenie Matter; jasność koloru jest pomijana."""
    h, s, _ = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
    return round(h * MAX_HUE) % (MAX_HUE + 1), round(s * MAX_HUE)


def xy_to_rgb(x: int, y: int) -> tuple[int, int, int]:
    """Współrzędne CIE xy (0-65535) -> RGB sRGB o pełnej jasności; dla światła w trybie XY."""
    cx, cy = x / 65536, y / 65536
    if cy <= 0:
        return DEFAULT_RGB
    big_x, big_z = cx / cy, (1 - cx - cy) / cy  # Y = 1
    red = 3.2406 * big_x - 1.5372 - 0.4986 * big_z
    green = -0.9689 * big_x + 1.8758 + 0.0415 * big_z
    blue = 0.0557 * big_x - 0.2040 + 1.0570 * big_z
    peak = max(red, green, blue, 1e-9)
    channels = []
    for value in (red, green, blue):
        value = max(0.0, value) / peak
        value = 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055
        channels.append(round(max(0.0, min(1.0, value)) * 255))
    return channels[0], channels[1], channels[2]

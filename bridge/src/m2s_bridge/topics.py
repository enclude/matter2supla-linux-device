# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Nazwy tematów MQTT — jedno miejsce wspólne dla łącznika i generatora konfiguracji SUPLA."""

from __future__ import annotations

from .config import GROUP_TYPES, Device


def _base(prefix: str, dev: Device) -> str:
    if dev.type in GROUP_TYPES:
        # Grupa nie ma węzła; nazwa jest stała, więc tematy nie zmieniają się ze składem grupy.
        return f"{prefix}/group/{dev.name}"
    return f"{prefix}/{dev.node_id}/{dev.endpoint}/{dev.type}"


def state_topic(prefix: str, dev: Device) -> str:
    return f"{_base(prefix, dev)}/state"


def set_topic(prefix: str, dev: Device) -> str:
    return f"{_base(prefix, dev)}/set"


def bridge_status_topic(prefix: str) -> str:
    return f"{prefix}/bridge/status"

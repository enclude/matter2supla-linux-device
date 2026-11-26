# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Nazwy tematów MQTT — jedno miejsce wspólne dla łącznika i generatora konfiguracji SUPLA."""

from __future__ import annotations

from .config import Device


def state_topic(prefix: str, dev: Device) -> str:
    return f"{prefix}/{dev.node_id}/{dev.endpoint}/{dev.type}/state"


def set_topic(prefix: str, dev: Device) -> str:
    return f"{prefix}/{dev.node_id}/{dev.endpoint}/{dev.type}/set"


def bridge_status_topic(prefix: str) -> str:
    return f"{prefix}/bridge/status"

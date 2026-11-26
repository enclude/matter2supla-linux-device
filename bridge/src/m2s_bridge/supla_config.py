"""Generator supla-device.yaml z pliku mapowania urządzeń.

Kanały SUPLA są trwałe: nie wolno zmienić typu ani usunąć kanału, wolno tylko dopisywać
na końcu. Generator zapisuje obok pliku wynikowego `channels.lock.json` i odmawia
wygenerowania konfiguracji, która złamałaby tę zasadę.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .config import Config, Device
from .topics import set_topic, state_topic

LOCK_FILE_NAME = "channels.lock.json"
STATE_FILES_PATH = "/var/lib/supla-device"

# typ urządzenia w devices.yaml -> typ kanału supla-device
SUPLA_TYPES = {"onoff": "CustomRelay"}


class ChannelConflictError(ValueError):
    """Zmiana złamałaby trwałość kanałów SUPLA."""


def _channel(config: Config, dev: Device) -> dict[str, Any]:
    prefix = config.mqtt.topic_prefix
    source: dict[str, Any] = {"type": "MQTT", "state_topic": state_topic(prefix, dev)}
    if config.state_expiration_sec:
        source["expiration_time_sec"] = config.state_expiration_sec
    channel: dict[str, Any] = {
        "type": SUPLA_TYPES[dev.type],
        "name": dev.name,
        "channel_number": dev.supla_channel,
    }
    if dev.caption:
        channel["initial_caption"] = dev.caption
    channel.update(
        {
            "state": "state",
            "offline_on_invalid_state": True,
            "turn_on_payload": "ON",
            "turn_off_payload": "OFF",
            "source": source,
            "parser": {"type": "Json", "refresh_time_ms": 1000},
            "output": {"type": "MQTT", "control_topic": set_topic(prefix, dev), "qos": 1},
            "payload": {"type": "Simple"},
        }
    )
    return channel


def _ordered(config: Config) -> list[Device]:
    devices = sorted(config.devices, key=lambda d: d.supla_channel)
    numbers = [d.supla_channel for d in devices]
    if numbers != list(range(len(devices))):
        raise ChannelConflictError(
            f"numery supla_channel muszą być kolejne od 0 bez przerw, są: {numbers}"
        )
    return devices


def build_supla_config(config: Config, server: str, mail: str) -> dict[str, Any]:
    if not server or not mail:
        raise ValueError("wymagane SUPLA_SERVER i SUPLA_MAIL")
    devices = _ordered(config)
    if not devices:
        raise ValueError("brak urządzeń w konfiguracji — nie ma czego generować")
    mqtt: dict[str, Any] = {
        "host": config.mqtt.host,
        "port": config.mqtt.port,
        "client_name": "m2s-supla-device",
        "use_ssl": False,
    }
    if config.mqtt.username:
        mqtt["username"] = config.mqtt.username
    if config.mqtt.password:
        mqtt["password"] = config.mqtt.password
    return {
        "name": "Matter2Supla",
        "state_files_path": STATE_FILES_PATH,
        "supla": {"server": server, "mail": mail},
        "mqtt": mqtt,
        "channels": [_channel(config, dev) for dev in devices],
    }


def lock_entries(config: Config) -> dict[str, dict[str, str]]:
    return {
        str(dev.supla_channel): {"type": SUPLA_TYPES[dev.type], "name": dev.name}
        for dev in _ordered(config)
    }


def check_lock(config: Config, locked: dict[str, dict[str, str]]) -> None:
    current = lock_entries(config)
    problems: list[str] = []
    for number, old in sorted(locked.items(), key=lambda kv: int(kv[0])):
        new = current.get(number)
        if new is None:
            problems.append(f"kanał {number} ({old.get('name')}) został usunięty")
        elif new["type"] != old.get("type"):
            problems.append(f"kanał {number} zmienił typ z {old.get('type')} na {new['type']}")
    if problems:
        raise ChannelConflictError(
            "zmiana złamałaby trwałość kanałów SUPLA (skutek: „Channel conflict” i konieczność "
            "usunięcia urządzenia z SUPLA Cloud razem z historią):\n  - "
            + "\n  - ".join(problems)
            + "\nKanały wolno tylko dopisywać na końcu. Użyj --force wyłącznie po świadomym "
            "usunięciu urządzenia z SUPLA Cloud."
        )


def write_supla_config(
    config: Config, output: str | Path, server: str, mail: str, force: bool = False
) -> Path:
    output = Path(output)
    lock_path = output.parent / LOCK_FILE_NAME
    document = build_supla_config(config, server, mail)
    if lock_path.exists() and not force:
        check_lock(config, json.loads(lock_path.read_text(encoding="utf-8")).get("channels", {}))
    output.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Plik wygenerowany przez `m2s-bridge gen-supla-config` z devices.yaml — nie edytuj ręcznie.\n"
        "# Zawiera e-mail konta SUPLA: nie umieszczaj go w repozytorium.\n"
    )
    _write_if_changed(output, header + yaml.safe_dump(document, sort_keys=False, allow_unicode=True))
    _write_if_changed(
        lock_path, json.dumps({"channels": lock_entries(config)}, indent=2, ensure_ascii=False) + "\n"
    )
    return output


def _write_if_changed(path: Path, text: str) -> None:
    """Zapis atomowy; niezmieniony plik zostaje nietknięty (supla-device restartuje się po zmianie)."""
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

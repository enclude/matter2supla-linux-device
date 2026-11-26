# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Wczytywanie i walidacja pliku mapowania urządzeń (devices.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .meter import MEASUREMENTS

# Typy urządzeń obsługiwane przez łącznik. Kolejne (jasność, czujniki) dojdą tutaj.
DEVICE_TYPES = ("onoff", "light_cct", "meter", "light_group")

# Grupa świateł: jeden kanał SUPLA sterujący kilkoma światłami naraz. Nie ma własnego węzła
# (node_id i endpoint są zerowe), a jej skład (`members`) wolno zmieniać bez wpływu na kanał.
GROUP_TYPE = "light_group"

# Urządzenie sterowane tylko z panelu i przez MQTT, bez kanału SUPLA (nie trafia do pliku).
NO_CHANNEL = -1

MAX_SUPLA_CHANNEL = 127  # zakres `channel_number` w supla-device
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_PREFIX_RE = re.compile(r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$")


class ConfigError(ValueError):
    """Błąd w pliku konfiguracyjnym."""


@dataclass(frozen=True)
class MatterConfig:
    url: str = "ws://127.0.0.1:5580/ws"


@dataclass(frozen=True)
class MqttConfig:
    host: str = "127.0.0.1"
    port: int = 1883
    username: str | None = None
    password: str | None = None
    topic_prefix: str = "m2s"


@dataclass(frozen=True)
class WebConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8099


@dataclass(frozen=True)
class Device:
    name: str
    node_id: int
    endpoint: int
    type: str
    supla_channel: int
    caption: str | None = None
    # Tylko typ `meter`: pomiary podawane przez urządzenie (patrz meter.py). Ustalane przy
    # dodaniu kanału; każdy z nich musi być w komunikacie stanu, inaczej SUPLA odrzuca odczyt.
    measurements: tuple[str, ...] = ()
    # Tylko typ `light_group`: światła należące do grupy jako pary (węzeł, endpoint).
    members: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class Config:
    matter: MatterConfig = field(default_factory=MatterConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    # Co ile sekund łącznik ponawia publikację wszystkich stanów. supla-device
    # uznaje stan za nieważny po `state_expiration_sec` bez nowej wiadomości,
    # więc martwy łącznik kończy się kanałem offline, a nie zamrożonym stanem.
    heartbeat_sec: int = 30
    state_expiration_sec: int = 120
    # Nowe węzły Matter z obsługiwanym typem są same dopisywane jako kolejne kanały SUPLA.
    auto_add: bool = True
    # Światła z regulacją bieli jako kanały SUPLA typu ściemniacz CCT (rozszerzenie MatterLight
    # w supla-device, protokół SUPLA 28). Po wyłączeniu światłem steruje się tylko z panelu.
    lights_as_channels: bool = True
    # Raz na dobę pytanie do API GitHuba o najnowsze wydanie — tylko informacja w panelu,
    # nic nie jest pobierane ani aktualizowane samoczynnie.
    update_check: bool = True
    web: WebConfig = field(default_factory=WebConfig)
    devices: tuple[Device, ...] = ()
    # Endpointy (węzeł, endpoint) celowo zostawione bez kanału SUPLA - `auto_add` je pomija.
    # Wpis powstaje przy parowaniu z wyborem "bez kanału" albo po usunięciu światła z grupy.
    no_channel: tuple[tuple[int, int], ...] = ()
    # Lokalne nazwy endpointów bez kanału SUPLA (np. świateł należących tylko do grupy) jako
    # (węzeł, endpoint, nazwa). Gdy endpoint dostaje kanał, nazwa przechodzi do `caption` kanału.
    names: tuple[tuple[int, int, str], ...] = ()


def _require_int(value: Any, what: str, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{what}: oczekiwano liczby całkowitej, jest {value!r}")
    if value < minimum or (maximum is not None and value > maximum):
        zakres = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        raise ConfigError(f"{what}: wartość {value} poza zakresem {zakres}")
    return value


def _measurements(item: dict[str, Any], dev_type: str, where: str) -> tuple[str, ...]:
    value = item.get("measurements")
    if dev_type != "meter":
        if value:
            raise ConfigError(f"{where}.measurements: pole dotyczy tylko typu `meter`")
        return ()
    if not isinstance(value, list) or not value or any(m not in MEASUREMENTS for m in value):
        raise ConfigError(
            f"{where}.measurements: wymagana niepusta lista spośród: {', '.join(MEASUREMENTS)}"
        )
    return tuple(m for m in MEASUREMENTS if m in value)


def _targets(value: Any, where: str) -> tuple[tuple[int, int], ...]:
    """Lista par (węzeł, endpoint) zapisana jako `[{node_id: N, endpoint: E}, ...]`."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{where}: oczekiwano listy")
    result: list[tuple[int, int]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or "node_id" not in item or "endpoint" not in item:
            raise ConfigError(f"{where}[{index}]: oczekiwano mapy z polami node_id i endpoint")
        target = (
            _require_int(item["node_id"], f"{where}[{index}].node_id", 1),
            _require_int(item["endpoint"], f"{where}[{index}].endpoint", 0, 65535),
        )
        if target in result:
            raise ConfigError(f"{where}: powtórzony węzeł {target[0]} endpoint {target[1]}")
        result.append(target)
    return tuple(result)


def _names(value: Any) -> tuple[tuple[int, int, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError("names: oczekiwano listy")
    result: dict[tuple[int, int], str] = {}
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not {"node_id", "endpoint", "caption"} <= set(item):
            raise ConfigError(f"names[{index}]: oczekiwano mapy z polami node_id, endpoint i caption")
        target = (
            _require_int(item["node_id"], f"names[{index}].node_id", 1),
            _require_int(item["endpoint"], f"names[{index}].endpoint", 0, 65535),
        )
        result[target] = str(item["caption"])
    return tuple((node_id, endpoint, caption) for (node_id, endpoint), caption in result.items())


def _targets_to_raw(targets: tuple[tuple[int, int], ...]) -> list[dict[str, int]]:
    return [{"node_id": node_id, "endpoint": endpoint} for node_id, endpoint in targets]


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, dict):
        raise ConfigError(f"sekcja `{key}` musi być mapą")
    return value


def parse_config(raw: Any) -> Config:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("plik konfiguracyjny musi być mapą YAML")

    matter_raw = _section(raw, "matter")
    matter = MatterConfig(url=str(matter_raw.get("url", MatterConfig.url)))
    if not matter.url.startswith(("ws://", "wss://")):
        raise ConfigError("matter.url musi zaczynać się od ws:// lub wss://")

    mqtt_raw = _section(raw, "mqtt")
    mqtt = MqttConfig(
        host=str(mqtt_raw.get("host", MqttConfig.host)),
        port=_require_int(mqtt_raw.get("port", MqttConfig.port), "mqtt.port", 1, 65535),
        username=mqtt_raw.get("username"),
        password=mqtt_raw.get("password"),
        topic_prefix=str(mqtt_raw.get("topic_prefix", MqttConfig.topic_prefix)),
    )
    if not _PREFIX_RE.match(mqtt.topic_prefix):
        raise ConfigError("mqtt.topic_prefix: dozwolone litery, cyfry, `_`, `-` i `/` między członami")

    heartbeat = _require_int(raw.get("heartbeat_sec", 30), "heartbeat_sec", 1)
    expiration = _require_int(raw.get("state_expiration_sec", 120), "state_expiration_sec", 0)
    if expiration and expiration < 2 * heartbeat:
        raise ConfigError(
            "state_expiration_sec musi być co najmniej 2x heartbeat_sec (albo 0 = bez wygasania)"
        )

    auto_add = raw.get("auto_add", True)
    if not isinstance(auto_add, bool):
        raise ConfigError("auto_add musi być wartością true/false")
    lights_as_channels = raw.get("lights_as_channels", True)
    if not isinstance(lights_as_channels, bool):
        raise ConfigError("lights_as_channels musi być wartością true/false")
    update_check = raw.get("update_check", True)
    if not isinstance(update_check, bool):
        raise ConfigError("update_check musi być wartością true/false")
    web_raw = _section(raw, "web")
    web_enabled = web_raw.get("enabled", True)
    if not isinstance(web_enabled, bool):
        raise ConfigError("web.enabled musi być wartością true/false")
    web = WebConfig(
        enabled=web_enabled,
        host=str(web_raw.get("host", WebConfig.host)),
        port=_require_int(web_raw.get("port", WebConfig.port), "web.port", 1, 65535),
    )

    devices_raw = raw.get("devices") or []
    if not isinstance(devices_raw, list):
        raise ConfigError("`devices` musi być listą")

    devices: list[Device] = []
    for index, item in enumerate(devices_raw):
        where = f"devices[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{where}: oczekiwano mapy")
        is_group = item.get("type") == GROUP_TYPE
        # Grupa bez `supla_channel` jeszcze nie ma kanału: dostaje go z pierwszym światłem.
        required = ("name", "type") if is_group else (
            "name", "node_id", "endpoint", "type", "supla_channel"
        )
        missing = [k for k in required if k not in item]
        if missing:
            raise ConfigError(f"{where}: brak pól: {', '.join(missing)}")
        name = str(item["name"])
        if not _NAME_RE.match(name):
            raise ConfigError(f"{where}.name: dozwolone litery, cyfry, `_` i `-`")
        dev_type = str(item["type"])
        if dev_type not in DEVICE_TYPES:
            raise ConfigError(
                f"{where}.type: nieznany typ {dev_type!r} (obsługiwane: {', '.join(DEVICE_TYPES)})"
            )
        caption = item.get("caption")
        if not is_group and item.get("members"):
            raise ConfigError(f"{where}.members: pole dotyczy tylko typu `{GROUP_TYPE}`")
        devices.append(
            Device(
                name=name,
                node_id=0 if is_group else _require_int(item["node_id"], f"{where}.node_id", 1),
                endpoint=0 if is_group else _require_int(item["endpoint"], f"{where}.endpoint", 0, 65535),
                type=dev_type,
                supla_channel=NO_CHANNEL
                if is_group and "supla_channel" not in item
                else _require_int(item["supla_channel"], f"{where}.supla_channel", 0, MAX_SUPLA_CHANNEL),
                caption=None if caption is None else str(caption),
                measurements=_measurements(item, dev_type, where),
                members=_targets(item.get("members"), f"{where}.members") if is_group else (),
            )
        )

    _check_unique(devices)
    return Config(
        matter=matter,
        mqtt=mqtt,
        heartbeat_sec=heartbeat,
        state_expiration_sec=expiration,
        auto_add=auto_add,
        lights_as_channels=lights_as_channels,
        update_check=update_check,
        web=web,
        devices=tuple(devices),
        no_channel=_targets(raw.get("no_channel"), "no_channel"),
        names=_names(raw.get("names")),
    )


def check_devices(devices: list[Device] | tuple[Device, ...]) -> None:
    """Sprawdza unikalność nazw, kanałów i celów; ConfigError przy konflikcie."""
    _check_unique(list(devices))


def config_to_raw(config: Config) -> dict[str, Any]:
    """Odwrotność parse_config — do zapisu pliku po automatycznym dopisaniu urządzenia."""
    mqtt: dict[str, Any] = {
        "host": config.mqtt.host,
        "port": config.mqtt.port,
        "topic_prefix": config.mqtt.topic_prefix,
    }
    if config.mqtt.username:
        mqtt["username"] = config.mqtt.username
    if config.mqtt.password:
        mqtt["password"] = config.mqtt.password
    devices = []
    for dev in config.devices:
        if dev.supla_channel == NO_CHANNEL and dev.type != GROUP_TYPE:
            continue
        item: dict[str, Any] = {"name": dev.name}
        if dev.caption:
            item["caption"] = dev.caption
        if dev.type == GROUP_TYPE:
            item["type"] = dev.type
            if dev.supla_channel != NO_CHANNEL:
                item["supla_channel"] = dev.supla_channel
            item["members"] = _targets_to_raw(dev.members)
        else:
            item.update(
                node_id=dev.node_id, endpoint=dev.endpoint, type=dev.type, supla_channel=dev.supla_channel
            )
        if dev.measurements:
            item["measurements"] = list(dev.measurements)
        devices.append(item)
    raw: dict[str, Any] = {
        "matter": {"url": config.matter.url},
        "mqtt": mqtt,
        "heartbeat_sec": config.heartbeat_sec,
        "state_expiration_sec": config.state_expiration_sec,
        "auto_add": config.auto_add,
        "lights_as_channels": config.lights_as_channels,
        "update_check": config.update_check,
        "web": {"enabled": config.web.enabled, "host": config.web.host, "port": config.web.port},
        "devices": devices,
    }
    if config.no_channel:
        raw["no_channel"] = _targets_to_raw(config.no_channel)
    if config.names:
        raw["names"] = [
            {"node_id": node_id, "endpoint": endpoint, "caption": caption}
            for node_id, endpoint, caption in config.names
        ]
    return raw


_FILE_HEADER = (
    "# Mapowanie urządzeń Matter na kanały SUPLA. Plik jest też zapisywany przez łącznik\n"
    "# (automatyczne dopisywanie urządzeń, panel WWW) — komentarze dodane ręcznie nie przetrwają.\n"
    "# UWAGA: supla_channel jest trwały. Wolno tylko dopisywać urządzenia z kolejnym numerem.\n"
)


def save_config(config: Config, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _FILE_HEADER + yaml.safe_dump(config_to_raw(config), sort_keys=False, allow_unicode=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _check_unique(devices: list[Device]) -> None:
    seen_names: set[str] = set()
    seen_channels: dict[int, str] = {}
    seen_targets: dict[tuple[int, int, bool], str] = {}
    for dev in devices:
        if dev.name in seen_names:
            raise ConfigError(f"powtórzona nazwa urządzenia: {dev.name}")
        seen_names.add(dev.name)
        if dev.supla_channel != NO_CHANNEL and dev.supla_channel in seen_channels:
            raise ConfigError(
                f"supla_channel {dev.supla_channel} użyty dwukrotnie "
                f"({seen_channels[dev.supla_channel]} i {dev.name})"
            )
        seen_channels[dev.supla_channel] = dev.name
        if dev.type == GROUP_TYPE:
            continue  # grupa nie ma własnego węzła; jej światła mogą mieć też osobne kanały
        # Licznik może dzielić endpoint z gniazdkiem — to osobny kanał z własnymi tematami.
        target = (dev.node_id, dev.endpoint, dev.type == "meter")
        if target in seen_targets:
            raise ConfigError(
                f"węzeł {dev.node_id} endpoint {dev.endpoint} zmapowany dwukrotnie "
                f"({seen_targets[target]} i {dev.name})"
            )
        seen_targets[target] = dev.name


def load_config(path: str | Path) -> Config:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise ConfigError(f"nie można odczytać {path}: {err}") from err
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise ConfigError(f"błąd składni YAML w {path}: {err}") from err
    return parse_config(raw)

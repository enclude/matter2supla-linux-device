# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Stan całego łącznika: lista urządzeń, automatyczne kanały, parowanie, dane dla panelu WWW."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import logging
import re
from pathlib import Path
from typing import Any

import aiomqtt

from . import light, meter
from .config import (
    GROUP_TYPE,
    MAX_SUPLA_CHANNEL,
    NO_CHANNEL,
    Config,
    ConfigError,
    Device,
    check_devices,
    save_config,
)
from .core import LIGHT_TYPES, BridgeCore
from .diagnostics import build_diagnostics
from .matter_client import MatterConnectionError, MatterError
from .supla_config import ChannelConflictError, write_supla_config
from .topics import set_topic
from .version import UpdateChecker

log = logging.getLogger(__name__)

COMMISSION_TIMEOUT_SEC = 300
# Co zrobić z urządzeniem po sparowaniu: osobny kanał (domyślnie), bez kanału albo `group:<nazwa>`.
TARGET_CHANNEL = "channel"
TARGET_NONE = "none"
TARGET_GROUP_PREFIX = "group:"
NEW_NODE_WAIT_SEC = 15
MAX_GROUP_MEMBERS = 32
LEVEL_CONTROL_CLUSTER = 8

ACCOUNT_FILE_NAME = "account.json"
_HOSTNAME = re.compile(r"(?=.{4,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")
_MAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s.]{2,}")
_ONOFF_PATH = re.compile(r"^(\d+)/6/0$")
_SLUG = re.compile(r"[^a-z0-9]+")


METER_SUFFIX = " — licznik"


def link_meter_captions(devices: list[Device]) -> list[Device]:
    """Nazwa licznika wynika z nazwy gniazdka na tym samym węźle (nie ustawia się jej osobno)."""
    plugs = {d.node_id: d for d in devices if d.type == "onoff"}
    linked = []
    for dev in devices:
        plug = plugs.get(dev.node_id) if dev.type == "meter" else None
        if plug is not None:
            dev = dataclasses.replace(dev, caption=(plug.caption or plug.name) + METER_SUFFIX)
        linked.append(dev)
    return linked


class ManagerError(Exception):
    """Błąd operacji zleconej z panelu — treść trafia do użytkownika."""


def onoff_endpoints(attributes: dict[str, Any]) -> list[int]:
    return sorted(int(m.group(1)) for path in attributes if (m := _ONOFF_PATH.match(path)))


def is_dimmable(attributes: dict[str, Any], endpoint: int) -> bool:
    prefix = f"{endpoint}/{LEVEL_CONTROL_CLUSTER}/"
    return any(path.startswith(prefix) for path in attributes)


def endpoint_type(attributes: dict[str, Any], endpoint: int) -> str | None:
    """Typ urządzenia, jakim łącznik umie sterować tym endpointem, albo None."""
    if not is_dimmable(attributes, endpoint):
        return "onoff"
    if light.level_path(endpoint) in attributes and light.mireds_path(endpoint) in attributes:
        return "light_cct"
    return None


class Manager:
    def __init__(
        self,
        config: Config,
        config_path: Path | None = None,
        supla_output: Path | None = None,
        supla_server: str = "",
        supla_mail: str = "",
    ):
        self.config = config
        self.config_path = config_path
        self.supla_output = supla_output
        # Dane konta SUPLA: z panelu WWW (plik obok konfiguracji SUPLA, poza repozytorium),
        # a gdy go nie ma — ze zmiennych środowiskowych.
        self._supla_server = supla_server
        self._supla_mail = supla_mail
        self._account_path = supla_output.parent / ACCOUNT_FILE_NAME if supla_output else None
        self._load_account()
        self.core = BridgeCore(config, self.publish)
        self.mqtt: aiomqtt.Client | None = None
        self.matter_client: Any = None
        self.server_info: dict[str, Any] = {}
        self.supla_error: str | None = None
        # Wersja łącznika i najnowsze wydanie (zapytania wysyła zadanie uruchamiane w runner.py).
        self.updates = UpdateChecker()
        self.commission_state: dict[str, Any] = {"running": False, "message": None, "error": None}
        self._commission_task: asyncio.Task[None] | None = None
        self._change_lock = asyncio.Lock()
        self._extras: tuple[Device, ...] = ()  # urządzenia bez kanału SUPLA, sterowane z panelu
        # Parowanie z wyborem "do grupy" / "bez kanału": węzły znane przed jego rozpoczęciem.
        # Dopóki trwa, `auto_add` nie rusza węzłów spoza tej listy - o nich decyduje wynik parowania.
        self._known_before_commission: set[int] | None = None

    # --- MQTT ---------------------------------------------------------------

    async def publish(self, topic: str, payload: str, retain: bool) -> None:
        mqtt = self.mqtt
        if mqtt is None:
            raise aiomqtt.MqttError("brak połączenia z brokerem MQTT")
        await mqtt.publish(topic, payload, qos=1, retain=retain)

    # --- urządzenia ---------------------------------------------------------

    def _mapped(self) -> set[tuple[int, int, bool]]:
        # trzeci człon: czy to licznik (może dzielić endpoint z gniazdkiem)
        return {
            (d.node_id, d.endpoint, d.type == "meter") for d in self.config.devices if d.type != GROUP_TYPE
        }

    def groups(self) -> list[Device]:
        return [d for d in self.config.devices if d.type == GROUP_TYPE]

    def _groups_of(self, target: tuple[int, int]) -> list[Device]:
        return [g for g in self.groups() if target in g.members]

    async def refresh_runtime(self) -> None:
        """Uzgadnia listę urządzeń łącznika: kanały SUPLA plus światła bez kanału.

        Światło bez kanału też ma swoje tematy MQTT i jest sterowalne z panelu.
        """
        taken = {d.name for d in self.config.devices}
        extras: list[Device] = []
        for candidate in self.candidates():
            if candidate["supported"] or candidate.get("type") != "light_cct":
                continue
            name = self._new_name(candidate["product"], candidate["node_id"], candidate["endpoint"], taken)
            taken.add(name)
            extras.append(
                Device(
                    name=name,
                    node_id=candidate["node_id"],
                    endpoint=candidate["endpoint"],
                    type="light_cct",
                    supla_channel=NO_CHANNEL,
                    caption=str(candidate["product"] or "") or None,
                )
            )
        if tuple(extras) == self._extras and self.core.devices == self.config.devices + self._extras:
            return
        old_topics = set(self.core.set_topics)
        self._extras = tuple(extras)
        self.core.set_devices(self.config.devices + self._extras)
        mqtt = self.mqtt
        if mqtt is not None:
            for topic in self.core.set_topics:
                if topic not in old_topics:
                    await mqtt.subscribe(topic, qos=1)
            await self.core.publish_new()

    def candidates(self) -> list[dict[str, Any]]:
        """Endpointy węzłów Matter, które nie są jeszcze kanałami SUPLA."""
        mapped = self._mapped()
        result: list[dict[str, Any]] = []
        for node_id, node in sorted(self.core.nodes.items()):
            attrs = node["attributes"]
            base = {
                "node_id": node_id,
                "available": node["available"],
                "vendor": attrs.get("0/40/1"),
                "product": attrs.get("0/40/3"),
            }
            endpoints = onoff_endpoints(attrs)
            meters = meter.endpoints(attrs)
            if not endpoints and not meters:
                if not any(n == node_id for n, _, _ in mapped):
                    result.append(
                        {**base, "endpoint": None, "supported": False, "reason": "typ urządzenia jeszcze nieobsługiwany"}
                    )
                continue
            for endpoint in endpoints:
                if (node_id, endpoint, False) in mapped:
                    continue
                kind = endpoint_type(attrs, endpoint)
                entry = {**base, "endpoint": endpoint, "type": kind}
                can_be_channel = kind == "onoff" or (kind == "light_cct" and self.config.lights_as_channels)
                in_groups = self._groups_of((node_id, endpoint)) if kind == "light_cct" else []
                # `manual`: kanał nie powstanie sam, ale da się go dodać przyciskiem w panelu.
                if in_groups:
                    names = ", ".join(f"„{g.caption or g.name}”" for g in in_groups)
                    result.append(
                        {**entry, "supported": False, "manual": can_be_channel, "reason": f"w grupie {names}, bez osobnego kanału"}
                    )
                elif can_be_channel and (node_id, endpoint) in self.config.no_channel:
                    result.append(
                        {**entry, "supported": False, "manual": True, "reason": "bez kanału SUPLA (wybór w panelu)"}
                    )
                elif kind == "onoff":
                    result.append({**entry, "supported": True, "reason": None})
                elif kind == "light_cct" and self.config.lights_as_channels:
                    result.append({**entry, "supported": True, "reason": None})
                elif kind == "light_cct":
                    result.append(
                        {**entry, "supported": False, "reason": "światło sterowane tylko z panelu (lights_as_channels: false)"}
                    )
                else:
                    result.append({**entry, "supported": False, "reason": "ten rodzaj światła nie jest jeszcze obsługiwany"})
            # Licznik energii to osobny kanał (zwykle osobny endpoint, bez klastra OnOff).
            for endpoint in meters:
                if (node_id, endpoint, True) in mapped:
                    continue
                entry = {**base, "endpoint": endpoint, "type": "meter"}
                if (node_id, endpoint) in self.config.no_channel:
                    result.append(
                        {**entry, "supported": False, "manual": True, "reason": "licznik bez kanału SUPLA (wybór w panelu)"}
                    )
                else:
                    result.append({**entry, "supported": True, "reason": None})
        return result

    def _new_name(self, product: Any, node_id: int, endpoint: int, taken: set[str], kind: str = "") -> str:
        slug = _SLUG.sub("_", str(product or "").lower()).strip("_") or "matter"
        if kind == "meter":
            name = f"{slug}_{node_id}_meter"
        else:
            name = f"{slug}_{node_id}" if endpoint == 1 else f"{slug}_{node_id}_e{endpoint}"
        candidate, counter = name, 2
        while candidate in taken:
            candidate = f"{name}_{counter}"
            counter += 1
        return candidate

    def _device_for(self, candidate: dict[str, Any], devices: list[Device]) -> Device:
        channel = max((d.supla_channel for d in devices), default=-1) + 1
        kind = candidate.get("type") or "onoff"
        if channel > MAX_SUPLA_CHANNEL:
            raise ManagerError("wyczerpano numery kanałów SUPLA (0–127)")
        node = self.core.nodes.get(candidate["node_id"], {"attributes": {}})
        label = node["attributes"].get("0/40/5")
        caption = str(label or candidate["product"] or "")
        measurements: tuple[str, ...] = ()
        if kind == "meter":
            caption = f"{caption} — licznik" if caption else "Licznik energii"
            measurements = meter.detect(node["attributes"], candidate["endpoint"])
        return Device(
            name=self._new_name(
                candidate["product"], candidate["node_id"], candidate["endpoint"], {d.name for d in devices}, kind
            ),
            node_id=candidate["node_id"],
            endpoint=candidate["endpoint"],
            type=kind,
            supla_channel=channel,
            caption=caption or None,
            measurements=measurements,
        )

    async def _apply(self, devices: list[Device], no_channel: list[tuple[int, int]] | None = None) -> None:
        devices = link_meter_captions(devices)
        check_devices(devices)
        old_topics = set(self.core.set_topics)
        new_config = dataclasses.replace(self.config, devices=tuple(devices))
        if no_channel is not None:
            new_config = dataclasses.replace(new_config, no_channel=tuple(dict.fromkeys(no_channel)))
        if self.config_path is not None:
            save_config(new_config, self.config_path)  # najpierw plik: nieudany zapis niczego nie zmienia
        self.config = new_config
        old_topics = set(self.core.set_topics)
        self._extras = ()
        self.core.set_devices(self.config.devices)
        mqtt = self.mqtt
        if mqtt is not None:
            for topic in self.core.set_topics:
                if topic not in old_topics:
                    await mqtt.subscribe(topic, qos=1)
            await self.core.publish_all()
        await self.refresh_runtime()
        self.regenerate_supla()

    async def auto_add(self) -> list[Device]:
        """Dopisuje jako kanały wszystkie obsługiwane, niezmapowane endpointy."""
        added: list[Device] = []
        if self.config.auto_add:
            try:
                added = await self._auto_add()
            except (ManagerError, ConfigError, OSError) as err:
                # Nie zatrzymujemy łącznika — istniejące kanały mają działać dalej.
                self.supla_error = f"Nie udało się dopisać nowego urządzenia: {err}"
                log.error(self.supla_error)
        await self.refresh_runtime()
        return added

    async def _auto_add(self) -> list[Device]:
        async with self._change_lock:
            devices = list(self.config.devices)
            added: list[Device] = []
            for candidate in self.candidates():
                held = self._known_before_commission
                if held is not None and candidate["node_id"] not in held:
                    continue  # o tym węźle zdecyduje wynik trwającego parowania
                if candidate["supported"]:
                    dev = self._device_for(candidate, devices)
                    devices.append(dev)
                    added.append(dev)
            if added:
                await self._apply(devices)
                for dev in added:
                    log.info(
                        "dodano kanał SUPLA %d: %s (węzeł %d, endpoint %d)",
                        dev.supla_channel, dev.name, dev.node_id, dev.endpoint,
                    )
            return added

    async def add_device(self, node_id: int, endpoint: int) -> Device:
        async with self._change_lock:
            for candidate in self.candidates():
                if candidate["node_id"] == node_id and candidate["endpoint"] == endpoint:
                    break
            else:
                raise ManagerError("nie ma takiego wolnego endpointu z obsługiwanym klastrem")
            if not candidate["supported"] and not candidate.get("manual"):
                raise ManagerError(f"tego urządzenia nie można jeszcze dodać: {candidate['reason']}")
            devices = list(self.config.devices)
            dev = self._device_for(candidate, devices)
            kept = [t for t in self.config.no_channel if t != (node_id, endpoint)]
            await self._apply(devices + [dev], kept)
            return dev

    # --- grupy świateł ------------------------------------------------------

    def lights(self) -> list[dict[str, Any]]:
        """Światła z regulacją bieli, które mogą należeć do grupy (także te już w grupach)."""
        own = {(d.node_id, d.endpoint): d for d in self.config.devices if d.type == "light_cct"}
        found: dict[tuple[int, int], dict[str, Any]] = {}
        for node_id, node in sorted(self.core.nodes.items()):
            attrs = node["attributes"]
            for endpoint in onoff_endpoints(attrs):
                if endpoint_type(attrs, endpoint) != "light_cct":
                    continue
                dev = own.get((node_id, endpoint))
                label = (dev.caption or dev.name) if dev else str(attrs.get("0/40/5") or attrs.get("0/40/3") or "Światło")
                found[(node_id, endpoint)] = {
                    "label": label,
                    "available": bool(node["available"]),
                    "supla_channel": dev.supla_channel if dev else None,
                }
        for group in self.groups():
            for target in group.members:
                found.setdefault(target, {"label": "nieznane urządzenie", "available": False, "supla_channel": None})
        return [
            {"node_id": str(node_id), "endpoint": endpoint, **info}
            for (node_id, endpoint), info in sorted(found.items())
        ]

    def _check_members(self, members: list[tuple[int, int]], current: tuple[tuple[int, int], ...] = ()) -> list[tuple[int, int]]:
        members = list(dict.fromkeys(members))
        if len(members) > MAX_GROUP_MEMBERS:
            raise ManagerError(f"grupa może mieć najwyżej {MAX_GROUP_MEMBERS} świateł")
        for node_id, endpoint in members:
            if (node_id, endpoint) in current:
                continue  # światło już w grupie zostaje, nawet gdy chwilowo nie odpowiada
            node = self.core.nodes.get(node_id)
            if node is None or endpoint_type(node["attributes"], endpoint) != "light_cct":
                raise ManagerError(
                    f"węzeł {node_id} endpoint {endpoint} nie jest światłem z regulacją jasności i bieli"
                )
        return members

    def _find_group(self, name: str) -> Device:
        for group in self.groups():
            if group.name == name:
                return group
        raise ManagerError(f"nie ma grupy {name}")

    def _no_channel_after(self, devices: list[Device], released: list[tuple[int, int]]) -> list[tuple[int, int]]:
        """Lista "bez kanału" po zmianie składu grup.

        Światło wyjęte z grupy nie dostaje kanału samo (kanał jest nieusuwalny) - trafia na listę
        i czeka na decyzję w panelu. Światło należące do jakiejś grupy na liście być nie musi.
        """
        grouped = {t for d in devices if d.type == GROUP_TYPE for t in d.members}
        own = {(d.node_id, d.endpoint) for d in devices if d.type == "light_cct"}
        result = [t for t in self.config.no_channel if t not in grouped]
        result += [t for t in released if t not in grouped and t not in own]
        return result

    def _next_channel(self, devices: list[Device]) -> int:
        channel = max((d.supla_channel for d in devices), default=-1) + 1
        if channel > MAX_SUPLA_CHANNEL:
            raise ManagerError("wyczerpano numery kanałów SUPLA (0–127)")
        return channel

    async def create_group(self, caption: str, members: list[tuple[int, int]]) -> Device:
        """Tworzy grupę świateł. Trwały kanał SUPLA powstaje dopiero z pierwszym światłem."""
        caption = caption.strip()[:100]
        if not caption:
            raise ManagerError("podaj nazwę grupy")
        async with self._change_lock:
            devices = list(self.config.devices)
            checked = tuple(self._check_members(members))
            channel = self._next_channel(devices) if checked else NO_CHANNEL
            taken = {d.name for d in devices} | {d.name for d in self._extras}
            counter = 1
            while (name := f"grupa_{counter}") in taken:
                counter += 1
            group = Device(
                name=name,
                node_id=0,
                endpoint=0,
                type=GROUP_TYPE,
                supla_channel=channel,
                caption=caption,
                members=checked,
            )
            devices.append(group)
            await self._apply(devices, self._no_channel_after(devices, []))
            log.info("utworzono grupę świateł %s (%d świateł, kanał SUPLA: %s)", name, len(checked), channel if checked else "jeszcze nie")
            return group

    async def set_group_members(self, name: str, members: list[tuple[int, int]]) -> Device:
        async with self._change_lock:
            return await self._set_group_members(name, members)

    async def _set_group_members(self, name: str, members: list[tuple[int, int]]) -> Device:
        group = self._find_group(name)
        checked = tuple(self._check_members(members, group.members))
        updated = dataclasses.replace(group, members=checked)
        if group.supla_channel == NO_CHANNEL and checked:
            # Pierwsze światło: dopiero teraz grupa zajmuje (nieusuwalny) kanał SUPLA.
            updated = dataclasses.replace(updated, supla_channel=self._next_channel(list(self.config.devices)))
            log.info("dodano kanał SUPLA %d: grupa świateł %s", updated.supla_channel, name)
        devices = [updated if d is group else d for d in self.config.devices]
        released = [t for t in group.members if t not in checked]
        await self._apply(devices, self._no_channel_after(devices, released))
        log.info("grupa %s: %d świateł", name, len(checked))
        return updated

    def _find(self, name: str) -> Device:
        for dev in self.core.devices:
            if dev.name == name:
                return dev
        raise ManagerError(f"nie ma urządzenia {name}")

    async def set_light(self, name: str, brightness: int | None, temperature: int | None) -> None:
        dev = self._find(name)
        if dev.type not in LIGHT_TYPES:
            raise ManagerError("to urządzenie nie jest światłem")
        if not await self.core.set_light(dev, brightness, temperature):
            raise ManagerError("komenda nie została wykonana — urządzenie niedostępne lub błąd Matter")

    async def set_caption(self, name: str, caption: str) -> None:
        async with self._change_lock:
            target = self._find(name)
            if target.supla_channel == NO_CHANNEL and target.type != GROUP_TYPE:
                raise ManagerError("nazwę można zmienić dopiero, gdy urządzenie ma kanał SUPLA")
            if target.type == "meter" and any(
                d.type == "onoff" and d.node_id == target.node_id for d in self.config.devices
            ):
                raise ManagerError("nazwa licznika wynika z nazwy gniazdka — zmień nazwę gniazdka")
            caption = caption.strip()[:100]
            devices = [
                dataclasses.replace(d, caption=caption or None) if d is target else d
                for d in self.config.devices
            ]
            await self._apply(devices)

    async def device_command(self, name: str, command: str) -> None:
        mapped = {"on": "On", "off": "Off", "toggle": "Toggle"}.get(command)
        if mapped is None:
            raise ManagerError("nieznana komenda")
        if self._find(name).type == "meter":
            raise ManagerError("licznik nie przyjmuje komend")
        if not await self.core.send_command(self._find(name), mapped):
            raise ManagerError("komenda nie została wykonana — urządzenie niedostępne lub błąd Matter")

    # --- SUPLA --------------------------------------------------------------

    def _load_account(self) -> None:
        if self._account_path is None or not self._account_path.exists():
            return
        try:
            data = json.loads(self._account_path.read_text(encoding="utf-8"))
            self._supla_server = str(data["server"])
            self._supla_mail = str(data["mail"])
        except (OSError, ValueError, KeyError, TypeError) as err:
            log.error("nie można odczytać %s: %s", self._account_path, err)

    async def set_supla_account(self, server: str, mail: str) -> None:
        server = server.strip().lower()
        mail = mail.strip()
        if not _HOSTNAME.fullmatch(server):
            raise ManagerError("adres serwera SUPLA ma postać np. svr12.supla.org (bez https:// i portu)")
        if not _MAIL.fullmatch(mail):
            raise ManagerError("podaj adres e-mail konta SUPLA")
        async with self._change_lock:
            if self._account_path is not None:
                try:
                    self._account_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = self._account_path.with_name(self._account_path.name + ".tmp")
                    tmp.write_text(json.dumps({"server": server, "mail": mail}) + "\n", encoding="utf-8")
                    os.chmod(tmp, 0o600)
                    tmp.replace(self._account_path)
                except OSError as err:
                    raise ManagerError(f"nie można zapisać danych konta: {err}") from err
            self._supla_server, self._supla_mail = server, mail
            self.supla_error = None
            self.regenerate_supla()

    def regenerate_supla(self) -> None:
        if self.supla_output is None or not any(d.supla_channel != NO_CHANNEL for d in self.config.devices):
            return
        if not self._supla_server or not self._supla_mail:
            self.supla_error = "Nie podano konta SUPLA — wpisz serwer i e-mail w sekcji „Konto SUPLA”. Kanały powstaną po zapisaniu."
            log.warning(self.supla_error)
            return
        try:
            write_supla_config(self.config, self.supla_output, self._supla_server, self._supla_mail)
            self.supla_error = None
        except (ChannelConflictError, ConfigError, ValueError, OSError) as err:
            self.supla_error = str(err)
            log.error("konfiguracja SUPLA: %s", err)

    # --- parowanie ----------------------------------------------------------

    def _client(self) -> Any:
        if self.matter_client is None:
            raise ManagerError("brak połączenia z matter-server")
        return self.matter_client

    async def set_thread_dataset(self, dataset: str) -> None:
        dataset = dataset.strip()
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2})+", dataset):
            raise ManagerError("dataset musi być ciągiem szesnastkowym (ot-ctl dataset active -x)")
        try:
            await self._client().command("set_thread_dataset", dataset=dataset)
        except (MatterError, MatterConnectionError) as err:
            raise ManagerError(str(err)) from err

    def start_commission(self, code: str, network_only: bool, target: str = TARGET_CHANNEL) -> None:
        code = code.strip()
        if not code:
            raise ManagerError("podaj kod parowania")
        if self.commission_state["running"]:
            raise ManagerError("parowanie już trwa")
        target = target or TARGET_CHANNEL
        if target.startswith(TARGET_GROUP_PREFIX):
            self._find_group(target[len(TARGET_GROUP_PREFIX):])
        elif target not in (TARGET_CHANNEL, TARGET_NONE):
            raise ManagerError("nieznany wybór przeznaczenia urządzenia")
        client = self._client()
        if target != TARGET_CHANNEL:
            self._known_before_commission = set(self.core.nodes)
        log.info(
            "parowanie: start (%s, po sparowaniu: %s)",
            "urządzenie już w sieci" if network_only else "przez Bluetooth",
            target,
        )
        self.commission_state = {"running": True, "message": "Parowanie trwa (do kilku minut)…", "error": None}
        self._commission_task = asyncio.create_task(self._commission(client, code, network_only, target))

    async def _commission(self, client: Any, code: str, network_only: bool, target: str = TARGET_CHANNEL) -> None:
        args: dict[str, Any] = {"code": code}
        if network_only:
            args["network_only"] = True
        try:
            node = await client.command("commission_with_code", timeout=COMMISSION_TIMEOUT_SEC, **args)
            node_id = node.get("node_id") if isinstance(node, dict) else None
            message = f"Sparowano węzeł {node_id}." if node_id is not None else "Sparowano."
            if target != TARGET_CHANNEL and node_id is not None:
                message += " " + await self._assign_new_node(node_id, node, target)
            self.commission_state = {"running": False, "message": message, "error": None}
            log.info("sparowano węzeł %s", node_id)
        except (MatterError, MatterConnectionError) as err:
            self.commission_state = {"running": False, "message": None, "error": str(err)}
            log.warning("parowanie nie powiodło się: %s", err)
        except (ManagerError, ConfigError, OSError) as err:
            self.commission_state = {
                "running": False,
                "message": None,
                "error": f"urządzenie sparowane, ale nie udało się go przypisać: {err}",
            }
            log.error("przypisanie po parowaniu nie powiodło się: %s", err)
        finally:
            if self._known_before_commission is not None:
                self._known_before_commission = None
                await self.auto_add()  # reszta endpointów nowego węzła idzie zwykłą drogą

    async def _assign_new_node(self, node_id: int, node: Any, target: str) -> str:
        """Stosuje wybór z formularza parowania do świeżo sparowanego węzła; zwraca opis dla panelu."""
        attributes = node.get("attributes") if isinstance(node, dict) else None
        if not isinstance(attributes, dict) or not attributes:
            # Odpowiedź bez atrybutów: czekamy, aż węzeł dotrze zdarzeniem node_added.
            async with asyncio.timeout(NEW_NODE_WAIT_SEC):
                while not self.core.nodes.get(node_id, {}).get("attributes"):
                    await asyncio.sleep(0.2)
            attributes = self.core.nodes[node_id]["attributes"]
        if node_id not in self.core.nodes:
            self.core.nodes[node_id] = {"available": True, "attributes": dict(attributes)}
        endpoints = onoff_endpoints(attributes)
        async with self._change_lock:
            if target == TARGET_NONE:
                held = [(node_id, e) for e in dict.fromkeys(endpoints + meter.endpoints(attributes))]
                await self._apply(list(self.config.devices), list(self.config.no_channel) + held)
                return "Urządzenie zostaje bez kanału SUPLA; kanał można dodać z listy urządzeń."
            lights = [(node_id, e) for e in endpoints if endpoint_type(attributes, e) == "light_cct"]
            if not lights:
                return "To nie jest światło z regulacją jasności i bieli, więc nie trafiło do grupy."
            group = self._find_group(target[len(TARGET_GROUP_PREFIX):])
            await self._set_group_members(group.name, list(group.members) + lights)
            return f"Światło dodane do grupy „{group.caption or group.name}”."

    # --- diagnostyka --------------------------------------------------------

    def diagnostics(self, node_id: int, full: bool = False) -> dict[str, Any]:
        node = self.core.nodes.get(node_id)
        if node is None:
            raise ManagerError(f"matter-server nie zna węzła {node_id}")
        return build_diagnostics(node_id, node, full)

    # --- dane dla panelu ----------------------------------------------------

    def _device_view(self, dev: Device) -> dict[str, Any]:
        state = self.core.state_of(dev)
        view: dict[str, Any] = {
            "name": dev.name,
            "caption": dev.caption or "",
            # identyfikatory węzłów jako tekst: mogą przekraczać zakres liczb JavaScript
            "node_id": str(dev.node_id),
            "endpoint": dev.endpoint,
            "type": dev.type,
            "supla_channel": dev.supla_channel,
            "state": {1: "on", 0: "off"}.get(state, "unavailable"),
            # nazwa licznika wynika z nazwy gniazdka i nie jest edytowalna
            "caption_linked": dev.type == "meter"
            and any(d.type == "onoff" and d.node_id == dev.node_id for d in self.config.devices),
        }
        if dev.type in LIGHT_TYPES:
            current = self.core.light_state(dev)
            view["brightness"] = current["brightness"] if current else None
            view["temperature"] = current["temperature"] if current else None
        if dev.type == GROUP_TYPE:
            view["members"] = [{"node_id": str(n), "endpoint": e} for n, e in dev.members]
            view["members_available"] = sum(
                1 for member in self.core.members_of(dev) if self.core.light_state(member) is not None
            )
        if dev.type == "meter":
            # jednostki jak w stanie MQTT: V, A, W, kWh, Hz
            values = self.core.meter_state(dev)
            view["state"] = "measuring" if values else "unavailable"
            view["meter"] = values
        return view

    def snapshot(self) -> dict[str, Any]:
        info = self.server_info
        # grupy bez kanału (jeszcze puste) na końcu listy
        devices = [
            self._device_view(d)
            for d in sorted(self.config.devices, key=lambda d: (d.supla_channel == NO_CHANNEL, d.supla_channel))
        ]
        extras = {(d.node_id, d.endpoint): self._device_view(d) for d in self._extras}
        candidates = []
        for c in self.candidates():
            item = {**c, "node_id": str(c["node_id"])}
            item["control"] = extras.get((c["node_id"], c["endpoint"]))
            candidates.append(item)
        return {
            "mqtt_connected": self.mqtt is not None,
            "matter_connected": self.core.matter is not None,
            "server": {
                "version": info.get("sdk_version"),
                "thread_dataset_set": bool(info.get("thread_credentials_set")),
                "bluetooth": bool(info.get("bluetooth_enabled")),
            },
            "auto_add": self.config.auto_add,
            "version": self.updates.info(),
            "supla": {"server": self._supla_server, "mail": self._supla_mail},
            "supla_error": self.supla_error
            or (None if self._supla_server and self._supla_mail else "Nie podano konta SUPLA — wpisz serwer i e-mail w sekcji „Konto SUPLA”."),
            "devices": devices,
            "lights": self.lights(),
            "candidates": candidates,
            "commission": self.commission_state,
        }

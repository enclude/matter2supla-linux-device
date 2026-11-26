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

from .config import Config, ConfigError, Device, check_devices, MAX_SUPLA_CHANNEL, save_config
from .core import BridgeCore
from .diagnostics import build_diagnostics
from .matter_client import MatterConnectionError, MatterError
from .supla_config import ChannelConflictError, write_supla_config
from .topics import set_topic

log = logging.getLogger(__name__)

COMMISSION_TIMEOUT_SEC = 300
LEVEL_CONTROL_CLUSTER = 8

ACCOUNT_FILE_NAME = "account.json"
_HOSTNAME = re.compile(r"(?=.{4,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")
_MAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s.]{2,}")
_ONOFF_PATH = re.compile(r"^(\d+)/6/0$")
_SLUG = re.compile(r"[^a-z0-9]+")


class ManagerError(Exception):
    """Błąd operacji zleconej z panelu — treść trafia do użytkownika."""


def onoff_endpoints(attributes: dict[str, Any]) -> list[int]:
    return sorted(int(m.group(1)) for path in attributes if (m := _ONOFF_PATH.match(path)))


def is_dimmable(attributes: dict[str, Any], endpoint: int) -> bool:
    prefix = f"{endpoint}/{LEVEL_CONTROL_CLUSTER}/"
    return any(path.startswith(prefix) for path in attributes)


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
        self.commission_state: dict[str, Any] = {"running": False, "message": None, "error": None}
        self._commission_task: asyncio.Task[None] | None = None
        self._change_lock = asyncio.Lock()

    # --- MQTT ---------------------------------------------------------------

    async def publish(self, topic: str, payload: str, retain: bool) -> None:
        mqtt = self.mqtt
        if mqtt is None:
            raise aiomqtt.MqttError("brak połączenia z brokerem MQTT")
        await mqtt.publish(topic, payload, qos=1, retain=retain)

    # --- urządzenia ---------------------------------------------------------

    def _mapped(self) -> set[tuple[int, int]]:
        return {(d.node_id, d.endpoint) for d in self.config.devices}

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
            if not endpoints:
                if not any(n == node_id for n, _ in mapped):
                    result.append(
                        {**base, "endpoint": None, "supported": False, "reason": "typ urządzenia jeszcze nieobsługiwany"}
                    )
                continue
            for endpoint in endpoints:
                if (node_id, endpoint) in mapped:
                    continue
                if is_dimmable(attrs, endpoint):
                    # Żarówka dodana dziś jako przekaźnik zajęłaby kanał na stałe (typu kanału
                    # SUPLA nie da się zmienić), więc czeka na obsługę jasności.
                    result.append(
                        {**base, "endpoint": endpoint, "supported": False, "reason": "ściemniane światło — czeka na obsługę jasności"}
                    )
                else:
                    result.append({**base, "endpoint": endpoint, "supported": True, "reason": None})
        return result

    def _new_name(self, product: Any, node_id: int, endpoint: int, taken: set[str]) -> str:
        slug = _SLUG.sub("_", str(product or "").lower()).strip("_") or "matter"
        name = f"{slug}_{node_id}" if endpoint == 1 else f"{slug}_{node_id}_e{endpoint}"
        candidate, counter = name, 2
        while candidate in taken:
            candidate = f"{name}_{counter}"
            counter += 1
        return candidate

    def _device_for(self, candidate: dict[str, Any], devices: list[Device]) -> Device:
        channel = max((d.supla_channel for d in devices), default=-1) + 1
        if channel > MAX_SUPLA_CHANNEL:
            raise ManagerError("wyczerpano numery kanałów SUPLA (0–127)")
        node = self.core.nodes.get(candidate["node_id"], {"attributes": {}})
        label = node["attributes"].get("0/40/5")
        return Device(
            name=self._new_name(candidate["product"], candidate["node_id"], candidate["endpoint"], {d.name for d in devices}),
            node_id=candidate["node_id"],
            endpoint=candidate["endpoint"],
            type="onoff",
            supla_channel=channel,
            caption=str(label or candidate["product"] or "") or None,
        )

    async def _apply(self, devices: list[Device]) -> None:
        check_devices(devices)
        old_topics = set(self.core.set_topics)
        new_config = dataclasses.replace(self.config, devices=tuple(devices))
        if self.config_path is not None:
            save_config(new_config, self.config_path)  # najpierw plik: nieudany zapis niczego nie zmienia
        self.config = new_config
        self.core.set_devices(self.config.devices)
        mqtt = self.mqtt
        if mqtt is not None:
            for dev in self.config.devices:
                topic = set_topic(self.config.mqtt.topic_prefix, dev)
                if topic not in old_topics:
                    await mqtt.subscribe(topic, qos=1)
            await self.core.publish_all()
        self.regenerate_supla()

    async def auto_add(self) -> list[Device]:
        """Dopisuje jako kanały wszystkie obsługiwane, niezmapowane endpointy."""
        if not self.config.auto_add:
            return []
        try:
            return await self._auto_add()
        except (ManagerError, ConfigError, OSError) as err:
            # Nie zatrzymujemy łącznika — istniejące kanały mają działać dalej.
            self.supla_error = f"Nie udało się dopisać nowego urządzenia: {err}"
            log.error(self.supla_error)
            return []

    async def _auto_add(self) -> list[Device]:
        async with self._change_lock:
            devices = list(self.config.devices)
            added: list[Device] = []
            for candidate in self.candidates():
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
                raise ManagerError("nie ma takiego wolnego endpointu z klastrem OnOff")
            if not candidate["supported"]:
                raise ManagerError(f"tego urządzenia nie można jeszcze dodać: {candidate['reason']}")
            devices = list(self.config.devices)
            dev = self._device_for(candidate, devices)
            await self._apply(devices + [dev])
            return dev

    def _find(self, name: str) -> Device:
        for dev in self.config.devices:
            if dev.name == name:
                return dev
        raise ManagerError(f"nie ma urządzenia {name}")

    async def set_caption(self, name: str, caption: str) -> None:
        async with self._change_lock:
            target = self._find(name)
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
        if self.supla_output is None or not self.config.devices:
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

    def start_commission(self, code: str, network_only: bool) -> None:
        code = code.strip()
        if not code:
            raise ManagerError("podaj kod parowania")
        if self.commission_state["running"]:
            raise ManagerError("parowanie już trwa")
        client = self._client()
        self.commission_state = {"running": True, "message": "Parowanie trwa (do kilku minut)…", "error": None}
        self._commission_task = asyncio.create_task(self._commission(client, code, network_only))

    async def _commission(self, client: Any, code: str, network_only: bool) -> None:
        args: dict[str, Any] = {"code": code}
        if network_only:
            args["network_only"] = True
        try:
            node = await client.command("commission_with_code", timeout=COMMISSION_TIMEOUT_SEC, **args)
            node_id = node.get("node_id") if isinstance(node, dict) else None
            self.commission_state = {
                "running": False,
                "message": f"Sparowano węzeł {node_id}." if node_id is not None else "Sparowano.",
                "error": None,
            }
            log.info("sparowano węzeł %s", node_id)
        except (MatterError, MatterConnectionError) as err:
            self.commission_state = {"running": False, "message": None, "error": str(err)}
            log.warning("parowanie nie powiodło się: %s", err)

    # --- diagnostyka --------------------------------------------------------

    def diagnostics(self, node_id: int, full: bool = False) -> dict[str, Any]:
        node = self.core.nodes.get(node_id)
        if node is None:
            raise ManagerError(f"matter-server nie zna węzła {node_id}")
        return build_diagnostics(node_id, node, full)

    # --- dane dla panelu ----------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        info = self.server_info
        devices = []
        for dev in sorted(self.config.devices, key=lambda d: d.supla_channel):
            state = self.core.state_of(dev)
            devices.append(
                {
                    "name": dev.name,
                    "caption": dev.caption or "",
                    # identyfikatory węzłów jako tekst: mogą przekraczać zakres liczb JavaScript
                    "node_id": str(dev.node_id),
                    "endpoint": dev.endpoint,
                    "type": dev.type,
                    "supla_channel": dev.supla_channel,
                    "state": {1: "on", 0: "off"}.get(state, "unavailable"),
                }
            )
        return {
            "mqtt_connected": self.mqtt is not None,
            "matter_connected": self.core.matter is not None,
            "server": {
                "version": info.get("sdk_version"),
                "thread_dataset_set": bool(info.get("thread_credentials_set")),
                "bluetooth": bool(info.get("bluetooth_enabled")),
            },
            "auto_add": self.config.auto_add,
            "supla": {"server": self._supla_server, "mail": self._supla_mail},
            "supla_error": self.supla_error
            or (None if self._supla_server and self._supla_mail else "Nie podano konta SUPLA — wpisz serwer i e-mail w sekcji „Konto SUPLA”."),
            "devices": devices,
            "candidates": [{**c, "node_id": str(c["node_id"])} for c in self.candidates()],
            "commission": self.commission_state,
        }

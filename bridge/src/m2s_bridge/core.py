"""Logika łącznika, niezależna od transportu (testowalna bez sieci).

Matter -> MQTT: stan atrybutu publikowany jako {"state": 1|0|-1} (retained).
Wartość -1 oznacza „stan nieznany” — supla-device z `offline_on_invalid_state: true`
przełącza wtedy kanał w offline.
MQTT -> Matter: ON / OFF / TOGGLE na temacie `set` -> komenda klastra OnOff.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Protocol

from .config import Config, Device
from .matter_client import MatterConnectionError, MatterError
from .topics import set_topic, state_topic

log = logging.getLogger(__name__)

ONOFF_CLUSTER = 0x0006
ONOFF_ATTRIBUTE = 0x0000
STATE_INVALID = -1

_COMMANDS = {
    "ON": "On",
    "1": "On",
    "TRUE": "On",
    "OFF": "Off",
    "0": "Off",
    "FALSE": "Off",
    "TOGGLE": "Toggle",
}

Publish = Callable[[str, str, bool], Awaitable[None]]


class CommandSender(Protocol):
    async def device_command(
        self,
        node_id: int,
        endpoint_id: int,
        cluster_id: int,
        command_name: str,
        payload: dict[str, Any] | None = None,
    ) -> Any: ...


def onoff_path(endpoint: int) -> str:
    return f"{endpoint}/{ONOFF_CLUSTER}/{ONOFF_ATTRIBUTE}"


class BridgeCore:
    def __init__(self, config: Config, publish: Publish):
        self._config = config
        self._publish = publish
        self._prefix = config.mqtt.topic_prefix
        self._matter: CommandSender | None = None
        self._nodes: dict[int, dict[str, Any]] = {}
        self._published: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self.set_devices(config.devices)

    def set_devices(self, devices: tuple[Device, ...] | list[Device]) -> None:
        self._devices = tuple(devices)
        self._by_set_topic = {set_topic(self._prefix, d): d for d in self._devices}
        self._by_node: dict[int, list[Device]] = {}
        for dev in self._devices:
            self._by_node.setdefault(dev.node_id, []).append(dev)

    @property
    def devices(self) -> tuple[Device, ...]:
        return self._devices

    @property
    def nodes(self) -> dict[int, dict[str, Any]]:
        return self._nodes

    @property
    def matter(self) -> CommandSender | None:
        return self._matter

    @property
    def set_topics(self) -> list[str]:
        return list(self._by_set_topic)

    # --- stan ---------------------------------------------------------------

    def state_of(self, dev: Device) -> int:
        if self._matter is None:
            return STATE_INVALID
        node = self._nodes.get(dev.node_id)
        if node is None or not node["available"]:
            return STATE_INVALID
        value = node["attributes"].get(onoff_path(dev.endpoint))
        if isinstance(value, bool):
            return int(value)
        return STATE_INVALID

    async def _publish_devices(self, devices: list[Device] | tuple[Device, ...], force: bool) -> None:
        async with self._lock:
            for dev in devices:
                topic = state_topic(self._prefix, dev)
                payload = json.dumps({"state": self.state_of(dev)})
                if not force and self._published.get(topic) == payload:
                    continue
                await self._publish(topic, payload, True)
                if self._published.get(topic) != payload:
                    log.info("%s: %s", dev.name, payload)
                self._published[topic] = payload

    async def publish_all(self) -> None:
        """Publikuje stan wszystkich urządzeń (także niezmieniony) — start i heartbeat."""
        await self._publish_devices(self._devices, force=True)

    # --- Matter -> MQTT -----------------------------------------------------

    def _store_node(self, data: Any) -> int | None:
        if not isinstance(data, dict) or not isinstance(data.get("node_id"), int):
            return None
        node_id = data["node_id"]
        attributes = data.get("attributes")
        self._nodes[node_id] = {
            "available": bool(data.get("available")),
            "attributes": dict(attributes) if isinstance(attributes, dict) else {},
        }
        return node_id

    async def matter_connected(self, matter: CommandSender, nodes: list[dict[str, Any]]) -> None:
        self._matter = matter
        self._nodes.clear()
        for node in nodes or []:
            self._store_node(node)
        for dev in self._devices:
            node = self._nodes.get(dev.node_id)
            if node is None:
                log.warning("%s: węzeł %d nie istnieje w matter-server", dev.name, dev.node_id)
            elif onoff_path(dev.endpoint) not in node["attributes"]:
                log.warning(
                    "%s: węzeł %d nie ma klastra OnOff na endpoincie %d",
                    dev.name,
                    dev.node_id,
                    dev.endpoint,
                )
        await self.publish_all()

    async def matter_disconnected(self) -> None:
        self._matter = None
        self._nodes.clear()
        await self.publish_all()

    async def handle_event(self, event: str, data: Any) -> None:
        node_id: int | None = None
        if event in ("node_added", "node_updated"):
            # Pierwszy raport węzła po restarcie serwera przychodzi tylko tędy,
            # nie jako attribute_updated.
            node_id = self._store_node(data)
        elif event == "node_removed":
            if isinstance(data, int):
                node_id = data
                self._nodes.pop(node_id, None)
        elif event == "attribute_updated":
            if isinstance(data, list) and len(data) == 3 and isinstance(data[0], int):
                node = self._nodes.get(data[0])
                if node is not None:
                    node["attributes"][str(data[1])] = data[2]
                    node_id = data[0]
        if node_id is not None and node_id in self._by_node:
            await self._publish_devices(self._by_node[node_id], force=False)

    # --- MQTT -> Matter -----------------------------------------------------

    async def send_command(self, dev: Device, command: str) -> bool:
        """Wysyła On/Off/Toggle do urządzenia; False, gdy się nie udało."""
        matter = self._matter
        if matter is None or self.state_of(dev) == STATE_INVALID:
            log.warning("%s: komenda %s odrzucona — urządzenie niedostępne", dev.name, command)
            return False
        try:
            await matter.device_command(dev.node_id, dev.endpoint, ONOFF_CLUSTER, command)
            log.info("%s: wysłano %s", dev.name, command)
            return True
        except (MatterError, MatterConnectionError) as err:
            log.error("%s: komenda %s nie powiodła się: %s", dev.name, command, err)
            # Przypominamy faktyczny stan, żeby SUPLA nie pokazywała niewykonanej zmiany.
            await self._publish_devices([dev], force=True)
            return False

    async def handle_mqtt(self, topic: str, payload: bytes | str | None, retain: bool = False) -> None:
        dev = self._by_set_topic.get(topic)
        if dev is None:
            return
        if retain:
            # Zachowana komenda to polecenie sprzed restartu — nie wykonujemy go ponownie.
            log.warning("%s: pominięto komendę z flagą retain", dev.name)
            return
        if isinstance(payload, (bytes, bytearray)):
            text = payload.decode("utf-8", errors="replace")
        else:
            text = "" if payload is None else str(payload)
        command = _COMMANDS.get(text.strip().strip('"').upper())
        if command is None:
            log.warning("%s: nieznana komenda %r", dev.name, text[:40])
            return
        await self.send_command(dev, command)

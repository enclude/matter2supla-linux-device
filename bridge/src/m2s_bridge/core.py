# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Logika łącznika, niezależna od transportu (testowalna bez sieci).

Gniazdko (`onoff`):
  stan    `{"state": 1|0|-1}`            (-1 = niedostępne; supla-device przełącza kanał w offline)
  komenda `ON` / `OFF` / `TOGGLE`
Światło z regulacją bieli (`light_cct`), format dla rozszerzenia MatterLight w supla-device:
  stan    `S B T`  — S: 1/0/-1, B: jasność 1–100, T: biel 0 (ciepła) – 100 (zimna)
  komenda `B T`    — B: 0 = zgaś, 1–100 = jasność; T: 0–100 albo -1 = bez zmiany
Grupa świateł (`light_group`): tematy `<prefix>/group/<nazwa>/state|set`, format jak dla światła.
  Stan zbiorczy: włączona, gdy świeci którekolwiek światło; jasność i biel z pierwszego
  świecącego (a gdy wszystkie zgaszone - z pierwszego dostępnego); niedostępna dopiero wtedy,
  gdy nie odpowiada żadne. Komenda trafia do wszystkich dostępnych świateł grupy naraz.
Licznik energii (`meter`), dla kanału ElectricityMeterParsed w supla-device:
  stan    `{"voltage": V, "current": A, "power": W, "energy": kWh, "frequency": Hz}` — tylko
          pomiary zapisane dla urządzenia; pusty komunikat = niedostępne (kasuje też stan
          zachowany w brokerze, więc supla-device nie dostanie starych wartości)
  komend nie ma — łącznik nie subskrybuje dla licznika żadnego tematu
Stany są publikowane z flagą retain.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Protocol

from . import light, meter
from .config import GROUP_TYPE, NO_CHANNEL, Config, Device
from .matter_client import MatterConnectionError, MatterError
from .topics import set_topic, state_topic

log = logging.getLogger(__name__)

ONOFF_CLUSTER = 0x0006
ONOFF_ATTRIBUTE = 0x0000
STATE_INVALID = -1
IDENTIFY_CLUSTER = 0x0003
IDENTIFY_SEC = 15  # tyle urządzenie daje o sobie znać (żarówka pulsuje, gniazdko miga diodą)
LIGHT_TYPES = ("light_cct", GROUP_TYPE)

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
        # Licznik nie przyjmuje komend, więc nie ma tematu `set`.
        self._by_set_topic = {set_topic(self._prefix, d): d for d in self._devices if d.type != "meter"}
        self._by_node: dict[int, list[Device]] = {}
        for dev in self._devices:
            if dev.type == GROUP_TYPE:
                # Stan grupy zależy od każdego z jej świateł.
                for node_id in dict.fromkeys(node_id for node_id, _ in dev.members):
                    self._by_node.setdefault(node_id, []).append(dev)
            else:
                self._by_node.setdefault(dev.node_id, []).append(dev)

    @staticmethod
    def members_of(group: Device) -> list[Device]:
        """Światła grupy jako urządzenia `light_cct` (bez kanału, tylko do odczytu i komend)."""
        return [
            Device(
                name=f"{group.name}:{node_id}/{endpoint}",
                node_id=node_id,
                endpoint=endpoint,
                type="light_cct",
                supla_channel=NO_CHANNEL,
            )
            for node_id, endpoint in group.members
        ]

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

    def _attributes(self, dev: Device) -> dict[str, Any] | None:
        if self._matter is None:
            return None
        node = self._nodes.get(dev.node_id)
        if node is None or not node["available"]:
            return None
        return node["attributes"]

    def state_of(self, dev: Device) -> int:
        if dev.type == GROUP_TYPE:
            current = self.light_state(dev)
            return STATE_INVALID if current is None else current["state"]
        attributes = self._attributes(dev)
        if attributes is None:
            return STATE_INVALID
        value = attributes.get(onoff_path(dev.endpoint))
        if isinstance(value, bool):
            return int(value)
        return STATE_INVALID

    def light_state(self, dev: Device) -> dict[str, int] | None:
        """Stan światła w skali SUPLA albo None, gdy niedostępne lub niepełne dane."""
        if dev.type == GROUP_TYPE:
            states = [s for member in self.members_of(dev) if (s := self.light_state(member)) is not None]
            if not states:
                return None
            lit = [s for s in states if s["state"] == 1]
            reference = (lit or states)[0]
            return {
                "state": 1 if lit else 0,
                "brightness": reference["brightness"],
                "temperature": reference["temperature"],
            }
        attributes = self._attributes(dev)
        state = self.state_of(dev)
        if attributes is None or state == STATE_INVALID:
            return None
        level = attributes.get(light.level_path(dev.endpoint))
        mireds = attributes.get(light.mireds_path(dev.endpoint))
        if not isinstance(level, int) or isinstance(level, bool):
            return None
        low, high = light.mireds_range(attributes, dev.endpoint)
        temperature = light.mireds_to_temperature(mireds, low, high) if isinstance(mireds, int) else 50
        return {"state": state, "brightness": light.level_to_percent(level), "temperature": temperature}

    def meter_state(self, dev: Device) -> dict[str, float] | None:
        """Pomiary licznika w jednostkach SUPLA albo None, gdy niedostępny lub niepełne dane."""
        attributes = self._attributes(dev)
        if attributes is None:
            return None
        return meter.values(attributes, dev.endpoint, dev.measurements)

    def state_payload(self, dev: Device) -> str:
        if dev.type == "meter":
            current = self.meter_state(dev)
            return "" if current is None else json.dumps(current)
        if dev.type in LIGHT_TYPES:
            current = self.light_state(dev)
            if current is None:
                return "-1 0 0"
            return f"{current['state']} {current['brightness']} {current['temperature']}"
        return json.dumps({"state": self.state_of(dev)})

    async def _publish_devices(self, devices: list[Device] | tuple[Device, ...], force: bool) -> None:
        async with self._lock:
            for dev in devices:
                topic = state_topic(self._prefix, dev)
                payload = self.state_payload(dev)
                if not force and self._published.get(topic) == payload:
                    continue
                await self._publish(topic, payload, True)
                if self._published.get(topic) != payload:
                    log.info("%s: %s", dev.name, payload or "(niedostępne)")
                self._published[topic] = payload

    async def publish_all(self) -> None:
        """Publikuje stan wszystkich urządzeń (także niezmieniony) — start i heartbeat."""
        await self._publish_devices(self._devices, force=True)

    async def publish_new(self) -> None:
        """Publikuje stany, których jeszcze nie wysłano (np. po dodaniu urządzenia)."""
        await self._publish_devices(self._devices, force=False)

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
            if dev.type == GROUP_TYPE:
                for node_id, _ in dev.members:
                    if node_id not in self._nodes:
                        log.warning("%s: węzeł %d z grupy nie istnieje w matter-server", dev.name, node_id)
                continue
            node = self._nodes.get(dev.node_id)
            if node is None:
                log.warning("%s: węzeł %d nie istnieje w matter-server", dev.name, dev.node_id)
            elif dev.type == "meter":
                if dev.endpoint not in meter.endpoints(node["attributes"]):
                    log.warning(
                        "%s: węzeł %d nie ma pomiaru mocy ani energii na endpoincie %d",
                        dev.name,
                        dev.node_id,
                        dev.endpoint,
                    )
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

    async def _invoke(self, dev: Device, cluster: int, command: str, payload: dict[str, Any] | None = None) -> bool:
        matter = self._matter
        if matter is None or self.state_of(dev) == STATE_INVALID:
            log.warning("%s: komenda %s odrzucona — urządzenie niedostępne", dev.name, command)
            return False
        try:
            if payload is None:
                await matter.device_command(dev.node_id, dev.endpoint, cluster, command)
            else:
                await matter.device_command(dev.node_id, dev.endpoint, cluster, command, payload)
            log.info("%s: wysłano %s %s", dev.name, command, payload or "")
            return True
        except (MatterError, MatterConnectionError) as err:
            log.error("%s: komenda %s nie powiodła się: %s", dev.name, command, err)
            # Przypominamy faktyczny stan, żeby SUPLA nie pokazywała niewykonanej zmiany.
            await self._publish_devices([dev], force=True)
            return False

    async def send_command(self, dev: Device, command: str) -> bool:
        """Wysyła On/Off/Toggle do urządzenia; False, gdy się nie udało."""
        if dev.type == GROUP_TYPE:
            if command == "Toggle":
                command = "Off" if self.state_of(dev) == 1 else "On"
            return await self._fan_out(dev, lambda member: self._invoke(member, ONOFF_CLUSTER, command))
        return await self._invoke(dev, ONOFF_CLUSTER, command)

    def can_identify(self, dev: Device) -> bool:
        """Czy urządzenie (albo któreś światło grupy) ma klaster Identify."""
        if dev.type == GROUP_TYPE:
            return any(self.can_identify(member) for member in self.members_of(dev))
        attributes = self._attributes(dev) or {}
        prefix = f"{dev.endpoint}/{IDENTIFY_CLUSTER}/"
        return any(path.startswith(prefix) for path in attributes)

    async def identify(self, dev: Device, seconds: int = IDENTIFY_SEC) -> bool:
        """Każe urządzeniu dać o sobie znać; dla grupy - wszystkim jej światłom naraz."""
        if dev.type == GROUP_TYPE:
            return await self._fan_out(dev, lambda member: self.identify(member, seconds))
        return await self._invoke(dev, IDENTIFY_CLUSTER, "Identify", {"identifyTime": seconds})

    async def _fan_out(self, group: Device, action: Callable[[Device], Awaitable[bool]]) -> bool:
        """Wykonuje komendę na wszystkich dostępnych światłach grupy; True, gdy udało się choć jedno."""
        members = [m for m in self.members_of(group) if self.state_of(m) != STATE_INVALID]
        if not members:
            log.warning("%s: komenda odrzucona — żadne światło grupy nie jest dostępne", group.name)
            return False
        results = await asyncio.gather(*(action(member) for member in members))
        if not all(results):
            # Przypominamy faktyczny stan grupy, żeby SUPLA nie pokazywała niewykonanej zmiany.
            await self._publish_devices([group], force=True)
        return any(results)

    async def set_light(self, dev: Device, brightness: int | None, temperature: int | None) -> bool:
        """Ustawia jasność (0 = zgaś, 1–100) i/lub biel (0–100). Pomija to, co już jest ustawione."""
        if dev.type == GROUP_TYPE:
            # Każde światło porównuje polecenie z własnym stanem, więc grupa się wyrównuje.
            return await self._fan_out(dev, lambda member: self.set_light(member, brightness, temperature))
        current = self.light_state(dev)
        if current is None:
            log.warning("%s: komenda światła odrzucona — urządzenie niedostępne", dev.name)
            return False
        ok = True
        if temperature is not None and not 0 <= temperature <= 100:
            temperature = None
        if temperature is not None and temperature != current["temperature"]:
            low, high = light.mireds_range(self._attributes(dev) or {}, dev.endpoint)
            ok &= await self._invoke(
                dev,
                light.COLOR_CLUSTER,
                "MoveToColorTemperature",
                {
                    "colorTemperatureMireds": light.temperature_to_mireds(temperature, low, high),
                    "transitionTime": light.TRANSITION,
                    "optionsMask": light.EXECUTE_IF_OFF,
                    "optionsOverride": light.EXECUTE_IF_OFF,
                },
            )
        if brightness is not None:
            brightness = max(0, min(100, brightness))
            if brightness == 0:
                if current["state"] == 1:
                    ok &= await self._invoke(dev, ONOFF_CLUSTER, "Off")
            elif current["state"] != 1 or brightness != current["brightness"]:
                ok &= await self._invoke(
                    dev,
                    light.LEVEL_CLUSTER,
                    "MoveToLevelWithOnOff",
                    {
                        "level": light.percent_to_level(brightness),
                        "transitionTime": light.TRANSITION,
                        "optionsMask": 0,
                        "optionsOverride": 0,
                    },
                )
        return ok

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
        if dev.type in LIGHT_TYPES:
            try:
                values = [int(part) for part in text.split()]
            except ValueError:
                values = []
            if not 1 <= len(values) <= 2:
                log.warning("%s: nieznana komenda światła %r", dev.name, text[:40])
                return
            temperature = values[1] if len(values) == 2 and values[1] >= 0 else None
            await self.set_light(dev, values[0], temperature)
            return
        command = _COMMANDS.get(text.strip().strip('"').upper())
        if command is None:
            log.warning("%s: nieznana komenda %r", dev.name, text[:40])
            return
        await self.send_command(dev, command)

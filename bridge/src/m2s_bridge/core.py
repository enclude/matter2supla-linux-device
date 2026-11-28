# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Logika łącznika, niezależna od transportu (testowalna bez sieci).

Gniazdko (`onoff`):
  stan    `{"state": 1|0|-1}`            (-1 = niedostępne; supla-device przełącza kanał w offline)
  komenda `ON` / `OFF` / `TOGGLE`
Światło z regulacją bieli (`light_cct`), format dla rozszerzenia MatterLight w supla-device:
  stan    `S B T`  — S: 1/0/-1, B: jasność 1–100, T: biel 0 (ciepła) – 100 (zimna)
  komenda `B T`    — B: 0 = zgaś, 1–100 = jasność; T: 0–100 albo -1 = bez zmiany
Światło kolorowe (`light_rgb`, żarówka z barwą i bielą), format dla rozszerzenia MatterRgbLight:
  stan    `S M B T R G B` — S: 1/0/-1, M: 1 = biała, 2 = kolorowa, B: jasność 1–100,
          T: biel 0–100, R G B: kolor 0–255 (ostatni ustawiony, także gdy świeci biel)
  komenda `M B T R G B`   — M: 0 = zgaś, 1 = biel (B, T), 2 = kolor (B, R, G, B); pola
          nieużywane w danym trybie są pomijane
Grupa świateł (`light_group`): tematy `<prefix>/group/<nazwa>/state|set`, format jak dla światła.
  Stan zbiorczy: włączona, gdy świeci którekolwiek światło; jasność i biel z pierwszego
  świecącego (a gdy wszystkie zgaszone - z pierwszego dostępnego); niedostępna dopiero wtedy,
  gdy nie odpowiada żadne. Komenda trafia do wszystkich dostępnych świateł grupy naraz.
Czujniki (`leak` i kolejne, patrz sensor.py), dla kanałów ...Parsed w supla-device:
  stan    JSON z wartościami czujnika (np. `{"state": 1, "battery": 87}`); pusty komunikat =
          niedostępny. Komend nie ma.
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
import time
from typing import Any, Awaitable, Callable, Protocol

from . import light, meter, sensor
from .config import GROUP_TYPE, NO_CHANNEL, Config, Device
from .matter_client import MatterConnectionError, MatterError
from .topics import set_topic, state_topic

log = logging.getLogger(__name__)

ONOFF_CLUSTER = 0x0006
ONOFF_ATTRIBUTE = 0x0000
STATE_INVALID = -1
STARTUP_ATTRIBUTE = 0x4003  # OnOff.StartUpOnOff: null = ostatni stan
STARTUP_VALUES: dict[str, int | None] = {"previous": None, "off": 0, "on": 1, "toggle": 2}
STARTUP_NAMES = {0: "off", 1: "on", 2: "toggle"}
IDENTIFY_CLUSTER = 0x0003
IDENTIFY_SEC = 15  # tyle urządzenie daje o sobie znać (żarówka pulsuje, gniazdko miga diodą)
LIGHT_TYPES = ("light_cct", "light_rgb", GROUP_TYPE)
RGB_TYPE = "light_rgb"

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
        # Kiedy węzeł ostatnio dał znak życia (czas systemowy); przetrwa ponowne połączenie z kontrolerem.
        self._last_seen: dict[int, float] = {}
        self._lock = asyncio.Lock()
        self.set_devices(config.devices)

    def set_devices(self, devices: tuple[Device, ...] | list[Device]) -> None:
        self._devices = tuple(devices)
        # Licznik i czujniki nie przyjmują komend, więc nie mają tematu `set`.
        self._by_set_topic = {
            set_topic(self._prefix, d): d for d in self._devices if d.type != "meter" and d.type not in sensor.SENSOR_TYPES
        }
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
        result = {"state": state, "brightness": light.level_to_percent(level), "temperature": temperature}
        if dev.type == RGB_TYPE:
            mode, rgb = self._color_of(attributes, dev.endpoint)
            result["mode"] = mode
            result["rgb"] = rgb  # type: ignore[assignment]
        return result

    @staticmethod
    def _color_of(attributes: dict[str, Any], endpoint: int) -> tuple[int, tuple[int, int, int]]:
        """Tryb (1 = biel, 2 = kolor) i kolor RGB światła kolorowego."""
        matter_mode = attributes.get(light.color_mode_path(endpoint))
        mode = light.MODE_WHITE if matter_mode == light.COLOR_MODE_CT else light.MODE_COLOR
        hue = attributes.get(light.hue_path(endpoint))
        saturation = attributes.get(light.saturation_path(endpoint))
        x, y = attributes.get(f"{endpoint}/{light.COLOR_CLUSTER}/3"), attributes.get(f"{endpoint}/{light.COLOR_CLUSTER}/4")
        if matter_mode == light.COLOR_MODE_XY and isinstance(x, int) and isinstance(y, int):
            return mode, light.xy_to_rgb(x, y)
        if isinstance(hue, int) and isinstance(saturation, int):
            return mode, light.hs_to_rgb(hue, saturation)
        return mode, light.DEFAULT_RGB

    def meter_state(self, dev: Device) -> dict[str, float] | None:
        """Pomiary licznika w jednostkach SUPLA albo None, gdy niedostępny lub niepełne dane."""
        attributes = self._attributes(dev)
        if attributes is None:
            return None
        return meter.values(attributes, dev.endpoint, dev.measurements)

    def sensor_state(self, dev: Device) -> dict[str, float | int] | None:
        """Wartości czujnika (jednostki SUPLA) albo None, gdy niedostępny lub niepełne dane."""
        attributes = self._attributes(dev)
        if attributes is None:
            return None
        return sensor.values(attributes, dev.type, dev.endpoint, dev.measurements)

    def state_payload(self, dev: Device) -> str:
        if dev.type in sensor.SENSOR_TYPES:
            current = self.sensor_state(dev)
            return "" if current is None else json.dumps(current)
        if dev.type == "meter":
            current = self.meter_state(dev)
            return "" if current is None else json.dumps(current)
        if dev.type in LIGHT_TYPES:
            current = self.light_state(dev)
            if dev.type == RGB_TYPE:
                if current is None:
                    return "-1 1 0 0 255 255 255"
                red, green, blue = current["rgb"]  # type: ignore[misc]
                return (
                    f"{current['state']} {current['mode']} {current['brightness']} "
                    f"{current['temperature']} {red} {green} {blue}"
                )
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
        if data.get("available"):
            self._last_seen[node_id] = time.time()
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
            elif dev.type in sensor.SENSOR_TYPES:
                if (dev.type, dev.endpoint) not in sensor.slots(node["attributes"]):
                    log.warning(
                        "%s: węzeł %d nie ma czujnika typu %s na endpoincie %d",
                        dev.name,
                        dev.node_id,
                        dev.type,
                        dev.endpoint,
                    )
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
                    self._last_seen[node_id] = time.time()
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

    def startup_of(self, dev: Device) -> str | None:
        """Zachowanie po powrocie zasilania: previous / on / off / toggle; None - brak danych albo nieobsługiwane.

        Dla grupy: wspólna wartość członków albo "mixed", gdy się różnią."""
        if dev.type == GROUP_TYPE:
            values = {v for m in self.members_of(dev) if (v := self.startup_of(m)) is not None}
            if not values:
                return None
            return values.pop() if len(values) == 1 else "mixed"
        attributes = self._attributes(dev)
        path = f"{dev.endpoint}/{ONOFF_CLUSTER}/{STARTUP_ATTRIBUTE}"
        if attributes is None or path not in attributes:
            return None
        return STARTUP_NAMES.get(attributes[path], "previous") if attributes[path] is not None else "previous"

    def can_set_startup(self, dev: Device) -> bool:
        if dev.type == GROUP_TYPE:
            return any(self.can_set_startup(m) for m in self.members_of(dev))
        return dev.type != "meter" and dev.type not in sensor.SENSOR_TYPES and self.startup_of(dev) is not None

    async def set_startup(self, dev: Device, mode: str) -> bool:
        """Ustawia zachowanie po powrocie zasilania w samym urządzeniu (atrybut StartUpOnOff)."""
        if mode not in STARTUP_VALUES:
            raise ValueError(f"nieznany tryb: {mode}")
        if dev.type == GROUP_TYPE:
            members = [m for m in self.members_of(dev) if self.can_set_startup(m)]
            results = await asyncio.gather(*(self.set_startup(m, mode) for m in members))
            return bool(results) and all(results)
        matter, attributes = self._matter, self._attributes(dev)
        if matter is None or attributes is None:
            log.warning("%s: zapis ustawienia odrzucony - urządzenie niedostępne", dev.name)
            return False
        path = f"{dev.endpoint}/{ONOFF_CLUSTER}/{STARTUP_ATTRIBUTE}"
        try:
            await matter.write_attribute(dev.node_id, path, STARTUP_VALUES[mode])
        except (MatterError, MatterConnectionError) as err:
            log.error("%s: zapis %s nie powiódł się: %s", dev.name, path, err)
            return False
        attributes[path] = STARTUP_VALUES[mode]
        log.info("%s: po powrocie zasilania: %s", dev.name, mode)
        return True

    def last_seen(self, node_id: int) -> float | None:
        """Czas ostatniego znaku życia węzła (sekundy epoki) albo None, gdy nie było go od startu łącznika."""
        return self._last_seen.get(node_id)

    @staticmethod
    def accepts_commands(dev: Device) -> bool:
        """Licznik energii i czujniki tylko raportują stan - nie włączysz ich ani nie wskażesz „Znajdź”."""
        return dev.type != "meter" and dev.type not in sensor.SENSOR_TYPES

    def can_identify(self, dev: Device) -> bool:
        """Czy urządzenie (albo któreś światło grupy) ma klaster Identify; licznik i czujniki nie przyjmują komend."""
        if not self.accepts_commands(dev):
            return False
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

    async def set_light_rgb(
        self,
        dev: Device,
        brightness: int | None,
        temperature: int | None,
        rgb: tuple[int, int, int] | None,
    ) -> bool:
        """Światło kolorowe: `rgb` ustawia kolor, `temperature` biel (kolor ma pierwszeństwo).

        Bez żadnego z nich zmienia się tylko jasność w bieżącym trybie. Pomija to, co już jest ustawione.
        """
        current = self.light_state(dev)
        attributes = self._attributes(dev)
        if current is None or attributes is None:
            log.warning("%s: komenda światła odrzucona — urządzenie niedostępne", dev.name)
            return False
        if brightness is not None:
            brightness = max(0, min(100, brightness))
        ok = True
        options = {"optionsMask": light.EXECUTE_IF_OFF, "optionsOverride": light.EXECUTE_IF_OFF}
        if rgb is not None and brightness != 0:
            hue, saturation = light.rgb_to_hs(*rgb)
            now_hue, now_saturation = light.rgb_to_hs(*current["rgb"])  # type: ignore[misc]
            if current["mode"] != light.MODE_COLOR or (hue, saturation) != (now_hue, now_saturation):
                ok &= await self._invoke(
                    dev,
                    light.COLOR_CLUSTER,
                    "MoveToHueAndSaturation",
                    {"hue": hue, "saturation": saturation, "transitionTime": light.TRANSITION, **options},
                )
        elif temperature is not None and brightness != 0:
            temperature = max(0, min(100, temperature))
            if current["mode"] != light.MODE_WHITE or temperature != current["temperature"]:
                low, high = light.mireds_range(attributes, dev.endpoint)
                ok &= await self._invoke(
                    dev,
                    light.COLOR_CLUSTER,
                    "MoveToColorTemperature",
                    {
                        "colorTemperatureMireds": light.temperature_to_mireds(temperature, low, high),
                        "transitionTime": light.TRANSITION,
                        **options,
                    },
                )
        if brightness is not None:
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
        if dev.type == RGB_TYPE:
            try:
                values = [int(part) for part in text.split()]
            except ValueError:
                values = []
            if len(values) != 6 or values[0] not in (0, light.MODE_WHITE, light.MODE_COLOR):
                log.warning("%s: nieznana komenda światła %r", dev.name, text[:40])
                return
            mode, brightness, temperature = values[0], values[1], values[2]
            color = tuple(max(0, min(255, v)) for v in values[3:6])
            if mode == 0:
                await self.set_light_rgb(dev, 0, None, None)
            elif mode == light.MODE_WHITE:
                await self.set_light_rgb(dev, brightness, temperature if temperature >= 0 else None, None)
            else:
                await self.set_light_rgb(dev, brightness, None, color)  # type: ignore[arg-type]
            return
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

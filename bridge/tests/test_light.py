# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Światło z regulacją bieli — na zrzucie diagnostycznym prawdziwej żarówki KAJPLATS."""

import json
from pathlib import Path

import pytest
import yaml

from m2s_bridge import light
from m2s_bridge.config import Device, load_config, parse_config
from m2s_bridge.core import BridgeCore
from m2s_bridge.manager import Manager, ManagerError

from test_manager import FakeClient, FakeMqtt

FIXTURES = Path(__file__).parent / "fixtures"
BULB = Device(name="lampa", node_id=3, endpoint=1, type="light_cct", supla_channel=1)
STATE = "m2s/3/1/light_cct/state"
SET = "m2s/3/1/light_cct/set"


def bulb_node(on=True, level=254, mireds=370):
    node = json.loads((FIXTURES / "ikea-kajplats-e27-ws.json").read_text(encoding="utf-8"))["data"]["node"]
    node["available"] = True
    node["attributes"].update({"1/6/0": on, "1/8/0": level, "1/768/7": mireds})
    return node


class Sender:
    def __init__(self):
        self.sent = []

    async def device_command(self, node_id, endpoint_id, cluster_id, command_name, payload=None):
        self.sent.append((cluster_id, command_name, payload))


class Recorder:
    def __init__(self):
        self.published = []

    async def __call__(self, topic, payload, retain):
        self.published.append((topic, payload))


@pytest.fixture
async def core():
    recorder = Recorder()
    core = BridgeCore(parse_config({}), recorder)
    core.set_devices([BULB])
    core.recorder, core.sender = recorder, Sender()
    await core.matter_connected(core.sender, [bulb_node()])
    return core


def test_scale_conversions():
    assert light.level_to_percent(254) == 100 and light.level_to_percent(1) == 1
    assert light.percent_to_level(100) == 254 and light.percent_to_level(1) == 3
    # zakres żarówki: 153 (zimna) – 454 (ciepła) miredów
    assert light.mireds_to_temperature(454, 153, 454) == 0
    assert light.mireds_to_temperature(153, 153, 454) == 100
    assert light.temperature_to_mireds(0, 153, 454) == 454
    assert light.temperature_to_mireds(100, 153, 454) == 153
    for temperature in (0, 13, 50, 77, 100):  # przeliczenie tam i z powrotem jest stabilne
        mireds = light.temperature_to_mireds(temperature, 153, 454)
        assert light.mireds_to_temperature(mireds, 153, 454) == temperature


async def test_state_is_published_in_supla_scale(core):
    assert core.recorder.published[-1] == (STATE, "1 100 28")  # 370 miredów -> 28 w skali 0–100
    await core.handle_event("attribute_updated", [3, "1/8/0", 127])
    await core.handle_event("attribute_updated", [3, "1/768/7", 153])
    await core.handle_event("attribute_updated", [3, "1/6/0", False])
    assert [p for _, p in core.recorder.published[-3:]] == ["1 50 28", "1 50 100", "0 50 100"]
    await core.handle_event("node_updated", {**bulb_node(), "available": False})
    assert core.recorder.published[-1] == (STATE, "-1 0 0")


async def test_brightness_and_temperature_commands(core):
    await core.handle_mqtt(SET, b"50 100")
    assert core.sender.sent == [
        (768, "MoveToColorTemperature", {"colorTemperatureMireds": 153, "transitionTime": 3, "optionsMask": 1, "optionsOverride": 1}),
        (8, "MoveToLevelWithOnOff", {"level": 127, "transitionTime": 3, "optionsMask": 0, "optionsOverride": 0}),
    ]


async def test_unchanged_values_send_nothing(core):
    await core.handle_mqtt(SET, b"100 28")  # dokładnie bieżący stan
    await core.handle_mqtt(SET, b"100 -1")
    await core.handle_mqtt(SET, b"100")
    assert core.sender.sent == []


async def test_zero_brightness_turns_off_and_keeps_level(core):
    await core.handle_mqtt(SET, b"0 -1")
    assert core.sender.sent == [(6, "Off", None)]
    await core.handle_event("attribute_updated", [3, "1/6/0", False])
    await core.handle_mqtt(SET, b"0 -1")  # już zgaszone — bez komendy
    await core.handle_mqtt(SET, b"100 -1")  # ta sama jasność, ale trzeba zapalić
    assert [c[1] for c in core.sender.sent] == ["Off", "MoveToLevelWithOnOff"]


async def test_temperature_can_change_while_off(core):
    await core.handle_event("attribute_updated", [3, "1/6/0", False])
    await core.set_light(BULB, None, 0)
    assert core.sender.sent == [
        (768, "MoveToColorTemperature", {"colorTemperatureMireds": 454, "transitionTime": 3, "optionsMask": 1, "optionsOverride": 1})
    ]


async def test_bad_or_retained_commands_are_ignored(core):
    for payload in (b"", b"jasno", b"1 2 3", b"ON"):
        await core.handle_mqtt(SET, payload)
    await core.handle_mqtt(SET, b"10 10", retain=True)
    assert core.sender.sent == []
    assert await core.set_light(BULB, 500, 500) is True  # wartości spoza zakresu są przycinane/pomijane
    assert core.sender.sent == []  # 500 -> 100 % = bieżąca jasność; biel spoza zakresu pominięta


async def test_unavailable_light_rejects_commands(core):
    await core.handle_event("node_updated", {**bulb_node(), "available": False})
    assert await core.set_light(BULB, 50, 50) is False
    assert core.sender.sent == []


# --- menedżer ---------------------------------------------------------------


async def make_manager(tmp_path, **config):
    manager = Manager(parse_config(config), tmp_path / "devices.yaml", tmp_path / "supla" / "supla-device.yaml", "s.example.org", "u@example.org")
    manager.mqtt = FakeMqtt()
    client = FakeClient()
    manager.matter_client = client
    await manager.core.matter_connected(client, [bulb_node()])
    await manager.auto_add()
    return manager, client


async def test_light_without_channel_is_controllable_from_panel(tmp_path):
    manager, client = await make_manager(tmp_path, lights_as_channels=False)
    assert manager.config.devices == ()  # żadnego kanału SUPLA
    assert not (tmp_path / "devices.yaml").exists() and not (tmp_path / "supla" / "supla-device.yaml").exists()
    snap = manager.snapshot()
    control = snap["candidates"][0]["control"]
    assert snap["candidates"][0]["supported"] is False
    assert (control["type"], control["state"], control["brightness"], control["temperature"]) == ("light_cct", "on", 100, 28)
    # tematy MQTT działają mimo braku kanału
    assert manager.mqtt.subscribed == ["m2s/3/1/light_cct/set"]
    assert ("m2s/3/1/light_cct/state", "1 100 28") in manager.mqtt.published
    await manager.set_light(control["name"], 40, None)
    assert client.calls[-1][1][2:4] == (8, "MoveToLevelWithOnOff")
    with pytest.raises(ManagerError):
        await manager.set_caption(control["name"], "Lampa")
    await manager.auto_add()  # kolejne zdarzenia nie dublują subskrypcji
    assert manager.mqtt.subscribed == ["m2s/3/1/light_cct/set"]


async def test_light_becomes_channel_by_default(tmp_path):
    manager, _ = await make_manager(tmp_path)
    dev = manager.config.devices[0]
    assert (dev.type, dev.supla_channel) == ("light_cct", 0)
    assert load_config(tmp_path / "devices.yaml").devices == manager.config.devices
    supla = yaml.safe_load((tmp_path / "supla" / "supla-device.yaml").read_text(encoding="utf-8"))
    assert supla["supla"]["proto"] == 28
    assert supla["channels"][0] == {
        "type": "MatterLight",
        "name": dev.name,
        "channel_number": 0,
        "initial_caption": "KAJPLATS E27 WS globe 1521lm",
        "state_topic": "m2s/3/1/light_cct/state",
        "set_topic": "m2s/3/1/light_cct/set",
        "expiration_time_sec": 120,
    }
    assert manager.snapshot()["candidates"] == []


def test_kajplats_gu10_is_the_same_kind_of_light():
    # Zrzut prawdziwej KAJPLATS GU10 WS 575lm: te same klastry co E27, więc ten sam typ `light_cct`
    # (obsługa wynika z klastrów, nie z identyfikatora produktu).
    from m2s_bridge.manager import endpoint_type

    node = json.loads((FIXTURES / "ikea-kajplats-gu10-ws.json").read_text(encoding="utf-8"))["data"]["node"]
    assert node["attributes"]["0/40/4"] == 36866
    assert endpoint_type(node["attributes"], 1) == "light_cct"
    assert light.mireds_range(node["attributes"], 1) == (153, 454)

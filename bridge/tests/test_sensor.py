# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Czujniki - na zrzutach diagnostycznych prawdziwych urządzeń IKEA."""

import json
from pathlib import Path

import pytest

from m2s_bridge import sensor
from m2s_bridge.config import ConfigError, Device, load_config, parse_config
from m2s_bridge.core import BridgeCore
from m2s_bridge.manager import Manager
from m2s_bridge.supla_config import build_supla_config, check_lock, lock_entries

from test_manager import FakeClient, FakeMqtt
from test_groups import paths  # noqa: F401  (fixture)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_node(name, node_id, **attributes):
    node = json.loads((FIXTURES / name).read_text(encoding="utf-8"))["data"]["node"]
    node["node_id"] = node_id
    node["available"] = True
    node["attributes"].update(attributes)
    return node


def leak_node(**attributes):
    return fixture_node("ikea-klippbok-leak.json", 15, **attributes)


class Recorder:
    def __init__(self):
        self.published = []

    async def __call__(self, topic, payload, retain):
        self.published.append((topic, payload, retain))


async def make_core(devices, *nodes):
    recorder = Recorder()
    core = BridgeCore(parse_config({}), recorder)
    core.set_devices(devices)
    core.recorder = recorder
    await core.matter_connected(FakeClient(), list(nodes))
    return core


def last(core):
    return json.loads(core.recorder.published[-1][1]) if core.recorder.published[-1][1] else None


# --- KLIPPBOK: czujnik zalania ----------------------------------------------------

LEAK = Device(name="woda", node_id=15, endpoint=1, type="leak", supla_channel=0, measurements=("battery",))


def test_leak_sensor_is_detected_on_real_dump():
    attrs = leak_node()["attributes"]
    assert sensor.slots(attrs) == [("leak", 1)]
    assert sensor.detect(attrs, "leak", 1) == ("battery",)
    assert sensor.battery_percent(attrs) == 100  # PowerSource 0/47/12 = 200 (jednostka 0,5 %)
    assert sensor.battery_percent({"0/47/12": 87}) == 44 and sensor.battery_percent({}) is None
    # BooleanState bez typu urządzenia "czujnik zalania" (np. czujnik drzwi) nie jest czujnikiem zalania
    assert sensor.slots({**attrs, "1/29/0": [{"0": 21, "1": 1}]}) == []


async def test_leak_state_and_battery():
    core = await make_core([LEAK], leak_node())
    assert last(core) == {"state": 0, "battery": 100}
    await core.handle_event("attribute_updated", [15, "1/69/0", True])
    await core.handle_event("attribute_updated", [15, "0/47/12", 150])
    assert last(core) == {"state": 1, "battery": 75}
    await core.handle_event("node_updated", {**leak_node(), "available": False})
    assert core.recorder.published[-1][1] == ""  # niedostępny: pusty stan


async def test_sensor_without_battery_has_no_battery_key():
    plain = Device(name="woda", node_id=15, endpoint=1, type="leak", supla_channel=0)
    core = await make_core([plain], leak_node())
    assert last(core) == {"state": 0}


async def test_sensor_has_no_set_topic():
    core = await make_core([LEAK], leak_node())
    assert core.set_topics == []


def test_leak_channel_is_binary_parsed():
    config = parse_config(
        {"devices": [{"name": "woda", "node_id": 15, "endpoint": 1, "type": "leak", "supla_channel": 0, "measurements": ["battery"]}]}
    )
    channel = build_supla_config(config, "svr.example.org", "a@example.org")["channels"][0]
    assert channel["type"] == "BinaryParsed" and channel["state"] == "state"
    assert channel["source"]["state_topic"] == "m2s/15/1/leak/state"
    assert channel["battery_level"] == "battery" and channel["multiplier_battery_level"] == 1
    assert lock_entries(config)["0"]["type"] == "BinaryParsed"


def test_sensor_measurements_validation():
    ok = {"name": "woda", "node_id": 15, "endpoint": 1, "type": "leak", "supla_channel": 0}
    assert parse_config({"devices": [ok]}).devices[0].measurements == ()
    with pytest.raises(ConfigError):
        parse_config({"devices": [{**ok, "measurements": ["voltage"]}]})


async def test_manager_adds_leak_sensor_with_battery(paths):  # noqa: F811
    manager = Manager(parse_config({}), paths[0], paths[1], "svr.example.org", "a@example.org")
    manager.mqtt = FakeMqtt()
    manager.matter_client = FakeClient()
    await manager.core.matter_connected(manager.matter_client, [leak_node()])
    candidates = manager.candidates()
    assert [(c["type"], c["supported"]) for c in candidates] == [("leak", True)]
    await manager.auto_add()
    dev = load_config(paths[0]).devices[0]
    assert (dev.type, dev.measurements, dev.supla_channel) == ("leak", ("battery",), 0)
    assert manager.candidates() == []
    view = manager.snapshot()["devices"][0]
    assert view["sensor"] == {"state": 0, "battery": 100} and view["state"] == "measuring"
    with pytest.raises(Exception):
        await manager.device_command(dev.name, "on")


# --- TIMMERFLOTTE: temperatura i wilgotność (osobne endpointy) --------------------------

CLIMATE = Device(name="klimat", node_id=16, endpoint=1, type="climate", supla_channel=1, measurements=("battery",))


def climate_node(**attributes):
    return fixture_node("ikea-timmerflotte-climate.json", 16, **attributes)


def test_climate_sensor_is_detected_on_real_dump():
    attrs = climate_node()["attributes"]
    assert sensor.slots(attrs) == [("climate", 1)]  # temperatura na endpoincie 1, wilgotność na 2
    assert sensor.humidity_endpoint(attrs, 1) == 2
    assert sensor.detect(attrs, "climate", 1) == ("battery",)
    # sam czujnik temperatury (bez wilgotności) nie jest jeszcze obsługiwany
    assert sensor.slots({k: v for k, v in attrs.items() if not k.startswith("2/")}) == []


async def test_climate_state_in_celsius_and_percent():
    core = await make_core([CLIMATE], climate_node())
    assert last(core) == {"temperature": 22.99, "humidity": 35.93, "battery": 100}
    await core.handle_event("attribute_updated", [16, "1/1026/0", -550])
    await core.handle_event("attribute_updated", [16, "2/1029/0", 6000])
    assert last(core) == {"temperature": -5.5, "humidity": 60.0, "battery": 100}
    await core.handle_event("attribute_updated", [16, "1/1026/0", None])  # brak pomiaru
    assert core.recorder.published[-1][1] == ""


async def test_climate_on_single_endpoint():
    # ALPSTUGA: temperatura i wilgotność na tym samym endpoincie
    attrs = {"1/29/0": [{"0": 44, "1": 1}], "1/1026/0": 2068, "1/1029/0": 3263}
    plain = Device(name="klimat", node_id=17, endpoint=1, type="climate", supla_channel=0)
    core = await make_core([plain], {"node_id": 17, "available": True, "attributes": attrs})
    assert last(core) == {"temperature": 20.68, "humidity": 32.63}


def test_climate_channel_is_thermhygrometer():
    config = parse_config(
        {"devices": [{"name": "klimat", "node_id": 16, "endpoint": 1, "type": "climate", "supla_channel": 0, "measurements": ["battery"]}]}
    )
    channel = build_supla_config(config, "svr.example.org", "a@example.org")["channels"][0]
    assert channel["type"] == "ThermHygroMeterParsed"
    assert (channel["temperature"], channel["humidity"]) == ("temperature", "humidity")
    assert channel["source"]["state_topic"] == "m2s/16/1/climate/state" and channel["battery_level"] == "battery"


# --- ALPSTUGA: jakość powietrza (CO2, PM2.5, temperatura i wilgotność, klaster OnOff na czujniku) -----------

CO2 = Device(name="co2", node_id=17, endpoint=1, type="co2", supla_channel=2)
PM25 = Device(name="pm", node_id=17, endpoint=1, type="pm25", supla_channel=3)


def air_node(**attributes):
    return fixture_node("ikea-alpstuga-air.json", 17, **attributes)


def test_air_quality_monitor_gives_three_sensors_and_is_not_a_plug():
    from m2s_bridge.manager import onoff_endpoints

    attrs = air_node()["attributes"]
    assert "1/6/0" in attrs  # urządzenie ma klaster OnOff...
    assert sensor.slots(attrs) == [("climate", 1), ("co2", 1), ("pm25", 1)]
    assert sensor.is_sensor_endpoint(attrs, 1)
    assert onoff_endpoints(attrs) == []  # ...ale nie jest gniazdkiem
    assert sensor.detect(attrs, "co2", 1) == ()  # zasilanie sieciowe: bez baterii


async def test_air_quality_values():
    core = await make_core([CO2, PM25], air_node())
    published = {t: json.loads(p) for t, p, _ in core.recorder.published}
    assert published["m2s/17/1/co2/state"] == {"value": 393.0}
    assert published["m2s/17/1/pm25/state"] == {"value": 6.0}
    # inne jednostki są przeliczane, nieznane - niedostępne
    await core.handle_event("attribute_updated", [17, "1/1037/8", 1])  # ppb
    await core.handle_event("attribute_updated", [17, "1/1066/8", 3])  # mg/m3
    published = {t: p for t, p, _ in core.recorder.published}
    assert json.loads(published["m2s/17/1/co2/state"]) == {"value": 0.4}
    assert json.loads(published["m2s/17/1/pm25/state"]) == {"value": 6000.0}
    await core.handle_event("attribute_updated", [17, "1/1037/8", 7])
    assert core.recorder.published[-1] == ("m2s/17/1/co2/state", "", True)


def test_air_quality_channels_are_general_purpose_measurements():
    config = parse_config(
        {
            "devices": [
                {"name": "co2", "node_id": 17, "endpoint": 1, "type": "co2", "supla_channel": 0},
                {"name": "pm", "node_id": 17, "endpoint": 1, "type": "pm25", "supla_channel": 1},
            ]
        }
    )
    co2, pm = build_supla_config(config, "svr.example.org", "a@example.org")["channels"]
    assert co2["type"] == pm["type"] == "GeneralPurposeMeasurementParsed"
    assert co2["value"] == "value" and co2["default_unit_after_value"] == "ppm"
    assert pm["default_unit_after_value"] == "µg/m³" and len(pm["default_unit_after_value"].encode()) <= 14
    assert co2["source"]["state_topic"] == "m2s/17/1/co2/state"


async def test_manager_adds_three_channels_for_air_quality_monitor(paths):  # noqa: F811
    manager = Manager(parse_config({}), paths[0], paths[1], "svr.example.org", "a@example.org")
    manager.mqtt = FakeMqtt()
    manager.matter_client = FakeClient()
    await manager.core.matter_connected(manager.matter_client, [air_node()])
    assert [c["type"] for c in manager.candidates()] == ["climate", "co2", "pm25"]  # żaden nie jest gniazdkiem
    await manager.auto_add()
    devices = load_config(paths[0]).devices
    assert [(d.type, d.supla_channel) for d in devices] == [("climate", 0), ("co2", 1), ("pm25", 2)]
    assert len({d.name for d in devices}) == 3
    assert [d.caption for d in devices][1:] == [
        "ALPSTUGA air quality monitor — CO2",
        "ALPSTUGA air quality monitor — PM2.5",
    ]


async def test_add_device_by_type_on_shared_endpoint(paths):  # noqa: F811
    manager = Manager(parse_config({"no_channel": [{"node_id": 17, "endpoint": 1}]}), paths[0], paths[1], "svr.example.org", "a@example.org")
    manager.mqtt = FakeMqtt()
    manager.matter_client = FakeClient()
    await manager.core.matter_connected(manager.matter_client, [air_node()])
    await manager.auto_add()
    assert manager.config.devices == ()  # endpoint celowo bez kanału
    dev = await manager.add_device(17, 1, "co2")
    assert dev.type == "co2"

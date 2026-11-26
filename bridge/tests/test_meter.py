"""Licznik energii — na zrzucie diagnostycznym prawdziwego gniazdka GRILLPLATS."""

import json
from pathlib import Path

import pytest
import yaml

from m2s_bridge import meter
from m2s_bridge.config import ConfigError, Device, load_config, parse_config
from m2s_bridge.core import BridgeCore
from m2s_bridge.manager import Manager, ManagerError
from m2s_bridge.supla_config import ChannelConflictError, build_supla_config, lock_entries, write_supla_config

from test_manager import FakeClient, FakeMqtt

FIXTURES = Path(__file__).parent / "fixtures"
MEASUREMENTS = ("voltage", "current", "power", "energy")
METER = Device(name="licznik", node_id=2, endpoint=2, type="meter", supla_channel=1, measurements=MEASUREMENTS)
STATE = "m2s/2/2/meter/state"


def plug_node(**attributes):
    node = json.loads((FIXTURES / "ikea-grillplats-plug.json").read_text(encoding="utf-8"))["data"]["node"]
    node["available"] = True
    node["attributes"].update(attributes)
    return node


class Recorder:
    def __init__(self):
        self.published = []

    async def __call__(self, topic, payload, retain):
        self.published.append((topic, payload, retain))


@pytest.fixture
async def core():
    recorder = Recorder()
    core = BridgeCore(parse_config({}), recorder)
    core.set_devices([METER])
    core.recorder = recorder
    await core.matter_connected(FakeClient(), [plug_node()])
    return core


def last(core):
    topic, payload, retain = core.recorder.published[-1]
    assert (topic, retain) == (STATE, True)
    return json.loads(payload) if payload else None


# --- przeliczenia -------------------------------------------------------------


def test_fixture_is_detected_as_meter_on_endpoint_2():
    attrs = plug_node()["attributes"]
    assert meter.endpoints(attrs) == [2]
    # Częstotliwość: gniazdko zgłasza 50, czyli według specyfikacji (mHz) 0,05 Hz — pomijana.
    assert meter.detect(attrs, 2) == MEASUREMENTS
    assert meter.detect({**attrs, "2/144/14": 50020}, 2) == MEASUREMENTS + ("frequency",)
    assert meter.detect({"1/144/8": 0}, 1) == ("power",)
    assert meter.endpoints({"1/6/0": True, "1/144/0": 2}) == []


def test_units_follow_matter_specification():
    attrs = {
        "2/144/11": 231500,  # RMSVoltage, mV
        "2/144/4": 1,  # Voltage — ustępuje RMSVoltage
        "2/144/12": 435,  # RMSCurrent, mA
        "2/144/8": 100250,  # ActivePower, mW
        "2/144/14": 49980,  # Frequency, mHz
        "2/145/1": {"0": 1234567, "4": 3253},  # pole 0 = energia w mWh, pole 4 = EndSystime
    }
    assert meter.values(attrs, 2, meter.MEASUREMENTS) == {
        "voltage": 231.5,
        "current": 0.435,
        "power": 100.25,
        "energy": 1.234567,
        "frequency": 49.98,
    }
    # bez wartości RMS brane są Voltage / ActiveCurrent (prąd bez znaku)
    assert meter.values({"1/144/4": 230000, "1/144/5": -1500}, 1, ("voltage", "current")) == {"voltage": 230.0, "current": 1.5}
    assert meter.read({"2/144/8": -5000}, 2, "power") == -5.0  # moc oddawana zachowuje znak


@pytest.mark.parametrize(
    "attributes",
    [
        {"2/144/8": None},
        {"2/144/11": None},
        {"2/144/8": True},
        {"2/145/1": None},
        {"2/145/1": {"4": 3253}},
        {"2/145/1": {"0": -1}},
        {"2/145/1": 12},
    ],
)
def test_incomplete_measurements_give_nothing(attributes):
    assert meter.values({**plug_node()["attributes"], **attributes}, 2, MEASUREMENTS) is None


# --- łącznik ------------------------------------------------------------------


async def test_state_is_published_in_supla_units(core):
    assert last(core) == {"voltage": 230.0, "current": 0.0, "power": 0.0, "energy": 0.0}
    await core.handle_event("attribute_updated", [2, "2/144/8", 1840500])
    await core.handle_event("attribute_updated", [2, "2/144/12", 8002])
    await core.handle_event("attribute_updated", [2, "2/145/1", {"0": 2500000, "4": 99999}])
    assert last(core) == {"voltage": 230.0, "current": 8.002, "power": 1840.5, "energy": 2.5}
    assert list(last(core)) == list(MEASUREMENTS)  # stała kolejność i komplet kluczy
    count = len(core.recorder.published)
    await core.handle_event("attribute_updated", [2, "2/145/1", {"0": 2500000, "4": 123456}])  # zmienił się tylko czas
    await core.handle_event("attribute_updated", [2, "1/6/0", True])  # inny endpoint
    assert len(core.recorder.published) == count
    await core.publish_all()  # heartbeat ponawia stan
    assert len(core.recorder.published) == count + 1


async def test_unavailable_meter_publishes_empty_state(core):
    await core.handle_event("node_updated", plug_node(**{"2/144/8": 5000}) | {"available": False})
    assert last(core) is None  # pusty komunikat z retain: brak danych, nie zera
    await core.handle_event("node_updated", plug_node(**{"2/144/8": 5000}))
    assert last(core)["power"] == 5.0
    await core.handle_event("attribute_updated", [2, "2/144/8", None])  # urządzenie nie zna mocy
    assert last(core) is None
    await core.handle_event("attribute_updated", [2, "2/144/8", 0])
    await core.matter_disconnected()
    assert last(core) is None
    await core.handle_event("node_removed", 2)
    assert core.meter_state(METER) is None


async def test_meter_has_no_command_topic(core):
    assert core.set_topics == []
    sent = []
    core.matter.device_command = lambda *args, **kwargs: sent.append(args)
    await core.handle_mqtt("m2s/2/2/meter/set", b"ON")
    assert sent == [] and core.matter.calls == []


# --- konfiguracja ---------------------------------------------------------------


def raw_device(**changes):
    item = {"name": "licznik", "node_id": 2, "endpoint": 2, "type": "meter", "supla_channel": 0, "measurements": ["power", "voltage"]}
    item.update(changes)
    return {"devices": [item]}


def test_measurements_in_devices_yaml():
    assert parse_config(raw_device()).devices[0].measurements == ("voltage", "power")  # kolejność stała
    for bad in ({"measurements": []}, {"measurements": ["volts"]}, {"measurements": None}, {"type": "onoff"}):
        with pytest.raises(ConfigError):
            parse_config(raw_device(**bad))
    # licznik i gniazdko mogą dzielić endpoint, dwa liczniki nie
    shared = raw_device()["devices"] + [{"name": "plug", "node_id": 2, "endpoint": 2, "type": "onoff", "supla_channel": 1}]
    assert len(parse_config({"devices": shared}).devices) == 2
    with pytest.raises(ConfigError):
        parse_config({"devices": shared + [{**shared[0], "name": "drugi", "supla_channel": 2}]})


def test_generated_supla_channel(tmp_path):
    plug = Device(name="plug", node_id=2, endpoint=1, type="onoff", supla_channel=0)
    with_frequency = Device(name="inny", node_id=5, endpoint=1, type="meter", supla_channel=2, caption="Inny", measurements=("power", "frequency"))
    config = parse_config({})
    config = type(config)(devices=(plug, METER, with_frequency))
    document = build_supla_config(config, "svr0.example.org", "user@example.org")
    assert "proto" not in document["supla"]
    assert document["channels"][1] == {
        "type": "ElectricityMeterParsed",
        "name": "licznik",
        "channel_number": 1,
        "source": {"type": "MQTT", "state_topic": STATE, "expiration_time_sec": 120},
        "parser": {"type": "Json", "refresh_time_ms": 1000},
        "phase_1": [{"voltage": "voltage"}, {"current": "current"}, {"power_active": "power"}, {"fwd_act_energy": "energy"}],
    }
    assert document["channels"][2] == {
        "type": "ElectricityMeterParsed",
        "name": "inny",
        "channel_number": 2,
        "initial_caption": "Inny",
        "source": {"type": "MQTT", "state_topic": "m2s/5/1/meter/state", "expiration_time_sec": 120},
        "parser": {"type": "Json", "refresh_time_ms": 1000},
        "frequency": "frequency",
        "phase_1": [{"power_active": "power"}],
    }
    assert lock_entries(config)["1"] == {"type": "ElectricityMeterParsed", "name": "licznik"}
    output = tmp_path / "supla-device.yaml"
    write_supla_config(config, output, "svr0.example.org", "user@example.org")
    # kanału licznika nie wolno potem zamienić na inny typ ani usunąć
    swapped = type(config)(devices=(plug, Device(name="licznik", node_id=2, endpoint=2, type="onoff", supla_channel=1)))
    with pytest.raises(ChannelConflictError):
        write_supla_config(swapped, output, "svr0.example.org", "user@example.org")
    with pytest.raises(ChannelConflictError):
        write_supla_config(type(config)(devices=(plug,)), output, "svr0.example.org", "user@example.org")


# --- menedżer ---------------------------------------------------------------------


async def make_manager(tmp_path, nodes=None, **config):
    manager = Manager(parse_config(config), tmp_path / "devices.yaml", tmp_path / "supla" / "supla-device.yaml", "s.example.org", "u@example.org")
    manager.mqtt = FakeMqtt()
    client = FakeClient()
    manager.matter_client = client
    await manager.core.matter_connected(client, nodes if nodes is not None else [plug_node()])
    await manager.auto_add()
    return manager, client


async def test_plug_gets_relay_and_meter_channels(tmp_path):
    manager, _ = await make_manager(tmp_path)
    plug, licznik = manager.config.devices
    assert (plug.type, plug.endpoint, plug.supla_channel, plug.measurements) == ("onoff", 1, 0, ())
    assert licznik == Device(
        name="grillplats_plug_2_meter",
        node_id=2,
        endpoint=2,
        type="meter",
        supla_channel=1,
        caption="GRILLPLATS Plug — licznik",
        measurements=MEASUREMENTS,
    )
    assert load_config(tmp_path / "devices.yaml").devices == manager.config.devices
    supla = yaml.safe_load((tmp_path / "supla" / "supla-device.yaml").read_text(encoding="utf-8"))
    assert [c["type"] for c in supla["channels"]] == ["CustomRelay", "ElectricityMeterParsed"]
    assert supla["channels"][1]["source"]["state_topic"] == STATE
    assert "output" not in supla["channels"][1]
    lock = json.loads((tmp_path / "supla" / "channels.lock.json").read_text(encoding="utf-8"))
    assert lock["channels"]["1"] == {"type": "ElectricityMeterParsed", "name": "grillplats_plug_2_meter"}
    # komendy subskrybowane tylko dla gniazdka
    assert manager.mqtt.subscribed == ["m2s/2/1/onoff/set"]
    assert (STATE, json.dumps({"voltage": 230.0, "current": 0.0, "power": 0.0, "energy": 0.0})) in manager.mqtt.published
    assert manager.candidates() == []
    await manager.auto_add()  # kolejne zdarzenia niczego nie dublują
    assert len(manager.config.devices) == 2 and manager.mqtt.subscribed == ["m2s/2/1/onoff/set"]


async def test_meter_is_appended_after_existing_channels(tmp_path):
    existing = {"name": "grillplats_plug_2", "node_id": 2, "endpoint": 1, "type": "onoff", "supla_channel": 0}
    manager, _ = await make_manager(tmp_path, devices=[existing])
    assert [(d.name, d.supla_channel) for d in manager.config.devices] == [("grillplats_plug_2", 0), ("grillplats_plug_2_meter", 1)]


async def test_meter_in_panel_snapshot(tmp_path):
    manager, _ = await make_manager(tmp_path)
    plug, licznik = manager.snapshot()["devices"]
    assert "meter" not in plug
    assert (licznik["type"], licznik["state"], licznik["supla_channel"]) == ("meter", "measuring", 1)
    assert licznik["meter"] == {"voltage": 230.0, "current": 0.0, "power": 0.0, "energy": 0.0}
    with pytest.raises(ManagerError):
        await manager.device_command(licznik["name"], "on")
    await manager.core.handle_event("node_updated", plug_node() | {"available": False})
    licznik = manager.snapshot()["devices"][1]
    assert (licznik["state"], licznik["meter"]) == ("unavailable", None)


async def test_meter_waits_when_auto_add_is_off(tmp_path):
    manager, _ = await make_manager(tmp_path, auto_add=False)
    assert manager.config.devices == ()
    assert [(c["endpoint"], c.get("type"), c["supported"]) for c in manager.candidates()] == [(1, "onoff", True), (2, "meter", True)]
    dev = await manager.add_device(2, 2)
    assert (dev.type, dev.supla_channel, dev.measurements) == ("meter", 0, MEASUREMENTS)

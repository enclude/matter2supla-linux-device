# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Grupa świateł: jeden kanał SUPLA dla kilku żarówek, obok zwykłych kanałów pojedynczych."""

import json

import pytest
import yaml

from m2s_bridge.config import ConfigError, Device, load_config, parse_config
from m2s_bridge.core import BridgeCore
from m2s_bridge.manager import Manager, ManagerError
from m2s_bridge.matter_client import MatterError
from m2s_bridge.supla_config import build_supla_config

from conftest import make_node
from test_light import bulb_node
from test_manager import FakeClient, FakeMqtt

GROUP = Device(
    name="grupa_1", node_id=0, endpoint=0, type="light_group", supla_channel=1, caption="Salon",
    members=((3, 1), (4, 1)),
)
STATE = "m2s/group/grupa_1/state"
SET = "m2s/group/grupa_1/set"


def node(node_id, **kwargs):
    data = bulb_node(**kwargs)
    data["node_id"] = node_id
    return data


class Sender:
    def __init__(self):
        self.sent, self.fail_nodes = [], set()

    async def device_command(self, node_id, endpoint_id, cluster_id, command_name, payload=None):
        if node_id in self.fail_nodes:
            raise MatterError(3, "Node is not ready")
        self.sent.append((node_id, command_name, payload))


class Recorder:
    def __init__(self):
        self.published = []

    async def __call__(self, topic, payload, retain):
        self.published.append((topic, payload))

    def last(self, topic):
        return [p for t, p in self.published if t == topic][-1]


async def make_core(*nodes):
    recorder = Recorder()
    core = BridgeCore(parse_config({}), recorder)
    core.set_devices([GROUP])
    core.recorder, core.sender = recorder, Sender()
    await core.matter_connected(core.sender, list(nodes))
    return core


# --- konfiguracja ---------------------------------------------------------------


def test_group_round_trip_and_single_light_side_by_side(tmp_path):
    raw = {
        "devices": [
            {"name": "lampa", "node_id": 3, "endpoint": 1, "type": "light_cct", "supla_channel": 0},
            {"name": "grupa_1", "caption": "Salon", "type": "light_group", "supla_channel": 1,
             "members": [{"node_id": 3, "endpoint": 1}, {"node_id": 4, "endpoint": 1}]},
        ],
        "no_channel": [{"node_id": 5, "endpoint": 1}],
    }
    config = parse_config(raw)
    assert config.devices[1].members == ((3, 1), (4, 1))
    assert config.no_channel == ((5, 1),)
    from m2s_bridge.config import save_config

    save_config(config, tmp_path / "devices.yaml")
    assert load_config(tmp_path / "devices.yaml") == config

    supla = build_supla_config(config, "svr.example.org", "a@example.org")
    single, group = supla["channels"]
    assert single["type"] == group["type"] == "MatterLight"
    assert single["state_topic"] == "m2s/3/1/light_cct/state"
    assert group["state_topic"] == STATE and group["set_topic"] == SET
    assert supla["supla"]["proto"] == 28


def test_members_only_for_groups():
    with pytest.raises(ConfigError):
        parse_config({"devices": [{"name": "x", "node_id": 1, "endpoint": 1, "type": "onoff",
                                   "supla_channel": 0, "members": [{"node_id": 2, "endpoint": 1}]}]})
    with pytest.raises(ConfigError):
        parse_config({"devices": [{"name": "g", "type": "light_group", "supla_channel": 0,
                                   "members": [{"node_id": 2, "endpoint": 1}, {"node_id": 2, "endpoint": 1}]}]})


# --- stan i komendy -------------------------------------------------------------


async def test_group_state_aggregates_members():
    core = await make_core(node(3, on=False, level=127), node(4, on=True, level=254, mireds=153))
    # świeci druga żarówka: grupa włączona, wartości z pierwszej świecącej
    assert core.recorder.last(STATE) == "1 100 100"
    await core.handle_event("attribute_updated", [4, "1/6/0", False])
    assert core.recorder.last(STATE).startswith("0 50 ")
    # jedna żarówka znika: grupa działa dalej; obie: niedostępna
    await core.handle_event("node_updated", {**node(3, on=False), "available": False})
    assert core.recorder.last(STATE).startswith("0 100 ")
    await core.handle_event("node_updated", {**node(4), "available": False})
    assert core.recorder.last(STATE) == "-1 0 0"


async def test_group_command_reaches_every_available_member():
    core = await make_core(node(3, on=False), node(4, on=True, level=254), {**node(5), "available": False})
    core.set_devices([Device(**{**GROUP.__dict__, "members": ((3, 1), (4, 1), (5, 1))})])
    await core.handle_mqtt(SET, b"40 -1")
    levels = [(n, c) for n, c, _ in core.sender.sent]
    assert sorted(levels) == [(3, "MoveToLevelWithOnOff"), (4, "MoveToLevelWithOnOff")]
    core.sender.sent.clear()
    await core.handle_mqtt(SET, b"0 -1")
    assert core.sender.sent == [(4, "Off", None)]  # zgaszona już była, niedostępna pominięta


async def test_group_partial_failure_still_succeeds():
    core = await make_core(node(3, on=False), node(4, on=False))
    core.sender.fail_nodes = {3}
    assert await core.set_light(GROUP, 80, None) is True
    core.sender.fail_nodes = {3, 4}
    assert await core.set_light(GROUP, 60, None) is False
    assert await core.send_command(Device(**{**GROUP.__dict__, "members": ()}), "On") is False


async def test_group_toggle():
    core = await make_core(node(3, on=False), node(4, on=True))
    await core.send_command(GROUP, "Toggle")
    assert core.sender.sent == [(3, "Off", None), (4, "Off", None)]


# --- zarządzanie ----------------------------------------------------------------


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "devices.yaml", tmp_path / "supla" / "supla-device.yaml"


async def make_manager(paths, *nodes, result=None):
    manager = Manager(parse_config({}), paths[0], paths[1], "svr.example.org", "a@example.org")
    manager.mqtt = FakeMqtt()
    manager.matter_client = FakeClient(result=result)
    await manager.core.matter_connected(manager.matter_client, list(nodes))
    await manager.auto_add()
    return manager


async def test_group_is_one_channel_and_singles_stay(paths):
    manager = await make_manager(paths, node(3))
    assert [d.type for d in manager.config.devices] == ["light_cct"]  # pojedyncze światło jak dotąd
    empty = await manager.create_group("Pusta", [])
    assert empty.supla_channel == -1 and manager.snapshot()["devices"][-1]["name"] == empty.name
    assert not (paths[1].parent / "channels.lock.json").exists() or "1" not in json.loads(
        (paths[1].parent / "channels.lock.json").read_text()
    )["channels"]
    assert load_config(paths[0]).devices[1].supla_channel == -1  # pusta grupa przetrwa restart
    group = await manager.create_group("Salon", [(3, 1)])
    assert group.supla_channel == 1 and group.members == ((3, 1),)
    assert f"m2s/group/{group.name}/set" in manager.mqtt.subscribed
    lock = json.loads((paths[1].parent / "channels.lock.json").read_text())["channels"]
    assert lock["1"]["type"] == "MatterLight"
    snapshot = manager.snapshot()
    assert snapshot["devices"][1]["members"] == [{"node_id": "3", "endpoint": 1}]
    assert snapshot["devices"][1]["members_available"] == 1
    assert snapshot["lights"] == [
        {"node_id": "3", "endpoint": 1, "label": manager.config.devices[0].caption, "available": True, "supla_channel": 0}
    ]
    with pytest.raises(ManagerError):
        await manager.set_group_members(group.name, [(9, 1)])  # nie ma takiego światła


async def test_pairing_into_group_creates_no_single_channel(paths):
    manager = await make_manager(paths, node(3))
    group = await manager.create_group("Salon", [])
    assert group.supla_channel == -1  # kanał dopiero z pierwszym światłem
    new = node(4)
    manager.matter_client.result = new
    manager.start_commission("MT:ABC", False, f"group:{group.name}")
    # węzeł pojawia się zdarzeniem jeszcze w trakcie parowania - nie może dostać własnego kanału
    await manager.core.handle_event("node_added", new)
    await manager.auto_add()
    assert len(manager.config.devices) == 2
    await manager._commission_task
    assert manager.commission_state["error"] is None and "grupy" in manager.commission_state["message"]
    assert manager._find_group(group.name).members == ((4, 1),)
    assert [d.supla_channel for d in manager.config.devices] == [0, 1]
    await manager.auto_add()
    assert len(manager.config.devices) == 2
    # światło z samej grupy da się nadal sterować z panelu i dodać ręcznie jako osobny kanał
    candidate = next(c for c in manager.snapshot()["candidates"] if c["node_id"] == "4")
    assert candidate["supported"] is False and candidate["manual"] is True and candidate["control"]
    dev = await manager.add_device(4, 1)
    assert dev.type == "light_cct" and dev.supla_channel == 2
    assert manager._find_group(group.name).members == ((4, 1),)


async def test_pairing_without_channel_and_default(paths):
    manager = await make_manager(paths)
    manager.matter_client.result = node(3)
    manager.start_commission("MT:ABC", False, "none")
    await manager._commission_task
    await manager.core.handle_event("node_added", node(3))
    await manager.auto_add()
    assert manager.config.devices == () and manager.config.no_channel == ((3, 1),)
    assert load_config(paths[0]).no_channel == ((3, 1),)
    # domyślny wybór: jak dotąd, osobny kanał
    manager.matter_client.result = node(4)
    manager.start_commission("MT:ABC", False)
    await manager._commission_task
    await manager.core.handle_event("node_added", node(4))
    await manager.auto_add()
    assert [(d.node_id, d.type) for d in manager.config.devices] == [(4, "light_cct")]
    with pytest.raises(ManagerError):
        manager.start_commission("MT:ABC", False, "group:nie_ma")


async def test_removed_member_does_not_get_channel_by_itself(paths):
    manager = await make_manager(paths)
    group = await manager.create_group("Salon", [])
    manager.matter_client.result = node(3)
    manager.start_commission("MT:ABC", False, f"group:{group.name}")
    await manager._commission_task
    await manager.set_group_members(group.name, [])
    await manager.auto_add()
    assert len(manager.config.devices) == 1 and manager.config.no_channel == ((3, 1),)
    assert manager.config.devices[0].supla_channel == 0  # raz nadany kanał zostaje także pustej grupie
    await manager.set_group_members(group.name, [(3, 1)])
    assert manager.config.no_channel == ()
    saved = yaml.safe_load(paths[0].read_text(encoding="utf-8"))
    assert saved["devices"][0]["members"] == [{"node_id": 3, "endpoint": 1}]


async def test_group_accepts_only_its_own_kind_of_device(paths):
    # Grupa jest jednorodna: ściemniacz z regulacją bieli. Gniazdko (ani inny rodzaj światła) nie wejdzie.
    manager = await make_manager(paths, node(3), make_node(7, on=True))
    group = await manager.create_group("Salon", [(3, 1)])
    with pytest.raises(ManagerError):
        await manager.set_group_members(group.name, [(3, 1), (7, 1)])
    with pytest.raises(ManagerError):
        await manager.create_group("Mieszana", [(7, 1)])
    assert [light["node_id"] for light in manager.snapshot()["lights"]] == ["3"]
    # parowanie gniazdka z wyborem grupy: do grupy nie trafia, dostaje zwykły kanał
    manager.matter_client.result = make_node(8, on=False)
    manager.start_commission("MT:ABC", False, f"group:{group.name}")
    await manager._commission_task
    assert "nie trafiło do grupy" in manager.commission_state["message"]
    assert manager._find_group(group.name).members == ((3, 1),)

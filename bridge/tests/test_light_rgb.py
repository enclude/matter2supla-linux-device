# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Światło kolorowe (`light_rgb`) - na zrzucie diagnostycznym prawdziwej żarówki KAJPLATS E27 CWS."""

import json
from pathlib import Path

import pytest

from m2s_bridge import light
from m2s_bridge.config import Device, parse_config
from m2s_bridge.core import BridgeCore
from m2s_bridge.manager import Manager, ManagerError, endpoint_type
from m2s_bridge.supla_config import build_supla_config

from test_light import Recorder, Sender
from test_manager import FakeClient, FakeMqtt

FIXTURES = Path(__file__).parent / "fixtures"
BULB = Device(name="kolor", node_id=14, endpoint=1, type="light_rgb", supla_channel=0)
STATE = "m2s/14/1/light_rgb/state"
SET = "m2s/14/1/light_rgb/set"


def cws_node(**attributes):
    node = json.loads((FIXTURES / "ikea-kajplats-e27-cws.json").read_text(encoding="utf-8"))["data"]["node"]
    node["available"] = True
    node["attributes"].update({"1/6/0": True, "1/8/0": 254, "1/768/8": light.COLOR_MODE_CT, "1/768/7": 370})
    node["attributes"].update(attributes)
    return node


@pytest.fixture
async def core():
    recorder = Recorder()
    core = BridgeCore(parse_config({}), recorder)
    core.set_devices([BULB])
    core.recorder, core.sender = recorder, Sender()
    await core.matter_connected(core.sender, [cws_node()])
    return core


def test_color_conversions():
    assert light.hs_to_rgb(0, 254) == (255, 0, 0)
    assert light.hs_to_rgb(127, 0) == (255, 255, 255)
    assert light.rgb_to_hs(255, 0, 0) == (0, 254)
    assert light.rgb_to_hs(255, 255, 255)[1] == 0
    for hue, saturation in ((24, 201), (100, 254), (200, 120)):  # tam i z powrotem w granicach 1 kroku
        back = light.rgb_to_hs(*light.hs_to_rgb(hue, saturation))
        assert abs(back[0] - hue) <= 1 and abs(back[1] - saturation) <= 1
    red, green, blue = light.xy_to_rgb(int(0.64 * 65536), int(0.33 * 65536))  # czerwień sRGB
    assert red == 255 and green < 60 and blue < 60


def test_real_bulb_is_detected_as_color_light():
    attributes = cws_node()["attributes"]
    assert light.is_color_capable(attributes, 1)
    assert endpoint_type(attributes, 1) == "light_rgb"
    ws = json.loads((FIXTURES / "ikea-kajplats-e27-ws.json").read_text(encoding="utf-8"))["data"]["node"]["attributes"]
    assert not light.is_color_capable(ws, 1)
    assert endpoint_type(ws, 1) == "light_cct"


async def test_state_in_white_and_color_mode(core):
    # biel: tryb 1, temperatura 370 miredów w zakresie 153-555 -> 46
    assert core.recorder.published[-1] == (STATE, "1 1 100 46 " + " ".join(map(str, light.hs_to_rgb(24, 201))))
    await core.handle_event("attribute_updated", [14, "1/768/8", light.COLOR_MODE_HS])
    await core.handle_event("attribute_updated", [14, "1/768/0", 0])
    await core.handle_event("attribute_updated", [14, "1/768/1", 254])
    assert core.recorder.published[-1] == (STATE, "1 2 100 46 255 0 0")
    await core.handle_event("attribute_updated", [14, "1/6/0", False])
    assert core.recorder.published[-1][1].startswith("0 2 ")
    await core.handle_event("node_updated", {**cws_node(), "available": False})
    assert core.recorder.published[-1] == (STATE, "-1 1 0 0 255 255 255")


async def test_state_in_xy_mode_uses_xy(core):
    await core.handle_event("attribute_updated", [14, "1/768/8", light.COLOR_MODE_XY])
    await core.handle_event("attribute_updated", [14, "1/768/3", int(0.64 * 65536)])
    await core.handle_event("attribute_updated", [14, "1/768/4", int(0.33 * 65536)])
    state = core.recorder.published[-1][1].split()
    assert state[1] == "2" and int(state[4]) == 255 and int(state[5]) < 60


async def test_color_command_sends_hue_and_saturation_then_level(core):
    await core.handle_mqtt(SET, "2 60 0 255 0 0")
    assert [(c, p["hue"], p["saturation"]) for c, n, p in core.sender.sent if n == "MoveToHueAndSaturation"] == [
        (light.COLOR_CLUSTER, 0, 254)
    ]
    levels = [p for _, n, p in core.sender.sent if n == "MoveToLevelWithOnOff"]
    assert levels[0]["level"] == light.percent_to_level(60)
    # kolor idzie przed jasnością i dopuszcza zmianę przy zgaszonym świetle
    assert core.sender.sent[0][1] == "MoveToHueAndSaturation"
    assert core.sender.sent[0][2]["optionsOverride"] == light.EXECUTE_IF_OFF


async def test_white_command_switches_back_from_color_mode(core):
    await core.handle_event("attribute_updated", [14, "1/768/8", light.COLOR_MODE_HS])
    await core.handle_mqtt(SET, "1 100 46 255 255 255")  # ta sama temperatura, ale tryb koloru -> trzeba przełączyć
    assert [n for _, n, _ in core.sender.sent] == ["MoveToColorTemperature"]


async def test_unchanged_command_sends_nothing_and_off_turns_off(core):
    await core.handle_mqtt(SET, "1 100 46 255 255 255")
    assert core.sender.sent == []
    await core.handle_mqtt(SET, "0 0 0 0 0 0")
    assert [n for _, n, _ in core.sender.sent] == ["Off"]


async def test_off_command_ignores_color_fields(core):
    await core.handle_mqtt(SET, "0 0 0 255 0 0")
    assert [n for _, n, _ in core.sender.sent] == ["Off"]


async def test_bad_commands_are_ignored(core):
    for text in ("", "2 60", "3 60 0 1 2 3", "x y z a b c", "1 2 3 4 5 6 7"):
        await core.handle_mqtt(SET, text)
    assert core.sender.sent == []


def test_supla_channel_uses_rgb_extension_type():
    config = parse_config({"devices": [{"name": "kolor", "node_id": 14, "endpoint": 1, "type": "light_rgb", "supla_channel": 0}]})
    built = build_supla_config(config, "svr.example.org", "a@example.org")
    channel = built["channels"][0]
    assert channel["type"] == "MatterRgbLight"
    assert channel["state_topic"] == STATE and channel["set_topic"] == SET
    assert built["supla"]["proto"] == 28


# --- grupy świateł kolorowych ----------------------------------------------------------

from m2s_bridge.config import load_config  # noqa: E402
from test_groups import paths  # noqa: E402,F401  (fixture)
from test_light import bulb_node  # noqa: E402


def cws_at(node_id, **attributes):
    node = cws_node(**attributes)
    node["node_id"] = node_id
    return node


async def rgb_manager(paths, *nodes):  # noqa: F811
    manager = Manager(parse_config({}), paths[0], paths[1], "svr.example.org", "a@example.org")
    manager.mqtt = FakeMqtt()
    manager.matter_client = FakeClient()
    await manager.core.matter_connected(manager.matter_client, list(nodes))
    return manager


async def test_rgb_group_is_one_matterrgblight_channel(paths):  # noqa: F811
    manager = await rgb_manager(paths, cws_at(14), cws_at(18))
    await manager.auto_add()
    group = await manager.create_group("Salon", [(14, 1), (18, 1)], "light_rgb_group")
    assert group.type == "light_rgb_group" and group.members == ((14, 1), (18, 1))
    channels = build_supla_config(manager.config, "svr.example.org", "a@example.org")["channels"]
    assert [c["type"] for c in channels if c["name"] == group.name] == ["MatterRgbLight"]
    assert load_config(paths[0]).devices[-1].type == "light_rgb_group"  # przetrwa zapis i odczyt
    kinds = {(light["node_id"], light["kind"]) for light in manager.snapshot()["lights"]}
    assert kinds == {("14", "light_rgb_group"), ("18", "light_rgb_group")}


async def test_rgb_group_state_and_commands_fan_out(paths):  # noqa: F811
    manager = await rgb_manager(paths, cws_at(14), cws_at(18, **{"1/768/8": light.COLOR_MODE_HS, "1/768/0": 0, "1/768/1": 254}))
    await manager.auto_add()
    group = await manager.create_group("Salon", [(14, 1), (18, 1)], "light_rgb_group")
    state = manager.core.state_payload(group).split()
    assert state[:3] == ["1", "1", "100"] and len(state) == 7  # pierwsze świecące światło: biel
    await manager.core.handle_mqtt(f"m2s/group/{group.name}/set", "2 50 0 0 0 255")
    hue = [args for name, args in manager.matter_client.calls if name == "device_command" and args[3] == "MoveToHueAndSaturation"]
    assert sorted(a[0] for a in hue) == [14, 18]  # kolor trafił do obu świateł
    assert all(a[4]["hue"] == 169 and a[4]["saturation"] == 254 for a in hue)  # niebieski
    levels = [args for name, args in manager.matter_client.calls if name == "device_command" and args[3] == "MoveToLevelWithOnOff"]
    assert sorted(a[0] for a in levels) == [14, 18]


async def test_groups_stay_homogeneous(paths):  # noqa: F811
    manager = await rgb_manager(paths, cws_at(14), bulb_node_at(3))
    await manager.auto_add()
    with pytest.raises(ManagerError):
        await manager.create_group("Mieszana", [(14, 1), (3, 1)], "light_rgb_group")
    with pytest.raises(ManagerError):
        await manager.create_group("Biel", [(14, 1)], "light_group")
    with pytest.raises(ManagerError):
        await manager.create_group("Nieznana", [], "nonsense")
    group = await manager.create_group("Kolor", [(14, 1)], "light_rgb_group")
    with pytest.raises(ManagerError):
        await manager.set_group_members(group.name, [(14, 1), (3, 1)])


def bulb_node_at(node_id):
    node = bulb_node()
    node["node_id"] = node_id
    return node

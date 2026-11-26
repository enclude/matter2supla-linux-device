import json

import pytest
import yaml

from m2s_bridge.config import load_config, parse_config
from m2s_bridge.manager import Manager, ManagerError

from conftest import make_node


class FakeMqtt:
    def __init__(self):
        self.published, self.subscribed = [], []

    async def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload))

    async def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)


class FakeClient:
    def __init__(self, result=None, error=None):
        self.calls, self.result, self.error = [], result, error

    async def device_command(self, *args, **kwargs):
        self.calls.append(("device_command", args))

    async def command(self, name, timeout=None, **args):
        self.calls.append((name, args))
        if self.error:
            raise self.error
        return self.result


def bulb(node_id=2):
    node = make_node(node_id=node_id, on=True)
    node["attributes"].update({"0/40/3": "KAJPLATS", "1/8/0": 120})
    return node


def sensor(node_id=3):
    node = make_node(node_id=node_id, on=None)
    node["attributes"].update({"0/40/3": "TIMMERFLOTTE", "1/1026/0": 2150})
    return node


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "devices.yaml", tmp_path / "supla" / "supla-device.yaml"


@pytest.fixture
async def manager(paths):
    m = Manager(parse_config({}), paths[0], paths[1], "svr.example.org", "user@example.org")
    m.mqtt = FakeMqtt()
    return m


async def connect(manager, *nodes):
    client = FakeClient()
    manager.matter_client = client
    await manager.core.matter_connected(client, list(nodes))
    return client


async def test_new_plug_becomes_channel_automatically(manager, paths):
    await connect(manager, make_node(on=True))
    added = await manager.auto_add()
    assert [(d.name, d.supla_channel, d.caption) for d in added] == [("grillplats_1", 0, "GRILLPLATS")]
    # plik mapowania i konfiguracja SUPLA są zapisane i spójne
    assert load_config(paths[0]).devices == manager.config.devices
    supla = yaml.safe_load(paths[1].read_text(encoding="utf-8"))
    assert supla["channels"][0]["source"]["state_topic"] == "m2s/1/1/onoff/state"
    # łącznik od razu nasłuchuje komend i publikuje stan nowego kanału
    assert manager.mqtt.subscribed == ["m2s/1/1/onoff/set"]
    assert ("m2s/1/1/onoff/state", json.dumps({"state": 1})) in manager.mqtt.published
    assert manager.supla_error is None


async def test_auto_add_is_idempotent_and_appends(manager):
    await connect(manager, make_node(on=True))
    await manager.auto_add()
    assert await manager.auto_add() == []
    await manager.core.handle_event("node_added", make_node(node_id=5, on=False))
    added = await manager.auto_add()
    assert [(d.name, d.supla_channel) for d in added] == [("grillplats_5", 1)]
    assert [d.supla_channel for d in manager.config.devices] == [0, 1]


async def test_supla_file_not_rewritten_without_changes(manager, paths):
    await connect(manager, make_node())
    await manager.auto_add()
    before = paths[1].stat().st_mtime_ns
    manager.regenerate_supla()
    assert paths[1].stat().st_mtime_ns == before  # brak zbędnego restartu supla-device


async def test_dimmable_and_unsupported_nodes_wait(manager):
    await connect(manager, bulb(), sensor())
    assert await manager.auto_add() == []
    reasons = {c["product"]: (c["supported"], c["endpoint"]) for c in manager.candidates()}
    assert reasons == {"KAJPLATS": (False, 1), "TIMMERFLOTTE": (False, None)}
    with pytest.raises(ManagerError):
        await manager.add_device(2, 1)


async def test_auto_add_disabled_then_manual(paths):
    manager = Manager(parse_config({"auto_add": False}), paths[0], paths[1], "s", "m")
    manager.mqtt = FakeMqtt()
    await connect(manager, make_node())
    assert await manager.auto_add() == []
    assert manager.candidates()[0]["supported"] is True
    dev = await manager.add_device(1, 1)
    assert dev.supla_channel == 0 and manager.candidates() == []
    with pytest.raises(ManagerError):
        await manager.add_device(1, 1)


async def test_missing_supla_credentials_is_reported(paths):
    manager = Manager(parse_config({}), paths[0], paths[1])
    manager.mqtt = FakeMqtt()
    await connect(manager, make_node())
    assert len(await manager.auto_add()) == 1
    assert "Konto SUPLA" in manager.supla_error
    assert not paths[1].exists()


async def test_unwritable_config_does_not_stop_bridge(manager, tmp_path):
    manager.config_path = tmp_path / "plik" / "devices.yaml"
    (tmp_path / "plik").write_text("to nie katalog")
    await connect(manager, make_node())
    assert await manager.auto_add() == []
    assert manager.config.devices == ()
    assert "Nie udało się" in manager.supla_error


async def test_caption_and_command(manager, paths):
    client = await connect(manager, make_node(on=False))
    await manager.auto_add()
    await manager.set_caption("grillplats_1", "  Lampa w salonie ")
    assert load_config(paths[0]).devices[0].caption == "Lampa w salonie"
    await manager.device_command("grillplats_1", "on")
    assert client.calls[-1] == ("device_command", (1, 1, 6, "On"))
    with pytest.raises(ManagerError):
        await manager.device_command("grillplats_1", "explode")
    with pytest.raises(ManagerError):
        await manager.device_command("nie_ma", "on")


async def test_commission_success_and_failure(manager):
    from m2s_bridge.matter_client import MatterError

    with pytest.raises(ManagerError):
        manager.start_commission("123", False)  # brak połączenia z matter-server
    manager.matter_client = FakeClient(result={"node_id": 7})
    manager.start_commission(" MT:ABC ", True)
    assert manager.commission_state["running"]
    with pytest.raises(ManagerError):
        manager.start_commission("x", False)  # już trwa
    await manager._commission_task
    assert manager.matter_client.calls == [("commission_with_code", {"code": "MT:ABC", "network_only": True})]
    assert manager.commission_state == {"running": False, "message": "Sparowano węzeł 7.", "error": None}

    manager.matter_client = FakeClient(error=MatterError(1, "passcode-mismatch"))
    manager.start_commission("123", False)
    await manager._commission_task
    assert "passcode-mismatch" in manager.commission_state["error"]


async def test_thread_dataset_validation(manager):
    manager.matter_client = FakeClient()
    with pytest.raises(ManagerError):
        await manager.set_thread_dataset("to nie hex")
    await manager.set_thread_dataset(" 0e08AB ")
    assert manager.matter_client.calls == [("set_thread_dataset", {"dataset": "0e08AB"})]


async def test_snapshot_uses_string_node_ids(manager):
    big = 0xFFFF_FFFE_0000_0001
    await connect(manager, make_node(node_id=big, on=True))
    await manager.auto_add()
    snap = manager.snapshot()
    assert snap["devices"][0]["node_id"] == str(big)
    assert snap["devices"][0]["state"] == "on"
    json.dumps(snap)


async def test_diagnostics_for_unsupported_node(manager):
    node = sensor()
    node["attributes"].update(
        {"0/40/15": "SN123", "0/40/18": "UID", "0/62/1": [{"1": "sekret"}], "0/53/2": "MojaSiecThread"}
    )
    await connect(manager, node)
    dump = manager.diagnostics(3)
    attrs = dump["data"]["node"]["attributes"]
    assert attrs["1/1026/0"] == 2150 and attrs["0/40/3"] == "TIMMERFLOTTE"
    assert not {"0/40/15", "0/40/18", "0/62/1", "0/53/2"} & set(attrs)
    assert dump["redacted"] and "0/62/1" in dump["redacted_paths"]
    assert dump["summary"]["clusters_by_endpoint"] == {"0": [40], "1": [1026]}
    assert "sekret" not in json.dumps(dump) and "SN123" not in json.dumps(dump)
    full = manager.diagnostics(3, full=True)
    assert full["data"]["node"]["attributes"]["0/40/15"] == "SN123" and not full["redacted"]
    assert manager.config.devices == ()  # diagnostyka nie tworzy kanału SUPLA
    with pytest.raises(ManagerError):
        manager.diagnostics(99)


async def test_supla_account_from_panel(paths):
    manager = Manager(parse_config({}), paths[0], paths[1])  # bez zmiennych środowiskowych
    manager.mqtt = FakeMqtt()
    await connect(manager, make_node())
    await manager.auto_add()
    assert not paths[1].exists() and "Konto SUPLA" in manager.supla_error
    for server, mail in [("https://svr1.supla.org", "a@b.pl"), ("svr1.supla.org", "bez-malpy"), ("", "")]:
        with pytest.raises(ManagerError):
            await manager.set_supla_account(server, mail)
    await manager.set_supla_account(" SVR12.supla.org ", "ktos@example.org")
    assert manager.supla_error is None
    supla = yaml.safe_load(paths[1].read_text(encoding="utf-8"))
    assert supla["supla"] == {"server": "svr12.supla.org", "mail": "ktos@example.org"}
    account = paths[1].parent / "account.json"
    assert (account.stat().st_mode & 0o777) == 0o600
    # po restarcie łącznika dane z panelu mają pierwszeństwo przed zmiennymi środowiskowymi
    again = Manager(parse_config({}), paths[0], paths[1], "inny.example.org", "inny@example.org")
    assert again.snapshot()["supla"] == {"server": "svr12.supla.org", "mail": "ktos@example.org"}

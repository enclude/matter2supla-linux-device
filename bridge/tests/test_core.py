# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

import json

import pytest

from m2s_bridge.core import BridgeCore
from m2s_bridge.matter_client import MatterError

from conftest import make_node

STATE = "m2s/1/1/onoff/state"
SET = "m2s/1/1/onoff/set"


class Recorder:
    def __init__(self):
        self.published = []

    async def __call__(self, topic, payload, retain):
        self.published.append((topic, json.loads(payload), retain))

    def states(self, topic=STATE):
        return [p["state"] for t, p, _ in self.published if t == topic]


class FakeMatter:
    def __init__(self, error=None):
        self.sent = []
        self.error = error

    async def device_command(self, node_id, endpoint_id, cluster_id, command_name, payload=None):
        self.sent.append((node_id, endpoint_id, cluster_id, command_name))
        if self.error:
            raise self.error


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def core(config, recorder):
    return BridgeCore(config, recorder)


async def test_unavailable_before_matter_connects(core, recorder):
    await core.publish_all()
    assert recorder.published == [(STATE, {"state": -1}, True)]


async def test_initial_state_from_node_dump(core, recorder):
    await core.matter_connected(FakeMatter(), [make_node(on=True)])
    assert recorder.states() == [1]


async def test_attribute_update_publishes_change_once(core, recorder):
    await core.matter_connected(FakeMatter(), [make_node(on=False)])
    await core.handle_event("attribute_updated", [1, "1/6/0", True])
    await core.handle_event("attribute_updated", [1, "1/6/0", True])  # bez zmiany — bez publikacji
    await core.handle_event("attribute_updated", [1, "1/6/0", False])
    assert recorder.states() == [0, 1, 0]


async def test_unrelated_events_are_ignored(core, recorder):
    await core.matter_connected(FakeMatter(), [make_node(on=False), make_node(node_id=7, on=True)])
    recorder.published.clear()
    await core.handle_event("attribute_updated", [7, "1/6/0", False])  # niezmapowany węzeł
    await core.handle_event("attribute_updated", [1, "1/8/0", 200])  # inny klaster
    await core.handle_event("attribute_updated", "śmieci")
    await core.handle_event("node_updated", None)
    await core.handle_event("nieznane", {})
    assert recorder.published == []


async def test_node_goes_offline_and_returns(core, recorder):
    await core.matter_connected(FakeMatter(), [make_node(on=True)])
    await core.handle_event("node_updated", make_node(on=True, available=False))
    await core.handle_event("node_updated", make_node(on=False, available=True))
    assert recorder.states() == [1, -1, 0]


async def test_node_added_after_start(core, recorder):
    await core.matter_connected(FakeMatter(), [])
    await core.handle_event("node_added", make_node(on=True))
    assert recorder.states() == [-1, 1]


async def test_node_removed(core, recorder):
    await core.matter_connected(FakeMatter(), [make_node(on=True)])
    await core.handle_event("node_removed", 1)
    assert recorder.states() == [1, -1]


async def test_missing_or_null_attribute_is_invalid(core, recorder):
    await core.matter_connected(FakeMatter(), [make_node(on=None)])
    await core.handle_event("attribute_updated", [1, "1/6/0", True])
    await core.handle_event("attribute_updated", [1, "1/6/0", None])  # atrybut usunięty
    assert recorder.states() == [-1, 1, -1]


async def test_matter_disconnect_marks_everything_invalid(core, recorder):
    await core.matter_connected(FakeMatter(), [make_node(on=True)])
    await core.matter_disconnected()
    assert recorder.states() == [1, -1]


async def test_heartbeat_republishes_unchanged_state(core, recorder):
    await core.matter_connected(FakeMatter(), [make_node(on=True)])
    await core.publish_all()
    assert recorder.states() == [1, 1]


@pytest.mark.parametrize(
    "payload,expected",
    [(b"ON", "On"), (b"OFF", "Off"), (b"on\n", "On"), (b"1", "On"), (b"0", "Off"), (b'"OFF"', "Off"), (b"toggle", "Toggle")],
)
async def test_set_command(core, payload, expected):
    matter = FakeMatter()
    await core.matter_connected(matter, [make_node(on=False)])
    await core.handle_mqtt(SET, payload)
    assert matter.sent == [(1, 1, 6, expected)]


async def test_command_does_not_fake_state(core, recorder):
    """Stan zmienia się dopiero po potwierdzeniu z urządzenia (attribute_updated)."""
    await core.matter_connected(FakeMatter(), [make_node(on=False)])
    await core.handle_mqtt(SET, b"ON")
    assert recorder.states() == [0]


async def test_ignored_commands(core):
    matter = FakeMatter()
    await core.matter_connected(matter, [make_node(on=False)])
    await core.handle_mqtt(SET, b"ON", retain=True)  # stara, zachowana komenda
    await core.handle_mqtt(SET, b"MAYBE")
    await core.handle_mqtt(SET, b"")
    await core.handle_mqtt("m2s/9/9/onoff/set", b"ON")
    assert matter.sent == []


async def test_command_rejected_when_unavailable(core):
    matter = FakeMatter()
    await core.handle_mqtt(SET, b"ON")  # brak połączenia z Matter
    await core.matter_connected(matter, [make_node(on=True, available=False)])
    await core.handle_mqtt(SET, b"ON")
    assert matter.sent == []


async def test_failed_command_republishes_real_state(core, recorder):
    matter = FakeMatter(error=MatterError(3, "Node is not ready"))
    await core.matter_connected(matter, [make_node(on=False)])
    await core.handle_mqtt(SET, b"ON")
    assert matter.sent == [(1, 1, 6, "On")]
    assert recorder.states() == [0, 0]

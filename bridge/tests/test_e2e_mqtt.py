"""Test całego łącznika z prawdziwym brokerem MQTT i fałszywym matter-server.

Wymaga brokera: M2S_TEST_MQTT=host:port (np. `docker run --rm -p 18883:1883 eclipse-mosquitto:2
mosquitto -c /mosquitto-no-auth.conf`). Bez tej zmiennej test jest pomijany.
"""

import asyncio
import contextlib
import json
import os

import aiomqtt
import pytest

from m2s_bridge.config import parse_config
from m2s_bridge.manager import Manager
from m2s_bridge.runner import run

from conftest import FakeMatterServer, make_node

BROKER = os.environ.get("M2S_TEST_MQTT")
pytestmark = pytest.mark.skipif(not BROKER, reason="ustaw M2S_TEST_MQTT=host:port")

PREFIX = f"m2s-test-{os.getpid()}"
STATE = f"{PREFIX}/1/1/onoff/state"
SET = f"{PREFIX}/1/1/onoff/set"
STATUS = f"{PREFIX}/bridge/status"


async def _next_state(messages, expected, timeout=10):
    async with asyncio.timeout(timeout):
        async for message in messages:
            if str(message.topic) == STATE and json.loads(message.payload)["state"] == expected:
                return


async def test_full_round_trip():
    host, port = BROKER.rsplit(":", 1)
    server = FakeMatterServer([make_node(on=False)])
    await server.start()
    config = parse_config(
        {
            "matter": {"url": server.url},
            "mqtt": {"host": host, "port": int(port), "topic_prefix": PREFIX},
            "heartbeat_sec": 1,
            "state_expiration_sec": 0,
            "auto_add": False,
            "web": {"enabled": False},
            "devices": [{"name": "plug", "node_id": 1, "endpoint": 1, "type": "onoff", "supla_channel": 0}],
        }
    )
    async with aiomqtt.Client(host, int(port)) as observer:
        await observer.subscribe(f"{PREFIX}/#", qos=1)
        bridge = asyncio.create_task(run(Manager(config)))
        try:
            # 1. stan początkowy z matter-server
            await _next_state(observer.messages, 0)
            # 2. SUPLA -> Matter: komenda ON trafia do urządzenia, a stan wraca po potwierdzeniu
            await observer.publish(SET, "ON", qos=1)
            await _next_state(observer.messages, 1)
            assert server.commands[-1]["args"]["command_name"] == "On"
            # 3. Matter -> SUPLA: zmiana przyciskiem na gniazdku
            server.nodes[0]["attributes"]["1/6/0"] = False
            await server.emit("attribute_updated", [1, "1/6/0", False])
            await _next_state(observer.messages, 0)
            # 4. heartbeat ponawia niezmieniony stan
            await _next_state(observer.messages, 0)
            await _next_state(observer.messages, 0)
            # 5. węzeł nieosiągalny -> stan -1 (kanał SUPLA offline)
            await server.emit("node_updated", make_node(on=False, available=False))
            await _next_state(observer.messages, -1)
            await server.emit("node_updated", make_node(on=True, available=True))
            await _next_state(observer.messages, 1)
            # 6. matter-server pada -> -1; po powrocie łącznik sam się łączy i publikuje stan
            port_before = server.port
            server.nodes[0] = make_node(on=True)
            await server.drop_clients()
            await server.stop()
            await _next_state(observer.messages, -1)
            await server.start(port_before)
            await _next_state(observer.messages, 1, timeout=20)
        finally:
            bridge.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bridge
            await server.stop()

    # 7. po czystym zatrzymaniu zostają zachowane (retained) „niedostępny” i „offline”
    async with aiomqtt.Client(host, int(port)) as observer:
        await observer.subscribe(f"{PREFIX}/#", qos=1)
        retained = {}
        async with asyncio.timeout(5):
            async for message in observer.messages:
                retained[str(message.topic)] = message.payload.decode()
                if STATE in retained and STATUS in retained:
                    break
        assert json.loads(retained[STATE]) == {"state": -1}
        assert retained[STATUS] == "offline"
        # sprzątanie zachowanych wiadomości testowych
        for topic in retained:
            await observer.publish(topic, None, retain=True)

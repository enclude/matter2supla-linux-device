# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

import asyncio

import pytest

from m2s_bridge.matter_client import MatterClient, MatterConnectionError, MatterError


async def test_connect_reads_server_info_and_nodes(matter_server):
    async with MatterClient(matter_server.url) as client:
        assert client.server_info["schema_version"] == 15
        nodes = await client.start_listening()
        assert nodes[0]["attributes"]["1/6/0"] is False


async def test_device_command_wire_format(matter_server):
    async with MatterClient(matter_server.url) as client:
        await client.device_command(1, 1, 6, "On")
    sent = matter_server.commands[-1]
    assert sent["command"] == "device_command"
    assert sent["args"] == {
        "node_id": 1,
        "endpoint_id": 1,
        "cluster_id": 6,
        "command_name": "On",
        "payload": {},
    }
    assert isinstance(sent["message_id"], str)


async def test_events_are_delivered_in_order(matter_server):
    async with MatterClient(matter_server.url) as client:
        await client.start_listening()
        await matter_server.emit("attribute_updated", [1, "1/6/0", True])
        await matter_server.emit("node_removed", 1)
        assert await client.next_event() == ("attribute_updated", [1, "1/6/0", True])
        assert await client.next_event() == ("node_removed", 1)


async def test_server_error_becomes_exception(matter_server):
    matter_server.fail_device_command = True
    async with MatterClient(matter_server.url) as client:
        with pytest.raises(MatterError) as err:
            await client.device_command(1, 1, 6, "On")
        assert err.value.error_code == 3
        # połączenie nadal działa
        assert await client.command("get_nodes")


async def test_connection_loss_fails_events_and_commands(matter_server):
    async with MatterClient(matter_server.url) as client:
        await client.start_listening()
        await matter_server.drop_clients()
        with pytest.raises(MatterConnectionError):
            await asyncio.wait_for(client.next_event(), 5)
        with pytest.raises(MatterConnectionError):
            await client.command("get_nodes")


async def test_connect_refused():
    client = MatterClient("ws://127.0.0.1:1/ws")
    with pytest.raises(MatterConnectionError):
        await client.connect()


async def test_large_node_ids_survive(matter_server):
    big = 0xFFFF_FFFE_0000_0001  # zakres węzłów testowych matterjs-server, ponad 2^53
    matter_server.nodes[0]["node_id"] = big
    async with MatterClient(matter_server.url) as client:
        nodes = await client.start_listening()
        assert nodes[0]["node_id"] == big

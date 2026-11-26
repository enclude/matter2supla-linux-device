# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Wspólne atrapy: fałszywy matter-server mówiący prawdziwym protokołem WebSocket."""

from __future__ import annotations

import json
from typing import Any

import pytest
import websockets

from m2s_bridge.config import parse_config

SERVER_INFO = {
    "fabric_id": 1,
    "compressed_fabric_id": 2,
    "schema_version": 15,
    "min_supported_schema_version": 11,
    "sdk_version": "fake/0.0",
    "wifi_credentials_set": False,
    "thread_credentials_set": False,
    "bluetooth_enabled": False,
}


def make_node(node_id: int = 1, on: bool | None = False, available: bool = True) -> dict[str, Any]:
    attributes: dict[str, Any] = {"0/40/1": "IKEA of Sweden", "0/40/2": 4476, "0/40/3": "GRILLPLATS", "0/40/4": 4096}
    if on is not None:
        attributes["1/6/0"] = on
    return {"node_id": node_id, "available": available, "attributes": attributes}


class FakeMatterServer:
    """Minimalny matter-server: server_info, start_listening, get_nodes, device_command."""

    def __init__(self, nodes: list[dict[str, Any]] | None = None):
        self.nodes = nodes if nodes is not None else [make_node()]
        self.commands: list[dict[str, Any]] = []
        self.fail_device_command = False
        self.apply_commands = True  # po komendzie On/Off wyślij attribute_updated jak prawdziwe urządzenie
        self._server: Any = None
        self._clients: set[Any] = set()
        self.port = 0

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"

    async def start(self, port: int = 0) -> None:
        self._server = await websockets.serve(self._handle, "127.0.0.1", port)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def drop_clients(self) -> None:
        for ws in list(self._clients):
            await ws.close()

    async def emit(self, event: str, data: Any) -> None:
        for ws in list(self._clients):
            await ws.send(json.dumps({"event": event, "data": data}))

    async def _handle(self, ws: Any) -> None:
        self._clients.add(ws)
        try:
            await ws.send(json.dumps(SERVER_INFO))
            async for raw in ws:
                msg = json.loads(raw)
                self.commands.append(msg)
                reply: dict[str, Any] = {"message_id": msg["message_id"]}
                command = msg["command"]
                if command in ("start_listening", "get_nodes"):
                    reply["result"] = self.nodes
                elif command == "device_command":
                    if self.fail_device_command:
                        reply.update(error_code=3, details="Node is not ready")
                    else:
                        reply["result"] = None
                elif command in ("set_thread_dataset", "commission_with_code"):
                    reply["result"] = self.nodes[0] if command == "commission_with_code" else None
                else:
                    reply.update(error_code=9, details=f"Invalid command {command}")
                await ws.send(json.dumps(reply))
                if command == "device_command" and "result" in reply and self.apply_commands:
                    args = msg["args"]
                    name = args["command_name"].lower()
                    path = f"{args['endpoint_id']}/{args['cluster_id']}/0"
                    for node in self.nodes:
                        if node["node_id"] == args["node_id"]:
                            current = bool(node["attributes"].get(path))
                            new = {"on": True, "off": False, "toggle": not current}[name]
                            node["attributes"][path] = new
                            await self.emit("attribute_updated", [args["node_id"], path, new])
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)


@pytest.fixture
async def matter_server():
    server = FakeMatterServer()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
def config():
    return parse_config(
        {
            "devices": [
                {"name": "grillplats_1", "node_id": 1, "endpoint": 1, "type": "onoff", "supla_channel": 0}
            ]
        }
    )

# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Cienki klient WebSocket do matter-server (matterjs-server / python-matter-server).

Protokół (docs/websockets_api.md w repozytorium matterjs-server):
- po połączeniu serwer wysyła `server_info`,
- żądanie: {"message_id", "command", "args"}; odpowiedź: {"message_id", "result"}
  albo {"message_id", "error_code", "details"},
- po `start_listening` serwer wysyła zdarzenia {"event", "data"}.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

log = logging.getLogger(__name__)

# Zakres wersji schematu API, z którym ten klient był pisany.
MIN_SCHEMA_VERSION = 11

_CLOSED = object()


class MatterError(Exception):
    """Serwer odrzucił komendę."""

    def __init__(self, error_code: int | None, details: str | None):
        super().__init__(f"matter-server: błąd {error_code}: {details}")
        self.error_code = error_code
        self.details = details


class MatterConnectionError(Exception):
    """Połączenie z matter-server zostało utracone lub nie doszło do skutku."""


class MatterClient:
    def __init__(self, url: str, command_timeout: float = 30.0):
        self._url = url
        self._command_timeout = command_timeout
        self._ws: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._events: asyncio.Queue[Any] = asyncio.Queue()
        self._ids = itertools.count(1)
        self.server_info: dict[str, Any] = {}

    async def connect(self) -> dict[str, Any]:
        try:
            # max_size=None: odpowiedź na start_listening zawiera wszystkie atrybuty wszystkich węzłów.
            self._ws = await websockets.connect(self._url, max_size=None)
            raw = await asyncio.wait_for(self._ws.recv(), timeout=self._command_timeout)
        except (OSError, ConnectionClosed, asyncio.TimeoutError, websockets.WebSocketException) as err:
            await self.close()
            raise MatterConnectionError(f"nie można połączyć z {self._url}: {err}") from err
        self.server_info = json.loads(raw)
        schema = self.server_info.get("schema_version")
        if isinstance(schema, int) and schema < MIN_SCHEMA_VERSION:
            await self.close()
            raise MatterConnectionError(
                f"matter-server ma schemat {schema}, wymagany co najmniej {MIN_SCHEMA_VERSION}"
            )
        self._reader = asyncio.create_task(self._read_loop(), name="matter-reader")
        return self.server_info

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):
                pass
            self._reader = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._fail_pending(MatterConnectionError("połączenie zamknięte"))

    async def __aenter__(self) -> "MatterClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def _fail_pending(self, err: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(err)
        self._pending.clear()

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    log.warning("matter-server: pominięto ramkę, która nie jest JSON-em")
                    continue
                if not isinstance(msg, dict):
                    continue
                if "event" in msg:
                    self._events.put_nowait((msg["event"], msg.get("data")))
                elif "message_id" in msg:
                    future = self._pending.pop(str(msg["message_id"]), None)
                    if future is None or future.done():
                        continue
                    if "error_code" in msg:
                        future.set_exception(MatterError(msg.get("error_code"), msg.get("details")))
                    else:
                        future.set_result(msg.get("result"))
        except ConnectionClosed:
            pass
        finally:
            self._fail_pending(MatterConnectionError("połączenie z matter-server utracone"))
            self._events.put_nowait(_CLOSED)

    async def command(self, command: str, *, timeout: float | None = None, **args: Any) -> Any:
        if self._ws is None:
            raise MatterConnectionError("brak połączenia z matter-server")
        message_id = str(next(self._ids))
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        payload: dict[str, Any] = {"message_id": message_id, "command": command}
        if args:
            payload["args"] = args
        try:
            await self._ws.send(json.dumps(payload))
            return await asyncio.wait_for(future, timeout=timeout or self._command_timeout)
        except ConnectionClosed as err:
            raise MatterConnectionError("połączenie z matter-server utracone") from err
        except asyncio.TimeoutError as err:
            raise MatterConnectionError(f"brak odpowiedzi na `{command}`") from err
        finally:
            self._pending.pop(message_id, None)

    async def start_listening(self) -> list[dict[str, Any]]:
        """Włącza strumień zdarzeń i zwraca aktualną listę węzłów."""
        return await self.command("start_listening") or []

    async def next_event(self) -> tuple[str, Any]:
        """Zwraca kolejne zdarzenie; MatterConnectionError, gdy połączenie padło."""
        item = await self._events.get()
        if item is _CLOSED:
            self._events.put_nowait(_CLOSED)
            raise MatterConnectionError("połączenie z matter-server utracone")
        return item

    async def write_attribute(self, node_id: int, attribute_path: str, value: Any) -> Any:
        """Zapisuje atrybut węzła; ścieżka `endpoint/klaster/atrybut`."""
        return await self.command(
            "write_attribute", node_id=node_id, attribute_path=attribute_path, value=value
        )

    async def device_command(
        self,
        node_id: int,
        endpoint_id: int,
        cluster_id: int,
        command_name: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        return await self.command(
            "device_command",
            node_id=node_id,
            endpoint_id=endpoint_id,
            cluster_id=cluster_id,
            command_name=command_name,
            payload=payload or {},
        )

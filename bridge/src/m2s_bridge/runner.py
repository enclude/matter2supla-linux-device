# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Spina łącznik z prawdziwym brokerem MQTT i matter-server; pilnuje ponownych połączeń."""

from __future__ import annotations

import asyncio
import contextlib
import logging

import aiomqtt

from .manager import Manager
from .matter_client import MatterClient, MatterConnectionError, MatterError
from .topics import bridge_status_topic
from .web import start_web

log = logging.getLogger(__name__)

MAX_BACKOFF_SEC = 30.0


async def _matter_loop(manager: Manager) -> None:
    core = manager.core
    backoff = 1.0
    while True:
        client = MatterClient(manager.config.matter.url)
        try:
            info = await client.connect()
            nodes = await client.start_listening()
            log.info(
                "połączono z matter-server (%s, schemat %s), węzłów: %d",
                info.get("sdk_version"),
                info.get("schema_version"),
                len(nodes),
            )
            manager.server_info = info
            manager.matter_client = client
            await core.matter_connected(client, nodes)
            await manager.auto_add()
            backoff = 1.0
            while True:
                event, data = await client.next_event()
                await core.handle_event(event, data)
                if event in ("node_added", "node_updated"):
                    await manager.auto_add()
                elif event == "server_info_updated" and isinstance(data, dict):
                    manager.server_info = data
        except (MatterConnectionError, MatterError) as err:
            log.warning("matter-server: %s — ponowna próba za %.0f s", err, backoff)
        finally:
            manager.matter_client = None
            await client.close()
            await core.matter_disconnected()
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF_SEC)


async def _mqtt_loop(mqtt: aiomqtt.Client, manager: Manager, tasks: asyncio.TaskGroup) -> None:
    async for message in mqtt.messages:
        # Komenda Matter może trwać kilka sekund — nie blokujemy odbioru kolejnych.
        tasks.create_task(
            manager.core.handle_mqtt(str(message.topic), message.payload, bool(message.retain))
        )


async def _heartbeat_loop(manager: Manager) -> None:
    while True:
        await asyncio.sleep(manager.config.heartbeat_sec)
        await manager.core.publish_all()


async def _session(manager: Manager) -> None:
    config = manager.config
    status_topic = bridge_status_topic(config.mqtt.topic_prefix)
    async with aiomqtt.Client(
        hostname=config.mqtt.host,
        port=config.mqtt.port,
        username=config.mqtt.username,
        password=config.mqtt.password,
        identifier="m2s-bridge",
        will=aiomqtt.Will(status_topic, "offline", qos=1, retain=True),
    ) as mqtt:
        log.info("połączono z brokerem MQTT %s:%d", config.mqtt.host, config.mqtt.port)
        manager.mqtt = mqtt
        try:
            await mqtt.publish(status_topic, "online", qos=1, retain=True)
            await manager.core.publish_all()  # do czasu połączenia z Matter wszystko jest „niedostępne”
            for topic in manager.core.set_topics:
                await mqtt.subscribe(topic, qos=1)
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(_matter_loop(manager))
                tasks.create_task(_mqtt_loop(mqtt, manager, tasks))
                tasks.create_task(_heartbeat_loop(manager))
        finally:
            # Czyste zamknięcie nie wyzwala LWT, więc stan „offline” publikujemy sami.
            with contextlib.suppress(Exception):
                async with asyncio.timeout(5):
                    await manager.core.matter_disconnected()
                    await mqtt.publish(status_topic, "offline", qos=1, retain=True)
            manager.mqtt = None


async def run(manager: Manager, web_password: str | None = None) -> None:
    manager.regenerate_supla()
    web_runner = await start_web(manager, web_password) if manager.config.web.enabled else None
    backoff = 1.0
    try:
        while True:
            try:
                await _session(manager)
            except* aiomqtt.MqttError as group:
                log.warning("MQTT: %s — ponowna próba za %.0f s", group.exceptions[0], backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SEC)
    finally:
        if web_runner is not None:
            await web_runner.cleanup()

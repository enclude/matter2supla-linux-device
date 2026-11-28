# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Zrzuty ekranu panelu do dokumentacji — panel uruchomiony na przykładowych danych.

Nie łączy się z niczym: prawdziwa aplikacja WWW łącznika dostaje atrapę z gotowym stanem.
Użycie (z katalogu repozytorium, w środowisku z zainstalowanym łącznikiem i Playwright):

    python docs/img/make_screenshots.py [ścieżka-do-chromium]
"""

from __future__ import annotations

import asyncio
import copy
import os
import sys
import tempfile
import time
from pathlib import Path

from aiohttp import web
from playwright.async_api import async_playwright

from m2s_bridge.web import create_app

# Przykładowy dziennik kontrolera dla zakładki „Logi” (zmyślone adresy i identyfikatory).
MATTER_LOG = """\
2026-11-26 20:26:22.027 NOTICE Discovery Initiating discovery of node with discriminator 1234
2026-11-26 20:26:24.222 INFO ControllerCommissioner Establish PASE to device VP: 4476+36866
2026-11-26 20:26:28.967 INFO BleChannel Peripheral AA:BB:CC:00:11:22: ATT_MTU still unknown, using the minimum BTP segment size of 20 bytes
2026-11-26 20:26:38.359 INFO PaseClient Paired successfully » ble://AA:BB:CC:00:11:22 3↔3
2026-11-26 20:26:38.374 INFO ControllerCommissioner Start commissioning of node @1:8 into fabric 1
2026-11-26 20:26:38.379 INFO ControllerCommissioner Executing commissioning step 0.1: GetInitialData
2026-11-26 20:26:48.700 INFO ControllerCommissioner Executing commissioning step 7.1: GeneralCommissioning.ArmFailsafe
2026-11-26 20:26:54.445 INFO ControllerCommissioner Executing commissioning step 8.1: GeneralCommissioning.ConfigureRegulatoryInformation
2026-11-26 20:26:59.423 INFO ControllerCommissioner Executing commissioning step 10.1: OperationalCredentials.DeviceAttestation
2026-11-26 20:27:24.552 INFO ControllerCommissioner Device attestation successfully verified with 1 accepted finding(s)
2026-11-26 20:27:24.560 INFO ControllerCommissioner Executing commissioning step 11.1: OperationalCredentials.Certificates
2026-11-26 20:27:45.862 INFO ControllerCommissioner Executing commissioning step 16.3: NetworkCommissioning.Thread
2026-11-26 20:28:12.157 WARN ControllerCommissioner Commissioning channel dropped while configuring the operational network - assuming device is non-concurrent, proceeding to CASE
2026-11-26 20:28:12.158 INFO ControllerCommissioner Executing commissioning step 18.1: Reconnect
2026-11-26 20:28:34.528 INFO ControllerCommissioner Executing commissioning step 20.1: GeneralCommissioning.Complete
2026-11-26 20:28:34.787 NOTICE CommissioningClient Commissioned peer12 as @1:8
"""

OUT = Path(__file__).parent
PORT = 18099
PASSWORD = "przykladowe-haslo"

NOW = time.time()
COMMON = {"vendor": "IKEA of Sweden", "last_seen": NOW, "can_identify": True, "groups": []}
PLUG = {**COMMON, "product": "GRILLPLATS Plug", "firmware": "1.4.6"}
BULB_E27 = {**COMMON, "product": "KAJPLATS E27 WS globe 1521lm", "firmware": "1.1.0"}
SENSOR = {**COMMON, "can_identify": False}
ALPSTUGA = {**SENSOR, "product": "ALPSTUGA air quality monitor", "firmware": "1.0.15"}
BULB_GU10 = {**COMMON, "product": "KAJPLATS GU10 WS 575lm", "firmware": "1.1.0"}

STATE = {
    "mqtt_connected": True,
    "matter_connected": True,
    "server": {"version": "matter.js/0.16", "thread_dataset_set": True, "bluetooth": True},
    "auto_add": True,
    "version": {
        "current": "0.2.0",
        "revision": "4f1c2d7a9b3e5f60718293a4b5c6d7e8f9012345",
        "latest": "0.3.0",
        "update_available": True,
        "url": "https://github.com/enclude/matter2supla-linux-device/releases/tag/v0.3.0",
    },
    "supla": {"server": "svr1.supla.org", "mail": "supla@zjawa.tech"},
    "supla_error": None,
    "devices": [
        {"name": "grillplats_plug_2", "caption": "Gniazdko - biurko", "node_id": "2", "endpoint": 1,
         "type": "onoff", "supla_channel": 0, "cloud_id": 1201, "state": "on", "caption_linked": False, **PLUG},
        {"name": "kajplats_e27_3", "caption": "Lampa - salon", "node_id": "3", "endpoint": 1,
         "type": "light_cct", "supla_channel": 1, "cloud_id": 1202, "state": "on", "caption_linked": False,
         "brightness": 65, "temperature": 30, **BULB_E27},
        {"name": "grillplats_plug_2_meter", "caption": "Gniazdko - biurko - licznik", "node_id": "2",
         "endpoint": 2, "type": "meter", "supla_channel": 2, "cloud_id": 1203, "state": "measuring", "caption_linked": True,
         "meter": {"voltage": 236.8, "current": 0.104, "power": 14.6, "energy": 1.273},
         "can_command": False, **{**PLUG, "can_identify": False}},
        {"name": "grupa_1", "caption": "Żyrandol - jadalnia", "node_id": "0", "endpoint": 0,
         "type": "light_group", "supla_channel": 3, "cloud_id": 1204, "state": "on", "caption_linked": False,
         "brightness": 80, "temperature": 45, "members_available": 2, "can_identify": True,
         "vendor": None, "product": None, "firmware": None, "last_seen": NOW, "groups": [],
         "members": [
             {"node_id": "5", "endpoint": 1, "device": {
                 "name": "kajplats_gu10_ws_575lm_5", "caption": "KAJPLATS GU10 WS 575lm", "node_id": "5", "endpoint": 1,
                 "type": "light_cct", "supla_channel": -1, "state": "on", "caption_linked": False,
                 "brightness": 80, "temperature": 45, **{**BULB_GU10, "groups": ["Żyrandol - jadalnia"]}}},
             {"node_id": "6", "endpoint": 1, "device": {
                 "name": "kajplats_gu10_ws_575lm_6", "caption": "KAJPLATS GU10 WS 575lm", "node_id": "6", "endpoint": 1,
                 "type": "light_cct", "supla_channel": -1, "state": "on", "caption_linked": False,
                 "brightness": 80, "temperature": 45, **{**BULB_GU10, "groups": ["Żyrandol - jadalnia"]}}},
         ]},
        {"name": "grillplats_plug_7", "caption": "Gniazdko - choinka", "node_id": "7", "endpoint": 1,
         "type": "onoff", "supla_channel": 4, "cloud_id": 1205, "state": "unavailable", "caption_linked": False,
         **{**PLUG, "last_seen": NOW - 3 * 3600, "can_identify": False}},
        {"name": "kajplats_e27_cws_globe_1055lm_14", "caption": "Lampa - sypialnia", "node_id": "14", "endpoint": 1,
         "type": "light_rgb", "supla_channel": 5, "cloud_id": 1206, "state": "on", "caption_linked": False,
         "brightness": 70, "temperature": 40, "mode": "color", "color": "#ff5a1e", "startup": "previous",
         **{**COMMON, "product": "KAJPLATS E27 CWS globe 1055lm", "firmware": "1.2.0"}},
        {"name": "klippbok_water_leak_sensor_15_leak", "caption": "Czujnik zalania - łazienka", "node_id": "15",
         "endpoint": 1, "type": "leak", "supla_channel": 6, "cloud_id": 1207, "state": "measuring",
         "caption_linked": False, "sensor": {"state": 0, "battery": 100}, "can_command": False,
         **{**SENSOR, "product": "KLIPPBOK water leak sensor", "firmware": "1.0.11"}},
        {"name": "timmerflotte_temp_hmd_sensor_16_climate", "caption": "Temperatura - garaż", "node_id": "16",
         "endpoint": 1, "type": "climate", "supla_channel": 7, "cloud_id": 1208, "state": "measuring",
         "caption_linked": False, "sensor": {"temperature": 22.8, "humidity": 34.1, "battery": 100}, "can_command": False,
         **{**SENSOR, "product": "TIMMERFLOTTE temp/hmd sensor", "firmware": "1.0.21"}},
        {"name": "alpstuga_air_quality_monitor_17_climate", "caption": "Powietrze - salon - temperatura i wilgotność",
         "node_id": "17", "endpoint": 1, "type": "climate", "supla_channel": 8, "cloud_id": 1209, "state": "measuring",
         "caption_linked": False, "sensor": {"temperature": 21.7, "humidity": 32.0}, "can_command": False, **ALPSTUGA},
        {"name": "alpstuga_air_quality_monitor_17_co2", "caption": "Powietrze - salon - CO2", "node_id": "17",
         "endpoint": 1, "type": "co2", "supla_channel": 9, "cloud_id": 1210, "state": "measuring",
         "caption_linked": False, "sensor": {"value": 391.0}, "can_command": False, **ALPSTUGA},
        {"name": "alpstuga_air_quality_monitor_17_pm25", "caption": "Powietrze - salon - PM2.5", "node_id": "17",
         "endpoint": 1, "type": "pm25", "supla_channel": 10, "cloud_id": 1211, "state": "measuring",
         "caption_linked": False, "sensor": {"value": 5.0}, "can_command": False, **ALPSTUGA},
    ],
    "lights": [
        {"node_id": "3", "endpoint": 1, "label": "Lampa - salon", "available": True, "supla_channel": 1},
        {"node_id": "5", "endpoint": 1, "label": "KAJPLATS GU10 WS 575lm", "available": True, "supla_channel": None},
        {"node_id": "6", "endpoint": 1, "label": "KAJPLATS GU10 WS 575lm", "available": True, "supla_channel": None},
    ],
    "candidates": [
        {"node_id": "4", "available": True, "vendor": "IKEA of Sweden", "product": "MYGGBETT door/window sensor",
         "endpoint": None, "supported": False, "reason": "typ urządzenia jeszcze nieobsługiwany", "control": None},
    ],
    "commission": {"running": False, "message": None, "error": None},
}

BACKUPS = [
    {"name": "m2s-manual-20261126-204500.tar.gz", "size": 48310, "time": 1795722300, "manual": True,
     "expires": 1795722300 + 3 * 86400},
    {"name": "m2s-backup-20261126-033000.tar.gz", "size": 48211, "time": 1795660200},
    {"name": "m2s-backup-20261125-033000.tar.gz", "size": 47903, "time": 1795573800},
    {"name": "m2s-backup-20261124-033000.tar.gz", "size": 47650, "time": 1795487400},
]


class FakeManager:
    def __init__(self) -> None:
        self.state = copy.deepcopy(STATE)
        self.config_path = None  # atrapa nie ma katalogu konfiguracji

    def snapshot(self) -> dict:
        return copy.deepcopy(self.state)


async def main() -> None:
    log_file = Path(tempfile.mkdtemp()) / "matter-server.log"
    log_file.write_text(MATTER_LOG, encoding="utf-8")
    os.environ["M2S_MATTER_LOG"] = str(log_file)
    manager = FakeManager()
    app = create_app(manager, PASSWORD, None)  # type: ignore[arg-type]

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()
    base = f"http://127.0.0.1:{PORT}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=sys.argv[1] if len(sys.argv) > 1 else None)

        async def open_page(width: int, height: int, mobile: bool = False):
            context = await browser.new_context(
                viewport={"width": width, "height": height}, device_scale_factor=2, is_mobile=mobile, has_touch=mobile, locale="pl-PL"
            )
            page = await context.new_page()

            async def fake_state(route):
                await route.fulfill(json={**manager.snapshot(), "auth": True, "backups": BACKUPS,
                                           "backup_request": {"supported": True, "pending": False, "stale": False, "last_result": "success"}})

            await page.route("**/api/state", fake_state)
            return page

        async def login(page):
            await page.goto(base + "/login")
            await page.fill("input[type=password]", PASSWORD)
            await page.keyboard.press("Enter")
            await page.wait_for_selector("footer a")
            await page.wait_for_timeout(800)

        page = await open_page(1320, 640)
        await page.goto(base + "/login")
        await page.screenshot(path=OUT / "panel-logowanie.png")

        await login(page)
        await page.screenshot(path=OUT / "panel.png", full_page=True)
        async def shot(tab: str, selector: str, name: str) -> None:
            await page.click(f'.tabs button[data-tab="{tab}"]')
            await page.locator(selector).screenshot(path=OUT / f"{name}.png")

        await page.click('.tabs button[data-tab="kanaly"]')
        await page.locator("#devices .item", has_text="Lampa - sypialnia").locator("button.more").click()  # szczegóły światła kolorowego
        await page.wait_for_timeout(400)
        await shot("kanaly", "#tab-kanaly section >> nth=0", "panel-kanaly")
        await shot("kanaly", "#candidates-section", "panel-bez-kanalu")
        await page.click('.tabs button[data-tab="grupy"]')
        await page.locator("#groups summary").first.click()
        await shot("grupy", "#tab-grupy section", "panel-grupy")
        await shot("parowanie", "#tab-parowanie section", "panel-parowanie")
        await shot("system", "#tab-system section >> nth=0", "panel-konto-supla")
        await shot("system", "#tab-system section >> nth=1", "panel-kopie")

        await page.add_style_tag(content="#log-view { height: auto !important; }")
        await shot("logi", "#tab-logi section", "panel-logi")

        manager.state["commission"] = {"running": True, "message": "Parowanie trwa (do kilku minut)…", "error": None}
        await page.wait_for_timeout(2500)
        await shot("parowanie", "#tab-parowanie section", "panel-parowanie-trwa")
        manager.state["commission"] = STATE["commission"]

        phone = await open_page(390, 800, mobile=True)
        await login(phone)  # telefon z ustawionym kontem SUPLA zaczyna od zakładki „Parowanie”
        await phone.wait_for_selector("#tab-parowanie:not([hidden])")
        await phone.screenshot(path=OUT / "panel-telefon.png", full_page=True)
        await browser.close()
    await runner.cleanup()


asyncio.run(main())

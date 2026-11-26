"""Zrzuty ekranu panelu do dokumentacji — panel uruchomiony na przykładowych danych.

Nie łączy się z niczym: prawdziwa aplikacja WWW łącznika dostaje atrapę z gotowym stanem.
Użycie (z katalogu repozytorium, w środowisku z zainstalowanym łącznikiem i Playwright):

    python docs/img/make_screenshots.py [ścieżka-do-chromium]
"""

from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path

from aiohttp import web
from playwright.async_api import async_playwright

from m2s_bridge.web import create_app

OUT = Path(__file__).parent
PORT = 18099
PASSWORD = "przykladowe-haslo"

STATE = {
    "mqtt_connected": True,
    "matter_connected": True,
    "server": {"version": "matter.js/0.16", "thread_dataset_set": True, "bluetooth": True},
    "auto_add": True,
    "supla": {"server": "svr1.supla.org", "mail": "supla@zjawa.tech"},
    "supla_error": None,
    "devices": [
        {"name": "grillplats_plug_2", "caption": "Gniazdko — biurko", "node_id": "2", "endpoint": 1,
         "type": "onoff", "supla_channel": 0, "state": "on", "caption_linked": False},
        {"name": "kajplats_e27_3", "caption": "Lampa — salon", "node_id": "3", "endpoint": 1,
         "type": "light_cct", "supla_channel": 1, "state": "on", "caption_linked": False,
         "brightness": 65, "temperature": 30},
        {"name": "grillplats_plug_2_meter", "caption": "Gniazdko — biurko — licznik", "node_id": "2",
         "endpoint": 2, "type": "meter", "supla_channel": 2, "state": "measuring", "caption_linked": True,
         "meter": {"voltage": 236.8, "current": 0.104, "power": 14.6, "energy": 1.273}},
    ],
    "candidates": [
        {"node_id": "4", "available": True, "vendor": "IKEA of Sweden", "product": "TIMMERFLOTTE temp/hmd sensor",
         "endpoint": None, "supported": False, "reason": "typ urządzenia jeszcze nieobsługiwany", "control": None},
    ],
    "commission": {"running": False, "message": None, "error": None},
}

BACKUPS = [
    {"name": "m2s-backup-20261126-033000.tar.gz", "size": 48211, "time": 1795660200},
    {"name": "m2s-backup-20261125-033000.tar.gz", "size": 47903, "time": 1795573800},
    {"name": "m2s-backup-20261124-033000.tar.gz", "size": 47650, "time": 1795487400},
]


class FakeManager:
    def __init__(self) -> None:
        self.state = copy.deepcopy(STATE)

    def snapshot(self) -> dict:
        return copy.deepcopy(self.state)


async def main() -> None:
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
                viewport={"width": width, "height": height}, device_scale_factor=2, is_mobile=mobile, locale="pl-PL"
            )
            page = await context.new_page()

            async def fake_state(route):
                await route.fulfill(json={**manager.snapshot(), "auth": True, "backups": BACKUPS})

            await page.route("**/api/state", fake_state)
            return page

        async def login(page):
            await page.goto(base + "/login")
            await page.fill("input[type=password]", PASSWORD)
            await page.keyboard.press("Enter")
            await page.wait_for_selector("h2")
            await page.wait_for_timeout(800)

        page = await open_page(900, 520)
        await page.goto(base + "/login")
        await page.screenshot(path=OUT / "panel-logowanie.png")

        await login(page)
        await page.screenshot(path=OUT / "panel.png", full_page=True)
        sections = page.locator("section:visible")
        names = ["panel-kanaly", "panel-bez-kanalu", "panel-konto-supla", "panel-parowanie", "panel-kopie"]
        for index, name in enumerate(names):
            await sections.nth(index).screenshot(path=OUT / f"{name}.png")

        manager.state["commission"] = {"running": True, "message": "Parowanie trwa (do kilku minut)…", "error": None}
        await page.wait_for_timeout(2500)
        await sections.nth(3).screenshot(path=OUT / "panel-parowanie-trwa.png")
        manager.state["commission"] = STATE["commission"]

        phone = await open_page(390, 800, mobile=True)
        await login(phone)
        await phone.screenshot(path=OUT / "panel-telefon.png", full_page=True)
        await browser.close()
    await runner.cleanup()


asyncio.run(main())

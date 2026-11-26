# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Panel WWW: stan, parowanie, lista kanałów. Domyślnie tylko na 127.0.0.1."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import logging
import os
import re
import secrets
import time
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web

from .config import ConfigError, WebConfig
from .manager import Manager, ManagerError
from .qr import QrError, decode_matter_qr

log = logging.getLogger(__name__)

SESSION_COOKIE = "m2s_session"
SESSION_TTL_SEC = 30 * 24 * 3600
BACKUP_NAME = re.compile(r"m2s-backup-\d{8}-\d{6}\.tar\.gz")
MAX_UPLOAD_BYTES = 12 * 1024 * 1024  # zdjęcie kodu QR z telefonu
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


def check_web_security(config: WebConfig, password: str | None) -> None:
    """Panel pozwala parować i sterować — poza localhostem tylko z hasłem."""
    if config.enabled and config.host not in LOOPBACK_HOSTS and not password:
        raise ConfigError(
            f"web.host = {config.host}: panel dostępny z sieci wymaga hasła — ustaw M2S_WEB_PASSWORD w .env"
        )


def _authorized(request: web.Request, password: str) -> bool:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    _, _, given = decoded.partition(":")
    return hmac.compare_digest(given.encode(), password.encode())


def list_backups(directory: Path | None) -> list[dict[str, Any]]:
    if directory is None or not directory.is_dir():
        return []
    items = []
    for path in directory.iterdir():
        if BACKUP_NAME.fullmatch(path.name) and path.is_file():
            stat = path.stat()
            items.append({"name": path.name, "size": stat.st_size, "time": int(stat.st_mtime)})
    return sorted(items, key=lambda item: item["name"], reverse=True)


def create_app(
    manager: Manager, password: str | None = None, backup_dir: Path | None = None
) -> web.Application:
    sessions: dict[str, float] = {}  # token -> czas wygaśnięcia; w pamięci, restart łącznika wylogowuje

    def logged_in(request: web.Request) -> bool:
        if not password:
            return True
        expires = sessions.get(request.cookies.get(SESSION_COOKIE, ""))
        if expires is not None and expires > time.time():
            return True
        return _authorized(request, password)  # nagłówek Basic — dla skryptów i curl

    def login_page(error: str = "", status: int = 200) -> web.Response:
        html = resources.files("m2s_bridge").joinpath("web_login.html").read_text(encoding="utf-8")
        block = f'<div class="error" role="alert">{error}</div>' if error else ""
        return web.Response(
            text=html.replace("<!--ERROR-->", block),
            status=status,
            content_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    @web.middleware
    async def guard(request: web.Request, handler: Any) -> web.StreamResponse:
        if request.method != "GET":
            # Ochrona przed żądaniami wysłanymi z obcej strony otwartej w tej samej przeglądarce.
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                return web.json_response({"error": "żądanie z obcej strony"}, status=403)
        if request.path == "/login":
            return await handler(request)
        if not logged_in(request):
            if request.path.startswith("/api/"):
                return web.json_response({"error": "wymagane logowanie"}, status=401)
            raise web.HTTPFound("/login")
        if request.method != "GET":
            is_photo = request.path == "/api/qr" and request.content_type.startswith("image/")
            if request.content_type != "application/json" and not is_photo:
                return web.json_response({"error": "wymagany JSON"}, status=415)
        try:
            return await handler(request)
        except (ManagerError, QrError) as err:
            return web.json_response({"error": str(err)}, status=400)
        except (ConfigError, ValueError, KeyError, TypeError) as err:
            return web.json_response({"error": f"błędne dane: {err}"}, status=400)

    async def index(request: web.Request) -> web.Response:
        html = resources.files("m2s_bridge").joinpath("web_ui.html").read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})

    async def login_form(request: web.Request) -> web.StreamResponse:
        if logged_in(request):
            raise web.HTTPFound("/")
        return login_page()

    async def login(request: web.Request) -> web.StreamResponse:
        if not password:
            raise web.HTTPFound("/")
        form = await request.post()
        given = str(form.get("password", ""))
        if not hmac.compare_digest(given.encode(), password.encode()):
            await asyncio.sleep(1)  # spowalnia zgadywanie hasła
            log.warning("panel: nieudane logowanie z %s", request.remote)
            return login_page("Nieprawidłowe hasło.", status=401)
        now = time.time()
        for token in [t for t, expires in sessions.items() if expires <= now]:
            del sessions[token]
        token = secrets.token_urlsafe(32)
        sessions[token] = now + SESSION_TTL_SEC
        response = web.Response(status=303, headers={"Location": "/"})
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_SEC, httponly=True, samesite="Strict")
        return response

    async def logout(request: web.Request) -> web.Response:
        sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        response = web.json_response({"ok": True})
        response.del_cookie(SESSION_COOKIE)
        return response

    async def state(request: web.Request) -> web.Response:
        data = manager.snapshot()
        data["auth"] = bool(password)
        data["backups"] = list_backups(backup_dir)
        return web.json_response(data, headers={"Cache-Control": "no-store"})

    async def body(request: web.Request) -> dict[str, Any]:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("oczekiwano obiektu JSON")
        return data

    async def commission(request: web.Request) -> web.Response:
        data = await body(request)
        manager.start_commission(str(data.get("code", "")), bool(data.get("network_only")))
        return web.json_response({"ok": True})

    async def thread_dataset(request: web.Request) -> web.Response:
        data = await body(request)
        await manager.set_thread_dataset(str(data.get("dataset", "")))
        return web.json_response({"ok": True})

    async def add_device(request: web.Request) -> web.Response:
        data = await body(request)
        dev = await manager.add_device(int(data["node_id"]), int(data["endpoint"]))
        return web.json_response({"ok": True, "name": dev.name, "supla_channel": dev.supla_channel})

    async def device_command(request: web.Request) -> web.Response:
        data = await body(request)
        await manager.device_command(request.match_info["name"], str(data.get("command", "")))
        return web.json_response({"ok": True})

    async def device_light(request: web.Request) -> web.Response:
        data = await body(request)

        def number(key: str) -> int | None:
            value = data.get(key)
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{key} musi być liczbą")
            return int(value)

        await manager.set_light(request.match_info["name"], number("brightness"), number("temperature"))
        return web.json_response({"ok": True})

    async def device_caption(request: web.Request) -> web.Response:
        data = await body(request)
        await manager.set_caption(request.match_info["name"], str(data.get("caption", "")))
        return web.json_response({"ok": True})

    async def supla_account(request: web.Request) -> web.Response:
        data = await body(request)
        await manager.set_supla_account(str(data.get("server", "")), str(data.get("mail", "")))
        return web.json_response({"ok": True})

    async def qr(request: web.Request) -> web.Response:
        photo = await request.read()
        code = await asyncio.to_thread(decode_matter_qr, photo)
        return web.json_response({"code": code})

    async def backup_download(request: web.Request) -> web.StreamResponse:
        name = request.match_info["name"]
        # Tylko nazwy w formacie skryptu kopii — żadnych ścieżek z zewnątrz.
        if backup_dir is None or not BACKUP_NAME.fullmatch(name) or not (backup_dir / name).is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(
            backup_dir / name,
            headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
        )

    async def diagnostics(request: web.Request) -> web.Response:
        node_id = int(request.match_info["node_id"])
        dump = manager.diagnostics(node_id, full=request.query.get("full") == "1")
        return web.json_response(
            dump,
            headers={
                "Content-Disposition": f'attachment; filename="matter-node-{node_id}.json"',
                "Cache-Control": "no-store",
            },
        )

    app = web.Application(middlewares=[guard], client_max_size=MAX_UPLOAD_BYTES)
    app.add_routes(
        [
            web.get("/", index),
            web.get("/login", login_form),
            web.post("/login", login),
            web.post("/api/logout", logout),
            web.get("/api/state", state),
            web.get("/api/nodes/{node_id}/diagnostics", diagnostics),
            web.get("/api/backups/{name}", backup_download),
            web.post("/api/commission", commission),
            web.post("/api/qr", qr),
            web.post("/api/supla", supla_account),
            web.post("/api/thread-dataset", thread_dataset),
            web.post("/api/devices", add_device),
            web.post("/api/devices/{name}/command", device_command),
            web.post("/api/devices/{name}/caption", device_caption),
            web.post("/api/devices/{name}/light", device_light),
        ]
    )
    return app


async def start_web(manager: Manager, password: str | None) -> web.AppRunner:
    config = manager.config.web
    check_web_security(config, password)
    backup_dir = Path(os.environ.get("M2S_BACKUP_DIR", "/backups"))
    runner = web.AppRunner(create_app(manager, password, backup_dir), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, config.host, config.port).start()
    log.info("panel WWW: http://%s:%d", config.host, config.port)
    return runner

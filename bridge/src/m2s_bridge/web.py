# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Panel WWW: stan, parowanie, lista kanałów. Domyślnie tylko na 127.0.0.1."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
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

from . import logs
from .config import ConfigError, WebConfig
from .manager import Manager, ManagerError
from .qr import QrError, decode_matter_qr

log = logging.getLogger(__name__)

SESSION_COOKIE = "m2s_session"
SESSION_TTL_SEC = 180 * 24 * 3600
SESSION_RENEW_SEC = 24 * 3600  # ważność przedłużana przy wejściu do panelu, nie częściej niż raz na dobę
SESSION_FILE_NAME = "web-sessions.json"
MAX_SESSIONS = 200
BACKUP_NAME = re.compile(r"m2s-(backup|manual)-\d{8}-\d{6}\.tar\.gz")
# Kopia ręczna: panel zostawia zlecenie w pliku, a wykonuje je host (jednostka systemd .path z
# host/install-backup-timer.sh). Łącznik nie ma dostępu do Dockera ani do katalogu data/.
BACKUP_REQUEST_NAME = "backup-request"
BACKUP_RESULT_NAME = "backup-result"
BACKUP_STALE_SEC = 120      # po tym czasie uznajemy, że host nie obsługuje zleceń
BACKUP_MIN_GAP_SEC = 60     # odstęp między kopiami ręcznymi (kopia zatrzymuje na chwilę kontroler)
MANUAL_BACKUP_DAYS = 3
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


class SessionStore:
    """Sesje panelu zapisane w pliku, żeby restart łącznika nie wylogowywał.

    W pliku są tylko skróty SHA-256 z hasła panelu i tokenu, nie same tokeny: odczytanie pliku
    nie pozwala się zalogować, a zmiana hasła unieważnia wszystkie sesje.
    """

    def __init__(self, password: str, path: Path | None = None) -> None:
        self._password = password
        self._path = path
        self._sessions: dict[str, float] = {}
        if path is not None:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                now = time.time()
                self._sessions = {
                    str(key): float(expires)
                    for key, expires in raw.get("sessions", {}).items()
                    if isinstance(expires, (int, float)) and expires > now
                }
            except FileNotFoundError:
                pass
            except (OSError, ValueError, AttributeError) as err:
                log.warning("panel: nie można odczytać sesji z %s (%s) — wymagane ponowne logowanie", path, err)

    def _key(self, token: str) -> str:
        return hashlib.sha256(f"{self._password}\0{token}".encode()).hexdigest()

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(json.dumps({"sessions": self._sessions}), encoding="utf-8")
            tmp.chmod(0o600)
            tmp.replace(self._path)
        except OSError as err:  # sesja działa dalej w pamięci
            log.warning("panel: nie można zapisać sesji do %s: %s", self._path, err)

    def create(self) -> str:
        now = time.time()
        alive = sorted(((e, k) for k, e in self._sessions.items() if e > now), reverse=True)[: MAX_SESSIONS - 1]
        self._sessions = {key: expires for expires, key in alive}
        token = secrets.token_urlsafe(32)
        self._sessions[self._key(token)] = now + SESSION_TTL_SEC
        self._save()
        return token

    def valid(self, token: str) -> bool:
        expires = self._sessions.get(self._key(token)) if token else None
        return expires is not None and expires > time.time()

    def renew(self, token: str) -> bool:
        """Przedłuża ważną sesję; True, gdy ciasteczko trzeba wysłać ponownie."""
        if not self.valid(token):
            return False
        key = self._key(token)
        fresh = time.time() + SESSION_TTL_SEC
        if fresh - self._sessions[key] < SESSION_RENEW_SEC:
            return False
        self._sessions[key] = fresh
        self._save()
        return True

    def remove(self, token: str) -> None:
        if token and self._sessions.pop(self._key(token), None) is not None:
            self._save()


def list_backups(directory: Path | None) -> list[dict[str, Any]]:
    if directory is None or not directory.is_dir():
        return []
    items = []
    for path in directory.iterdir():
        if BACKUP_NAME.fullmatch(path.name) and path.is_file():
            stat = path.stat()
            item: dict[str, Any] = {"name": path.name, "size": stat.st_size, "time": int(stat.st_mtime)}
            if path.name.startswith("m2s-manual-"):
                item["manual"] = True
                item["expires"] = int(stat.st_mtime) + MANUAL_BACKUP_DAYS * 86400
            items.append(item)
    # Najnowsze na górze według znacznika czasu z nazwy (ręczne i z harmonogramu razem). Nie według
    # czasu pliku: kopie powstałe w tej samej sekundzie nie miałyby wtedy ustalonej kolejności.
    return sorted(items, key=lambda item: item["name"].split("-", 2)[2], reverse=True)


def backup_request_state(config_dir: Path | None) -> dict[str, Any]:
    """Stan zlecenia kopii ręcznej dla panelu."""
    if config_dir is None:
        return {"supported": False, "pending": False, "stale": False, "last_result": None}
    pending, stale, result = False, False, None
    try:
        age = time.time() - (config_dir / BACKUP_REQUEST_NAME).stat().st_mtime
        pending, stale = True, age > BACKUP_STALE_SEC
    except OSError:
        pass
    try:
        result = (config_dir / BACKUP_RESULT_NAME).read_text(encoding="utf-8").strip()[:40] or None
    except OSError:
        pass
    return {"supported": True, "pending": pending, "stale": stale, "last_result": result}


def request_backup(config_dir: Path | None, backup_dir: Path | None) -> None:
    if config_dir is None:
        raise ManagerError("ręczna kopia wymaga katalogu konfiguracji na dysku")
    request = config_dir / BACKUP_REQUEST_NAME
    state = backup_request_state(config_dir)
    if state["pending"] and not state["stale"]:
        raise ManagerError("kopia jest już w toku")
    newest = max((b["time"] for b in list_backups(backup_dir) if b.get("manual")), default=0)
    if time.time() - newest < BACKUP_MIN_GAP_SEC:
        raise ManagerError("ostatnia kopia ręczna powstała przed chwilą - odczekaj minutę")
    (config_dir / BACKUP_RESULT_NAME).unlink(missing_ok=True)
    request.unlink(missing_ok=True)   # przeterminowane zlecenie: zapis od nowa wyzwala hosta ponownie
    request.write_text(f"{int(time.time())}\n", encoding="utf-8")


def create_app(
    manager: Manager,
    password: str | None = None,
    backup_dir: Path | None = None,
    session_file: Path | None = None,
) -> web.Application:
    sessions = SessionStore(password or "", session_file)
    config_dir = manager.config_path.parent if manager.config_path else None

    def set_session_cookie(response: web.StreamResponse, token: str) -> None:
        response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_SEC, httponly=True, samesite="Strict")

    def logged_in(request: web.Request) -> bool:
        if not password:
            return True
        if sessions.valid(request.cookies.get(SESSION_COOKIE, "")):
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
        except OSError as err:
            # Najczęściej: katalog konfiguracji nie należy do użytkownika, na którym działa łącznik.
            log.error("panel: błąd zapisu lub odczytu pliku: %s", err)
            return web.json_response(
                {"error": f"nie udało się zapisać zmian na dysku: {err.strerror or err} ({err.filename or 'plik'})"},
                status=500,
            )

    async def index(request: web.Request) -> web.Response:
        html = resources.files("m2s_bridge").joinpath("web_ui.html").read_text(encoding="utf-8")
        response = web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})
        token = request.cookies.get(SESSION_COOKIE, "")
        if password and sessions.renew(token):
            set_session_cookie(response, token)
        return response

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
        response = web.Response(status=303, headers={"Location": "/"})
        set_session_cookie(response, sessions.create())
        return response

    async def logout(request: web.Request) -> web.Response:
        sessions.remove(request.cookies.get(SESSION_COOKIE, ""))
        response = web.json_response({"ok": True})
        response.del_cookie(SESSION_COOKIE)
        return response

    async def state(request: web.Request) -> web.Response:
        data = manager.snapshot()
        data["auth"] = bool(password)
        data["backups"] = list_backups(backup_dir)
        data["backup_request"] = backup_request_state(config_dir)
        return web.json_response(data, headers={"Cache-Control": "no-store"})

    async def body(request: web.Request) -> dict[str, Any]:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("oczekiwano obiektu JSON")
        return data

    async def commission(request: web.Request) -> web.Response:
        data = await body(request)
        manager.start_commission(
            str(data.get("code", "")), bool(data.get("network_only")), str(data.get("target") or "channel")
        )
        return web.json_response({"ok": True})

    def members(data: dict[str, Any]) -> list[tuple[int, int]]:
        value = data.get("members", [])
        if not isinstance(value, list):
            raise ValueError("members musi być listą")
        return [(int(item["node_id"]), int(item["endpoint"])) for item in value]

    async def group_create(request: web.Request) -> web.Response:
        data = await body(request)
        group = await manager.create_group(str(data.get("caption", "")), members(data))
        return web.json_response({"ok": True, "name": group.name, "supla_channel": group.supla_channel})

    async def group_members(request: web.Request) -> web.Response:
        data = await body(request)
        await manager.set_group_members(request.match_info["name"], members(data))
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

    async def device_cloud_id(request: web.Request) -> web.Response:
        data = await body(request)
        raw = str(data.get("id") or "").strip()
        cloud_id = None
        if raw:
            # Przyjmujemy sam numer albo wklejony adres kanału z SUPLA Cloud.
            match = re.fullmatch(r"(?:https?://\S*?/channels/)?(\d{1,10})/?(?:[?#]\S*)?", raw)
            if not match:
                raise ValueError("podaj numer kanału albo adres w postaci https://cloud.supla.org/channels/12345")
            cloud_id = int(match.group(1))
        await manager.set_cloud_id(request.match_info["name"], cloud_id, bool(data.get("fill_others")))
        return web.json_response({"ok": True})

    async def node_name(request: web.Request) -> web.Response:
        data = await body(request)
        await manager.set_name(int(data["node_id"]), int(data.get("endpoint") or 0), str(data.get("caption", "")))
        return web.json_response({"ok": True})

    async def device_identify(request: web.Request) -> web.Response:
        await body(request)
        await manager.identify(request.match_info["name"])
        return web.json_response({"ok": True})

    async def device_startup(request: web.Request) -> web.Response:
        data = await body(request)
        await manager.set_startup(request.match_info["name"], str(data.get("mode", "")))
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

    async def backup_create(request: web.Request) -> web.Response:
        await body(request)
        request_backup(config_dir, backup_dir)
        log.info("panel: zlecono ręczną kopię zapasową")
        return web.json_response({"ok": True})

    async def backup_download(request: web.Request) -> web.StreamResponse:
        name = request.match_info["name"]
        # Tylko nazwy w formacie skryptu kopii — żadnych ścieżek z zewnątrz.
        if backup_dir is None or not BACKUP_NAME.fullmatch(name) or not (backup_dir / name).is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(
            backup_dir / name,
            headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"},
        )

    async def log_view(request: web.Request) -> web.Response:
        source = request.query.get("source", "bridge")
        if source not in ("bridge", "matter"):
            raise ValueError("source: bridge albo matter")
        count = int(request.query.get("lines", "300"))
        data = await asyncio.to_thread(logs.read, source, count)
        return web.json_response(data, headers={"Cache-Control": "no-store"})

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
            web.get("/api/logs", log_view),
            web.get("/api/nodes/{node_id}/diagnostics", diagnostics),
            web.get("/api/backups/{name}", backup_download),
            web.post("/api/commission", commission),
            web.post("/api/backups", backup_create),
            web.post("/api/qr", qr),
            web.post("/api/supla", supla_account),
            web.post("/api/thread-dataset", thread_dataset),
            web.post("/api/devices", add_device),
            web.post("/api/groups", group_create),
            web.post("/api/names", node_name),
            web.post("/api/groups/{name}/members", group_members),
            web.post("/api/devices/{name}/command", device_command),
            web.post("/api/devices/{name}/caption", device_caption),
            web.post("/api/devices/{name}/identify", device_identify),
            web.post("/api/devices/{name}/startup", device_startup),
            web.post("/api/devices/{name}/cloud-id", device_cloud_id),
            web.post("/api/devices/{name}/light", device_light),
        ]
    )
    return app


async def start_web(manager: Manager, password: str | None) -> web.AppRunner:
    config = manager.config.web
    check_web_security(config, password)
    backup_dir = Path(os.environ.get("M2S_BACKUP_DIR", "/backups"))
    # Obok devices.yaml: katalog konfiguracji jest trwały i zapisywalny dla łącznika.
    session_file = manager.config_path.parent / SESSION_FILE_NAME if manager.config_path else None
    runner = web.AppRunner(create_app(manager, password, backup_dir, session_file), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, config.host, config.port).start()
    log.info("panel WWW: http://%s:%d", config.host, config.port)
    return runner

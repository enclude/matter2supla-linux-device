# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

import base64

import pytest
from aiohttp.test_utils import TestClient, TestServer

from m2s_bridge.config import ConfigError, WebConfig, load_config, parse_config
from m2s_bridge.manager import Manager
from m2s_bridge.web import check_web_security, create_app

from conftest import make_node
from test_manager import FakeClient, FakeMqtt


async def make(tmp_path, password=None, auto_add=True):
    manager = Manager(
        parse_config({"auto_add": auto_add}), tmp_path / "devices.yaml", tmp_path / "supla.yaml", "s", "m"
    )
    manager.mqtt = FakeMqtt()
    manager.matter_client = FakeClient(result={"node_id": 9})
    await manager.core.matter_connected(manager.matter_client, [make_node(on=False)])
    await manager.auto_add()
    client = TestClient(TestServer(create_app(manager, password)))
    await client.start_server()
    return manager, client


async def test_page_and_state(tmp_path):
    manager, client = await make(tmp_path)
    try:
        page = await client.get("/")
        assert page.status == 200 and "Matter2Supla" in await page.text()
        state = await (await client.get("/api/state")).json()
        assert state["devices"][0]["name"] == "grillplats_1"
        assert state["devices"][0]["state"] == "off"
        assert state["matter_connected"] and state["mqtt_connected"]
    finally:
        await client.close()


async def test_command_and_caption(tmp_path):
    manager, client = await make(tmp_path)
    try:
        r = await client.post("/api/devices/grillplats_1/command", json={"command": "on"})
        assert r.status == 200
        assert manager.matter_client.calls[-1] == ("device_command", (1, 1, 6, "On"))
        r = await client.post("/api/devices/grillplats_1/caption", json={"caption": "Biurko"})
        assert r.status == 200 and manager.config.devices[0].caption == "Biurko"
        r = await client.post("/api/devices/brak/command", json={"command": "on"})
        assert r.status == 400 and "brak" in (await r.json())["error"]
    finally:
        await client.close()


async def test_manual_add_and_commission(tmp_path):
    manager, client = await make(tmp_path, auto_add=False)
    try:
        r = await client.post("/api/devices", json={"node_id": "1", "endpoint": 1})
        assert (await r.json())["supla_channel"] == 0
        r = await client.post("/api/devices", json={"node_id": "abc", "endpoint": 1})
        assert r.status == 400
        r = await client.post("/api/commission", json={"code": "MT:XYZ", "network_only": True})
        assert r.status == 200
        await manager._commission_task
        assert manager.matter_client.calls[-1] == ("commission_with_code", {"code": "MT:XYZ", "network_only": True})
        r = await client.post("/api/commission", json={"code": ""})
        assert r.status == 400
        r = await client.post("/api/thread-dataset", json={"dataset": "zz"})
        assert r.status == 400
    finally:
        await client.close()


async def test_cross_site_and_non_json_posts_are_rejected(tmp_path):
    manager, client = await make(tmp_path)
    try:
        calls = len(manager.matter_client.calls)
        r = await client.post(
            "/api/devices/grillplats_1/command", json={"command": "on"}, headers={"Origin": "http://evil.example"}
        )
        assert r.status == 403
        r = await client.post("/api/devices/grillplats_1/command", data={"command": "on"})
        assert r.status == 415
        assert len(manager.matter_client.calls) == calls
    finally:
        await client.close()


async def test_login_form_instead_of_browser_popup(tmp_path):
    manager, client = await make(tmp_path, password="tajne")
    try:
        r = await client.get("/api/state")
        assert r.status == 401 and "WWW-Authenticate" not in r.headers  # bez wyskakującego okienka
        r = await client.get("/", allow_redirects=False)
        assert r.status == 302 and r.headers["Location"] == "/login"
        page = await client.get("/login")
        assert page.status == 200 and 'name="password"' in await page.text()
        r = await client.post("/api/devices/grillplats_1/command", json={"command": "on"})
        assert r.status == 401

        r = await client.post("/login", data={"password": "zle"}, allow_redirects=False)
        assert r.status == 401 and "Nieprawidłowe hasło" in await r.text()
        assert (await client.get("/api/state")).status == 401

        r = await client.post("/login", data={"password": "tajne"}, allow_redirects=False)
        assert r.status == 303 and r.headers["Location"] == "/"
        cookie = r.cookies["m2s_session"]
        assert cookie["httponly"] and cookie["samesite"] == "Strict"
        state = await client.get("/api/state")
        assert state.status == 200 and (await state.json())["auth"] is True
        assert (await client.get("/")).status == 200

        r = await client.post("/login", data={"password": "tajne"}, headers={"Origin": "http://evil.example"})
        assert r.status == 403

        assert (await client.post("/api/logout", json={})).status == 200
        assert (await client.get("/api/state")).status == 401
        # skrypty mogą nadal użyć nagłówka Basic
        good = {"Authorization": "Basic " + base64.b64encode(b"admin:tajne").decode()}
        assert (await client.get("/api/state", headers=good)).status == 200
    finally:
        await client.close()


async def test_no_login_without_password(tmp_path):
    manager, client = await make(tmp_path)
    try:
        r = await client.get("/login", allow_redirects=False)
        assert r.status == 302 and r.headers["Location"] == "/"
        assert (await (await client.get("/api/state")).json())["auth"] is False
    finally:
        await client.close()


def test_network_binding_requires_password():
    check_web_security(WebConfig(host="127.0.0.1"), None)
    check_web_security(WebConfig(host="0.0.0.0", enabled=False), None)
    check_web_security(WebConfig(host="0.0.0.0"), "haslo")
    with pytest.raises(ConfigError):
        check_web_security(WebConfig(host="0.0.0.0"), None)


async def test_diagnostics_download(tmp_path):
    manager, client = await make(tmp_path)
    try:
        r = await client.get("/api/nodes/1/diagnostics")
        assert r.status == 200 and "attachment" in r.headers["Content-Disposition"]
        assert (await r.json())["summary"]["product"] == "GRILLPLATS"
        assert (await client.get("/api/nodes/77/diagnostics")).status == 400
    finally:
        await client.close()


async def test_qr_photo_upload(tmp_path):
    from test_qr import CODE, qr_photo

    manager, client = await make(tmp_path)
    try:
        r = await client.post("/api/qr", data=qr_photo(CODE), headers={"Content-Type": "image/jpeg"})
        assert r.status == 200 and (await r.json()) == {"code": CODE}
        r = await client.post("/api/qr", data=b"nie obraz", headers={"Content-Type": "image/jpeg"})
        assert r.status == 400 and "zdjęcia" in (await r.json())["error"]
        # zdjęcie przyjmuje tylko /api/qr
        r = await client.post("/api/commission", data=b"x", headers={"Content-Type": "image/jpeg"})
        assert r.status == 415
    finally:
        await client.close()


async def test_supla_account_endpoint(tmp_path):
    manager, client = await make(tmp_path)
    try:
        r = await client.post("/api/supla", json={"server": "svr5.supla.org", "mail": "ja@example.org"})
        assert r.status == 200
        state = await (await client.get("/api/state")).json()
        assert state["supla"] == {"server": "svr5.supla.org", "mail": "ja@example.org"}
        r = await client.post("/api/supla", json={"server": "zły adres", "mail": "ja@example.org"})
        assert r.status == 400
    finally:
        await client.close()


async def test_backups_list_and_download(tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "m2s-backup-20261126-033000.tar.gz").write_bytes(b"nowsza")
    (backups / "m2s-backup-20261125-033000.tar.gz").write_bytes(b"starsza")
    (backups / "m2s-backup-20261126-033000.tar.gz.tmp").write_bytes(b"w trakcie")
    (backups / "sekret.txt").write_text("nie do pobrania")
    manager, _ = await make(tmp_path)
    await _.close()
    client = TestClient(TestServer(create_app(manager, "tajne", backups)))
    await client.start_server()
    try:
        assert (await client.get("/api/backups/m2s-backup-20261126-033000.tar.gz")).status == 401
        auth = {"Authorization": "Basic " + base64.b64encode(b"a:tajne").decode()}
        state = await (await client.get("/api/state", headers=auth)).json()
        assert [b["name"] for b in state["backups"]] == [
            "m2s-backup-20261126-033000.tar.gz",
            "m2s-backup-20261125-033000.tar.gz",
        ]
        r = await client.get("/api/backups/m2s-backup-20261126-033000.tar.gz", headers=auth)
        assert r.status == 200 and await r.read() == b"nowsza"
        assert "attachment" in r.headers["Content-Disposition"]
        for bad in ("sekret.txt", "m2s-backup-20261126-033000.tar.gz.tmp", "..%2Fdevices.yaml", "m2s-backup-20990101-000000.tar.gz"):
            assert (await client.get(f"/api/backups/{bad}", headers=auth)).status == 404
    finally:
        await client.close()


async def test_light_endpoint(tmp_path):
    from test_light import bulb_node

    manager = Manager(
        parse_config({"lights_as_channels": False}), tmp_path / "devices.yaml", tmp_path / "supla.yaml", "s", "m"
    )
    manager.mqtt = FakeMqtt()
    manager.matter_client = FakeClient()
    await manager.core.matter_connected(manager.matter_client, [bulb_node()])
    await manager.auto_add()
    client = TestClient(TestServer(create_app(manager)))
    await client.start_server()
    try:
        name = (await (await client.get("/api/state")).json())["candidates"][0]["control"]["name"]
        r = await client.post(f"/api/devices/{name}/light", json={"temperature": 100})
        assert r.status == 200
        assert manager.matter_client.calls[-1][1][2:4] == (768, "MoveToColorTemperature")
        r = await client.post(f"/api/devices/{name}/light", json={"brightness": "jasno"})
        assert r.status == 400
        r = await client.post(f"/api/devices/{name}/command", json={"command": "off"})
        assert r.status == 200 and manager.matter_client.calls[-1][1][2:4] == (6, "Off")
    finally:
        await client.close()


async def test_state_reports_version(tmp_path, monkeypatch):
    monkeypatch.setenv("M2S_BUILD_VERSION", "0.2.0")
    monkeypatch.setenv("M2S_BUILD_REVISION", "0123456789abcdef")
    manager, client = await make(tmp_path)
    try:
        state = await (await client.get("/api/state")).json()
        assert state["version"] == {
            "current": "0.2.0",
            "revision": "0123456789abcdef",
            "latest": None,
            "update_available": False,
            "url": None,
        }
        manager.updates.latest = "0.3.0"
        manager.updates.release_url = "https://github.com/enclude/matter2supla-linux-device/releases/tag/v0.3.0"
        version = (await (await client.get("/api/state")).json())["version"]
        assert version["update_available"] is True and version["latest"] == "0.3.0"
        assert version["url"].endswith("/v0.3.0")
        page = await (await client.get("/")).text()
        assert "update-notice" in page and "sudo ./host/update.sh" in page
    finally:
        await client.close()


def test_sessions_survive_restart_and_follow_password(tmp_path, monkeypatch):
    from m2s_bridge import web as web_module
    from m2s_bridge.web import SessionStore

    path = tmp_path / "web-sessions.json"
    store = SessionStore("tajne", path)
    token = store.create()
    assert store.valid(token) and not store.valid("inny") and not store.valid("")
    assert token not in path.read_text(encoding="utf-8")  # w pliku tylko skrót

    assert SessionStore("tajne", path).valid(token)  # po restarcie łącznika
    assert not SessionStore("nowe-haslo", path).valid(token)  # zmiana hasła unieważnia sesje

    # przedłużanie: nie częściej niż raz na dobę
    assert store.renew(token) is False
    real_time = web_module.time.time
    monkeypatch.setattr(web_module.time, "time", lambda: real_time() + 2 * 24 * 3600)
    assert store.renew(token) is True and store.renew(token) is False
    monkeypatch.setattr(web_module.time, "time", lambda: real_time() + 181 * 24 * 3600)
    assert store.valid(token)  # przedłużona o 180 dni od dnia odnowienia
    monkeypatch.setattr(web_module.time, "time", lambda: real_time() + 183 * 24 * 3600)
    assert not store.valid(token) and store.renew(token) is False
    monkeypatch.setattr(web_module.time, "time", real_time)

    store.remove(token)
    assert not SessionStore("tajne", path).valid(token)

    path.write_text("to nie jest JSON", encoding="utf-8")
    broken = SessionStore("tajne", path)  # uszkodzony plik: po prostu ponowne logowanie
    assert broken.valid(broken.create())


async def test_unwritable_config_gives_readable_error(tmp_path, monkeypatch):
    # Katalog konfiguracji bez prawa zapisu: panel ma podać przyczynę, a nie gołe „błąd 500”.
    manager, client = await make(tmp_path)

    def fail(*args, **kwargs):
        raise PermissionError(13, "Permission denied", "/config/devices.yaml.tmp")

    monkeypatch.setattr("m2s_bridge.manager.save_config", fail)
    response = await client.post("/api/groups", json={"caption": "Salon", "members": []})
    assert response.status == 500
    assert "devices.yaml" in (await response.json())["error"]
    assert manager.groups() == []
    await client.close()


async def test_logs_endpoint(tmp_path, monkeypatch):
    import logging

    from m2s_bridge import logs

    logs.install("%(levelname)s %(name)s: %(message)s")
    manager, client = await make(tmp_path)
    logging.getLogger("m2s_bridge.test").warning("kod MT:Y.K9042C00KA0648G00 i klucz %s", "ab" * 40)
    data = await (await client.get("/api/logs?source=bridge&lines=5")).json()
    assert data["available"] and "MT:(ukryty)" in data["lines"][-1]
    assert "MT:Y" not in data["lines"][-1] and "abababababab…" in data["lines"][-1]

    # dziennik kontrolera: brak pliku -> podpowiedź, plik -> koniec pliku bez kodów sterujących terminala
    monkeypatch.setenv("M2S_MATTER_LOG", str(tmp_path / "matter.log"))
    data = await (await client.get("/api/logs?source=matter")).json()
    assert data["available"] is False and "LOG_FILE" in data["hint"]
    (tmp_path / "matter.log").write_text(
        "\n".join(f"\x1b[2K2026-11-26 20:0{i % 10}:00 INFO krok {i}" for i in range(50)) + "\n", encoding="utf-8"
    )
    data = await (await client.get("/api/logs?source=matter&lines=3")).json()
    assert data["lines"] == [f"2026-11-26 20:0{i % 10}:00 INFO krok {i}" for i in (47, 48, 49)]
    assert (await client.get("/api/logs?source=inne")).status == 400
    await client.close()


async def test_cloud_link_ids(tmp_path):
    manager, client = await make(tmp_path)
    plug = manager.config.devices[0].name
    supla_file = (tmp_path / "supla.yaml").read_text(encoding="utf-8")
    # wklejony adres kanału; pozostałe kanały dostają kolejne numery
    response = await client.post(
        f"/api/devices/{plug}/cloud-id", json={"id": "https://cloud.supla.org/channels/17462", "fill_others": True}
    )
    assert response.status == 200
    assert (await (await client.get("/api/state")).json())["devices"][0]["cloud_id"] == 17462
    assert load_config(tmp_path / "devices.yaml").devices[0].cloud_id == 17462
    assert (tmp_path / "supla.yaml").read_text(encoding="utf-8") == supla_file  # SUPLA nie jest ruszana
    assert (await client.post(f"/api/devices/{plug}/cloud-id", json={"id": "abc"})).status == 400
    # zmiana konta kasuje identyfikatory - na nowym koncie będą inne
    await manager.set_supla_account("svr2.supla.org", "nowe@example.org")
    assert manager.config.devices[0].cloud_id is None
    await client.close()

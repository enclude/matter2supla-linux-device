import base64

import pytest
from aiohttp.test_utils import TestClient, TestServer

from m2s_bridge.config import ConfigError, WebConfig, parse_config
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

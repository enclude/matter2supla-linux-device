# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from m2s_bridge import cli, version
from m2s_bridge.version import UpdateChecker, is_newer, parse_version

RELEASE = {
    "tag_name": "v0.3.0",
    "html_url": "https://github.com/enclude/matter2supla-linux-device/releases/tag/v0.3.0",
}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.2.0", (0, 2, 0)),
        ("v1.10.3", (1, 10, 3)),
        (" 2.0 ", (2, 0)),
        ("dev", None),
        ("edge", None),
        ("1.0.0-rc1", None),
        ("", None),
        (None, None),
        (3, None),
    ],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("0.3.0", "0.2.0", True),
        ("v0.3.0", "0.2.0", True),
        ("0.10.0", "0.9.9", True),  # liczbowo, nie alfabetycznie
        ("1.0", "1.0.0", False),
        ("1.0.1", "1.0", True),
        ("0.2.0", "0.2.0", False),
        ("0.1.0", "0.2.0", False),
        ("0.3.0", "dev", False),
        ("0.3.0", "edge", False),
        ("0.3.0", "", False),
        (None, "0.2.0", False),
        ("nightly", "0.2.0", False),
    ],
)
def test_is_newer(latest, current, expected):
    assert is_newer(latest, current) is expected


def test_current_version_from_environment(monkeypatch):
    monkeypatch.delenv("M2S_BUILD_VERSION", raising=False)
    monkeypatch.delenv("M2S_BUILD_REVISION", raising=False)
    assert version.current_version() == "dev" and version.current_revision() == ""
    assert version.describe() == "dev"
    monkeypatch.setenv("M2S_BUILD_VERSION", "0.2.0")
    monkeypatch.setenv("M2S_BUILD_REVISION", "0123456789abcdef")
    assert version.describe() == "0.2.0 (rewizja 0123456)"
    assert UpdateChecker().info() == {
        "current": "0.2.0",
        "revision": "0123456789abcdef",
        "latest": None,
        "update_available": False,
        "url": None,
    }


def test_cli_version(monkeypatch, capsys):
    monkeypatch.setenv("M2S_BUILD_VERSION", "0.2.0")
    monkeypatch.setenv("M2S_BUILD_REVISION", "0123456789abcdef")
    with pytest.raises(SystemExit) as stop:
        cli.main(["--version"])
    assert stop.value.code == 0
    assert capsys.readouterr().out.strip() == "m2s-bridge 0.2.0 (rewizja 0123456)"


async def serve(handler):
    """Lokalna atrapa API GitHuba — testy nie wychodzą do sieci."""
    app = web.Application()
    app.router.add_get("/latest", handler)
    server = TestServer(app)
    await server.start_server()
    return server, str(server.make_url("/latest"))


async def test_check_reads_latest_release():
    seen = {}

    async def handler(request):
        seen.update(request.headers)
        seen["query"] = request.query_string
        return web.json_response(RELEASE)

    server, url = await serve(handler)
    try:
        checker = UpdateChecker(url, current="0.2.0", revision="abc")
        assert await checker.check() is True
        assert checker.info() == {
            "current": "0.2.0",
            "revision": "abc",
            "latest": "0.3.0",
            "update_available": True,
            "url": RELEASE["html_url"],
        }
        # żądanie nie niesie nic poza ogólną nazwą klienta
        assert seen["User-Agent"] == "matter2supla" and seen["query"] == ""
        assert "Authorization" not in seen and "Cookie" not in seen
    finally:
        await server.close()


@pytest.mark.parametrize("current", ["dev", "edge", "0.3.0", "1.0.0"])
async def test_check_without_update(current):
    async def handler(request):
        return web.json_response(RELEASE)

    server, url = await serve(handler)
    try:
        checker = UpdateChecker(url, current=current, revision="")
        assert await checker.check() is True
        info = checker.info()
        assert info["latest"] == "0.3.0" and info["update_available"] is False
        assert info["revision"] is None
    finally:
        await server.close()


@pytest.mark.parametrize("status", [404, 403, 429, 500])
async def test_check_ignores_http_errors(status):
    async def handler(request):
        return web.json_response({"message": "Not Found"}, status=status)

    server, url = await serve(handler)
    try:
        checker = UpdateChecker(url, current="0.2.0")
        assert await checker.check() is False
        assert checker.info()["latest"] is None and checker.info()["update_available"] is False
    finally:
        await server.close()


@pytest.mark.parametrize(
    "body",
    ["to nie jest JSON", "[]", '{"tag_name": 5}', '{"tag_name": "nightly"}', '{"html_url": "https://github.com/x"}'],
)
async def test_check_ignores_bad_answers(body):
    async def handler(request):
        return web.Response(text=body, content_type="application/json")

    server, url = await serve(handler)
    try:
        checker = UpdateChecker(url, current="0.2.0")
        assert await checker.check() is False and checker.latest is None
    finally:
        await server.close()


async def test_check_rejects_foreign_link():
    async def handler(request):
        return web.json_response({"tag_name": "v0.3.0", "html_url": "javascript:alert(1)"})

    server, url = await serve(handler)
    try:
        checker = UpdateChecker(url, current="0.2.0")
        assert await checker.check() is True
        assert checker.info()["update_available"] is True and checker.info()["url"] is None
    finally:
        await server.close()


async def test_check_survives_network_error_and_keeps_last_answer():
    async def handler(request):
        return web.json_response(RELEASE)

    server, url = await serve(handler)
    checker = UpdateChecker(url, current="0.2.0")
    assert await checker.check() is True
    await server.close()  # nikt już nie słucha na tym porcie
    assert await checker.check() is False
    assert checker.info()["latest"] == "0.3.0"


async def test_run_checks_periodically():
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return web.json_response(RELEASE)

    server, url = await serve(handler)
    checker = UpdateChecker(url, current="0.2.0")
    task = asyncio.create_task(checker.run(first_delay=0, interval=0.01))
    try:
        async with asyncio.timeout(5):
            while calls < 3:
                await asyncio.sleep(0.01)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await server.close()
    assert checker.latest == "0.3.0"

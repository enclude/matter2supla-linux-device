# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Wersja łącznika i sprawdzanie, czy na GitHubie jest nowsze wydanie.

Sprawdzenie służy wyłącznie do pokazania informacji w panelu WWW — niczego nie pobiera
i niczego nie aktualizuje. Aktualizację wykonuje ręcznie użytkownik (`host/update.sh`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

RELEASES_URL = "https://api.github.com/repos/enclude/matter2supla-linux-device/releases/latest"
RELEASE_PAGE_PREFIX = "https://github.com/"
# Żądanie nie zawiera żadnych danych użytkownika ani instalacji — tylko ogólną nazwę klienta.
USER_AGENT = "matter2supla"
FIRST_CHECK_DELAY_SEC = 60.0
CHECK_INTERVAL_SEC = 24 * 3600.0
REQUEST_TIMEOUT_SEC = 15.0

DEV_VERSION = "dev"  # obraz zbudowany lokalnie, bez numeru wydania
_VERSION_RE = re.compile(r"v?(\d+(?:\.\d+)*)")


def current_version() -> str:
    """Wersja wbudowana w obraz (`M2S_BUILD_VERSION`): numer wydania, `edge` albo `dev`."""
    return os.environ.get("M2S_BUILD_VERSION", "").strip() or DEV_VERSION


def current_revision() -> str:
    """Commit, z którego zbudowano obraz (`M2S_BUILD_REVISION`); pusty przy budowie lokalnej."""
    return os.environ.get("M2S_BUILD_REVISION", "").strip()


def describe() -> str:
    revision = current_revision()
    return current_version() + (f" (rewizja {revision[:7]})" if revision else "")


def parse_version(text: Any) -> tuple[int, ...] | None:
    """`0.2.0` lub `v0.2.0` -> (0, 2, 0); wszystko inne (dev, edge, 1.0-rc1) -> None."""
    if not isinstance(text, str):
        return None
    match = _VERSION_RE.fullmatch(text.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def is_newer(latest: str | None, current: str | None) -> bool:
    """Czy `latest` jest nowszym wydaniem niż `current`. Wersji nieliczbowych nie porównujemy."""
    new, old = parse_version(latest), parse_version(current)
    if new is None or old is None:
        return False
    size = max(len(new), len(old))
    return new + (0,) * (size - len(new)) > old + (0,) * (size - len(old))


class UpdateChecker:
    """Pamięta najnowsze wydanie odczytane z API GitHuba. Błędy sieci nie wychodzą na zewnątrz."""

    def __init__(self, url: str = RELEASES_URL, current: str | None = None, revision: str | None = None):
        self.url = url
        self.current = current if current is not None else current_version()
        self.revision = revision if revision is not None else current_revision()
        self.latest: str | None = None
        self.release_url: str | None = None

    def info(self) -> dict[str, Any]:
        """Pole `version` w stanie panelu."""
        return {
            "current": self.current,
            "revision": self.revision or None,
            "latest": self.latest,
            "update_available": is_newer(self.latest, self.current),
            "url": self.release_url,
        }

    async def check(self) -> bool:
        """Jedno zapytanie o najnowsze wydanie; True, gdy odpowiedź została przyjęta."""
        try:
            timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SEC)
            headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(self.url) as response:
                    if response.status != 200:
                        # 404 = projekt nie ma jeszcze wydań; 403/429 = limit zapytań API.
                        log.debug("sprawdzanie wersji: odpowiedź HTTP %d", response.status)
                        return False
                    data = await response.json(content_type=None)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # sieć, DNS, TLS, błędny JSON — nic z tego nie może zatrzymać łącznika
            log.debug("sprawdzanie wersji nie powiodło się: %s", err)
            return False
        tag = data.get("tag_name") if isinstance(data, dict) else None
        if parse_version(tag) is None:
            log.debug("sprawdzanie wersji: nieoczekiwana odpowiedź")
            return False
        latest = tag.strip().removeprefix("v")
        page = data.get("html_url")
        if latest != self.latest and is_newer(latest, self.current):
            log.info("dostępna jest nowsza wersja: %s (zainstalowana: %s)", latest, self.current)
        self.latest = latest
        # Adres trafia do odnośnika w panelu — przyjmujemy tylko stronę wydania na GitHubie.
        self.release_url = page if isinstance(page, str) and page.startswith(RELEASE_PAGE_PREFIX) else None
        return True

    async def run(self, first_delay: float = FIRST_CHECK_DELAY_SEC, interval: float = CHECK_INTERVAL_SEC) -> None:
        """Sprawdza krótko po starcie, a potem raz na dobę."""
        await asyncio.sleep(first_delay)
        while True:
            await self.check()
            await asyncio.sleep(interval)

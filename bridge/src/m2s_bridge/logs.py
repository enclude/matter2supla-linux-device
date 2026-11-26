# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Dzienniki dla zakładki „Logi” panelu: własny (z pamięci) i kontrolera Matter (z pliku).

Kontroler zapisuje dziennik do pliku (`LOG_FILE` w docker-compose.yml); łącznik czyta tylko jego
koniec. Kody parowania i długie ciągi szesnastkowe (certyfikaty, klucze) są wycinane przed
pokazaniem w panelu.
"""

from __future__ import annotations

import collections
import logging
import os
import re
from pathlib import Path

BRIDGE_LINES = 1000
TAIL_BYTES = 256 * 1024
MAX_LINES = 1000
DEFAULT_MATTER_LOG = "/matter-data/logs/matter-server.log"

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_PAIRING_CODE = re.compile(r"MT:[A-Z0-9.\-]{6,}")
_LONG_HEX = re.compile(r"\b[0-9a-fA-F]{48,}\b")


def clean(line: str) -> str:
    line = _ANSI.sub("", line).rstrip()
    line = _PAIRING_CODE.sub("MT:(ukryty)", line)
    return _LONG_HEX.sub(lambda m: m.group(0)[:12] + "…", line)


class MemoryLog(logging.Handler):
    """Ostatnie wpisy dziennika łącznika trzymane w pamięci."""

    def __init__(self, capacity: int = BRIDGE_LINES):
        super().__init__()
        self._lines: collections.deque[str] = collections.deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._lines.extend(self.format(record).splitlines())
        except Exception:  # dziennik nie może zatrzymać łącznika
            self.handleError(record)

    def tail(self, count: int) -> list[str]:
        return [clean(line) for line in list(self._lines)[-count:]]


memory_log = MemoryLog()


def install(fmt: str) -> None:
    """Podpina dziennik w pamięci do głównego loggera (raz)."""
    root = logging.getLogger()
    if memory_log not in root.handlers:
        memory_log.setFormatter(logging.Formatter(fmt))
        root.addHandler(memory_log)


def matter_log_path() -> Path:
    return Path(os.environ.get("M2S_MATTER_LOG", DEFAULT_MATTER_LOG))


def file_tail(path: Path, count: int) -> list[str] | None:
    """Ostatnie wiersze pliku albo None, gdy pliku nie ma lub nie da się go odczytać."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - TAIL_BYTES))
            data = handle.read()
    except OSError:
        return None
    lines = data.decode("utf-8", errors="replace").splitlines()
    if size > TAIL_BYTES:
        lines = lines[1:]  # pierwszy wiersz jest ucięty
    return [cleaned for line in lines[-count:] if (cleaned := clean(line))]


def read(source: str, count: int) -> dict[str, object]:
    count = max(1, min(MAX_LINES, count))
    if source == "matter":
        lines = file_tail(matter_log_path(), count)
        if lines is None:
            return {
                "source": source,
                "available": False,
                "lines": [],
                "hint": "Dziennik kontrolera Matter nie jest dostępny. Wymaga aktualnego docker-compose.yml "
                "(zmienna LOG_FILE kontrolera i katalog data/matter podłączony do łącznika) "
                "oraz ponownego utworzenia kontenerów: docker compose up -d.",
            }
        return {"source": source, "available": True, "lines": lines}
    return {"source": "bridge", "available": True, "lines": memory_log.tail(count)}

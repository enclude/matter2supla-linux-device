# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Odczyt kodu parowania Matter (MT:…) z kodu QR na zdjęciu."""

from __future__ import annotations

import io

import zxingcpp
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIDE = 2400  # większe zdjęcia są zmniejszane — kod QR pozostaje czytelny, a dekodowanie szybkie
Image.MAX_IMAGE_PIXELS = 60_000_000


TILE_GRID = 4  # drugie podejście: zdjęcie dzielone na zachodzące na siebie wycinki
TILE_SCALES = (1.0, 0.5, 2.0)


def _scan(image: Image.Image) -> list[str]:
    results = zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.QRCode, try_rotate=True, try_downscale=True)
    return [r.text.strip() for r in results]


def _read(image: Image.Image) -> list[str]:
    """Teksty kodów QR ze zdjęcia; kolejne, coraz kosztowniejsze podejścia aż do pierwszego kodu Matter.

    Mała naklejka na zdjęciu z telefonu (kilkanaście megapikseli, kod zajmuje ułamek kadru, bywa
    na zaokrąglonej, błyszczącej powierzchni) często nie daje się odczytać z całego kadru, a daje
    się z wycinka w odpowiedniej skali i po wyrównaniu kontrastu.
    """
    small = image
    if max(image.size) > MAX_SIDE:
        small = image.copy()
        small.thumbnail((MAX_SIDE, MAX_SIDE))
    found = _scan(small)
    if any(text.startswith("MT:") for text in found):
        return found
    gray = image.convert("L")
    width, height = gray.size
    tile_w, tile_h = width * 2 // (TILE_GRID + 1), height * 2 // (TILE_GRID + 1)
    for scale in TILE_SCALES:
        for row in range(TILE_GRID):
            for column in range(TILE_GRID):
                left, top = column * tile_w // 2, row * tile_h // 2
                tile = gray.crop((left, top, min(width, left + tile_w), min(height, top + tile_h)))
                if scale != 1.0:
                    size = (int(tile.width * scale), int(tile.height * scale))
                    if min(size) < 64 or max(size) > 2 * MAX_SIDE:
                        continue
                    tile = tile.resize(size, Image.LANCZOS)
                for variant in (tile, ImageOps.autocontrast(tile, cutoff=2)):
                    texts = _scan(variant)
                    if any(text.startswith("MT:") for text in texts):
                        return texts
                    found += [text for text in texts if text not in found]
    return found


class QrError(ValueError):
    """Na zdjęciu nie ma czytelnego kodu Matter — treść trafia do użytkownika."""


def decode_matter_qr(data: bytes) -> str:
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as err:
        raise QrError("nie udało się odczytać pliku jako zdjęcia") from err
    texts = _read(image)
    for text in texts:
        if text.startswith("MT:"):
            return text
    if texts:
        raise QrError("na zdjęciu jest kod QR, ale to nie jest kod parowania Matter (powinien zaczynać się od MT:)")
    raise QrError("nie znaleziono kodu QR — zrób ostrzejsze zdjęcie z bliska, bez odblasków")

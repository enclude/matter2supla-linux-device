# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

"""Odczyt kodu parowania Matter (MT:…) z kodu QR na zdjęciu."""

from __future__ import annotations

import io

import zxingcpp
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIDE = 2400  # większe zdjęcia są zmniejszane — kod QR pozostaje czytelny, a dekodowanie szybkie
Image.MAX_IMAGE_PIXELS = 60_000_000


class QrError(ValueError):
    """Na zdjęciu nie ma czytelnego kodu Matter — treść trafia do użytkownika."""


def decode_matter_qr(data: bytes) -> str:
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as err:
        raise QrError("nie udało się odczytać pliku jako zdjęcia") from err
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE))
    texts = [r.text.strip() for r in zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.QRCode)]
    for text in texts:
        if text.startswith("MT:"):
            return text
    if texts:
        raise QrError("na zdjęciu jest kod QR, ale to nie jest kod parowania Matter (powinien zaczynać się od MT:)")
    raise QrError("nie znaleziono kodu QR — zrób ostrzejsze zdjęcie z bliska, bez odblasków")

# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later

import io

import pytest
import zxingcpp
from PIL import Image

from m2s_bridge.qr import QrError, decode_matter_qr

CODE = "MT:Y.K9042C00KA0648G00"


def qr_photo(text, canvas=(1600, 1200), fmt="JPEG"):
    """Kod QR wklejony w większe „zdjęcie”, jak naklejka sfotografowana telefonem."""
    code = zxingcpp.write_barcode_to_image(zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode), scale=8)
    qr = Image.frombuffer("L", (code.shape[1], code.shape[0]), bytes(memoryview(code)))
    photo = Image.new("RGB", canvas, (205, 200, 190))
    photo.paste(qr.convert("RGB").rotate(12, expand=True, fillcolor=(205, 200, 190)), (500, 300))
    out = io.BytesIO()
    photo.save(out, fmt)
    return out.getvalue()


def test_reads_matter_code_from_photo():
    assert decode_matter_qr(qr_photo(CODE)) == CODE
    assert decode_matter_qr(qr_photo(CODE, canvas=(5000, 3800), fmt="PNG")) == CODE  # duże zdjęcie jest zmniejszane


def test_other_qr_code_is_rejected():
    with pytest.raises(QrError, match="nie jest kod parowania Matter"):
        decode_matter_qr(qr_photo("https://www.ikea.com/"))


def test_photo_without_code_and_garbage():
    blank = io.BytesIO()
    Image.new("RGB", (800, 600), (120, 130, 140)).save(blank, "JPEG")
    with pytest.raises(QrError, match="nie znaleziono"):
        decode_matter_qr(blank.getvalue())
    with pytest.raises(QrError, match="zdjęcia"):
        decode_matter_qr(b"to nie jest obraz")

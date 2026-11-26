#!/bin/bash
# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later
# Buduje i wgrywa na ESP32-C5 gotowy przykład `ot_rcp` z ESP-IDF (radio Thread, RCP).
# To niezmieniony firmware Espressifa — w tym repozytorium nie ma własnego kodu dla ESP.
# UWAGA: erase-flash kasuje dotychczasowy firmware płytki i jego parowania.
# Wymaga ESP-IDF 5.5.x. Użycie: ./host/flash-rcp.sh /dev/ttyUSB0
set -euo pipefail

PORT="${1:?podaj port szeregowy płytki, np. /dev/ttyUSB0}"
: "${IDF_PATH:?najpierw: source ~/esp/esp-idf/export.sh}"

WORK="$(mktemp -d)"
cp -r "${IDF_PATH}/examples/openthread/ot_rcp" "${WORK}/ot_rcp"
cd "${WORK}/ot_rcp"
idf.py set-target esp32c5
idf.py build
idf.py -p "${PORT}" erase-flash flash
echo "Gotowe. Domyślnie RCP pracuje na UART0, 460800 bodów."

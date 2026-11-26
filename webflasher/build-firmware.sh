#!/bin/bash
# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later
# Buduje `ot_rcp` (ESP-IDF, bez zmian w kodzie) dla każdego układu w dwóch wariantach:
#   uart — przez mostek USB-UART płytki (UART0, 460800 bodów; domyślna konfiguracja przykładu),
#   usb  — przez wbudowany port USB układu (USB Serial/JTAG).
# Wynik: jeden scalony obraz na wariant (wgrywany od adresu 0) oraz manifest dla ESP Web Tools.
# Uruchamiany w obrazie espressif/idf po załadowaniu export.sh.
set -euo pipefail

OUT="${1:?katalog wyjściowy}"
TARGETS=(esp32c5 esp32c6 esp32h2)
declare -A FAMILY=([esp32c5]=ESP32-C5 [esp32c6]=ESP32-C6 [esp32h2]=ESP32-H2)
mkdir -p "$OUT"

IDF_VERSION="$(git -C "$IDF_PATH" describe --tags --always 2>/dev/null || echo unknown)"
builds=()

for target in "${TARGETS[@]}"; do
  for variant in uart usb; do
    work="/tmp/ot_rcp-$target-$variant"
    rm -rf "$work"
    cp -r "$IDF_PATH/examples/openthread/ot_rcp" "$work"
    cd "$work"
    defaults="sdkconfig.defaults"
    if [ "$variant" = usb ]; then
      echo "CONFIG_OPENTHREAD_RCP_USB_SERIAL_JTAG=y" > sdkconfig.m2s
      defaults="sdkconfig.defaults;sdkconfig.m2s"
    fi
    idf.py -D SDKCONFIG_DEFAULTS="$defaults" set-target "$target" >/dev/null
    idf.py build >/dev/null
    if [ "$variant" = usb ]; then
      grep -q '^CONFIG_OPENTHREAD_RCP_USB_SERIAL_JTAG=y' sdkconfig \
        || { echo "wariant usb dla $target: opcja USB Serial/JTAG nie została włączona" >&2; exit 1; }
    else
      grep -q '^CONFIG_OPENTHREAD_RCP_UART=y' sdkconfig \
        || { echo "wariant uart dla $target: przykład nie używa UART" >&2; exit 1; }
    fi
    name="ot_rcp-$target-$variant.bin"
    (cd build && python -m esptool --chip "$target" merge_bin -o "$OUT/$name" @flash_args >/dev/null)
    echo "zbudowano $name ($(stat -c %s "$OUT/$name") B)"
    serial=uart; [ "$variant" = usb ] && serial=cdc
    builds+=("    {\"chipFamily\": \"${FAMILY[$target]}\", \"serialType\": \"$serial\", \"parts\": [{\"path\": \"$name\", \"offset\": 0}]}")
  done
done

{
  echo '{'
  echo '  "name": "Matter2Supla — radio Thread (ot_rcp)",'
  echo "  \"version\": \"ESP-IDF $IDF_VERSION\","
  echo '  "new_install_prompt_erase": false,'
  echo '  "new_install_improv_wait_time": 0,'
  echo '  "builds": ['
  for i in "${!builds[@]}"; do
    sep=','; [ "$i" -eq $((${#builds[@]} - 1)) ] && sep=''
    echo "${builds[$i]}$sep"
  done
  echo '  ]'
  echo '}'
} > "$OUT/manifest.json"
echo "$IDF_VERSION" > "$OUT/idf-version.txt"
(cd "$OUT" && sha256sum ./*.bin > SHA256SUMS)

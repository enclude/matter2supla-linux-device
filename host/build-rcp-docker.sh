#!/bin/bash
# Buduje gotowy przykład `ot_rcp` z ESP-IDF dla ESP32-C5 w oficjalnym obrazie Docker —
# bez instalowania ESP-IDF. Wynik: bootloader.bin, partition-table.bin, esp_ot_rcp.bin.
# Użycie: ./host/build-rcp-docker.sh [katalog-wyjściowy]     (domyślnie ./rcp-build)
set -euo pipefail

OUT="$(mkdir -p "${1:-rcp-build}" && cd "${1:-rcp-build}" && pwd)"
docker run --rm -v "${OUT}:/out" espressif/idf:release-v5.5 bash -c '
  set -e
  cp -r "$IDF_PATH/examples/openthread/ot_rcp" /tmp/ot_rcp && cd /tmp/ot_rcp
  idf.py set-target esp32c5 && idf.py build
  cp build/bootloader/bootloader.bin build/partition_table/partition-table.bin build/esp_ot_rcp.bin /out/
  chown -R '"$(id -u):$(id -g)"' /out'

cat <<MSG

Gotowe: ${OUT}
Wgranie (esptool >= 5, UWAGA: kasuje dotychczasowy firmware płytki):
  esptool --chip esp32c5 --port <PORT> erase-flash
  esptool --chip esp32c5 --port <PORT> --baud 460800 write-flash --flash-mode dio --flash-freq 80m \\
      --flash-size 2MB 0x2000 bootloader.bin 0x8000 partition-table.bin 0x10000 esp_ot_rcp.bin
<PORT> to /dev/ttyUSB0 w Linuksie albo COMx w Windows.
MSG

#!/bin/bash
# Odtworzenie instalacji z kopii zapasowej — także na innym urządzeniu.
#
# Na nowym urządzeniu najpierw: Docker, katalog projektu z repozytorium, ./host/setup-host.sh <interfejs>,
# reguła udev dla dongla (host/99-thread-rcp.rules.example). Potem:
#   sudo ./host/restore.sh /ścieżka/m2s-backup-RRRRMMDD-GGMMSS.tar.gz
# Po odtworzeniu popraw w .env INFRA_IF i w config/devices.yaml web.host, jeśli nowe urządzenie
# ma inny interfejs lub adres IP, i uruchom: docker compose up -d --build
#
# NIE uruchamiaj jednocześnie starej i odtworzonej instalacji: miałyby tę samą tożsamość
# w Matter, Thread i SUPLA.
set -euo pipefail

ARCHIVE="$(readlink -f "${1:?podaj plik kopii zapasowej}")"
[ -f "$ARCHIVE" ] || { echo "Brak pliku $ARCHIVE" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "Uruchom przez sudo" >&2; exit 1; }
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
tar -tzf "$ARCHIVE" >/dev/null

if [ -d data ] && [ -n "$(ls -A data 2>/dev/null)" ] && [ "${2:-}" != "--force" ]; then
  echo "W $DIR/data są już dane. Odtworzenie je zastąpi — dodaj --force, jeśli to zamierzone." >&2
  exit 1
fi

docker compose down 2>/dev/null || true
if [ -d data ] || [ -d config/supla ] || [ -f config/devices.yaml ]; then
  SAFE="pre-restore-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$SAFE"
  [ -d data ] && mv data "$SAFE/"
  [ -d config ] && cp -a config "$SAFE/"
  [ -f .env ] && cp -a .env "$SAFE/"
  echo "Dotychczasowy stan przeniesiony do $DIR/$SAFE"
fi
tar --numeric-owner -xzpf "$ARCHIVE" -C "$DIR"

echo "Odtworzono z $ARCHIVE."
echo "Sprawdź INFRA_IF w .env oraz web.host w config/devices.yaml, potem: docker compose up -d --build"
if [ -f data/otbr/dataset-active.txt ]; then
  cat <<MSG
Jeśli używasz INNEGO dongla niż w chwili robienia kopii, border router nie znajdzie swoich
ustawień. Przywróć wtedy sieć Thread z zapisanego datasetu:
  docker exec otbr ot-ctl dataset set active \$(cat data/otbr/dataset-active.txt)
  docker exec otbr ot-ctl ifconfig up && docker exec otbr ot-ctl thread start
MSG
fi

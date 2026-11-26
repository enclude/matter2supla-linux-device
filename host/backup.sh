#!/bin/bash
# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later
# Kopia zapasowa stanu instalacji: data/ (tożsamość kontrolera Matter, sieć Thread, GUID/AUTHKEY
# SUPLA), config/ (mapowanie kanałów, konto SUPLA) i .env. Kod projektu nie jest kopiowany —
# pochodzi z repozytorium.
#
# Na czas pakowania (kilkanaście sekund) zatrzymywane są matter-server i supla, żeby pliki były
# spójne; border router działa bez przerwy. Kopia ZAWIERA SEKRETY — przechowuj ją bezpiecznie.
#
# Użycie: sudo ./host/backup.sh           kopia z harmonogramu (pliki m2s-backup-*)
#         sudo ./host/backup.sh --prune   tylko usuwa przeterminowane kopie ręczne
# Zmienne: M2S_BACKUP_DIR (domyślnie ./backups), M2S_BACKUP_KEEP (ile kopii zostawić, domyślnie 14),
#          M2S_BACKUP_KIND=manual - kopia ręczna (zlecana z panelu WWW): pliki m2s-manual-*,
#          zostają najwyżej M2S_MANUAL_KEEP (3) i znikają po M2S_MANUAL_DAYS (3) dniach.
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"
DEST="${M2S_BACKUP_DIR:-$DIR/backups}"
KEEP="${M2S_BACKUP_KEEP:-14}"
MANUAL_KEEP="${M2S_MANUAL_KEEP:-3}"
MANUAL_DAYS="${M2S_MANUAL_DAYS:-3}"
PREFIX="m2s-backup"
if [ "${M2S_BACKUP_KIND:-}" = manual ]; then PREFIX="m2s-manual"; KEEP="$MANUAL_KEEP"; fi
OUT="$DEST/$PREFIX-$(date +%Y%m%d-%H%M%S).tar.gz"

prune_manual() {  # kopie ręczne są tymczasowe: znikają same po kilku dniach
  [ -d "$DEST" ] || return 0
  find "$DEST" -maxdepth 1 -type f -name 'm2s-manual-*.tar.gz' -mmin "+$((MANUAL_DAYS * 1440))" -delete
}
if [ "${1:-}" = --prune ]; then prune_manual; exit 0; fi

mkdir -p "$DEST"
chown 1000:1000 "$DEST"   # panel WWW (uid 1000) udostępnia kopie do pobrania
chmod 700 "$DEST"

# Dataset sieci Thread osobno, czytelnie: plik ustawień OTBR jest przypisany do konkretnego
# dongla, a dataset pozwala odtworzyć tę samą sieć także na innym donglu.
if DATASET="$(docker exec otbr ot-ctl dataset active -x 2>/dev/null | head -1 | tr -d '\r\n')" \
   && [[ "$DATASET" =~ ^[0-9a-fA-F]{20,}$ ]]; then
  ( umask 077; echo "$DATASET" > data/otbr/dataset-active.txt )
else
  echo "UWAGA: nie udało się odczytać datasetu Thread (otbr nie działa?) — kopia bez niego" >&2
fi

STOPPED=""
restart() { [ -z "$STOPPED" ] || docker compose start $STOPPED >/dev/null 2>&1 || echo "BŁĄD: nie udało się uruchomić: $STOPPED" >&2; }
trap restart EXIT
for service in matter-server supla; do
  if [ -n "$(docker compose ps -q --status running "$service" 2>/dev/null)" ]; then
    STOPPED="$STOPPED $service"
  fi
done
[ -z "$STOPPED" ] || docker compose stop $STOPPED >/dev/null 2>&1

INCLUDE="data config"
[ -f .env ] && INCLUDE="$INCLUDE .env"
# Bez dziennika kontrolera Matter (data/matter/logs): duży, a do odtworzenia niepotrzebny.
( umask 077; tar --numeric-owner --exclude=data/matter/logs \
    --exclude=config/backup-request --exclude=config/backup-result -czpf "$OUT.tmp" $INCLUDE )
restart; STOPPED=""; trap - EXIT

tar -tzf "$OUT.tmp" >/dev/null   # kontrola, że archiwum da się odczytać
mv "$OUT.tmp" "$OUT"
chown 1000:1000 "$OUT"
chmod 600 "$OUT"

# Rotacja: zostają najnowsze kopie.
ls -1t "$DEST"/$PREFIX-*.tar.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
prune_manual

echo "Kopia: $OUT ($(du -h "$OUT" | cut -f1)), plików: $(tar -tzf "$OUT" | wc -l), zachowanych kopii: $(ls -1 "$DEST"/$PREFIX-*.tar.gz | wc -l)"

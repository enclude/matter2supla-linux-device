#!/bin/bash
# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later
# Instaluje codzienną kopię zapasową jako timer systemd (domyślnie 03:30, 14 ostatnich kopii)
# oraz obsługę kopii ręcznej zlecanej z panelu WWW: panel zapisuje plik config/backup-request,
# a systemd (jednostka .path) uruchamia wtedy host/backup.sh. Kontener panelu nie dostaje przez to
# żadnych uprawnień do hosta ani do Dockera.
# Użycie: sudo ./host/install-backup-timer.sh [daily|weekly] [liczba-kopii]
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Uruchom przez sudo" >&2; exit 1; }
DIR="$(cd "$(dirname "$0")/.." && pwd)"
WHEN="${1:-daily}"; KEEP="${2:-14}"
case "$WHEN" in
  daily)  CALENDAR="*-*-* 03:30:00" ;;
  weekly) CALENDAR="Sun *-*-* 03:30:00" ;;
  *) echo "Pierwszy argument: daily albo weekly" >&2; exit 1 ;;
esac

cat > /etc/systemd/system/m2s-backup.service <<UNIT
[Unit]
Description=Matter2Supla - kopia zapasowa
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
WorkingDirectory=$DIR
Environment=M2S_BACKUP_KEEP=$KEEP
ExecStart=$DIR/host/backup.sh
UNIT
cat > /etc/systemd/system/m2s-backup.timer <<UNIT
[Unit]
Description=Matter2Supla - harmonogram kopii zapasowej

[Timer]
OnCalendar=$CALENDAR
Persistent=true

[Install]
WantedBy=timers.target
UNIT
cat > /etc/systemd/system/m2s-backup-manual.service <<UNIT
[Unit]
Description=Matter2Supla - kopia zapasowa zlecona z panelu
After=docker.service
Requires=docker.service
StartLimitIntervalSec=600
StartLimitBurst=5

[Service]
Type=oneshot
WorkingDirectory=$DIR
Environment=M2S_BACKUP_KIND=manual
ExecStart=$DIR/host/backup.sh
# Wynik dla panelu i zdjęcie zlecenia - także po błędzie, żeby jednostka .path nie ruszała w kółko.
ExecStopPost=/bin/sh -c 'echo "\$\$SERVICE_RESULT" > "\$\$1/config/backup-result"; rm -f "\$\$1/config/backup-request"' sh $DIR
UNIT
cat > /etc/systemd/system/m2s-backup-manual.path <<UNIT
[Unit]
Description=Matter2Supla - zlecenie kopii zapasowej z panelu

[Path]
PathExists=$DIR/config/backup-request

[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/m2s-backup-prune.service <<UNIT
[Unit]
Description=Matter2Supla - usuwanie przeterminowanych kopii ręcznych

[Service]
Type=oneshot
WorkingDirectory=$DIR
ExecStart=$DIR/host/backup.sh --prune
UNIT
cat > /etc/systemd/system/m2s-backup-prune.timer <<UNIT
[Unit]
Description=Matter2Supla - co godzinę: usuwanie przeterminowanych kopii ręcznych

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
UNIT
rm -f "$DIR/config/backup-request"   # stare zlecenie nie może wyzwolić kopii przy instalacji
systemctl daemon-reload
systemctl enable --now m2s-backup.timer m2s-backup-prune.timer m2s-backup-manual.path
systemctl list-timers m2s-backup.timer --no-pager | head -3

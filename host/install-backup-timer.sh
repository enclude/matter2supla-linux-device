#!/bin/bash
# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later
# Instaluje codzienną kopię zapasową jako timer systemd (domyślnie 03:30, 14 ostatnich kopii).
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
systemctl daemon-reload
systemctl enable --now m2s-backup.timer
systemctl list-timers m2s-backup.timer --no-pager | head -3

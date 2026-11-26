#!/bin/bash
# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later
# Instalacja bramki jednym poleceniem. Bezpieczna do ponownego uruchomienia: niczego, co już
# istnieje (plik .env, sieć Thread, dane parowań), nie nadpisuje.
#
# Użycie (w katalogu projektu, z podłączonym donglem):
#   sudo ./host/install.sh [--lan] [interfejs]
#     --lan       panel WWW dostępny z sieci lokalnej, z wylosowanym hasłem
#                 (bez tej opcji panel działa tylko na tym urządzeniu: 127.0.0.1:8099)
#     interfejs   interfejs LAN; domyślnie ten z trasy domyślnej
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Uruchom przez sudo" >&2; exit 1; }
DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR"

LAN=0; IFACE=""
for arg in "$@"; do
  case "$arg" in
    --lan) LAN=1 ;;
    -*) echo "Nieznana opcja: $arg" >&2; exit 1 ;;
    *) IFACE="$arg" ;;
  esac
done
step() { echo; echo "== $*"; }

step "1/7 Interfejs sieciowy"
if [ -z "$IFACE" ] && [ -f .env ]; then IFACE="$(grep -E '^INFRA_IF=' .env | cut -d= -f2 || true)"; fi
[ -n "$IFACE" ] || IFACE="$(ip -o route show default | awk '{print $5; exit}')"
[ -n "$IFACE" ] && [ -d "/sys/class/net/$IFACE" ] || { echo "Nie znaleziono interfejsu LAN — podaj go jako argument (ip -br link)." >&2; exit 1; }
ADDRESS="$(ip -o -4 addr show dev "$IFACE" | awk '{print $4}' | cut -d/ -f1 | head -1)"
echo "Interfejs: $IFACE ${ADDRESS:+($ADDRESS)}"

step "2/7 Docker"
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1 || ! docker buildx version >/dev/null 2>&1; then
  command -v apt-get >/dev/null || { echo "Zainstaluj Dockera z wtyczkami compose i buildx, potem uruchom skrypt ponownie." >&2; exit 1; }
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  COMPOSE=docker-compose
  apt-cache show docker-compose-v2 >/dev/null 2>&1 && COMPOSE=docker-compose-v2   # Ubuntu
  apt-get install -y -qq docker.io docker-buildx "$COMPOSE"
  systemctl enable --now docker
fi
docker --version

step "3/7 Ustawienia systemu"
./host/setup-host.sh "$IFACE" >/dev/null && echo "Przekazywanie pakietów włączone (/etc/sysctl.d/60-otbr.conf)."

step "4/7 Dongle Thread"
if [ ! -e /dev/thread-rcp ]; then
  mapfile -t PORTS < <(ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null || true)
  if [ "${#PORTS[@]}" -ne 1 ]; then
    echo "Znaleziono ${#PORTS[@]} portów szeregowych USB (${PORTS[*]:-brak}). Zostaw podłączony tylko dongle" >&2
    echo "albo utwórz regułę ręcznie według host/99-thread-rcp.rules.example, potem uruchom skrypt ponownie." >&2
    exit 1
  fi
  VID="$(udevadm info -q property -n "${PORTS[0]}" | sed -n 's/^ID_VENDOR_ID=//p')"
  PID="$(udevadm info -q property -n "${PORTS[0]}" | sed -n 's/^ID_MODEL_ID=//p')"
  [ -n "$VID" ] && [ -n "$PID" ] || { echo "Nie udało się odczytać identyfikatorów USB portu ${PORTS[0]}." >&2; exit 1; }
  echo "SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"$VID\", ATTRS{idProduct}==\"$PID\", SYMLINK+=\"thread-rcp\"" \
    > /etc/udev/rules.d/99-thread-rcp.rules
  udevadm control --reload && udevadm trigger --subsystem-match=tty && sleep 1
fi
[ -e /dev/thread-rcp ] || { echo "Brak /dev/thread-rcp mimo reguły udev." >&2; exit 1; }
echo "Dongle: /dev/thread-rcp -> $(readlink /dev/thread-rcp)"

step "5/7 Bluetooth (potrzebny do parowania nowych urządzeń)"
BLUETOOTH=0
if [ -d /sys/class/bluetooth/hci0 ]; then
  if ! command -v bluetoothctl >/dev/null && command -v apt-get >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq bluez
  fi
  command -v rfkill >/dev/null && rfkill unblock bluetooth || true
  systemctl enable --now bluetooth >/dev/null 2>&1 || true
  BLUETOOTH=1
  echo "Adapter Bluetooth: jest."
else
  echo "Brak adaptera Bluetooth — parowanie tylko urządzeń udostępnionych z innej aplikacji."
fi

step "6/7 Konfiguracja"
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s|^INFRA_IF=.*|INFRA_IF=$IFACE|" .env
  if [ "$BLUETOOTH" = 1 ]; then
    printf '\n# Dodatek Bluetooth\nCOMPOSE_FILE=docker-compose.yml:docker-compose.bluetooth.yml\n' >> .env
  fi
  if [ "$LAN" = 1 ]; then
    PASSWORD="$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | cut -c1-20)"
    sed -i "s|^M2S_WEB_PASSWORD=.*|M2S_WEB_PASSWORD=$PASSWORD|" .env
  fi
  chmod 600 .env
  echo "Utworzono .env."
else
  echo ".env już istnieje — bez zmian."
fi
if [ ! -f config/devices.yaml ]; then
  cp config/devices.example.yaml config/devices.yaml
  if [ "$LAN" = 1 ] && [ -n "$ADDRESS" ]; then
    sed -i "/^web:/,/^[a-z]/ s|^  host: .*|  host: $ADDRESS|" config/devices.yaml
  fi
  echo "Utworzono config/devices.yaml."
else
  echo "config/devices.yaml już istnieje — bez zmian."
fi
mkdir -p data/otbr data/matter data/supla config/supla backups
chown -R 1000:1000 data/matter data/supla config backups
chmod 700 config/supla backups

step "7/7 Sieć Thread i start usług (pierwsza budowa obrazów trwa kilka–kilkanaście minut)"
./host/init-thread.sh
./host/install-backup-timer.sh daily 14 >/dev/null && echo "Codzienna kopia zapasowa: włączona (03:30, 14 kopii)."

WEB_HOST="$(sed -n '/^web:/,/^[a-z]/ s/^  host: *//p' config/devices.yaml | head -1)"
PASSWORD="$(grep -E '^M2S_WEB_PASSWORD=' .env | cut -d= -f2)"
echo
echo "Gotowe. Panel: http://${WEB_HOST:-127.0.0.1}:8099"
if [ -n "$PASSWORD" ]; then
  echo "Hasło panelu jest w pliku $DIR/.env (M2S_WEB_PASSWORD)."
elif [ "${WEB_HOST:-127.0.0.1}" = "127.0.0.1" ]; then
  echo "Panel działa tylko lokalnie; z innego komputera: ssh -L 8099:127.0.0.1:8099 <ten-host>"
fi
echo "W panelu: wpisz konto SUPLA, potem sparuj urządzenia."

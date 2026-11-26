#!/bin/bash
# Uruchamia border router i tworzy sieć Thread, jeśli jeszcze jej nie ma, a potem przekazuje
# jej dane kontrolerowi Matter. Bezpieczny do wielokrotnego uruchamiania: istniejącej sieci
# NIGDY nie tworzy od nowa (nowa sieć odcięłaby wszystkie sparowane urządzenia).
# Użycie: sudo ./host/init-thread.sh
set -euo pipefail
cd "$(cd "$(dirname "$0")/.." && pwd)"

otctl() { docker exec otbr ot-ctl "$@" 2>&1 | tr -d '\r'; }

docker compose up -d otbr >/dev/null
echo -n "Czekam na border router"
for _ in $(seq 1 30); do
  STATE="$(otctl state | head -1 || true)"
  case "$STATE" in disabled|detached|child|router|leader) break ;; esac
  echo -n "."; sleep 2
done
echo
case "$STATE" in
  disabled|detached|child|router|leader) ;;
  *) echo "Border router nie odpowiada (stan: ${STATE:-brak}). Sprawdź: docker logs otbr" >&2
     echo "Najczęstsza przyczyna: brak dongla pod /dev/thread-rcp albo zły INFRA_IF w .env." >&2
     exit 1 ;;
esac

if otctl dataset active -x | head -1 | grep -qE '^[0-9a-fA-F]{20,}$'; then
  echo "Sieć Thread już istnieje — zostaje bez zmian."
else
  echo "Tworzę nową sieć Thread."
  otctl dataset init new >/dev/null
  otctl dataset commit active >/dev/null
fi
if [ "$(otctl state | head -1)" = "disabled" ]; then
  otctl ifconfig up >/dev/null
  otctl thread start >/dev/null
fi
echo -n "Czekam na rolę w sieci"
for _ in $(seq 1 30); do
  STATE="$(otctl state | head -1)"
  case "$STATE" in leader|router|child) break ;; esac
  echo -n "."; sleep 2
done
echo " $STATE"
case "$STATE" in leader|router|child) ;; *) echo "Sieć Thread nie wystartowała (stan: $STATE)." >&2; exit 1 ;; esac

DATASET="$(otctl dataset active -x | head -1)"
# Gotowe obrazy z GHCR, jeśli są dostępne; w przeciwnym razie budowa na miejscu (trwa dłużej).
# Pobierane są tylko obrazy tego projektu — wersje pozostałych usług zostają bez zmian.
if [ "${M2S_BUILD:-0}" = 1 ] || ! docker compose pull --quiet bridge supla 2>/dev/null; then
  echo "Buduję obrazy lokalnie (pierwszy raz trwa to kilka–kilkanaście minut)."
  docker compose build --quiet bridge supla
fi
docker compose up -d
echo -n "Czekam na kontroler Matter"
for _ in $(seq 1 40); do
  if docker compose run --rm -T bridge set-thread-dataset "$DATASET" >/dev/null 2>&1; then
    echo; echo "Dane sieci Thread przekazane kontrolerowi Matter."; exit 0
  fi
  echo -n "."; sleep 3
done
echo
echo "Nie udało się przekazać danych sieci kontrolerowi. Sprawdź: docker logs matter-server" >&2
exit 1

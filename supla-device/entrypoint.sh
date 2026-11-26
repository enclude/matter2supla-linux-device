#!/bin/sh
# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later
# Uruchamia supla-device i restartuje go, gdy łącznik zapisze nową konfigurację
# (np. po automatycznym dopisaniu kanału). Czeka też, aż konfiguracja w ogóle powstanie.
CONFIG="${SUPLA_CONFIG:-/etc/supla-device/supla-device.yaml}"
CHILD=""

stop_child() {
    if [ -n "$CHILD" ] && kill -0 "$CHILD" 2>/dev/null; then
        kill -TERM "$CHILD" 2>/dev/null
        wait "$CHILD" 2>/dev/null
    fi
    CHILD=""
}
trap 'stop_child; exit 0' TERM INT

checksum() { cksum "$CONFIG" 2>/dev/null || echo none; }

WAITING=0
while :; do
    if [ ! -s "$CONFIG" ]; then
        [ "$WAITING" = 0 ] && echo "supla: czekam na $CONFIG (powstanie po dodaniu pierwszego urządzenia)"
        WAITING=1
        sleep 2 & wait $!
        continue
    fi
    WAITING=0
    SUM="$(checksum)"
    echo "supla: start supla-device"
    supla-device-linux -c "$CONFIG" "$@" &
    CHILD=$!
    while kill -0 "$CHILD" 2>/dev/null; do
        sleep 2 & wait $!
        if [ "$(checksum)" != "$SUM" ]; then
            echo "supla: konfiguracja zmieniona — restart supla-device"
            stop_child
            break
        fi
    done
    if [ -n "$CHILD" ]; then
        wait "$CHILD"; echo "supla: supla-device zakończył pracę (kod $?) — ponowny start za 5 s"
        CHILD=""
        sleep 5 & wait $!
    fi
done

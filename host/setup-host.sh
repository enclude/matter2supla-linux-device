#!/bin/bash
# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later
# Jednorazowe przygotowanie hosta (VM Ubuntu) pod OTBR. Odpowiada skryptowi `setup-host`
# z openthread/ot-br-posix (etc/docker/border-router): przyjmowanie RA mimo włączonego
# forwardingu oraz forwarding IPv6/IPv4.
# Użycie: sudo ./host/setup-host.sh <interfejs-LAN>     (np. ens18)
set -euo pipefail

IF="${1:?podaj interfejs LAN, np. ens18 (ip -br link)}"
[ -d "/proc/sys/net/ipv6/conf/${IF}" ] || { echo "Brak interfejsu ${IF} lub IPv6 wyłączone" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "Uruchom przez sudo" >&2; exit 1; }

cat > /etc/sysctl.d/60-otbr.conf <<CONF
net.ipv6.conf.${IF}.accept_ra = 2
net.ipv6.conf.${IF}.accept_ra_rt_info_max_plen = 64
net.ipv6.conf.all.forwarding = 1
net.ipv4.ip_forward = 1
CONF
sysctl --system > /dev/null
echo "Zapisano /etc/sysctl.d/60-otbr.conf:"
sysctl "net.ipv6.conf.${IF}.accept_ra" "net.ipv6.conf.${IF}.accept_ra_rt_info_max_plen" \
       net.ipv6.conf.all.forwarding net.ipv4.ip_forward
[ -c /dev/net/tun ] || echo "UWAGA: brak /dev/net/tun (modprobe tun)" >&2

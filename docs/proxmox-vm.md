# Wariant: maszyna wirtualna na Proxmox VE

Stos był uruchomiony w VM z Ubuntu 26.04 na Proxmox VE (2026-11-25), po czym ten wariant **porzucono na rzecz Raspberry Pi**, bo nie udało się w nim sparować żadnego urządzenia (patrz „Znane ograniczenia”). Opis zostaje dla osób, które mają w hoście Proxmox sprawny adapter Bluetooth.

Co działało w VM: dongle przez USB passthrough, border router jako lider sieci Thread, cały stos po restarcie VM, panel z LAN. Co nie zostało osiągnięte: parowanie, a więc także rejestracja w SUPLA Cloud i sterowanie urządzeniem.

Instalacja przebiega jak w [instalacja-raspberry-pi.md](instalacja-raspberry-pi.md); poniżej tylko różnice.

## Wymagania wobec VM

- Karta sieciowa VM **zmostkowana** z LAN (most `vmbr0`), IPv6 włączone.
- Interfejs LAN w VM z kartą VirtIO nazywa się zwykle `ens18` (taka wartość jest w `.env.example`); sprawdź `ip -br link`.
- Docker: `sudo apt install docker.io docker-compose-v2 docker-buildx`.

## Przekazanie dongla do VM

Na hoście Proxmox odczytaj identyfikator mostka USB-UART (`lsusb`) i przekaż urządzenie:

```bash
qm set <VMID> -usb0 host=<vid>:<pid>     # dla mostka CH340: host=1a86:7523
```

W VM port pojawi się jako `/dev/ttyUSB0` (albo `/dev/ttyACM0`). Dalej jak na RPi: reguła udev z `host/99-thread-rcp.rules.example` daje stałą nazwę `/dev/thread-rcp`.

## Bluetooth w VM

Adapter Bluetooth hosta Proxmox przekazuje się do VM tak samo, jako kolejne urządzenie USB. W VM instalujesz BlueZ i dopisujesz do `.env`:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.bluetooth.yml
```

Dodatek ustawia dla `matter-server` `apparmor=unconfined`. Na Ubuntu 26.04 jest to konieczne: domyślny profil AppArmor Dockera blokuje kontenerowi D-Bus hosta i kontroler pada z `write EPIPE` przy pierwszym użyciu Bluetootha (zaobserwowane).

## Zapora i multicast

- Jeśli dla VM włączona jest zapora Proxmox, jej conntrack działa na hoście Proxmox. Dokumentacja matterjs-server zaleca wtedy na **hipernadzorcy** `net.netfilter.nf_conntrack_udp_timeout_stream=3600` (inaczej urządzenia bateryjne gubią subskrypcje). Najprościej: zapora wyłączona dla tej VM.
- Ruch multicast IPv6 (mDNS, RA) musi przechodzić przez most `vmbr0`. Przy problemach z wykrywaniem urządzeń sprawdź `multicast_snooping` mostu - wskazówka **niezweryfikowana** na sprzęcie.

## Znane ograniczenia

- **Adapter Bluetooth po passthrough może nie działać radiowo.** W sprawdzanej konfiguracji adapter hosta (Qualcomm, `0cf3:e009`) był w VM widoczny, kontroler zgłaszał włączony Bluetooth i przyjął dataset Thread, ale trzy kolejne skany nie wykryły żadnego urządzenia (prawdopodobnie brak podłączonych anten). Parowanie kończyło się błędem „No commissionable device was discovered”. Zanim zaczniesz parować, upewnij się, że skan Bluetooth w VM widzi jakiekolwiek urządzenia w otoczeniu.
- Bez działającego Bluetootha pozostaje parowanie urządzeń będących już w innej sieci (udostępnienie z innej aplikacji, opcja „urządzenie jest już w sieci”) - ta droga nie była sprawdzana, a dongle nie bierze wtedy udziału w komunikacji z urządzeniem.
- Komentarze w `docker-compose.yml`, `.env.example` i `host/setup-host.sh` mówią o „VM” - pochodzą z tego wariantu; pliki są te same dla Raspberry Pi.

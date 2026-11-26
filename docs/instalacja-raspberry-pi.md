# Instalacja na Raspberry Pi

Instrukcja odpowiada wdrożeniu uruchomionemu na **Raspberry Pi 4B z Raspberry Pi OS (Debian 13, arm64)**, z siecią przewodową zarządzaną przez NetworkManager. Na innych modelach i dystrybucjach kroki powinny być takie same, ale nie były sprawdzane. Różnice dla maszyny wirtualnej: [proxmox-vm.md](proxmox-vm.md).

Zanim zaczniesz, przygotuj dongle: [dongle-esp32-c5.md](dongle-esp32-c5.md).

## Instalacja jednym poleceniem

Po przygotowaniu systemu (punkt 1) i pobraniu projektu (punkt 3) całą resztę robi skrypt:

```bash
sudo ./host/install.sh --lan [interfejs]
```

- `--lan` — panel WWW dostępny z sieci lokalnej, z wylosowanym hasłem (zapisanym w `.env`); bez tej opcji panel działa tylko na samym urządzeniu;
- `interfejs` — interfejs LAN; domyślnie ten, przez który wychodzi trasa domyślna.

Skrypt wykonuje punkty 2 oraz 4–10 tej instrukcji i włącza codzienną kopię zapasową. Jest bezpieczny do ponownego uruchomienia: istniejących plików `.env` i `config/devices.yaml` nie zmienia, a sieci Thread, która już istnieje, nie tworzy od nowa. Wymaga, żeby w chwili uruchomienia podłączony był dokładnie jeden adapter szeregowy USB (dongle).

Ponowne uruchomienie na działającej instalacji było sprawdzone (Raspberry Pi 4B: konfiguracja, tożsamość urządzenia i sieć Thread bez zmian). Pełna instalacja tym skryptem na czystym systemie **nie była jeszcze sprawdzona**. Poniższe punkty opisują te same kroki wykonywane ręcznie i służą też do zrozumienia, co skrypt robi.

Sam krok tworzenia sieci Thread i startu usług można powtórzyć osobno: `sudo ./host/init-thread.sh`.

### Gotowe obrazy

`bridge` i `supla-device` są publikowane w GitHub Container Registry przez pipeline `.github/workflows/docker.yml` (amd64 i arm64, tag `latest` dla gałęzi `main`, numer wersji dla tagów `v*`). `docker compose pull bridge supla` pobiera je zamiast budować; wersję wybiera zmienna `M2S_VERSION` w `.env`. Budowa lokalna: `docker compose build bridge supla` albo `M2S_BUILD=1 sudo ./host/init-thread.sh`.

## 1. System

- System **64-bitowy** (Raspberry Pi OS 64-bit albo Ubuntu Server arm64) — obrazy kontenerów są arm64.
- Raspberry Pi podłączone do sieci LAN, najlepiej kablem. W sieci musi działać IPv6 na łączu (nie może być wyłączone w systemie); router IPv6 w LAN nie jest wymagany.
- Nie WSL2 i nie kontener z NAT-em: wszystkie usługi pracują w sieci hosta, bo Matter wymaga IPv6, multicastu i mDNS.

## 2. Docker

Raspberry Pi OS / Debian:

```bash
sudo apt update
sudo apt install docker.io docker-compose docker-buildx
```

Ubuntu:

```bash
sudo apt install docker.io docker-compose-v2 docker-buildx
```

Dalsze polecenia mają postać `docker compose …` (wtyczka Compose). Uruchamiaj je jako root lub użytkownik z dostępem do Dockera.

## 3. Pobranie projektu

```bash
git clone <adres-repozytorium> matter2supla
cd matter2supla
```

Wszystkie dalsze polecenia wykonuj w tym katalogu.

## 4. Ustawienia sieci hosta (sysctl)

Odczytaj nazwę interfejsu, którym Raspberry Pi jest w LAN — zwykle `eth0` lub `end0`, przy Wi-Fi `wlan0`:

```bash
ip -br link
```

Uruchom skrypt z tą nazwą:

```bash
sudo ./host/setup-host.sh eth0
```

Po co: border router przekazuje ruch między siecią Thread a LAN, więc host musi mieć włączony forwarding IPv6/IPv4, a mimo to nadal przyjmować ogłoszenia routerów (RA). Skrypt zapisuje `/etc/sysctl.d/60-otbr.conf` (`accept_ra = 2`, `accept_ra_rt_info_max_plen = 64`, `net.ipv6.conf.all.forwarding = 1`, `net.ipv4.ip_forward = 1`) i ostrzega, jeśli brakuje `/dev/net/tun`.

Zaobserwowane na RPi: NetworkManager ustawia `accept_ra=0` na swoim interfejsie mimo pliku sysctl, bo sam obsługuje RA. W sieci bez routera IPv6 nie przeszkadzało to w działaniu; zachowanie w sieci z routerem IPv6 nie było sprawdzane.

## 5. Wi-Fi i Bluetooth

Jeśli RPi jest podłączone kablem, wyłącz Wi-Fi — dwa interfejsy w tej samej podsieci utrudniają pracę mDNS i routing do sieci Thread:

```bash
sudo nmcli radio wifi off
```

Bluetooth jest potrzebny do parowania urządzeń bezpośrednio do własnej sieci Thread. Zainstaluj BlueZ i odblokuj adapter (na RPi bywał zablokowany programowo):

```bash
sudo apt install bluez
sudo rfkill unblock bluetooth
```

## 6. Stała nazwa portu dongla (udev)

Wepnij dongle (port USB 2.0, przez przedłużacz) i sprawdź identyfikatory mostka USB-UART:

```bash
lsusb
```

Plik `host/99-thread-rcp.rules.example` zawiera regułę dla mostka CH340 (`1a86:7523`). Jeśli Twoja płytka ma inny mostek, po skopiowaniu popraw `idVendor` i `idProduct`:

```bash
sudo cp host/99-thread-rcp.rules.example /etc/udev/rules.d/99-thread-rcp.rules
sudo udevadm control --reload && sudo udevadm trigger
ls -l /dev/thread-rcp
```

Po co: `/dev/ttyUSB0` może zmienić numer po restarcie lub wpięciu innego urządzenia; `/dev/thread-rcp` jest stałe i tej nazwy używa `docker-compose.yml`.

## 7. Plik `.env` i katalogi

```bash
cp .env.example .env
cp config/devices.example.yaml config/devices.yaml
mkdir -p data/otbr data/matter data/supla config/supla
sudo chown -R 1000:1000 data/matter data/supla config
```

Kontenery `matter-server`, `bridge` i `supla` pracują jako użytkownik o uid 1000 — bez `chown` nie zapiszą swoich danych.

Uzupełnij `.env`:

| Zmienna | Znaczenie |
| --- | --- |
| `INFRA_IF` | interfejs LAN z kroku 4 (w przykładzie jest `ens18` — na RPi **zmień**, np. na `eth0`) |
| `RCP_DEVICE` | port dongla, domyślnie `/dev/thread-rcp` |
| `RCP_BAUDRATE` | prędkość portu, domyślnie `460800` |
| `SUPLA_SERVER`, `SUPLA_MAIL` | opcjonalne; wygodniej wpisać konto w panelu (dane z panelu mają pierwszeństwo) |
| `M2S_WEB_PASSWORD` | hasło panelu; wymagane, gdy panel ma być dostępny z LAN ([panel-www.md](panel-www.md)) |
| `TZ` | strefa czasowa, domyślnie `Europe/Warsaw` |

Dla parowania przez Bluetooth dopisz do `.env` wiersz, który na stałe dołącza dodatek `docker-compose.bluetooth.yml`:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.bluetooth.yml
```

Dodatek przekazuje kontrolerowi Matter magistralę D-Bus hosta (`/run/dbus`), przez którą rozmawia z BlueZ. Bez tego wiersza trzeba by każde polecenie `docker compose` pisać z `-f docker-compose.yml -f docker-compose.bluetooth.yml`.

Plik `.env` zawiera sekrety i nie trafia do repozytorium.

## 8. Sieć Thread

Uruchom sam border router i utwórz nową sieć:

```bash
docker compose up -d otbr
docker exec otbr ot-ctl rcp version          # kontrola: border router widzi dongle
docker exec otbr ot-ctl dataset init new
docker exec otbr ot-ctl dataset commit active
docker exec otbr ot-ctl ifconfig up
docker exec otbr ot-ctl thread start
docker exec otbr ot-ctl state                # po chwili: leader
```

To robi się raz. Ustawienia sieci zapisują się w `data/otbr` i po restarcie border router sam wraca jako lider tej samej sieci (sprawdzone restartem RPi).

Jeśli `otbr` restartuje się w kółko — patrz [rozwiązywanie problemów](rozwiazywanie-problemow.md).

## 9. Start całego stosu

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f bridge supla
```

Pierwsze budowanie obrazu `supla` (kompilacja `supla-device`) trwa na Raspberry Pi wyraźnie dłużej niż na PC.

Czego się spodziewać:

- `supla` wypisuje „czekam na /etc/supla-device/supla-device.yaml” — to normalne, dopóki nie ma konta SUPLA i pierwszego kanału;
- `bridge` loguje połączenie z brokerem MQTT i z `matter-server` oraz adres panelu WWW;
- jeśli `matter-server` restartuje się z błędem `Unknown interface`, w `.env` jest zła nazwa `INFRA_IF`.

## 10. Dataset Thread dla kontrolera

Kontroler Matter musi znać dane sieci Thread, żeby przekazać je urządzeniu podczas parowania przez Bluetooth:

```bash
docker compose run --rm bridge set-thread-dataset "$(docker exec otbr ot-ctl dataset active -x | head -1)"
docker compose run --rm bridge nodes
```

Drugie polecenie powinno pokazać `dataset Thread ustawiony: True` oraz `Bluetooth: True`. Dataset można też wkleić w panelu (sekcja „Dataset sieci Thread”).

**Dataset to klucz do sieci Thread** — nie zapisuj go w repozytorium ani w notatkach dostępnych dla innych.

## 11. Dalej

1. [Panel WWW](panel-www.md) — dostęp, hasło, konto SUPLA.
2. [Parowanie urządzeń](parowanie.md).
3. [Kopie zapasowe](kopie-zapasowe.md) — włącz harmonogram od razu po pierwszym parowaniu.

## Aktualizacja

Po pobraniu nowej wersji repozytorium:

```bash
git pull
docker compose up -d --build
```

Dane w `data/` i `config/` zostają. Przed aktualizacją zrób kopię: `sudo ./host/backup.sh`. (Procedura aktualizacji między wersjami nie była osobno testowana.)

## Restart i autostart

Wszystkie usługi mają `restart: unless-stopped`, więc wracają po restarcie hosta bez ingerencji. Sprawdzone na RPi: po ok. minucie od startu działało wszystkie pięć usług, border router był liderem tej samej sieci, sparowane gniazdko było dostępne, a `supla-device` zarejestrowany w SUPLA Cloud.

## Zapisy na nośnik (karta SD, dysk)

Pomiar na działającej instalacji (RPi 4B, 2 urządzenia, 3 minuty): około 0,3 MB zapisów na 3 minuty poza jednorazowym pobraniem pliku aktualizacji firmware przez kontroler, czyli rząd 150 MB na dobę razem z systemem. Logi kontenerów to około 1 MB na dobę, a pliki w `data/` nie były w tym czasie zapisywane.

Co jest zrobione, żeby nie zużywać nośnika bez potrzeby:

- logi kontenerów mają limit rozmiaru (`docker-compose.yml`, 2 × 5 MB na usługę);
- broker MQTT nie zapisuje niczego na dysk (`persistence false`);
- łącznik zapisuje `config/` tylko przy zmianie listy urządzeń lub konta.

Katalogu `data/` **nie przenoś do pamięci RAM**: zawiera tożsamość kontrolera Matter, klucze sieci Thread i identyfikator urządzenia SUPLA. Po utracie zasilania trzeba by parować wszystko od nowa. Na karcie SD można dodatkowo ograniczyć zapisy systemu (np. dziennik systemowy w pamięci) — to ustawienia systemu, poza tym projektem, nieuruchamiane tutaj.


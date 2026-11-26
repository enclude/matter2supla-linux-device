# matter2supla-linux-device

Most **Matter over Thread → SUPLA** na Linuksie. Urządzenia Matter (na początek gniazdko IKEA GRILLPLATS) są widoczne w SUPLA Cloud jako kanały jednego wirtualnego urządzenia SUPLA, ze sterowaniem w obie strony.

```text
GRILLPLATS / KAJPLATS / czujniki
   │ Matter over Thread (802.15.4)
ESP32-C5 (firmware ot_rcp) ──USB── Proxmox VE ──passthrough── VM Ubuntu
                                                               ├─ otbr           OpenThread Border Router
                                                               ├─ matter-server  kontroler Matter (matterjs-server, WebSocket)
                                                               ├─ bridge         łącznik Matter ⇄ MQTT (ten projekt)
                                                               ├─ mqtt           Mosquitto
                                                               └─ supla          supla-device dla Linuksa ⇄ SUPLA Cloud
```

Własnym kodem jest tylko `bridge/` (Python) i konfiguracja. ESP32-C5 dostaje **niezmieniony** przykład `ot_rcp` z ESP-IDF — w repozytorium nie ma firmware dla ESP.

## Stan projektu

| Element | Stan |
| --- | --- |
| `bridge` — OnOff (stan, sterowanie, dostępność) | napisany i przetestowany bez sprzętu (szczegóły niżej) |
| obrazy Docker `bridge` i `supla-device` | budują się; uruchomione lokalnie |
| OTBR + dongle ESP32-C5, parowanie, prawdziwe gniazdko, SUPLA Cloud | **nieuruchamiane** — wymaga sprzętu i VM |

### Co zostało sprawdzone (2026-11-25, bez sprzętu, na maszynie deweloperskiej)

- 78 testów jednostkowych łącznika oraz test całości z prawdziwym brokerem Mosquitto.
- W zbudowanym obrazie `bridge`: odczyt kodu `MT:…` z wygenerowanego zdjęcia kodu QR oraz zapis konta SUPLA z panelu (przez API). Zdjęć prawdziwych naklejek ani aparatu telefonu nie sprawdzano.
- Stos z `docker-compose.yml` bez OTBR (`mqtt`, `matter-server` 1.6.1, `bridge`, `supla`) z węzłami testowymi zaimportowanymi do kontrolera:
  - gniazdko pojawiło się samo jako kanał 0, a `supla-device` sam wystartował z wygenerowaną konfiguracją;
  - czujnik i ściemniana żarówka trafiły na listę „bez kanału”, z diagnostyką do pobrania;
  - komenda i zmiana nazwy z panelu zadziałały, zmiana konfiguracji zrestartowała `supla-device`;
  - żądanie z obcej strony zostało odrzucone.
- `supla-device` z wygenerowaną konfiguracją: kanał reaguje na `{"state": 1}` / `{"state": 0}`, przechodzi w offline po `{"state": -1}` oraz po 120 s bez wiadomości.

Czego to **nie** potwierdza: kontroler nie wykonuje komend na węzłach testowych, a serwer SUPLA był fikcyjny — faktyczne przełączenie urządzenia, rejestracja w SUPLA Cloud, sterowanie z aplikacji i samo parowanie nie były sprawdzane. Panel WWW był testowany przez API; jego wygląd w przeglądarce nie był oglądany.

### Sprawdzone na sprzęcie (2026-11-25, VM Ubuntu 26.04 na Proxmox)

- Dongle ESP32-C5 (mostek CH340) po passthrough widoczny w VM jako `/dev/ttyUSB0`; OTBR łączy się z RCP przy 460800 bodów i tworzy sieć Thread (stan `leader`).
- Wymagana opcja `uart-init-deassert` w adresie radia (jest w `docker-compose.yml`): bez niej otwarcie portu wprowadza ESP w tryb programowania i `otbr-agent` kończy się błędem `spinel_driver … Init() Failure`.

- Restart VM: wszystkie kontenery wracają same, border router ponownie zostaje liderem tej samej sieci Thread.
- Bluetooth: adapter hosta Proxmox przekazany do VM (USB passthrough), BlueZ w VM, dodatek `docker-compose.bluetooth.yml` — kontroler zgłasza włączony Bluetooth i przyjmuje dataset Thread. Dodatek wymaga `apparmor=unconfined` (jest w pliku): bez tego AppArmor blokuje D-Bus i kontroler pada przy pierwszym parowaniu.
- **Parowanie się nie udało:** ten konkretny adapter nie odbierał żadnych pakietów Bluetooth (skan: zero urządzeń), więc kontroler nie znalazł gniazdka. Parowanie, rejestracja w SUPLA Cloud i sterowanie prawdziwym gniazdkiem pozostają niesprawdzone.
- Panel z LAN z logowaniem formularzem.

- Raspberry Pi 4B (2026-11-26): obrazy `bridge` i `supla-device` budują się na arm64, cały stos działa, border router z donglem jest liderem sieci Thread, kontroler ma włączony Bluetooth, a skan Bluetooth z RPi widzi urządzenia w otoczeniu.

- **Pierwszy kamień milowy w większości osiągnięty (2026-11-26):** GRILLPLATS sparowany kodem QR przez Bluetooth RPi do sieci Thread dongla; kanał 0 powstał sam; `supla-device` zarejestrował się w SUPLA Cloud; włącz/wyłącz z panelu przełącza gniazdko, a stan wraca z urządzenia i trafia do `supla-device` w ułamku sekundy.
- Parowanie przez Bluetooth RPi 4 bywa zawodne (połączenie udaje się w części prób) — nieudane parowanie wystarczy ponowić.

- Restart `supla-device` przy włączonym gniazdku nie wyłącza go (startowe `OFF` nie wychodzi, bo MQTT łączy się później — potwierdzone na gniazdku).
- Odłączenie gniazdka na krótko i ponowne włożenie: gniazdko wraca do sieci Thread samo, kontroler odnawia sesję po ok. 1–2 minutach; w tym czasie komendy kończą się błędem.

- Restart Raspberry Pi (2026-11-26): po ok. minucie od startu wszystkie usługi działają, border router jest liderem tej samej sieci Thread, gniazdko jest dostępne bez ponownego parowania i zachowuje stan, `supla-device` ponownie rejestruje się w SUPLA Cloud, sterowanie działa.

### Do sprawdzenia na sprzęcie

- Nazwy komend `On` / `Off` na prawdziwym urządzeniu.
- Czy SUPLA Cloud przyjmuje kanał dopisany do już zarejestrowanego urządzenia bez dodatkowych kroków (być może trzeba na chwilę włączyć rejestrację urządzeń).
- Test akceptacyjny: sterowanie z aplikacji SUPLA, zmiana przyciskiem, odłączenie gniazdka → offline, restart VM bez ponownego parowania.

## Wymagania

- Host docelowy: **Raspberry Pi** (64-bitowy system, wbudowany Bluetooth) — patrz „Raspberry Pi” niżej. Alternatywnie VM z Ubuntu na Proxmox VE (karta sieciowa zmostkowana z LAN, IPv6 włączone) — ten wariant był uruchamiany, ale wymaga działającego adaptera Bluetooth przekazanego do VM. Nie WSL2.
- ESP32-C5 N16R8 podłączony portem **UART** (mostek USB-UART) przez przedłużacz do portu USB 2.0.
- Docker z wtyczką Compose w VM.
- Konto SUPLA z włączonym dodawaniem urządzeń (cloud.supla.org → „Moja SUPLA” → rejestracja urządzeń).

## Instalacja krok po kroku

### 1. Firmware RCP na ESP32-C5

Wgranie kasuje dotychczasowy firmware płytki i jego parowania. Sprawdzona droga (2026-11-25, płytka ESP32-C5HR8 z mostkiem CH340) — build w Dockerze, bez instalowania ESP-IDF:

```bash
./host/build-rcp-docker.sh rcp-build      # ESP-IDF 5.5, wypisuje polecenia esptool do wgrania
```

Wgrywać można z dowolnego systemu, w którym płytka ma port szeregowy (w Windows: `COMx` po instalacji sterownika CH340, `pip install esptool`). Po wgraniu RCP odpowiada na UART z prędkością 460800 bodów.

Alternatywa z zainstalowanym ESP-IDF 5.5.x (nieuruchamiana): `./host/flash-rcp.sh /dev/ttyUSB0`.

### 2. Proxmox: przekazanie USB do VM

Na hoście Proxmox odczytaj identyfikator mostka USB-UART (`lsusb`) i przekaż urządzenie do VM:

```bash
qm set <VMID> -usb0 host=<vid>:<pid>     # dla mostka CH340: host=1a86:7523
```

W VM port pojawi się jako `/dev/ttyUSB0` albo `/dev/ttyACM0`. Nadaj mu stałą nazwę `/dev/thread-rcp` według `host/99-thread-rcp.rules.example`.

Uwagi dla Proxmox:
- Jeśli dla VM włączona jest zapora Proxmox, to jej conntrack działa na hoście Proxmox. Dokumentacja matterjs-server zaleca wtedy na **hipernadzorcy** `net.netfilter.nf_conntrack_udp_timeout_stream=3600` (inaczej urządzenia bateryjne gubią subskrypcje). Najprościej: zapora wyłączona dla tej VM.
- Ruch multicast IPv6 (mDNS, RA) musi przechodzić przez most `vmbr0`. Przy problemach z wykrywaniem urządzeń sprawdź `multicast_snooping` mostu (wskazówka niezweryfikowana na tym sprzęcie).

### Raspberry Pi zamiast VM

Kroki 3–6 są takie same, z różnicami (wariant uruchomiony 2026-11-26 na RPi 4B, Raspberry Pi OS / Debian 13 arm64):
- krok 2 (Proxmox) odpada — dongle wpinasz bezpośrednio w port USB 2.0 Raspberry Pi, przez przedłużacz;
- system 64-bitowy (Raspberry Pi OS 64-bit albo Ubuntu Server arm64), Docker: `sudo apt install docker.io docker-compose-v2 docker-buildx`;
- `INFRA_IF` to interfejs, którym RPi jest w LAN (`ip -br link`: zwykle `eth0` lub `end0`, przy Wi-Fi `wlan0`);
- Docker w Debianie: `sudo apt install docker.io docker-compose docker-buildx`;
- wbudowany Bluetooth bywa programowo zablokowany: `sudo rfkill unblock bluetooth`; Wi-Fi w tej samej podsieci co kabel najlepiej wyłączyć (`sudo nmcli radio wifi off`);
- Bluetooth jest wbudowany: `sudo apt install bluez`, a w `.env` dopisz `COMPOSE_FILE=docker-compose.yml:docker-compose.bluetooth.yml`;
- kompilacja `supla-device` na RPi trwa wyraźnie dłużej niż na PC.

### 3. Przygotowanie VM

```bash
ip -br link                              # nazwa interfejsu LAN, zwykle ens18
sudo ./host/setup-host.sh ens18          # sysctl wymagane przez OTBR
cp .env.example .env                     # uzupełnij INFRA_IF (konto SUPLA wpiszesz w panelu)
cp config/devices.example.yaml config/devices.yaml
mkdir -p data/otbr data/matter data/supla config/supla
sudo chown -R 1000:1000 data/matter data/supla config
```

### 4. Sieć Thread

```bash
docker compose up -d otbr
docker exec otbr ot-ctl dataset init new
docker exec otbr ot-ctl dataset commit active
docker exec otbr ot-ctl ifconfig up
docker exec otbr ot-ctl thread start
docker exec otbr ot-ctl state            # po chwili: leader
```

### 5. Start stosu

```bash
docker compose up -d --build
docker compose logs -f bridge supla
```

Jeśli `matter-server` restartuje się w kółko z błędem `Unknown interface`, w `.env` jest zła nazwa `INFRA_IF`.

### 6. Panel WWW i parowanie

Panel działa na `127.0.0.1:8099` w VM. Z własnego komputera:

```bash
ssh -L 8099:127.0.0.1:8099 <vm>      # potem http://localhost:8099
```

Dostęp bezpośrednio z LAN: w `config/devices.yaml` ustaw `web.host` na adres VM, a w `.env` hasło `M2S_WEB_PASSWORD` (bez hasła łącznik nie wystartuje z takim ustawieniem). Połączenie nie jest szyfrowane.

W panelu:
- **Konto SUPLA** — serwer (np. `svr12.supla.org`) i e-mail konta. Zapisywane w `config/supla/account.json` (poza repozytorium, uprawnienia 600); po zapisie `supla-device` startuje ponownie z nowymi danymi. Zmienne `SUPLA_SERVER` / `SUPLA_MAIL` w `.env` są tylko wartością początkową.
- **Parowanie** — wpisujesz kod urządzenia albo odczytujesz go ze zdjęcia kodu QR („Zrób zdjęcie kodu QR” / „Wybierz zdjęcie z galerii”). Zdjęcie jest dekodowane lokalnie w łączniku i nigdzie nie jest zapisywane. Żeby użyć aparatu telefonu, panel musi być dostępny z LAN (patrz wyżej).
  - *Bezpośrednio do własnej sieci Thread:* wymaga adaptera Bluetooth w VM (stos z `-f docker-compose.yml -f docker-compose.bluetooth.yml`) i zapisanego datasetu Thread (sekcja „Dataset sieci Thread”, wartość z `docker exec otbr ot-ctl dataset active -x`).
  - *Udostępnienie z aplikacji IKEA:* zaznacz „urządzenie jest już w sieci”; Bluetooth niepotrzebny, ale urządzenie zostaje w sieci Thread huba IKEA.
- **Kanały SUPLA** — sparowane gniazdko pojawia się samo jako kolejny kanał; `supla-device` restartuje się z nową konfiguracją. Są tu też przyciski włącz/wyłącz do testu.
- **Urządzenia bez kanału** — typy jeszcze nieobsługiwane (czujniki, ściemniane żarówki). Można je sparować i przyciskiem „Diagnostyka” pobrać zrzut atrybutów; na jego podstawie dopisuje się obsługę. Kanał SUPLA powstaje dopiero po dodaniu obsługi, bo kanału nie da się później zmienić ani usunąć.

To samo z wiersza poleceń:

```bash
docker compose run --rm bridge commission [--network-only]
docker compose run --rm bridge nodes
docker compose run --rm bridge diagnostics <node_id> > node.json
docker compose run --rm bridge set-thread-dataset "$(docker exec otbr ot-ctl dataset active -x | head -1)"
```

Po pierwszym połączeniu urządzenie „Matter2Supla” pojawi się w SUPLA Cloud; funkcję kanału (np. „włącznik zasilania”) ustaw w Cloud.

Dataset Thread i kody parowania są sekretami — nie zapisuj ich w repozytorium.

### Zrzut diagnostyczny

Zawiera atrybuty węzła i listę klastrów na endpointach. Domyślnie usuwane są numer seryjny, identyfikator egzemplarza, etykieta i klastry z danymi sieci oraz poświadczeniami (`--full` albo `?full=1` wyłącza to — takiego zrzutu nie publikuj). Format pasuje do komendy `import_test_node` kontrolera, więc zrzut można wczytać jako węzeł testowy i rozwijać obsługę bez urządzenia.

Automatyczne dopisywanie można wyłączyć (`auto_add: false` w `config/devices.yaml`); wtedy kanał dodaje się przyciskiem w panelu.

## Jak to działa

| Kierunek | Temat MQTT | Treść |
| --- | --- | --- |
| Matter → SUPLA | `m2s/<node>/<endpoint>/onoff/state` (retained) | `{"state": 1}`, `{"state": 0}`, `{"state": -1}` = niedostępne |
| SUPLA → Matter | `m2s/<node>/<endpoint>/onoff/set` | `ON` / `OFF` (także `TOGGLE`, `1`, `0`) |
| diagnostyka | `m2s/bridge/status` (retained, LWT) | `online` / `offline` |

Dostępność — kanał SUPLA przechodzi w offline, gdy:
- węzeł Matter jest nieosiągalny albo `matter-server` nie działa → łącznik publikuje `{"state": -1}`;
- łącznik został zatrzymany → przy zamykaniu publikuje `-1`;
- łącznik padł bez sprzątania → stan wygasa po `state_expiration_sec` (łącznik ponawia publikację co `heartbeat_sec`).

Stan zmienia się dopiero po potwierdzeniu z urządzenia; łącznik nie „zgaduje” wyniku komendy. Komendy z flagą retain są ignorowane.

## Trwałość kanałów SUPLA

Kolejność i typ kanałów w SUPLA są trwałe. **Wolno tylko dopisywać urządzenia z kolejnym `supla_channel`.** Usunięcie kanału lub zmiana typu kończy się błędem „Channel conflict” i koniecznością usunięcia urządzenia z SUPLA Cloud razem z historią. Łącznik pilnuje tego plikiem `config/supla/channels.lock.json` i odmawia wygenerowania niezgodnej konfiguracji. Wyparowane urządzenie zostaje jako kanał offline. Reset po świadomym usunięciu urządzenia z Cloud: `docker compose run --rm bridge gen-supla-config --force`.

## Sekrety i kopie zapasowe

Poza repozytorium (`.gitignore`): `.env`, `data/`, `backups/`, `config/devices.yaml`, `config/supla/`.

Usługi nasłuchują tylko na `127.0.0.1` (WebSocket i dashboard `matter-server` :5580, Mosquitto :1883, REST/WWW OTBR :8081/:8080); panel :8099 domyślnie też, a po ustawieniu `web.host` — z LAN z hasłem.

### Automatyczna kopia zapasowa

```bash
sudo ./host/install-backup-timer.sh daily 14     # codziennie 03:30, 14 ostatnich kopii (albo: weekly 8)
sudo ./host/backup.sh                            # kopia na żądanie
```

Kopia (`backups/m2s-backup-RRRRMMDD-GGMMSS.tar.gz`) zawiera `data/` (tożsamość kontrolera Matter z parowaniami, sieć Thread wraz z czytelnym datasetem, GUID/AUTHKEY SUPLA), `config/` i `.env`. Na czas pakowania (kilka sekund) zatrzymywane są `matter-server` i `supla`; gniazdko wraca po kilkunastu sekundach. Kopie można pobrać z panelu (sekcja „Kopie zapasowe”) — **zawierają sekrety**, a panel nie szyfruje połączenia, więc pobieraj je tylko w zaufanej sieci i trzymaj poza tym urządzeniem.

### Odtworzenie na innym urządzeniu

1. Docker, katalog projektu z repozytorium, `sudo ./host/setup-host.sh <interfejs>`, reguła udev dla dongla.
2. `sudo ./host/restore.sh /ścieżka/m2s-backup-….tar.gz`
3. Popraw `INFRA_IF` w `.env` i `web.host` w `config/devices.yaml`, jeśli interfejs lub adres IP są inne; `docker compose up -d --build`.

Nie uruchamiaj jednocześnie starej i odtworzonej instalacji — miałyby tę samą tożsamość w Matter, Thread i SUPLA. Przy innym donglu niż w chwili robienia kopii skrypt podpowie, jak przywrócić sieć Thread z zapisanego datasetu. Sprawdzone: tworzenie kopii, harmonogram, pobieranie z panelu i rozpakowanie kopii z porównaniem zawartości. **Pełne odtworzenie na drugim urządzeniu nie było wykonywane.**

## Rozwój

```bash
cd bridge
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
# test z prawdziwym brokerem:
docker run -d --rm -p 127.0.0.1:18883:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf
M2S_TEST_MQTT=127.0.0.1:18883 .venv/bin/pytest tests/test_e2e_mqtt.py
```

## Komponenty zewnętrzne

- [openthread/ot-br-posix](https://github.com/openthread/ot-br-posix) — obraz `openthread/border-router`
- [matter-js/matterjs-server](https://github.com/matter-js/matterjs-server) — następca `python-matter-server` (ten zakończono na wersji 8.1.2), to samo API WebSocket
- [SUPLA/supla-device](https://github.com/SUPLA/supla-device) — `extras/examples/linux`
- ESP-IDF, przykład `examples/openthread/ot_rcp`

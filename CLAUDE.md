# matter2supla-linux-device — brief dla agenta

Stan na 2026-11-25. Dokument zbiera ustalenia z wcześniejszej sesji; w repozytorium nie ma jeszcze żadnego kodu.
Przy każdej informacji zaznaczono, czy została **sprawdzona** (w źródłach/dokumentacji), czy pochodzi **z pamięci** i wymaga weryfikacji przed użyciem. Niczego z tego jeszcze nie uruchamiano.

## 0. Aktualizacja po drugiej sesji (2026-11-25) — ma pierwszeństwo przed resztą dokumentu

**Repozytorium jest publiczne.** Adresy, nazwy hostów, nazwy kluczy SSH i serwer konta SUPLA są tu zastąpione symbolami (`<adres-rpi>`, `<klucz-ssh>`, `svrXX` itd.); prawdziwe wartości są w lokalnej notatce agenta (pamięć projektu), nie w repo. Nie wpisuj ich do żadnego pliku w repozytorium.

Kroki 1–3 planu (pkt 12) są zrobione: szkielet repo, weryfikacja API, `bridge` z OnOff i testami. Szczegóły i lista „sprawdzone / do sprawdzenia na sprzęcie” — w `README.md`.

- **Host (decyzja użytkownika):** VM Ubuntu na Proxmox VE, ESP32-C5 przez USB passthrough. Nazwa interfejsu LAN w VM nadal do potwierdzenia (domyślnie przyjęto `ens18`, zmienna `INFRA_IF` w `.env`).
- **Kontroler Matter:** `python-matter-server` zakończono na 8.1.2; używamy następcy `ghcr.io/matter-js/matterjs-server:stable` (to samo API WebSocket, sprawdzone lokalnie na wersji 1.6.1). Kontener działa jako uid 1000 — `data/matter` musi do niego należeć. `apparmor=unconfined` nie jest już potrzebne; BLE przez D-Bus (`NOBLE_BINDINGS=dbus`), patrz `docker-compose.bluetooth.yml`.
- **API (sprawdzone w dokumentacji i na żywym serwerze):** `ws://localhost:5580/ws`; po połączeniu `server_info`; `start_listening` zwraca listę węzłów; zdarzenia `attribute_updated` = `[node_id, "ep/klaster/atrybut", wartość]`, `node_added`, `node_updated`, `node_removed`. Pierwszy raport węzła po restarcie serwera przychodzi w `node_updated`, nie w `attribute_updated`. Własny cienki klient w `bridge/src/m2s_bridge/matter_client.py` — bez biblioteki `python-matter-server[client]`.
- **supla-device:** budowany z przypiętego commita `main` (`supla-device/Dockerfile`), bo wydanie v26.9.2 nie ma `expiration_time_sec` dla źródła MQTT. Sprawdzone uruchomieniem: `offline_on_invalid_state: true` + `{"state": -1}` → kanał offline; wygasanie po 120 s działa; `channel_number` jest obsługiwany. `CustomRelay` **nie** obsługuje `default_function` (funkcję ustawia się w Cloud). Sekcja `mqtt` wymaga jawnego `use_ssl`.
- **Uwaga:** `CustomRelay` przy starcie wywołuje `turnOff` i próbuje opublikować `OFF`; w teście nie wyszło, bo MQTT łączy się później. Do potwierdzenia na sprzęcie, że restart `supla-device` nie wyłącza gniazdka.
- **OTBR:** dane w `/data/thread`; REST i WWW domyślnie na `127.0.0.1`; skrypt `setup-host` ustawia `accept_ra=2`, `accept_ra_rt_info_max_plen=64`, forwarding IPv6/IPv4 (odpowiednik: `host/setup-host.sh`). `cap_add: NET_ADMIN, NET_RAW` — z pamięci, do potwierdzenia.
- **Testy:** `cd bridge && pytest` (środowisko wirtualne trzymać poza `/mnt/c`, np. `uv venv /tmp/m2s-venv`); test z brokerem: `M2S_TEST_MQTT=host:port`.
- **Automatyczne kanały i panel WWW (decyzja użytkownika):** `bridge/src/m2s_bridge/manager.py` dopisuje nowe węzły z OnOff (bez LevelControl) jako kolejne kanały, zapisuje `config/devices.yaml` i generuje konfigurację SUPLA; kontener `supla` (`supla-device/entrypoint.sh`) restartuje się po zmianie pliku. Panel (`web.py`, `web_ui.html`) na `127.0.0.1:8099`: parowanie, dataset Thread, lista kanałów. Poza localhostem wymaga `M2S_WEB_PASSWORD`.
- **Urządzenia nieobsługiwane (decyzja użytkownika):** wolno je parować; nie dostają kanału SUPLA (kanał jest nieusuwalny), ale panel i `m2s-bridge diagnostics` dają zrzut atrybutów w formacie `import_test_node` — to punkt wyjścia do dopisania obsługi nowego typu. Ściemniane światła celowo czekają na obsługę jasności, żeby nie zająć kanału typem `CustomRelay`.
- Zły `INFRA_IF` → `matter-server` pada w pętli z `Unknown interface` (zaobserwowane).
- **Dongle gotowy (2026-11-25):** płytka to ESP32-C5HR8 rev. v1.0, flash 16 MB, PSRAM 8 MB, mostek USB-UART **CH340 (`1a86:7523`)**. Wgrano niezmieniony `ot_rcp` (ESP-IDF v5.5.6, build w obrazie `espressif/idf:release-v5.5`, skrypt `host/build-rcp-docker.sh`) przez COM3 w Windows; RCP odpowiada na zapytanie Spinel o wersję przy 460800 bodów. Stary firmware Matter2Supla skasowany.
- **VM docelowa (wdrożone 2026-11-25):** `root@<adres-vm>` (hostname `<nazwa-hosta-vm>`, Ubuntu 26.04, interfejs `ens18`, systemd-networkd, w LAN brak routera IPv6). Logowanie kluczem `~/.ssh/<klucz-ssh>` (jeden z pięciu kluczy WSL dopuszczonych na serwerze; `<klucz-ssh-projektu>` nie jest tam dodany). Projekt w `/opt/matter2supla`, kopiowany przez `tar | ssh` (bez `.git`, `.env`, `data/`, `config/devices.yaml`, `config/supla/`). Docker z pakietów Ubuntu (`docker.io`, `docker-compose-v2`, `docker-buildx`).
- **OTBR + dongle (sprawdzone na sprzęcie):** dongle to `/dev/ttyUSB0` → `/dev/thread-rcp` (reguła udev). Adres radia **musi** mieć `&uart-init-deassert` — bez tego otwarcie portu ustawia DTR/RTS, ESP wchodzi w tryb programowania i `otbr-agent` pada z `spinel_driver … Init() Failure`. Z opcją: `ot-ctl rcp version` zwraca wersję, sieć Thread utworzona, stan `leader`. `cap_add: NET_ADMIN, NET_RAW` wystarcza.
- **Panel — konto SUPLA i QR (decyzja użytkownika):** serwer i e-mail wpisuje się w panelu (`config/supla/account.json`, pierwszeństwo przed `.env`); kod parowania można odczytać ze zdjęcia kodu QR (`bridge/src/m2s_bridge/qr.py`, zxing-cpp + Pillow, dekodowanie po stronie łącznika, bo aparat przez `<input type=file>` działa bez HTTPS).
- **Parowanie: bezpośrednio przez Bluetooth (decyzja użytkownika, 2026-11-25).** Adapter BT hosta Proxmox (`0cf3:e009`, Qualcomm) przekazany do VM jako drugie urządzenie USB; na VM BlueZ, w `.env` `COMPOSE_FILE=docker-compose.yml:docker-compose.bluetooth.yml`. Kontroler zgłasza „Bluetooth enabled”, dataset Thread z OTBR zapisany. Użytkownik **nie zgadza się** na uruchamianie pośrednika Bluetooth (`matter-ble-proxy`) na swoim Windows.
- **Panel na VM:** `http://<adres-vm>:8099`, logowanie formularzem (sesja w ciasteczku, hasło w `/opt/matter2supla/.env`). Konto SUPLA wpisane przez użytkownika w panelu. Restart VM sprawdzony: stos i sieć Thread wracają same.
- **Zmiana hosta (decyzja użytkownika, 2026-11-25): VM na Proxmox porzucona, docelowo Raspberry Pi.** Powód praktyczny: adapter Bluetooth hosta Proxmox (`0cf3:e009`) po przekazaniu do VM działa programowo, ale nie odbiera żadnych pakietów (trzy skany, zero urządzeń — prawdopodobnie brak anten), więc parowanie kończyło się na „No commissionable device was discovered”. Na VM nic nie zostało sparowane, więc nie ma czego migrować; stos na VM został uruchomiony i nie był zatrzymywany. Na RPi: wbudowany Bluetooth, dongle wpięty bezpośrednio, interfejs LAN do odczytania (`eth0` / `end0` / `wlan0`), obrazy muszą być arm64. Budowa obrazów na arm64 nie była sprawdzana.
- **Parowanie GRILLPLATS (instrukcja IKEA):** reset = przytrzymanie ON/OFF ok. 10 s; po włożeniu do prądu tryb parowania przez 15 min (w części wersji po naciśnięciu ON/OFF). Kod QR z naklejki gniazdka czytnik w `qr.py` rozpoznaje poprawnie (sprawdzone na zdjęciu użytkownika).
- **Raspberry Pi (wdrażane 2026-11-26):** `root@<adres-rpi>` (hostname `<nazwa-hosta-rpi>`, RPi 4B, Raspberry Pi OS / Debian 13 arm64, NetworkManager, AppArmor nieaktywny, dysk SSD przez USB 3). Ten sam klucz `~/.ssh/<klucz-ssh>`. `eth0` (używany jako `INFRA_IF`), `wlan0` w tej samej podsieci. Docker z pakietów Debiana (`docker.io`, `docker-compose`, `docker-buildx`). Wbudowany Bluetooth był „soft blocked” (rfkill) — po odblokowaniu skan widzi urządzenia w otoczeniu. Projekt w `/opt/matter2supla`, w `.env` `COMPOSE_FILE` z dodatkiem Bluetooth i losowe hasło panelu; panel na `http://<adres-rpi>:8099`. Stan na 2026-11-26: budowa arm64 udana, wszystkie pięć usług działa, OTBR = `leader` (sieć `OpenThread-xxxx`, kanał 16), dataset zapisany w kontrolerze, Wi-Fi wyłączone na prośbę użytkownika (`nmcli radio wifi off`). **GRILLPLATS sparowany 2026-11-26** przez Bluetooth RPi do sieci Thread dongla: węzeł 2, endpoint 1, kanał SUPLA 0 (`grillplats_plug_2`) utworzony automatycznie, `supla-device` zarejestrowany na koncie użytkownika („Registered and ready”). Komenda z panelu → potwierdzenie z urządzenia ok. 25 ms → aktualizacja kanału w `supla-device` (sprawdzone). Uwaga: połączenie BLE RPi 4 ↔ gniazdko jest niestabilne (ręcznie 2/6 prób, błąd `le-connection-abort-by-local` / HCI 0x3e) — pierwsze parowanie padło po 3 próbach kontrolera, drugie się udało; przy kolejnych urządzeniach po prostu ponawiać. Niesprawdzone: sterowanie z aplikacji SUPLA, przycisk na gniazdku, odłączenie → offline, restart RPi, zachowanie przy restarcie `supla-device` z włączonym gniazdkiem.
- **Kopie zapasowe (prośba użytkownika, 2026-11-26):** `host/backup.sh` (tar `data/` + `config/` + `.env`, zapis datasetu Thread do `data/otbr/dataset-active.txt`, krótkie zatrzymanie `matter-server` i `supla`, rotacja), `host/install-backup-timer.sh` (timer systemd; na RPi codziennie 03:30, 14 kopii, katalog `/opt/matter2supla/backups`), `host/restore.sh`, pobieranie z panelu (`/api/backups/<nazwa>`). Sprawdzone na RPi poza pełnym odtworzeniem na drugim urządzeniu.
- **Potwierdzone na gniazdku (2026-11-26):** restart `supla-device` nie wyłącza włączonego gniazdka; po krótkim odłączeniu gniazdka kontroler odnawia sesję po ok. 1–2 min (do tego czasu komendy padają, a kanał nie przechodzi w offline, bo subskrypcja wygasa dopiero po 1 min 44 s).
- **Restart RPi sprawdzony (2026-11-26):** ok. 65 s po starcie wszystko działa bez ingerencji (5 usług, `leader` tej samej sieci, węzeł 2 dostępny, SUPLA „Registered and ready”, Bluetooth, Wi-Fi nadal wyłączone, timer kopii aktywny). NetworkManager ustawia `accept_ra=0` na `eth0` mimo pliku sysctl (obsługuje RA sam; w LAN nie ma routera IPv6). Z testu akceptacyjnego zostały: sterowanie z aplikacji SUPLA i przycisk na gniazdku (po stronie użytkownika) oraz odłączenie gniazdka na ponad 2 min → offline.
- **VM na Proxmox (`<adres-vm>`) wyłączona i usunięta przez użytkownika 2026-11-26** — wpisy o niej wyżej mają już tylko wartość historyczną; jedynym hostem jest Raspberry Pi.
- **Potwierdzone przez użytkownika w aplikacji SUPLA (2026-11-26):** naciśnięcie przycisku na gniazdku zmienia stan w aplikacji.
- **KAJPLATS E27 WS globe 1521lm sparowana 2026-11-26** (węzeł 3, PID 36867, fw 1.1.0) — za pierwszym podejściem, zdjęciem kodu QR z telefonu przez panel. Endpoint 1, typ urządzenia 268: OnOff, LevelControl (`1/8/0` CurrentLevel, zakres 1–254), ColorControl w trybie temperatury barwowej (`1/768/7` w miredach, zakres 153–454). Celowo bez kanału SUPLA do czasu obsługi jasności.
- **GRILLPLATS udostępnia pomiary** (rozstrzyga wątpliwość z pkt 7): endpoint 2, typ 1296, klastry ElectricalPowerMeasurement `144` (m.in. napięcie `2/144/4` i `2/144/11` w mV, prąd `2/144/5` i `2/144/12` w mA, moc czynna `2/144/8` w mW) i ElectricalEnergyMeasurement `145` (energia narastająco `2/145/1` = struktura EnergyMeasurementStruct: pole `0` to energia w mWh, pole `4` to EndSystime — milisekundy od startu urządzenia, nie energia; w zrzucie `{"0": 0, "4": 3253}` oznacza więc 0 mWh). Jednostki sprawdzone w definicjach klastrów 0x0090/0x0091 (connectedhomeip, matter.js). Częstotliwość `2/144/14` powinna być w mHz, a gniazdko (fw 1.4.6) zgłasza `50` — wartość niezgodna ze specyfikacją, łącznik jej nie przekazuje.
- **Licznik energii w łączniku (2026-11-26):** typ `meter` (`bridge/src/m2s_bridge/meter.py`), osobny kanał `ElectricityMeterParsed`, stan JSON na `<prefix>/<node>/<endpoint>/meter/state` (V, A, W, kWh), bez tematu `set`; lista pomiarów zapisana w `devices.yaml` (`measurements`). Sprawdzone lokalnie z supla-device (bez serwera SUPLA): kanał przyjmuje dane („go online”). **`ElectricityMeterParsed` nie przechodzi w offline** po wygaśnięciu lub pustym stanie — zgłasza tylko „data source error” i oznacza pomiary jako niedostępne; offline jest dopiero po restarcie supla-device albo rozłączeniu brokera. Pełne offline wymaga własnego rozszerzenia supla-device (jak MatterLight). Na sprzęcie i w aplikacji SUPLA niesprawdzone.
- Zrzuty diagnostyczne obu urządzeń (bez numerów seryjnych i danych sieci) leżą w `bridge/tests/fixtures/` — materiał do testów nowych typów; jeszcze niecommitowane.
- **Światła — kanał DIMMER_CCT (2026-11-26, decyzja użytkownika: pełne sterowanie z aplikacji SUPLA).** `supla-device` dla Linuksa ma dla świateł tylko atrapę (`RgbCctParsed`, pusta `setRGBCCTValueOnDevice`), więc powstało własne rozszerzenie `supla-device/extensions/matter_light` (typ `MatterLight`, podklasa `LightingPwmBase`, funkcje DIMMER i DIMMER_CCT, protokół 28). Do światła trafiają wyłącznie polecenia z `handleNewValueFromServer`; stan z urządzenia wraca przez `setRGBCCT(..., instant)` z 3-sekundowym wstrzymaniem po poleceniu. Tematy tekstowe `S B T` / `B T` (opis w `core.py`). Typ łącznika `light_cct` (`light.py`). Sprawdzone: testowe urządzenie zarejestrowało się na `svrXX` w protokole 28, Cloud pokazuje ściemniacz CCT, polecenie z Cloud (39/21) dotarło do żarówki w ok. 0,5 s. Żarówka jest kanałem 1 właściwego urządzenia; testowe urządzenie usunięte. Komendy Matter potwierdzone na żarówce: `MoveToLevelWithOnOff` i `MoveToColorTemperature` wymagają `optionsMask`/`optionsOverride` (liczby), bit 1 = wykonaj przy zgaszonym.
- **Pułapka `qos` w supla-device:** liczba z YAML (`output.qos`) i trzeci argument `LinuxMqttClient::publish` to flagi MQTT-C, nie numer QoS — `1` znaczy „retain”. Generator używa `qos: 2` (faktyczne QoS 1), rozszerzenie `MQTT_PUBLISH_QOS_1`. Łącznik i tak ignoruje polecenia z flagą retain.
- **Licznik energii (subagent, 2026-11-26):** typ `meter` (`meter.py`), kanał `ElectricityMeterParsed`, pole `measurements` w `devices.yaml`. Sprawdzony lokalnie z supla-device i **wdrożony na RPi 2026-11-26 jako kanał 2** (`grillplats_plug_2_meter`): łącznik publikuje realne pomiary (ok. 237 V, 0,1 A, 15 W), `supla-device` przyjął kanał; wygląd w aplikacji SUPLA i decyzja o rozszerzeniu (offline, jedna faza) — po testach użytkownika. Ograniczenia standardowego typu: brak offline przy braku danych, kanał trójfazowy z pustymi fazami. Po wdrożeniu łącznika licznik dopisze się sam jako kolejny kanał.
- **Zapisy na nośnik (pomiar na RPi):** ok. 150 MB/dobę razem z systemem, logi kontenerów ok. 1 MB/dobę, `data/` bez zapisów w stanie spoczynku; nośnikiem jest SSD na USB, nie karta SD. Decyzja: nie przenosić `data/` do tmpfs; dodano limit logów w compose. Kontroler sam pobrał plik OTA dla żarówki do `data/matter/ota`.
- **Dokumentacja:** `docs/` (11 plików) i skrócone `README.md` napisał subagent; poleceń nie uruchamiał.
- **Licznik jednofazowy (prośba użytkownika, 2026-11-26):** rozszerzenie `supla-device/extensions/matter_meter` (typ `MatterMeter` = `ElectricityMeterParsed` z flagami `PHASE2/3_UNSUPPORTED` i offline przy nieważnych danych). Blokada kanałów traktuje `ElectricityMeterParsed` i `MatterMeter` jako ten sam typ. Wdrożone na RPi: rejestracja bez konfliktu kanałów. Użytkownik potwierdził, że licznik w aplikacji wygląda dobrze (jeszcze w wersji trójfazowej); czy serwer zdejmie fazy 2/3 z już zarejestrowanego kanału — do potwierdzenia.
- **Nazwy w panelu (prośba użytkownika):** nazwa licznika = „<nazwa gniazdka> — licznik” (`link_meter_captions` w `manager.py`), bez osobnej edycji; w panelu licznik stoi z wcięciem pod swoim gniazdkiem. Zmiana nazwy w SUPLA Cloud nie wraca do panelu (i odwrotnie poza pierwszą rejestracją).
- **Instalacja jednym poleceniem (prośba użytkownika, 2026-11-26):** `host/install.sh [--lan] [interfejs]` (Docker, sysctl, udev z wykryciem dongla, Bluetooth, `.env`, katalogi, timer kopii) i `host/init-thread.sh` (sieć Thread tylko gdy jej nie ma, start usług, dataset dla kontrolera). Uruchomione ponownie na działającym RPi: `.env`, `devices.yaml`, `guid_auth.yaml`, blokada kanałów i dataset Thread bez zmian, usługi wróciły. Czysta instalacja nadal niesprawdzona.
- **Obrazy w GHCR (prośba użytkownika):** `.github/workflows/docker.yml` — testy, budowa `bridge` i `supla-device` na natywnych runnerach amd64/arm64, wspólny tag przez `imagetools`. Compose ma `image: ghcr.io/enclude/matter2supla-{bridge,supla-device}:${M2S_VERSION:-latest}` obok `build:`. Pipeline jeszcze nie był uruchomiony.
- **Commity:** na prośbę użytkownika bez stopek o Claude.
- **Licencja (decyzja użytkownika):** GPL-2.0-or-later, autor „Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>”; nagłówki SPDX w plikach źródłowych, nowe pliki dostają takie same.
- **Wydania i aktualizacja (prośba użytkownika, 2026-11-26):** tag `vX.Y.Z` → obrazy `X.Y.Z` + `latest` + GitHub Release; `main` → tylko `edge`. Wersja w obrazach (`M2S_BUILD_VERSION`; `M2S_VERSION` w `.env` to tag obrazu). Panel raz na dobę pyta API GitHuba o najnowsze wydanie (`version.py`, wyłącznik `update_check`) i tylko informuje. Aktualizacja wyłącznie ręcznie: `sudo ./host/update.sh` (kopia do osobnego `backups-update/`, wycofanie przy błędzie). Sprawdzone na RPi: lokalne obrazy → 0.2.0 → 0.2.1, powiadomienie w panelu. Nowe wydanie wystawia się dopiero po wyraźnej zgodzie użytkownika. Opis: `docs/aktualizacja.md`.
- **Sesje panelu:** zapisane w `config/web-sessions.json` (skróty), 180 dni z przedłużaniem; restart łącznika nie wylogowuje (sprawdzone na RPi).
- **Webflasher (prośba użytkownika):** katalog `webflasher/` (ESP Web Tools + `ot_rcp` dla C5/C6/H2, warianty UART i USB), publicznie pod `https://matter2supla.zjawa.tech`. Strona działa; użytkownik wgrał przez nią firmware na ESP32-C5 (UART) - płytka startuje; praca tej płytki z OTBR oraz warianty C6/H2/USB niesprawdzone. Obraz budowany na hoście strony, nie w pipeline.
- **Dokumentacja bez długich łączników** (prośba użytkownika): w `README.md`, `docs/` i na stronie webflashera używać zwykłego „-”.
- **Grupy świateł (prośba użytkownika, 2026-11-26):** typ `light_group` w `devices.yaml` (`members`, bez własnego węzła), dla SUPLA zwykły `MatterLight`, tematy `<prefix>/group/<nazwa>/...`. Pojedyncze światła jako osobne kanały działają jak dotąd. Formularz parowania ma wybór „po sparowaniu” (osobny kanał / do grupy / bez kanału); lista `no_channel` trzyma endpointy pomijane przez `auto_add`. Kanał SUPLA grupy powstaje dopiero z pierwszym światłem (pusta grupa nie ma `supla_channel`). **Grupa jest jednorodna (wymóg użytkownika):** należą do niej tylko urządzenia tego samego rodzaju. Dziś jedyny rodzaj to ściemniacz z regulacją bieli (`light_cct`); gdy dojdą kolejne (sam ściemniacz, RGB), każdy dostaje własny rodzaj grupy z własnym typem kanału, a mieszanie rodzajów w jednej grupie ma być odrzucane (`_check_members`). Natywne grupy Matter celowo nieużywane. Sprawdzone testami i na atrapie panelu; na prawdziwych żarówkach jeszcze nie.
- **Potwierdzone przez użytkownika (2026-11-26):** sterowanie gniazdkiem z aplikacji SUPLA działa; po odłączeniu gniazdka kanał przechodzi w SUPLA w offline po ok. 120 s. Test akceptacyjny z pkt 12.5 jest tym samym zamknięty. Wgrywanie firmware przez stronę webflashera działa na ESP32-C5.
- **Zakładka „Logi” (prośba użytkownika, 2026-11-26):** `logs.py` - dziennik łącznika z pamięci i koniec pliku dziennika kontrolera (`LOG_FILE=/data/logs/matter-server.log`; łącznik ma `data/matter` podłączone tylko do odczytu jako `/matter-data`). `backup.sh` pomija `data/matter/logs`.
- **Zakładka Kanały (prośba użytkownika, 2026-11-26, wzorowana na zigbee2mqtt):** zwarte wiersze z rozwijanymi szczegółami, światła grupy pod grupą, filtr, „ostatnio widziane”, przycisk „Znajdź” (klaster Identify, także dla całej grupy - potwierdzone na żarówkach GU10). Zasięg Thread i usuwanie urządzeń odłożone.
- **Sparowane 2026-11-26:** cztery KAJPLATS GU10 WS 575lm (węzły 8-11) do grupy na kanale 3 oraz drugie gniazdko GRILLPLATS (węzeł 13, kanały 4 i 5). Po fabrycznym resecie urządzenia rozgłaszają się pod nowym adresem Bluetooth - kontroler najpierw próbuje starych (błąd „interface not found in proxy object”), potem znajduje nowe.
- **Nazwy są lokalne (decyzja użytkownika, 2026-11-26):** panel nie pobiera nazw z SUPLA Cloud i ich tam nie wysyła. Zmiana nazwy zapisuje tylko `devices.yaml` (bez przebudowy pliku SUPLA i restartu supla-device); do SUPLA trafia jako `initial_caption` przy pierwszej rejestracji kanału, np. po zmianie konta lub serwera. Endpointy bez kanału mają nazwy w liście `names`.
- Otwarte pytania z pkt 11: 1–3 rozstrzygnięte (jak wyżej); nr 4 (inny border router Thread w domu) nadal otwarty.

## 1. Cel

Most **Matter over Thread → SUPLA** działający na Linuksie: urządzenia Matter mają być widoczne w SUPLA Cloud jako wirtualne kanały jednego urządzenia SUPLA, łącznie ze sterowaniem (SUPLA → Matter).

Pierwszy kamień milowy: **gniazdko IKEA GRILLPLATS — włącz/wyłącz z aplikacji SUPLA i stan zwrotny**.
Dalej (w tej kolejności): pomiar energii gniazdka, żarówki KAJPLATS (włącz/wyłącz → jasność → barwa/kolor), czujniki IKEA.

## 2. Skąd ten projekt (kontekst)

- Punkt wyjścia: projekt `krzychunh/Matter2Supla` — firmware bramki na ESP32-C5. Obsługiwał tylko czujniki (odczyt), bez sterowania.
- 2026-11-25 autor wycofał źródła z GitHuba (repo zwraca 404); zostało `krzychunh/Matter2Supla-Releases` z samymi binarkami S7.18. Forków i kopii w archiwach (Wayback, Software Heritage, Sourcegraph) nie ma — sprawdzone.
- Kod tamtego projektu nie miał licencji. **Nie odtwarzaj go ani nie kopiuj jego fragmentów.** Ten projekt powstaje od zera na otwartych komponentach.
- Decyzja użytkownika: zamiast firmware na ESP32 — rozwiązanie na Linuksie, a posiadana płytka ESP32-C5 N16R8 pracuje jako dongle USB (radio Thread).

## 3. Architektura

```text
GRILLPLATS / KAJPLATS / czujniki
   │ Matter over Thread (802.15.4)
ESP32-C5 (firmware ot_rcp) ──USB/UART── host Linux
                                         ├─ otbr           OpenThread Border Router
                                         ├─ matter-server  kontroler Matter (WebSocket)
                                         ├─ bridge         łącznik Matter ⇄ MQTT   ← DO NAPISANIA
                                         ├─ mqtt           Mosquitto
                                         └─ supla          supla-device dla Linuksa ⇄ SUPLA Cloud
```

Wszystkie kontenery w `network_mode: host` (Matter wymaga IPv6, multicastu i mDNS).
Do napisania jest wyłącznie `bridge` oraz konfiguracja; reszta to gotowe projekty.

## 4. Sprzęt i host

- **ESP32-C5 N16R8** jako Thread RCP. Sprawdzone: przykład `examples/openthread/ot_rcp` w ESP-IDF `release/v5.5` ma ESP32-C5 na liście obsługiwanych układów; domyślnie UART0, **460800 bodów**, Wi-Fi/koegzystencja wyłączone. Alternatywnie opcja `CONFIG_OPENTHREAD_RCP_USB_SERIAL_JTAG` (natywny port USB).
- Kabel do portu **UART** płytki (mostek USB-UART). Przedłużacz USB, port USB 2.0 (USB 3.0 zakłóca 2,4 GHz).
- Wgranie `ot_rcp` kasuje dotychczasowy firmware Matter2Supla i jego parowania.
- **Host: prawdziwy Linux** (Debian/Ubuntu, RPi, mini-PC lub VM z mostkowaną kartą) w tej samej sieci LAN. **Nie WSL2** (NAT psuje mDNS/IPv6). Maszyna użytkownika, na której działa Claude Code, to WSL2 — nadaje się do pisania kodu, nie do uruchomienia stosu.
- Bluetooth w hoście potrzebny tylko przy parowaniu bezpośrednim (patrz 8).

### Wgranie firmware RCP

```bash
source ~/esp/esp-idf/export.sh            # ESP-IDF 5.5.x
cd $IDF_PATH/examples/openthread/ot_rcp
idf.py set-target esp32c5 && idf.py build
idf.py -p /dev/ttyUSB0 erase-flash flash
```

## 5. Komponenty

| Rola | Projekt | Stan wiedzy |
| --- | --- | --- |
| Border router | `openthread/ot-br-posix`, obraz `openthread/border-router` (pliki w `etc/docker/border-router/`: `otbr-env.list`, `setup-host`) | Sprawdzone: obraz istnieje, zmienne `OT_RCP_DEVICE`, `OT_INFRA_IF` (domyślnie `wlan0`), `OT_THREAD_IF` (`wpan0`), `OT_LOG_LEVEL`, REST na `127.0.0.1:8081`. Uprawnienia kontenera i ścieżka danych — do potwierdzenia w README obrazu |
| Kontroler Matter | `ghcr.io/matter-js/python-matter-server:stable` | Sprawdzone: obraz i uruchomienie (`--network=host`, `apparmor=unconfined`, wolumen `/data`; do BLE dodatkowo `/run/dbus` i `--bluetooth-adapter 0`). API WebSocket — z pamięci, patrz 7 |
| Broker | `eclipse-mosquitto:2` | standard |
| SUPLA | `SUPLA/supla-device`, katalog `extras/examples/linux` (binarka `supla-device-linux`) | Sprawdzone w README, patrz 6 |

Szkic `docker-compose.yml` (do dopracowania, nieuruchamiany):

```yaml
services:
  otbr:
    image: openthread/border-router:latest
    network_mode: host
    cap_add: [NET_ADMIN]
    devices: ["/dev/thread-rcp:/dev/ttyUSB0", "/dev/net/tun"]
    environment:
      OT_RCP_DEVICE: "spinel+hdlc+uart:///dev/ttyUSB0?uart-baudrate=460800"
      OT_INFRA_IF: "eth0"        # interfejs LAN hosta — do ustalenia
      OT_THREAD_IF: "wpan0"
    volumes: ["./data/otbr:/data"]
    restart: unless-stopped
  matter-server:
    image: ghcr.io/matter-js/python-matter-server:stable
    network_mode: host
    security_opt: ["apparmor=unconfined"]
    volumes: ["./data/matter:/data"]
    restart: unless-stopped
  mqtt:
    image: eclipse-mosquitto:2
    network_mode: host
    volumes: ["./config/mosquitto:/mosquitto/config"]
    restart: unless-stopped
  supla:
    build: ./supla-device
    network_mode: host
    volumes: ["./config/supla:/etc/supla-device", "./data/supla:/var/lib/supla-device"]
    restart: unless-stopped
  bridge:
    build: ./bridge
    network_mode: host
    restart: unless-stopped
```

Stała nazwa portu: reguła udev z `SYMLINK+="thread-rcp"` (idVendor/idProduct z `lsusb`).

## 6. supla-device dla Linuksa (sprawdzone w README)

- Budowanie: `apt install git libssl-dev libcurl4-openssl-dev build-essential libyaml-cpp-dev cmake`, potem `cmake .. && make` w `extras/examples/linux/build`.
- Konfiguracja w YAML: `server`, `mail`, sekcja brokera MQTT, lista `channels`.
- **Kolejność kanałów jest trwała.** Pierwszy kanał = numer 0. Nie wolno zmieniać kolejności, typu ani usuwać kanału — kończy się „Channel conflict” i koniecznością usunięcia urządzenia z SUPLA Cloud razem z historią. Wolno tylko dopisywać na końcu.
- GUID/AUTHKEY i stan trzymane w plikach (`state_files_path`) — muszą być na trwałym wolumenie i w kopii zapasowej.
- Przydatne typy kanałów: `CustomRelay`, `VirtualRelay`, `CmdRelay`, `RgbCctParsed`, `ThermHygroMeterParsed`, `ThermometerParsed`, `BinaryParsed`, `ElectricityMeterParsed`, `GeneralPurposeMeasurementParsed`, `ActionTriggerParsed`. Kanały `Parsed` mają pola baterii (`battery_level` 0–100, `multiplier_battery_level`).
- Źródła (`source`): `File`, `Cmd`, `MQTT` (`state_topic`), `HTTP`. Wyjścia (`output`): `File`, `Cmd`, `MQTT` (`control_topic`, opcjonalnie `qos`). Parsery/payloady: `Simple`, `Json`.

Wzorzec kanału dla gniazdka (na podstawie przykładu z README):

```yaml
- type: CustomRelay
  name: grillplats_1
  state: state                 # klucz w JSON ze stanem
  turn_on_payload: "ON"
  turn_off_payload: "OFF"
  source:
    type: MQTT
    state_topic: "m2s/<node>/<endpoint>/onoff/state"   # bridge publikuje {"state": 1}
  parser:
    type: Json
    refresh_time_ms: 1000
  output:
    type: MQTT
    control_topic: "m2s/<node>/<endpoint>/onoff/set"   # bridge odbiera ON/OFF
  payload:
    type: Simple
```

Do sprawdzenia w README: dokładne wartości akceptowane w `state` / `state_on_values`, `offline_on_invalid_state`, `initial_state`.

## 7. Łącznik `bridge` (do napisania)

Proponowany Python (asyncio), klient WebSocket do `matter-server` + klient MQTT.

Zadania:
1. Połączyć się z `matter-server` (z pamięci: `ws://localhost:5580/ws`, komenda `start_listening`, zdarzenia `attribute_updated`, komenda `device_command`; istnieje biblioteka kliencka `python-matter-server[client]`). **Zweryfikować w dokumentacji repozytorium przed pisaniem.**
2. Matter → MQTT: zmiana atrybutu → publikacja na temat stanu (retained).
3. MQTT → Matter: komunikat na temacie `set` → komenda klastra.
4. Dostępność: gdy węzeł jest nieosiągalny, publikować stan, po którym kanał SUPLA przejdzie w offline (nie zostawiać ostatniej wartości bez końca).
5. Mapowanie urządzeń w pliku konfiguracyjnym (node, endpoint, typ, numer kanału SUPLA); z niego generować fragment `supla-device.yaml`, zawsze dopisując na końcu.

Mapowanie klastrów Matter (standard Matter; identyfikatory z pamięci, endpointy odczytać z urządzenia):

| Funkcja | Klaster | Szczegóły |
| --- | --- | --- |
| Włącz/wyłącz | OnOff `0x0006` | atrybut `OnOff` `0x0000` (bool); komendy `Off` `0x00`, `On` `0x01`, `Toggle` `0x02` |
| Jasność | LevelControl `0x0008` | `CurrentLevel`, komenda `MoveToLevel` |
| Barwa/kolor | ColorControl `0x0300` | temperatura barwowa w miredach, kolor HS/XY |
| Moc | ElectricalPowerMeasurement `0x0090` | nie potwierdzono, czy GRILLPLATS go udostępnia |
| Energia | ElectricalEnergyMeasurement `0x0091` | jak wyżej |
| Temperatura | `0x0402` | wartość /100 °C; `-32768` = brak |
| Wilgotność | `0x0405` | wartość /100 % |
| Ruch | OccupancySensing `0x0406` | bit 0 |
| Kontakt / zalanie | BooleanState `0x0045` | kontakt: `true` = zamknięte; zalanie: `true` = wykryto |
| CO2 / PM2.5 | `0x040D` / `0x042A` | float |
| Bateria | PowerSource `0x002F`, atrybut `0x000C` | 0–200, jednostka 0,5 % |
| Identyfikacja | BasicInformation `0x0028` | VID = atrybut 2, PID = atrybut 4, endpoint 0 |

## 8. Parowanie

Dwie drogi — **decyzja użytkownika, jeszcze nie podjęta**:

- **Bezpośrednio do własnej sieci Thread**: adapter Bluetooth w hoście, w `matter-server` ustawić dataset Thread, parować kodem z urządzenia. Tylko wtedy dongle ESP32-C5 faktycznie obsługuje urządzenia.
- **Udostępnienie z aplikacji IKEA** (multi-admin): bez Bluetootha, ale urządzenie zostaje w sieci Thread huba IKEA i dongle nie jest potrzebny.

Utworzenie sieci Thread: `docker exec otbr ot-ctl dataset init new` → `dataset commit active` → `ifconfig up` → `thread start`; `ot-ctl state` ma zwrócić `leader`. Dataset: `ot-ctl dataset active -x`.

## 9. Identyfikatory IKEA (sprawdzone w rejestrze CSA DCL, VID 4476 / 0x117C)

Źródło: `https://on.dcl.csa-iot.org/dcl/model/models/4476`

| PID | Urządzenie |
| --- | --- |
| 4096 (0x1000) | **GRILLPLATS plug** (E2491) |
| 4097, 4098 | TOFSMYGGA plug zewnętrzny (NA, EU) |
| 36864–36868 | KAJPLATS WS (biel regulowana): E27 470 lm, E27 1055 lm, GU10 575 lm, E27 1521 lm, E14 806 lm |
| 36869–36871 | KAJPLATS CWS (kolor): E27 1055 lm, E14 806 lm, GU10 575 lm |
| 36872+ | kolejne KAJPLATS — lista była ucięta, pobrać ponownie z DCL |
| 32773 | TIMMERFLOTTE (temperatura/wilgotność) |
| 32774 | KLIPPBOK (zalanie) |
| 32775, 12290 | MYGGBETT (drzwi/okno) |
| 12288, 12291 | MYGGSPRAY (ruch) |
| 12289, 12292 | ALPSTUGA (jakość powietrza) |
| 32768, 32769, 8192, 8193 | BILRESA (piloty) |
| 20480 | LILJEBAGGE (głowica termostatyczna) |
| 32771 | DIRIGERA (hub) |

## 10. Zasady

- **Sekrety poza repozytorium**: dataset Thread, dane fabric Matter (`data/matter`), GUID/AUTHKEY SUPLA, e-mail konta SUPLA, kody parowania `MT:…`. Katalog `data/` i pliki `.env` do `.gitignore`.
- Kanały SUPLA tylko dopisywane na końcu (patrz 6).
- Nie wystawiać do Internetu: REST OTBR, WebSocket `matter-server`, brokera MQTT.
- Raportować uczciwie, co zostało uruchomione i przetestowane na sprzęcie, a co tylko napisane.

## 11. Otwarte pytania do użytkownika

1. Na jakiej maszynie stanie stos (RPi / serwer / VM) i jak nazywa się jej interfejs LAN?
2. Parowanie bezpośrednie (potrzebny Bluetooth w hoście) czy udostępnianie z aplikacji IKEA?
3. Serwer SUPLA konta (np. `svrXX.supla.org`) i e-mail — podać dopiero przy konfiguracji, nie zapisywać w repo.
4. Czy w domu działa już inny border router Thread (DIRIGERA, Apple TV, Nest)?

## 12. Plan

1. Szkielet repo: `docker-compose.yml`, `bridge/`, `supla-device/Dockerfile`, `config/`, `.gitignore`, README z instrukcją.
2. Weryfikacja API `matter-server` i opcji `CustomRelay` w dokumentacji; poprawienie punktów oznaczonych „z pamięci”.
3. `bridge` z obsługą OnOff + testy jednostkowe z atrapą WebSocket/MQTT (da się zrobić bez sprzętu).
4. Uruchomienie na hoście: RCP → OTBR (`leader`) → `matter-server` → parowanie GRILLPLATS.
5. Test akceptacyjny: włączenie i wyłączenie z aplikacji SUPLA; zmiana przyciskiem na gniazdku widoczna w SUPLA; odłączenie gniazdka → kanał offline; restart hosta → wszystko wraca bez ponownego parowania.
6. Kolejne funkcje wg punktu 1.

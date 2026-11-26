# Jak to działa

## Usługi

| Usługa (`docker compose`) | Obraz | Rola |
| --- | --- | --- |
| `otbr` | `openthread/border-router` | border router Thread; rozmawia z donglem przez port szeregowy |
| `matter-server` | `ghcr.io/matter-js/matterjs-server:stable` | kontroler Matter z API WebSocket |
| `mqtt` | `eclipse-mosquitto:2` | broker dla lokalnych usług stosu |
| `bridge` | budowany z `bridge/` | łącznik Matter ⇄ MQTT, generator konfiguracji SUPLA, panel WWW |
| `supla` | budowany z `supla-device/` | `supla-device` dla Linuksa — wirtualne urządzenie w SUPLA Cloud |

Przepływ: urządzenie Matter ⇄ `matter-server` ⇄ `bridge` ⇄ MQTT ⇄ `supla` ⇄ SUPLA Cloud.

## Tematy MQTT

Dla gniazdka (typ `onoff`); `m2s` to domyślny `mqtt.topic_prefix`:

| Kierunek | Temat | Treść |
| --- | --- | --- |
| Matter → SUPLA | `m2s/<node>/<endpoint>/onoff/state` (retained) | `{"state": 1}`, `{"state": 0}`, `{"state": -1}` = niedostępne |
| SUPLA → Matter | `m2s/<node>/<endpoint>/onoff/set` | `ON` / `OFF` (także `TOGGLE`, `1`, `0`, `TRUE`, `FALSE`) |
| stan łącznika | `m2s/bridge/status` (retained, LWT) | `online` / `offline` |

Zasady:

- Stan zmienia się dopiero po potwierdzeniu z urządzenia — łącznik nie „zgaduje” wyniku komendy. Gdy komenda się nie powiedzie, łącznik publikuje ponownie faktyczny stan.
- Komendy z flagą retain są ignorowane (to polecenia sprzed restartu).
- Komenda do urządzenia niedostępnego jest odrzucana.

Światło z regulacją bieli (`light_cct`) używa zwykłego tekstu, w skali SUPLA:

| Kierunek | Temat | Treść |
| --- | --- | --- |
| Matter → SUPLA | `m2s/<node>/<endpoint>/light_cct/state` (retained) | `S B T` — `S`: 1 świeci, 0 zgaszone, -1 niedostępne; `B`: jasność 1–100; `T`: biel 0 (ciepła) – 100 (zimna) |
| SUPLA → Matter | `m2s/<node>/<endpoint>/light_cct/set` | `B T` — `B`: 0 = zgaś, 1–100 = jasność; `T`: 0–100 albo -1 = bez zmiany |

Licznik energii (`meter`) publikuje na `m2s/<node>/<endpoint>/meter/state` (retained) JSON z pomiarami, np. `{"voltage": 230.0, "current": 8.002, "power": 1840.5, "energy": 2.5}` (V, A, W, kWh), albo pusty komunikat, gdy urządzenie jest niedostępne. Licznik nie ma tematu komend.

## Dostępność (offline)

Kanał SUPLA przechodzi w offline, gdy:

- węzeł Matter jest nieosiągalny albo `matter-server` nie działa → łącznik publikuje `{"state": -1}`;
- łącznik został zatrzymany → przy zamykaniu publikuje `-1`;
- łącznik padł bez sprzątania → stan wygasa w `supla-device` po `state_expiration_sec` (łącznik w normalnej pracy ponawia publikację co `heartbeat_sec`).

Sprawdzone uruchomieniem `supla-device`: offline po `{"state": -1}` i po 120 s bez wiadomości. Na prawdziwym gniazdku: po krótkim odłączeniu kontroler uznaje węzeł za niedostępny dopiero po wygaśnięciu subskrypcji (ok. 1 min 44 s), więc kanał nie przechodzi w offline natychmiast. Dłuższe odłączenie → offline w aplikacji SUPLA — niesprawdzone.

Restart `supla-device` nie wyłącza włączonego gniazdka (potwierdzone na GRILLPLATS).

## Automatyczne kanały

Gdy kontroler zgłosi nowy węzeł, łącznik szuka endpointów z klastrem OnOff:

- OnOff bez LevelControl → typ `onoff`, kanał SUPLA `CustomRelay`; przy `auto_add: true` dopisywany od razu z kolejnym numerem;
- OnOff + LevelControl + temperatura bieli → typ `light_cct`, kanał SUPLA `MatterLight` (ściemniacz CCT, własne rozszerzenie `supla-device`); przy `lights_as_channels: false` bez kanału, sterowanie tylko z panelu;
- inne światła (np. sam ściemniacz bez regulacji bieli) → bez kanału, jeszcze nieobsługiwane;
- endpoint z pomiarem mocy lub energii → typ `meter`, kanał SUPLA `MatterMeter` (jednofazowy licznik energii, własne rozszerzenie `supla-device`); w panelu stoi pod swoim gniazdkiem ([zrzut](img/panel-kanaly.png)), a jego nazwa wynika z nazwy gniazdka;
- węzły bez OnOff (czujniki itp.) → lista „bez kanału”, tylko diagnostyka.

Po dopisaniu łącznik zapisuje `config/devices.yaml` i generuje `config/supla/supla-device.yaml`. Kontener `supla` wykrywa zmianę pliku (sprawdza co 2 s) i restartuje `supla-device`.

Nazwa urządzenia powstaje z nazwy produktu i numeru węzła, np. `grillplats_plug_2`.

## Trwałość kanałów SUPLA

Kolejność i typ kanałów w SUPLA są trwałe. **Wolno tylko dopisywać urządzenia z kolejnym `supla_channel`** (numery od 0, bez przerw, najwyżej 127). Usunięcie kanału lub zmiana typu kończy się w SUPLA błędem „Channel conflict” i koniecznością usunięcia urządzenia z SUPLA Cloud **razem z historią**.

Zabezpieczenia:

- łącznik zapisuje `config/supla/channels.lock.json` i odmawia wygenerowania konfiguracji, w której kanał zniknął albo zmienił typ;
- urządzenie wyparowane z Matter zostaje jako kanał offline — nie usuwaj go z `devices.yaml`;
- typy nieobsługiwane nie dostają kanału „na zapas”, żeby nie zająć numeru niewłaściwym typem.

Reset — **wyłącznie po świadomym usunięciu urządzenia z SUPLA Cloud**:

```bash
docker compose run --rm bridge gen-supla-config --force --server svrXX.supla.org --mail <e-mail-konta>
```

(`gen-supla-config` bierze serwer i e-mail z opcji albo ze zmiennych `SUPLA_SERVER` / `SUPLA_MAIL`; nie czyta konta zapisanego w panelu.) Procedura resetu nie była wykonywana na sprzęcie.

## Plik `config/devices.yaml`

Wzór: `config/devices.example.yaml`. Jeśli pliku nie ma, łącznik startuje z ustawieniami domyślnymi i tworzy go przy pierwszym dopisaniu urządzenia.

| Klucz | Domyślnie | Znaczenie |
| --- | --- | --- |
| `matter.url` | `ws://127.0.0.1:5580/ws` | adres WebSocket kontrolera |
| `mqtt.host`, `mqtt.port` | `127.0.0.1`, `1883` | broker |
| `mqtt.username`, `mqtt.password` | brak | opcjonalne; dołączony Mosquitto przyjmuje połączenia anonimowe z localhosta |
| `mqtt.topic_prefix` | `m2s` | prefiks tematów |
| `heartbeat_sec` | `30` | co ile łącznik ponawia publikację stanów |
| `state_expiration_sec` | `120` | po jakim czasie bez wiadomości `supla-device` uznaje stan za nieważny; co najmniej 2× `heartbeat_sec`, `0` = bez wygasania |
| `auto_add` | `true` | automatyczne dopisywanie kanałów |
| `lights_as_channels` | `true` | światła z regulacją bieli jako kanał SUPLA „ściemniacz CCT”; `false` = sterowanie tylko z panelu |
| `web.enabled`, `web.host`, `web.port` | `true`, `127.0.0.1`, `8099` | panel WWW ([panel-www.md](panel-www.md)) |
| `devices` | `[]` | lista urządzeń; wypełnia ją łącznik |

Wpis na liście `devices`: `name` (litery, cyfry, `_`, `-`), opcjonalny `caption`, `node_id`, `endpoint`, `type`, `supla_channel`. Ręcznie wolno tylko dopisywać na końcu. Plik jest nadpisywany przez łącznik — komentarze dodane ręcznie nie przetrwają.

## Pliki instalacji

| Ścieżka | Zawartość | W repozytorium |
| --- | --- | --- |
| `.env` | interfejs, port dongla, hasło panelu | nie |
| `config/devices.yaml` | ustawienia łącznika i mapowanie kanałów | nie |
| `config/supla/account.json` | serwer i e-mail konta SUPLA (z panelu) | nie |
| `config/supla/supla-device.yaml` | wygenerowana konfiguracja `supla-device` — nie edytuj ręcznie | nie |
| `config/supla/channels.lock.json` | zapis trwałych kanałów | nie |
| `config/mosquitto/mosquitto.conf` | konfiguracja brokera | tak |
| `data/otbr` | sieć Thread | nie |
| `data/matter` | tożsamość kontrolera Matter i parowania | nie |
| `data/supla` | GUID / AUTHKEY urządzenia SUPLA | nie |
| `backups/` | kopie zapasowe | nie |

## Porty

Wszystkie usługi pracują w sieci hosta i domyślnie nasłuchują **tylko na `127.0.0.1`**:

| Port | Usługa |
| --- | --- |
| 5580 | WebSocket i dashboard `matter-server` |
| 1883 | Mosquitto (bez uwierzytelniania) |
| 8081, 8080 | REST i WWW border routera |
| 8099 | panel łącznika (z LAN dopiero po ustawieniu `web.host` i hasła) |

Żadnego z nich nie wystawiaj do Internetu. Dostęp zdalny do usług lokalnych: tunel SSH, np. `ssh -L 5580:127.0.0.1:5580 <użytkownik>@<adres-rpi>`.

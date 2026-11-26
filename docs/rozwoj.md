# Rozwój i testy

Własnym kodem projektu jest łącznik w `bridge/` (Python ≥ 3.11, asyncio) oraz rozszerzenia `supla-device` w `supla-device/extensions/`. Do pracy nad łącznikiem nie potrzeba sprzętu.

## Struktura `bridge/src/m2s_bridge/`

| Plik | Rola |
| --- | --- |
| `cli.py` | wiersz poleceń ([cli.md](cli.md)) |
| `config.py` | wczytywanie, walidacja i zapis `devices.yaml` |
| `core.py` | logika Matter ⇄ MQTT, niezależna od transportu |
| `matter_client.py` | cienki klient WebSocket kontrolera |
| `manager.py` | lista urządzeń, automatyczne kanały, parowanie, dane dla panelu |
| `runner.py` | połączenia z brokerem i kontrolerem, ponawianie |
| `supla_config.py` | generator `supla-device.yaml` i kontrola trwałości kanałów |
| `topics.py` | nazwy tematów MQTT |
| `web.py`, `web_ui.html`, `web_login.html` | panel WWW |
| `qr.py` | odczyt kodu `MT:…` ze zdjęcia (zxing-cpp + Pillow) |
| `diagnostics.py` | zrzut diagnostyczny węzła |
| `light.py` | przeliczenia dla świateł: poziom i miredy Matter ↔ skala SUPLA 0–100 |
| `meter.py` | licznik energii: wykrywanie pomiarów i przeliczenia jednostek |

## Testy jednostkowe

```bash
cd bridge
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Pracując w WSL na dysku Windows (`/mnt/c/…`), trzymaj środowisko wirtualne poza nim, np. `uv venv /tmp/m2s-venv`.

Testy używają atrap kontrolera Matter i MQTT (`tests/conftest.py`).

## Test z prawdziwym brokerem

```bash
docker run -d --rm -p 127.0.0.1:18883:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf
M2S_TEST_MQTT=127.0.0.1:18883 .venv/bin/pytest tests/test_e2e_mqtt.py
```

Bez zmiennej `M2S_TEST_MQTT` ten test jest pomijany.

## Dopisywanie obsługi nowego typu urządzenia

1. Sparuj urządzenie — trafi na listę „bez kanału” i nie zajmie numeru kanału SUPLA.
2. Pobierz zrzut diagnostyczny (panel → „Diagnostyka” albo `docker compose run --rm bridge diagnostics <node_id> > node.json`). Domyślny zrzut nie zawiera numeru seryjnego ani danych sieci.
3. Zrzut ma format komendy `import_test_node` kontrolera — można go wczytać do `matter-server` jako węzeł testowy i rozwijać obsługę bez urządzenia. Ograniczenie: kontroler nie wykonuje komend na węzłach testowych.
4. Przykładowe zrzuty leżą w `bridge/tests/fixtures/` (gniazdko GRILLPLATS, żarówka KAJPLATS E27 WS).
5. Nowy typ dopisuje się w `config.py` (`DEVICE_TYPES`), `manager.py` (rozpoznanie endpointu), `core.py` (stan i komendy) oraz `supla_config.py` (typ kanału `supla-device`).

Pamiętaj o trwałości kanałów: typ kanału SUPLA przypisany urządzeniu jest ostateczny, więc nowy typ włączaj dopiero, gdy jego kanał działa na koncie SUPLA.

## Obrazy

- `bridge/Dockerfile` — `python:3.12-slim`, proces jako uid 1000.
- `supla-device/Dockerfile` — buduje `supla-device` z **przypiętego commita** gałęzi `main` repozytorium SUPLA/supla-device (argument `SUPLA_DEVICE_REF`), bo wydanie v26.9.2 nie ma `expiration_time_sec` dla źródła MQTT. Do budowy dołączane są rozszerzenia z `supla-device/extensions/`.
- `supla-device/entrypoint.sh` — czeka na konfigurację i restartuje `supla-device` po każdej zmianie pliku.

Budowa obu obrazów była sprawdzona na x86-64 i na arm64 (Raspberry Pi 4B).

## Rozszerzenia supla-device

`supla-device/extensions/` zawiera własne typy kanałów dołączane przy budowie obrazu (`SUPLA_LINUX_EXTENSION_DIRS` w `supla-device/Dockerfile`). `matter_light` dodaje typ `MatterLight` — ściemniacz z regulacją bieli: przekazuje jasność i biel z SUPLA do łącznika i odbiera stan światła. Wersja `supla-device` dla Linuksa ma dla świateł tylko klasę testową bez sterowania, stąd własne rozszerzenie.

## Znane ograniczenia

- Licznik energii: `ElectricityMeterParsed` przy braku lub wygaśnięciu danych nie przełącza kanału w offline (zgłasza tylko brak pomiarów) i pokazuje kanał jako trójfazowy z pustymi fazami 2 i 3. Poprawienie tego wymagałoby kolejnego rozszerzenia.
- GRILLPLATS (fw 1.4.6) zgłasza częstotliwość w innej jednostce niż specyfikacja, więc łącznik jej nie przekazuje.

## Zasady

- Sekrety (dataset Thread, `data/`, konto SUPLA, kody `MT:…`) nie trafiają do repozytorium ani do zrzutów dołączanych do zgłoszeń.
- Opisując zmianę, zaznaczaj uczciwie, co zostało uruchomione na sprzęcie, a co tylko napisane lub przetestowane na atrapach.

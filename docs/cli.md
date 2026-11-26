# Polecenia CLI łącznika

Program `m2s-bridge` jest punktem wejścia kontenera `bridge`. Polecenia pomocnicze uruchamia się w osobnym, jednorazowym kontenerze:

```bash
docker compose run --rm bridge <polecenie> [opcje]
```

Bez polecenia (tak działa usługa `bridge`) wykonywane jest `run`.

## Polecenia

| Polecenie | Działanie |
| --- | --- |
| `run` | uruchamia łącznik i panel WWW (domyślne) |
| `nodes` | wypisuje wersję kontrolera, czy ustawiony jest dataset Thread i Bluetooth, oraz sparowane węzły (node_id, dostępność, VID/PID, nazwa, endpointy OnOff) |
| `commission [kod] [--network-only]` | paruje urządzenie kodem `MT:…` albo numerycznym; bez argumentu pyta o kod (nie trafia do historii powłoki); `--network-only` — bez Bluetootha, dla urządzenia będącego już w sieci |
| `set-thread-dataset [hex]` | przekazuje kontrolerowi dataset Thread; bez argumentu pyta o wartość |
| `diagnostics <node_id> [--full]` | wypisuje zrzut diagnostyczny węzła (JSON); `--full` — bez usuwania numeru seryjnego i danych sieci |
| `gen-supla-config [--output plik] [--server adres] [--mail e-mail] [--force]` | generuje `supla-device.yaml` z `devices.yaml`; `--force` pomija kontrolę trwałości kanałów |

Przykłady:

```bash
docker compose run --rm bridge nodes
docker compose run --rm bridge commission
docker compose run --rm bridge commission --network-only
docker compose run --rm bridge set-thread-dataset "$(docker exec otbr ot-ctl dataset active -x | head -1)"
docker compose run --rm bridge diagnostics <node_id> > node.json
```

Uwagi:

- `commission` czeka na wynik do 5 minut.
- `gen-supla-config` w normalnej pracy nie jest potrzebne — łącznik generuje konfigurację sam. Opcji `--force` używa się **wyłącznie** po świadomym usunięciu urządzenia z SUPLA Cloud ([jak-to-dziala.md](jak-to-dziala.md#trwałość-kanałów-supla)).
- Pełnego zrzutu (`--full`) nie publikuj.

## Opcje globalne

Podaje się je przed nazwą polecenia.

| Opcja | Zmienna | Domyślnie |
| --- | --- | --- |
| `--config` | `M2S_CONFIG` | `/config/devices.yaml` |
| `--matter-url` | `M2S_MATTER_URL` | z pliku konfiguracyjnego (`ws://127.0.0.1:5580/ws`) |
| `--log-level` | `M2S_LOG_LEVEL` | `INFO` |

## Pozostałe zmienne środowiskowe

| Zmienna | Znaczenie |
| --- | --- |
| `SUPLA_SERVER`, `SUPLA_MAIL` | wartość początkowa konta SUPLA (konto zapisane w panelu ma pierwszeństwo) |
| `M2S_WEB_PASSWORD` | hasło panelu WWW |
| `M2S_SUPLA_OUTPUT` | ścieżka wygenerowanej konfiguracji, domyślnie `/config/supla/supla-device.yaml` |
| `M2S_BACKUP_DIR` | katalog kopii udostępnianych w panelu, domyślnie `/backups` |

`docker-compose.yml` przekazuje do kontenera z pliku `.env` tylko `SUPLA_SERVER`, `SUPLA_MAIL` i `M2S_WEB_PASSWORD`; pozostałe mają wartości domyślne dopasowane do wolumenów kontenera i zwykle nie trzeba ich zmieniać.

## Kody wyjścia

`0` — powodzenie; `1` — błąd połączenia z kontrolerem lub błąd Matter; `2` — błąd konfiguracji (w tym konflikt kanałów).

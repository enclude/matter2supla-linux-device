# Kopie zapasowe i odtwarzanie

Stanu instalacji nie da się odtworzyć z repozytorium. Utrata katalogu `data/` oznacza ponowne parowanie wszystkich urządzeń, nową sieć Thread i nowe urządzenie w SUPLA Cloud (bez historii starego).

Sprawdzone na Raspberry Pi: tworzenie kopii, harmonogram, pobieranie z panelu, rozpakowanie kopii z porównaniem zawartości. **Pełne odtworzenie na drugim urządzeniu nie było wykonywane.**

## Co zawiera kopia

Plik `backups/m2s-backup-RRRRMMDD-GGMMSS.tar.gz`:

| Ścieżka | Zawartość |
| --- | --- |
| `data/matter` | tożsamość kontrolera Matter i parowania |
| `data/otbr` | ustawienia sieci Thread oraz `dataset-active.txt` - dataset zapisany czytelnie, żeby sieć dało się odtworzyć także na innym donglu |
| `data/supla` | GUID / AUTHKEY urządzenia SUPLA |
| `config/` | `devices.yaml` (mapowanie kanałów), `supla/` (konto, wygenerowana konfiguracja, `channels.lock.json`), konfiguracja Mosquitto |
| `.env` | ustawienia, w tym hasło panelu |

Kod projektu nie jest kopiowany - pochodzi z repozytorium.

> **Kopia zawiera wszystkie sekrety instalacji.** Kto ją ma, może przejąć kontrolę nad urządzeniami Matter i podszyć się pod urządzenie SUPLA. Przechowuj ją poza tym urządzeniem, w miejscu z ograniczonym dostępem.

## Kopia na żądanie

```bash
sudo ./host/backup.sh
```

Na czas pakowania skrypt zatrzymuje `matter-server` i `supla` (od kilku do kilkunastu sekund), żeby pliki były spójne; border router pracuje bez przerwy. Potem usługi startują ponownie, a skrypt sprawdza, czy archiwum da się odczytać, i usuwa najstarsze kopie ponad limit.

Zmienne środowiskowe:

| Zmienna | Domyślnie | Znaczenie |
| --- | --- | --- |
| `M2S_BACKUP_DIR` | `./backups` | katalog docelowy |
| `M2S_BACKUP_KEEP` | `14` | ile najnowszych kopii zostawić |

Tych samych zmiennych używa `host/update.sh`, który przed aktualizacją zapisuje kopię do osobnego katalogu `backups-update/` ([aktualizacja.md](aktualizacja.md)) - rotacja codziennych kopii jej nie obejmuje.

Jeśli `otbr` nie działa, kopia powstanie bez `dataset-active.txt` (skrypt wypisze ostrzeżenie).

### Kopia ręczna z panelu

W panelu, w zakładce „System”, przycisk „Zrób kopię teraz” zleca kopię bez logowania się na host - np. tuż przed parowaniem kilku urządzeń albo zmianą konta. Kopie ręczne są tymczasowe i nie mieszają się z kopiami z harmonogramu:

- mają nazwy `m2s-manual-…` i leżą w tym samym katalogu;
- zostają najwyżej trzy - czwarta zastępuje najstarszą (`M2S_MANUAL_KEEP`);
- każda usuwa się sama po 3 dniach od utworzenia (`M2S_MANUAL_DAYS`; sprawdza to co godzinę timer `m2s-backup-prune.timer`).

Jak to działa: panel zapisuje plik `config/backup-request`, a jednostka systemd `m2s-backup-manual.path` na hoście uruchamia wtedy `host/backup.sh`. Kontener panelu nie dostaje dostępu do Dockera ani do katalogu `data/`. Jednostki instaluje `host/install-backup-timer.sh` (robi to też `host/install.sh`); **po aktualizacji ze starszej wersji trzeba uruchomić go raz ręcznie** - bez tego panel po 2 minutach pokaże, że host nie podjął zlecenia. Kopię ręczną można też zrobić z konsoli: `sudo M2S_BACKUP_KIND=manual ./host/backup.sh`.

## Harmonogram

```bash
sudo ./host/install-backup-timer.sh daily 14     # codziennie o 03:30, 14 ostatnich kopii
sudo ./host/install-backup-timer.sh weekly 8     # albo: w niedzielę o 03:30, 8 kopii
```

Skrypt tworzy jednostki systemd `m2s-backup.service` i `m2s-backup.timer` i włącza timer, a także jednostki kopii ręcznej z panelu (`m2s-backup-manual.path`, `m2s-backup-manual.service`, `m2s-backup-prune.timer`). Kontrola:

```bash
systemctl list-timers m2s-backup.timer
```

## Wyniesienie kopii poza urządzenie

Kopia leżąca na tej samej karcie SD nie chroni przed jej awarią. Możliwości:

- **panel WWW**, sekcja „Kopie zapasowe” ([zrzut](img/panel-kopie.png)) → „Pobierz” - połączenie nie jest szyfrowane, więc tylko w zaufanej sieci;
- skopiowanie pliku z katalogu `backups/` własnym sposobem (np. `scp`). Katalog ma uprawnienia 700 i należy do uid 1000.

## Odtworzenie na innym urządzeniu

1. Przygotuj nowy host jak przy instalacji, kroki 1–6 ([instalacja-raspberry-pi.md](instalacja-raspberry-pi.md)): Docker, katalog projektu z repozytorium, `sudo ./host/setup-host.sh <interfejs>`, reguła udev dla dongla. Kroków z `.env`, katalogami i tworzeniem sieci Thread **nie wykonuj** - przyjdą z kopii.
2. **Wyłącz starą instalację** (`docker compose down` na starym hoście), jeśli jeszcze działa.
3. Odtwórz:

   ```bash
   sudo ./host/restore.sh /ścieżka/m2s-backup-RRRRMMDD-GGMMSS.tar.gz
   ```

4. Popraw `INFRA_IF` w `.env` oraz `web.host` w `config/devices.yaml`, jeśli nowe urządzenie ma inny interfejs lub adres IP.
5. Uruchom:

   ```bash
   docker compose up -d --build
   ```

> **Nie uruchamiaj jednocześnie starej i odtworzonej instalacji.** Miałyby tę samą tożsamość w Matter, Thread i SUPLA.

### Zachowanie `restore.sh`

- Jeśli w `data/` są już dane, skrypt odmówi; żeby je zastąpić, dodaj `--force` jako drugi argument.
- Przed rozpakowaniem zatrzymuje stos (`docker compose down`) i przenosi dotychczasowy stan (`data/`, kopię `config/` i `.env`) do katalogu `pre-restore-RRRRMMDD-GGMMSS/` - można do niego wrócić ręcznie.

### Inny dongle niż przy tworzeniu kopii

Plik ustawień border routera jest przypisany do konkretnego dongla. Z innym donglem border router nie znajdzie swoich ustawień - przywróć wtedy sieć Thread z zapisanego datasetu (skrypt wypisuje tę podpowiedź):

```bash
docker exec otbr ot-ctl dataset set active $(cat data/otbr/dataset-active.txt)
docker exec otbr ot-ctl ifconfig up && docker exec otbr ot-ctl thread start
```

Ta ścieżka nie była sprawdzana.

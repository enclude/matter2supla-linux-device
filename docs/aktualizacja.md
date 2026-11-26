# Aktualizacja

> **Stan sprawdzenia.** Sprawdzone na Raspberry Pi 4B: pipeline wydał `v0.2.0` (obrazy `0.2.0` i `latest`, wydanie w GitHubie), a `sudo ./host/update.sh --yes` zaktualizował działającą instalację z obrazów zbudowanych lokalnie (katalog bez repozytorium git) w ok. 1,5 minuty - kopia trafiła do `backups-update/`, `.env` zmienił się tylko o linię `M2S_VERSION`, `config/`, tożsamość urządzenia SUPLA i sieć Thread zostały bez zmian, kanały wróciły same, panel pokazuje wersję. Wycofanie po nieudanej aktualizacji i katalog będący repozytorium git sprawdzone tylko w kontenerze testowym z atrapami poleceń `docker` i `curl`.

Zasada: **nic nie aktualizuje się samo.** Panel WWW tylko informuje, że jest nowsze wydanie; aktualizację uruchamia ręcznie użytkownik, jednym poleceniem na hoście.

## Wersje i tagi obrazów

Obrazy `ghcr.io/enclude/matter2supla-bridge` i `ghcr.io/enclude/matter2supla-supla-device` buduje pipeline `.github/workflows/docker.yml`:

| Zdarzenie | Tagi obrazów | Wbudowana wersja |
| --- | --- | --- |
| tag gita `vX.Y.Z` | `X.Y.Z` i `latest`; powstaje też wydanie (GitHub Release) z automatycznymi notatkami | `X.Y.Z` |
| zmiana w gałęzi `main` | `edge` oraz `sha-<commit>` | `edge` |
| pull request | bez publikacji (sama budowa) | - |
| budowa lokalna (`docker compose build`) | - | `dev` |

- `latest` oznacza więc **ostatnie wydanie**, a nie stan gałęzi `main`; wersja rozwojowa ma tag `edge`.
- Którą wersję uruchamia `docker compose`, wybiera zmienna `M2S_VERSION` w `.env` (tag obrazu; bez niej `latest`). `host/update.sh` wpisuje tam konkretny numer.
- Każdy obraz ma w sobie numer wersji i commit: zmienne `M2S_BUILD_VERSION` i `M2S_BUILD_REVISION`. Odczyt:

  ```bash
  docker exec bridge m2s-bridge --version
  docker exec supla printenv M2S_BUILD_VERSION
  ```

  Obrazy zbudowane przed wprowadzeniem wersjonowania tych zmiennych nie mają - skrypt aktualizacji pokazuje wtedy wersję „nieznana”.

## Powiadomienie w panelu

Łącznik minutę po starcie, a potem raz na dobę pyta API GitHuba o najnowsze wydanie projektu (`https://api.github.com/repos/enclude/matter2supla-linux-device/releases/latest`). Żądanie nie zawiera żadnych danych użytkownika ani instalacji - tylko ogólny nagłówek `User-Agent: matter2supla`. Błąd sieci, brak wydań albo limit zapytań niczego nie zmienia w pracy łącznika (ślad jest tylko w logu na poziomie `DEBUG`).

- W stopce panelu widać zainstalowaną wersję i skrót commita.
- Gdy istnieje wydanie o wyższym numerze, u góry pojawia się komunikat „Dostępna wersja X” z odnośnikiem do opisu wydania i poleceniem aktualizacji ([zrzut](img/panel.png)). W panelu **nie ma** przycisku aktualizacji.
- Wersje `dev` i `edge` nie są porównywane z wydaniami - komunikat się wtedy nie pojawia.

Wyłączenie sprawdzania - w `config/devices.yaml` (edytuj przy zatrzymanym łączniku, patrz [panel-www.md](panel-www.md)):

```yaml
update_check: false
```

Po `docker compose up -d bridge` łącznik nie wysyła już żadnych zapytań o wersję.

## Aktualizacja: `host/update.sh`

```bash
cd /opt/matter2supla
sudo ./host/update.sh --check     # tylko pokaż wersję zainstalowaną i najnowsze wydanie
sudo ./host/update.sh             # aktualizacja do najnowszego wydania (z pytaniem o potwierdzenie)
sudo ./host/update.sh 0.3.0       # konkretna wersja (także powrót do starszej)
sudo ./host/update.sh --yes       # bez pytania
```

Skrypt uruchamia wyłącznie użytkownik - nie ma timera ani wywołania z panelu.

> **Instalacje sprzed wersji 0.2.0** nie mają jeszcze tego skryptu. Jednorazowo trzeba go dograć ręcznie: skopiować `host/update.sh` z repozytorium do katalogu `host/` na urządzeniu i nadać prawo wykonywania (`chmod +x host/update.sh`); w repozytorium git wystarczy `git pull`.

### Co robi, krok po kroku

1. **Wersje.** Zainstalowaną odczytuje z działającego kontenera `bridge`, docelową z argumentu albo z listy wydań na GitHubie. Jeśli są równe, kończy bez zmian.
2. **Potwierdzenie i kontrole wstępne - jeszcze bez zmian.** Sprawdza `.env`, przygotowuje pliki nowej wersji (niżej) i pobiera obrazy `bridge` i `supla` w wersji docelowej. Gdy wydania albo obrazów nie ma, kończy się tutaj i niczego nie zmienia.
3. **Kopia zapasowa do osobnego katalogu** `backups-update/` (inny niż `backups/`, więc rotacja codziennych kopii jej nie usunie):
   - `m2s-backup-….tar.gz` - pełna kopia stanu, wykonana przez `host/backup.sh` (na kilkanaście sekund zatrzymuje `matter-server` i `supla`), w tym samym formacie co kopie codzienne - odtwarza ją `host/restore.sh`;
   - `m2s-project-….tar.gz` - dotychczasowe pliki projektu (bez `data/`, `.env`, `config/devices.yaml`, `config/supla/`, katalogów kopii i `.git`).

   Zostaje 5 ostatnich kopii każdego rodzaju. Dotychczasowe obrazy `bridge` i `supla` dostają tag `przed-aktualizacja`.
4. **Pliki projektu w nowej wersji:**
   - katalog jest repozytorium git bez lokalnych zmian → `git fetch --tags` i przejście na tag `vX.Y.Z` (stan „detached HEAD”);
   - repozytorium git z lokalnymi zmianami → skrypt przerywa już w kroku 2 i wypisuje listę zmienionych plików;
   - katalog bez `.git` (projekt skopiowany archiwum) → pobranie `https://github.com/enclude/matter2supla-linux-device/archive/refs/tags/vX.Y.Z.tar.gz` i nałożenie plików na katalog projektu. Pliki, których w nowej wersji już nie ma, zostają na dysku (nie przeszkadzają - obrazy są pobierane gotowe).
5. **Obrazy i start:** wpisuje `M2S_VERSION=X.Y.Z` do `.env` (dopisuje albo podmienia tę jedną linię), potem `docker compose pull bridge supla` i `docker compose up -d`.
6. **Sprawdzenie:** przez ok. minutę obserwuje, czy wszystkie usługi, które działały przed aktualizacją, działają bez restartów, i czy `bridge` oraz `supla` zgłaszają wersję docelową. Na końcu wypisuje wersję przed i po oraz ścieżki kopii.

### Czego skrypt nie rusza

- `.env` poza linią `M2S_VERSION`, `data/`, `config/devices.yaml`, `config/supla/`, katalogów `backups/` i `backups-update/`;
- **obrazów obcych usług** - `otbr` (`openthread/border-router`), `matter-server` (`ghcr.io/matter-js/matterjs-server`) i `mqtt` (`eclipse-mosquitto`) zostają w wersjach już pobranych. Ich aktualizacja to osobna, świadoma decyzja (`docker compose pull <usługa>` i `docker compose up -d`), najlepiej po zrobieniu kopii.

Uwaga: jeśli nowa wersja projektu zmienia w `docker-compose.yml` ustawienia którejś z obcych usług, `docker compose up -d` utworzy jej kontener ponownie (z tym samym obrazem).

### Zmienne

| Zmienna | Domyślnie | Znaczenie |
| --- | --- | --- |
| `M2S_UPDATE_BACKUP_DIR` | `./backups-update` | katalog kopii sprzed aktualizacji |
| `M2S_UPDATE_KEEP` | `5` | ile kopii sprzed aktualizacji zostawić |
| `M2S_UPDATE_WAIT` | `45` | ile sekund obserwować usługi po starcie (po 10 s rozruchu) |

Kopie w `backups-update/` zawierają te same sekrety co kopie codzienne ([kopie-zapasowe.md](kopie-zapasowe.md)); nie są widoczne w panelu.

## Cofanie

### Samoczynne - gdy aktualizacja się nie powiedzie

Jeśli po rozpoczęciu zmian cokolwiek zawiedzie (pobranie obrazów, start, restartująca się usługa, zła wersja w obrazie, przerwanie skryptu), skrypt przywraca poprzednie pliki projektu, plik `.env` i poprzednie obrazy, uruchamia usługi ponownie i kończy się błędem, wypisując, gdzie leżą kopie. Danych w `data/` i `config/` aktualizacja nie zmienia, więc nie są przy tym odtwarzane.

### Ręczne - gdy nowa wersja działa, ale chcesz wrócić

Do poprzedniego wydania:

```bash
sudo ./host/update.sh 0.2.0
```

Do obrazów sprzed ostatniej aktualizacji (także wtedy, gdy nie miały numeru wersji):

```bash
cd /opt/matter2supla
sudo tar -xzpf backups-update/m2s-project-RRRRMMDD-GGMMSS.tar.gz --no-overwrite-dir   # poprzednie pliki projektu
sudo sed -i 's|^M2S_VERSION=.*|M2S_VERSION=przed-aktualizacja|' .env
sudo docker compose up -d
```

Tag `przed-aktualizacja` wskazuje zawsze obrazy sprzed **ostatniej** aktualizacji.

Stan instalacji (parowania, sieć Thread, tożsamość SUPLA) odtwarza się tylko wtedy, gdy rzeczywiście został uszkodzony - z kopii `backups-update/m2s-backup-….tar.gz` skryptem `host/restore.sh` ([kopie-zapasowe.md](kopie-zapasowe.md)).

## Wydawanie nowej wersji (dla rozwijających projekt)

1. Podnieś `version` w `bridge/pyproject.toml` (i `__version__` w `bridge/src/m2s_bridge/__init__.py`).
2. Utwórz i wypchnij tag: `git tag v0.3.0 && git push origin v0.3.0`.
3. Pipeline uruchamia testy, buduje oba obrazy dla amd64 i arm64, publikuje tagi `0.3.0` i `latest`, a na końcu tworzy wydanie z automatycznymi notatkami. Dopiero wtedy panel i `update.sh` widzą nową wersję - obrazy są już wtedy dostępne.

Tag spoza schematu `vX.Y.Z` (np. `v0.3.0-rc1`) buduje obrazy tylko z tagiem commita, bez `latest` i bez wydania.

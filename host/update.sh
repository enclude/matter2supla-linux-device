#!/bin/bash
# SPDX-FileCopyrightText: 2026 Zjawa.IT - Jarosław Zjawiński <kontakt@zjawa.it>
# SPDX-License-Identifier: GPL-2.0-or-later
# Ręczna aktualizacja bramki do wybranego wydania. Nic nie uruchamia tego skryptu samoczynnie —
# ani timer, ani panel WWW; panel tylko informuje, że jest nowsza wersja.
#
# Użycie (w katalogu projektu):
#   sudo ./host/update.sh [--check] [--yes] [wersja]
#     --check    tylko pokaż wersję zainstalowaną i najnowsze wydanie, niczego nie zmieniaj
#     --yes      nie pytaj o potwierdzenie
#     wersja     np. 0.3.0 albo v0.3.0; domyślnie najnowsze wydanie z GitHuba
#
# Kolejność: kontrole wstępne (w tym pobranie obrazów nowej wersji) -> kopia zapasowa stanu
# i dotychczasowych plików projektu do OSOBNEGO katalogu -> nowe pliki projektu -> M2S_VERSION
# w .env -> `docker compose pull bridge supla` i `up -d` -> sprawdzenie, że usługi działają
# stabilnie, a łącznik zgłasza nową wersję. Gdy coś zawiedzie po rozpoczęciu zmian, skrypt
# przywraca poprzednie pliki, .env i obrazy i uruchamia usługi ponownie.
#
# Nie są zmieniane: .env (poza linią M2S_VERSION), data/, config/devices.yaml, config/supla/,
# katalogi kopii oraz obrazy obcych usług (otbr, matter-server, mqtt).
#
# Zmienne:
#   M2S_UPDATE_BACKUP_DIR  katalog kopii sprzed aktualizacji (domyślnie ./backups-update) — celowo
#                          inny niż ./backups, żeby rotacja codziennych kopii ich nie usunęła
#   M2S_UPDATE_KEEP        ile kopii sprzed aktualizacji zostawić (domyślnie 5)
#   M2S_UPDATE_WAIT        ile sekund obserwować usługi po starcie (domyślnie 45)
set -euo pipefail

REPO="enclude/matter2supla-linux-device"
API_URL="https://api.github.com/repos/$REPO/releases/latest"
ROLLBACK_TAG="przed-aktualizacja"
OWN_SERVICES=(bridge supla)   # tylko obrazy tego projektu są pobierane i podmieniane
UNKNOWN="nieznana"

DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP=""
CHANGED=0          # 1 od chwili rozpoczęcia zmian do pomyślnego końca — wtedy wyjście = wycofanie
MODE=""            # git | tar
GIT_OWNER=root
PREV_REF=""
WATCHED=()
STATE_BACKUP=""
PROJECT_BACKUP=""
UPDATE_DIR=""
CURRENT="$UNKNOWN"
TARGET=""
declare -A OLD_ID=() OLD_REF=()

step() { echo; echo "== $*"; }
die() { echo "BŁĄD: $*" >&2; exit 1; }

usage() { sed -n '4,12p' "$0" | sed 's/^# \{0,1\}//'; }

fetch() {   # fetch <adres> [plik]; bez pliku wynik trafia na standardowe wyjście
  if command -v curl >/dev/null; then
    curl -fsSL --retry 2 --connect-timeout 15 --max-time 300 -H "User-Agent: matter2supla" -o "${2:--}" "$1"
  elif command -v wget >/dev/null; then
    wget -q -T 30 -U matter2supla -O "${2:--}" "$1"
  else
    die "potrzebny jest curl albo wget"
  fi
}

latest_release() {   # numer najnowszego wydania (bez „v”) albo błąd
  local json tag
  json="$(fetch "$API_URL" 2>/dev/null)" || return 1
  tag="$(printf '%s' "$json" | grep -o '"tag_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')"
  [[ "$tag" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
  echo "${tag#v}"
}

is_number() { [[ "$1" =~ ^[0-9]+(\.[0-9]+)*$ ]]; }
newer() {   # newer A B: czy A jest nowsze niż B (obie liczbowe)
  is_number "$1" && is_number "$2" && [ "$1" != "$2" ] \
    && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$1" ]
}

container() { docker compose ps -a -q "$1" 2>/dev/null | head -1; }

build_version() {   # wersja wbudowana w obraz działającego kontenera; stare obrazy jej nie mają
  local id value
  id="$(container "$1")"
  [ -n "$id" ] || { echo "$UNKNOWN"; return; }
  value="$(docker exec "$id" printenv M2S_BUILD_VERSION 2>/dev/null | tr -d '\r\n' || true)"
  echo "${value:-$UNKNOWN}"
}

g() {   # git w katalogu projektu, jako właściciel repozytorium (pliki nie zmieniają właściciela)
  if [ "$GIT_OWNER" != root ] && command -v runuser >/dev/null; then
    runuser -u "$GIT_OWNER" -- git -C "$DIR" "$@"
  else
    git -c safe.directory="$DIR" -C "$DIR" "$@"
  fi
}

apply_tree() {   # nakłada drzewo plików na katalog projektu; istniejące katalogi zachowują właściciela i prawa
  tar -C "$1" -cf - . | tar -C "$DIR" -xpf - --no-overwrite-dir
}

set_env_version() {   # dopisuje albo podmienia linię M2S_VERSION, reszty .env nie rusza
  if grep -q '^M2S_VERSION=' .env; then
    sed -i "s|^M2S_VERSION=.*|M2S_VERSION=$1|" .env
  else
    [ -z "$(tail -c1 .env)" ] || echo >> .env
    echo "M2S_VERSION=$1" >> .env
  fi
}

snapshot() {   # „usługa stan czas-startu liczba-restartów” dla obserwowanych usług
  local service id
  for service in "${WATCHED[@]}"; do
    id="$(container "$service")"
    if [ -z "$id" ]; then
      echo "$service brak"
    else
      echo "$service $(docker inspect -f '{{.State.Status}} {{.State.StartedAt}} {{.RestartCount}}' "$id" 2>/dev/null || echo brak)"
    fi
  done
}

verify() {   # usługi działają bez restartów przez M2S_UPDATE_WAIT sekund, a obrazy zgłaszają wersję docelową
  local wait="${M2S_UPDATE_WAIT:-45}" base now service version elapsed=0
  sleep 10
  base="$(snapshot)"
  if grep -qv ' running ' <<<"$base"; then
    echo "Nie wszystkie usługi działają:" >&2; echo "$base" >&2; return 1
  fi
  echo -n "Obserwuję usługi przez ${wait} s"
  while [ "$elapsed" -lt "$wait" ]; do
    sleep 5; elapsed=$((elapsed + 5)); echo -n "."
    now="$(snapshot)"
    if [ "$now" != "$base" ]; then
      echo; echo "Usługa zatrzymała się albo uruchomiła ponownie:" >&2; echo "$now" >&2; return 1
    fi
  done
  echo " bez restartów."
  for service in "${OWN_SERVICES[@]}"; do
    version="$(build_version "$service")"
    if [ "$version" != "$TARGET" ]; then
      echo "Usługa $service zgłasza wersję „$version” zamiast „$TARGET”." >&2; return 1
    fi
  done
}

rollback() {
  set +e
  echo >&2
  echo "== Aktualizacja nie powiodła się — przywracam poprzedni stan" >&2
  cd "$DIR" || return
  if [ "$MODE" = git ] && [ -n "$PREV_REF" ]; then
    g checkout --quiet "$PREV_REF" && echo "Pliki projektu: przywrócone ($PREV_REF)." >&2
  elif [ "$MODE" = tar ] && [ -f "$PROJECT_BACKUP" ]; then
    tar -C "$DIR" -xzpf "$PROJECT_BACKUP" --no-overwrite-dir && echo "Pliki projektu: przywrócone z archiwum." >&2
  fi
  if [ -f "$TMP/env.before" ]; then
    cat "$TMP/env.before" > .env && echo "Plik .env: przywrócony." >&2
  fi
  local service
  for service in "${!OLD_ID[@]}"; do
    # Poprzedni obraz wraca pod nazwę, z której korzystała dotychczasowa konfiguracja.
    docker tag "${OLD_ID[$service]}" "${OLD_REF[$service]}" && echo "Obraz $service: przywrócony." >&2
  done
  if docker compose up -d --no-build >/dev/null 2>&1; then
    echo "Usługi uruchomione ponownie w poprzedniej wersji." >&2
  else
    echo "UWAGA: nie udało się uruchomić usług — sprawdź: docker compose ps, docker compose logs" >&2
  fi
  echo >&2
  echo "Kopia stanu sprzed aktualizacji:  ${STATE_BACKUP:-brak}" >&2
  echo "Archiwum plików projektu:         ${PROJECT_BACKUP:-brak}" >&2
  echo "Dane (data/, config/) nie były zmieniane; kopię stanu odtwarza w razie potrzeby host/restore.sh." >&2
}

on_exit() {
  local code=$?
  trap - EXIT INT TERM
  if [ "$CHANGED" = 1 ]; then
    rollback
    [ "$code" -ne 0 ] || code=1
  fi
  [ -z "$TMP" ] || rm -rf "$TMP"
  exit "$code"
}

main() {
  local check=0 yes=0 wanted="" arg
  for arg in "$@"; do
    case "$arg" in
      --check) check=1 ;;
      --yes|-y) yes=1 ;;
      -h|--help) usage; exit 0 ;;
      -*) echo "Nieznana opcja: $arg" >&2; usage >&2; exit 1 ;;
      *) [ -z "$wanted" ] || die "podaj najwyżej jedną wersję"; wanted="$arg" ;;
    esac
  done
  [ "$(id -u)" -eq 0 ] || { echo "Uruchom przez sudo" >&2; exit 1; }
  command -v docker >/dev/null && docker compose version >/dev/null 2>&1 || die "brak Dockera z wtyczką compose"

  UPDATE_DIR="$(readlink -m "${M2S_UPDATE_BACKUP_DIR:-$DIR/backups-update}")"
  local keep="${M2S_UPDATE_KEEP:-5}"
  [[ "$keep" =~ ^[1-9][0-9]*$ ]] || die "M2S_UPDATE_KEEP musi być liczbą dodatnią"
  cd "$DIR"
  [ -f docker-compose.yml ] || die "w $DIR nie ma docker-compose.yml"
  trap on_exit EXIT
  trap 'exit 130' INT TERM

  step "1/6 Wersje"
  CURRENT="$(build_version bridge)"
  local latest=""
  if [ -n "$wanted" ]; then
    TARGET="${wanted#v}"
    [[ "$TARGET" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "wersja ma mieć postać X.Y.Z (np. 0.3.0), jest: $wanted"
  fi
  if [ -z "$TARGET" ] || [ "$check" = 1 ]; then
    latest="$(latest_release)" || latest=""
  fi
  echo "Zainstalowana:      $CURRENT"
  if [ "$check" = 1 ]; then
    echo "Najnowsze wydanie:  ${latest:-nie udało się odczytać (brak sieci, brak wydań albo limit zapytań GitHuba)}"
    if [ -n "$latest" ] && [ "$latest" = "$CURRENT" ]; then
      echo "Zainstalowana wersja jest aktualna."
    elif [ -n "$latest" ] && { newer "$latest" "$CURRENT" || ! is_number "$CURRENT"; }; then
      echo "Aktualizacja: sudo ./host/update.sh"
    fi
    exit 0
  fi
  if [ -z "$TARGET" ]; then
    [ -n "$latest" ] || die "nie udało się odczytać najnowszego wydania z GitHuba — podaj wersję jako argument"
    TARGET="$latest"
  fi
  echo "Docelowa:           $TARGET"
  if [ "$CURRENT" = "$TARGET" ]; then
    echo "Ta wersja jest już zainstalowana — nic do zrobienia."
    exit 0
  fi
  if newer "$CURRENT" "$TARGET"; then
    echo "UWAGA: wersja docelowa jest STARSZA od zainstalowanej."
  fi

  if [ "$yes" != 1 ]; then
    [ -t 0 ] || die "brak potwierdzenia — uruchom w terminalu albo dodaj --yes"
    local answer
    read -r -p "Zaktualizować $CURRENT -> $TARGET? Usługi bridge i supla zostaną na chwilę zatrzymane. [t/N] " answer
    case "$answer" in t|T|tak|TAK|y|Y) ;; *) echo "Przerwano — nic nie zostało zmienione."; exit 0 ;; esac
  fi

  step "2/6 Kontrole wstępne (jeszcze bez zmian)"
  [ -f .env ] || die "brak pliku .env — to nie wygląda na zainstalowaną bramkę (patrz host/install.sh)"
  TMP="$(mktemp -d)"
  if [ -e .git ]; then
    MODE=git
    command -v git >/dev/null || die "katalog projektu jest repozytorium git, a polecenia git nie ma"
    GIT_OWNER="$(stat -c %U .git)"
    [ "$GIT_OWNER" != UNKNOWN ] || GIT_OWNER=root
    if [ -n "$(g status --porcelain --untracked-files=no)" ]; then
      g status --short --untracked-files=no >&2
      die "w katalogu projektu są lokalne zmiany (lista wyżej). Zatwierdź je albo wycofaj (git stash) i uruchom skrypt ponownie."
    fi
    PREV_REF="$(g symbolic-ref -q --short HEAD || g rev-parse HEAD)"
    g fetch --quiet --tags || die "git fetch nie powiódł się"
    g rev-parse -q --verify "refs/tags/v$TARGET^{commit}" >/dev/null || die "w repozytorium nie ma tagu v$TARGET"
    echo "Pliki projektu: repozytorium git (teraz: $PREV_REF), przejście na tag v$TARGET."
  else
    MODE=tar
    mkdir "$TMP/new"
    fetch "https://github.com/$REPO/archive/refs/tags/v$TARGET.tar.gz" "$TMP/source.tar.gz" \
      || die "nie udało się pobrać plików wersji $TARGET (czy takie wydanie istnieje?)"
    tar -xzf "$TMP/source.tar.gz" -C "$TMP/new" --strip-components=1 --no-same-owner
    [ -f "$TMP/new/docker-compose.yml" ] || die "pobrane archiwum nie zawiera projektu"
    # Nic z listy rzeczy nietykalnych nie może przyjść z archiwum, nawet gdyby się w nim znalazło.
    rm -rf "$TMP/new/.env" "$TMP/new/data" "$TMP/new/backups" "$TMP/new/backups-update" \
      "$TMP/new/config/devices.yaml" "$TMP/new/config/supla"
    chown -R "$(stat -c %u:%g "$DIR")" "$TMP/new"
    echo "Pliki projektu: pobrane archiwum wydania v$TARGET (katalog nie jest repozytorium git)."
  fi

  local service id ref repo
  local -a running=()
  mapfile -t running < <(docker compose ps --services --status running 2>/dev/null || true)
  WATCHED=("${OWN_SERVICES[@]}")
  for service in "${running[@]}"; do
    [[ " ${WATCHED[*]} " == *" $service "* ]] || WATCHED+=("$service")
  done
  for service in "${OWN_SERVICES[@]}"; do
    id="$(container "$service")"
    [ -n "$id" ] || continue
    OLD_ID[$service]="$(docker inspect -f '{{.Image}}' "$id")"
    OLD_REF[$service]="$(docker inspect -f '{{.Config.Image}}' "$id")"
  done
  # Obrazy nowej wersji pobieramy zawczasu: jeśli nie są opublikowane, kończymy bez żadnych zmian.
  echo "Pobieram obrazy wersji $TARGET…"
  M2S_VERSION="$TARGET" docker compose pull --quiet "${OWN_SERVICES[@]}" \
    || die "nie udało się pobrać obrazów wersji $TARGET — nic nie zostało zmienione"

  step "3/6 Kopia zapasowa do $UPDATE_DIR"
  local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
  M2S_BACKUP_DIR="$UPDATE_DIR" M2S_BACKUP_KEEP="$keep" "$DIR/host/backup.sh"
  STATE_BACKUP="$(ls -1t "$UPDATE_DIR"/m2s-backup-*.tar.gz 2>/dev/null | head -1 || true)"
  [ -f "$STATE_BACKUP" ] || die "kopia stanu nie powstała"
  PROJECT_BACKUP="$UPDATE_DIR/m2s-project-$stamp.tar.gz"
  local -a exclude=(./data ./backups ./backups-update ./.git ./rcp-build './pre-restore-*'
                    ./.env ./config/devices.yaml ./config/supla)
  case "$UPDATE_DIR/" in "$DIR"/*) exclude+=("./${UPDATE_DIR#"$DIR"/}") ;; esac
  ( umask 077
    tar --numeric-owner -czpf "$PROJECT_BACKUP.tmp" -C "$DIR" --anchored "${exclude[@]/#/--exclude=}" . )
  tar -tzf "$PROJECT_BACKUP.tmp" >/dev/null
  mv "$PROJECT_BACKUP.tmp" "$PROJECT_BACKUP"
  chmod 600 "$PROJECT_BACKUP"
  ls -1t "$UPDATE_DIR"/m2s-project-*.tar.gz | tail -n +"$((keep + 1))" | xargs -r rm -f
  echo "Pliki projektu: $PROJECT_BACKUP"
  ( umask 077; cat .env > "$TMP/env.before" )
  for service in "${!OLD_ID[@]}"; do
    # Stały tag na poprzednim obrazie: nie zniknie przy sprzątaniu obrazów i da się do niego wrócić.
    ref="${OLD_REF[$service]}"; repo="${ref%%@*}"
    case "${repo##*/}" in *:*) repo="${repo%:*}" ;; esac
    docker tag "${OLD_ID[$service]}" "$repo:$ROLLBACK_TAG"
    echo "Poprzedni obraz $service: $repo:$ROLLBACK_TAG"
  done

  CHANGED=1
  step "4/6 Pliki projektu w wersji $TARGET"
  if [ "$MODE" = git ]; then
    g checkout --quiet --detach "v$TARGET"
  else
    apply_tree "$TMP/new"
  fi
  echo "Gotowe."

  step "5/6 Obrazy i start usług"
  set_env_version "$TARGET"
  docker compose pull --quiet "${OWN_SERVICES[@]}"
  docker compose up -d --no-build

  step "6/6 Sprawdzenie"
  verify
  CHANGED=0

  echo
  echo "Aktualizacja zakończona."
  echo "  Wersja przed:            $CURRENT"
  echo "  Wersja po:               $(build_version bridge)"
  echo "  Kopia stanu:             $STATE_BACKUP"
  echo "  Poprzednie pliki:        $PROJECT_BACKUP"
  echo "  Poprzednie obrazy:       tag :$ROLLBACK_TAG"
  echo "Obrazy otbr, matter-server i mqtt nie były aktualizowane."
}

# Całość jest w funkcjach, a wywołanie i wyjście w jednej linii: skrypt podmienia własny plik
# w trakcie działania, a bash czyta plik przyrostowo.
main "$@"; exit

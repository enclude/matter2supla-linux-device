# Rozwiązywanie problemów

Przypadki poniżej pochodzą z rzeczywistych uruchomień (VM Ubuntu 26.04 na Proxmox oraz Raspberry Pi 4B), chyba że zaznaczono inaczej.

## Od czego zacząć

```bash
docker compose ps                               # czy działa pięć usług: otbr, matter-server, mqtt, bridge, supla
docker compose logs --tail 50 <usługa>
docker exec otbr ot-ctl state                   # oczekiwane: leader
docker compose run --rm bridge nodes            # kontroler: dataset, Bluetooth, sparowane węzły
```

W panelu znaczniki „MQTT” i „Matter” w nagłówku pokazują, czy łącznik ma połączenie z brokerem i kontrolerem.

## Border router nie widzi dongla: `spinel_driver … Init() Failure`

**Objaw:** `otbr` kończy pracę i restartuje się, w logach `spinel_driver … Init() Failure`.

**Przyczyna:** otwarcie portu szeregowego ustawia linie DTR/RTS, a układ auto-resetu płytki wprowadza wtedy ESP32 w tryb programowania — RCP nie odpowiada.

**Rozwiązanie:** adres radia musi zawierać opcję `uart-init-deassert`. W `docker-compose.yml` jest ona ustawiona na stałe:

```text
spinel+hdlc+uart:///dev/ttyRCP?uart-baudrate=460800&uart-init-deassert
```

Jeśli zmieniałeś `OT_RCP_DEVICE`, przywróć tę opcję. Sprawdzone na mostku CH340.

Inne rzeczy do sprawdzenia przy tym objawie: czy istnieje `/dev/thread-rcp` (reguła udev, właściwe `idVendor`/`idProduct`), czy `RCP_BAUDRATE` to 460800, czy na płytkę faktycznie wgrano `ot_rcp` ([dongle-esp32-c5.md](dongle-esp32-c5.md)).

## `matter-server` restartuje się z błędem `Unknown interface`

**Przyczyna:** zła nazwa interfejsu w `INFRA_IF` (plik `.env`). Domyślna wartość z `.env.example` to `ens18` — właściwa dla VM, nie dla Raspberry Pi.

**Rozwiązanie:** odczytaj nazwę (`ip -br link`), popraw `.env`, potem `docker compose up -d`.

## `docker compose` zgłasza „ustaw INFRA_IF w pliku .env”

Brakuje pliku `.env` albo zmiennej `INFRA_IF` — patrz [instalacja, krok 7](instalacja-raspberry-pi.md#7-plik-env-i-katalogi).

## Kontroler nie ma Bluetootha

**Objaw:** panel pisze „Bluetooth w kontrolerze jest wyłączony — dostępne tylko parowanie urządzeń będących już w sieci”; `bridge nodes` pokazuje `Bluetooth: False`.

Sprawdź po kolei:

1. Czy stos działa z dodatkiem Bluetooth: w `.env` musi być `COMPOSE_FILE=docker-compose.yml:docker-compose.bluetooth.yml`, a po zmianie `docker compose up -d`.
2. Czy na hoście jest BlueZ (`sudo apt install bluez`).
3. Czy adapter nie jest zablokowany — patrz niżej.

## Bluetooth zablokowany (`rfkill`)

**Objaw (RPi):** wbudowany Bluetooth jest „soft blocked” i nie skanuje.

**Rozwiązanie:**

```bash
sudo rfkill unblock bluetooth
```

Po odblokowaniu skan z Raspberry Pi widział urządzenia w otoczeniu.

## Kontroler pada przy parowaniu: `write EPIPE` (AppArmor a D-Bus)

**Objaw (Ubuntu 26.04):** `matter-server` kończy pracę z `write EPIPE` przy pierwszym użyciu Bluetootha.

**Przyczyna:** domyślny profil AppArmor Dockera blokuje kontenerowi dostęp do D-Bus hosta, przez który kontroler rozmawia z BlueZ.

**Rozwiązanie:** `docker-compose.bluetooth.yml` ustawia dla `matter-server` `security_opt: apparmor=unconfined` — nie usuwaj tego wpisu. Na sprawdzanym Raspberry Pi OS AppArmor nie był aktywny, więc problem tam nie występował.

## „No commissionable device was discovered”

Kontroler nie znalazł urządzenia przez Bluetooth.

- Urządzenie nie jest w trybie parowania albo jest już sparowane z innym systemem — [parowanie.md](parowanie.md#tryb-parowania-urządzenia).
- Urządzenie jest za daleko od hosta.
- **Adapter Bluetooth nie odbiera pakietów.** Tak było w wariancie z VM: adapter hosta Proxmox przekazany do VM działał programowo (kontroler zgłaszał włączony Bluetooth), ale trzy skany nie wykryły żadnego urządzenia — prawdopodobnie brak podłączonych anten. Sprawdź, czy skan Bluetooth na hoście widzi cokolwiek w otoczeniu (np. narzędziem `bluetoothctl` z pakietu BlueZ); jeśli nie, problem leży w adapterze, a nie w konfiguracji. Rozwiązaniem był host z działającym Bluetooth (Raspberry Pi).

## Parowanie przerywa się: `le-connection-abort-by-local` (HCI 0x3e)

**Objaw (RPi 4):** połączenie BLE z urządzeniem jest nawiązywane tylko w części prób; parowanie kończy się błędem po kilku próbach kontrolera.

**Rozwiązanie:** ponowić parowanie tym samym kodem; urządzenie trzymać blisko Raspberry Pi i upewnić się, że nadal jest w trybie parowania. W testach gniazdko sparowało się przy drugim podejściu. Przyczyna niestabilności nie została ustalona.

## Urządzenie nie reaguje po odłączeniu z prądu

**Objaw:** po krótkim wyjęciu gniazdka i ponownym włożeniu komendy z panelu kończą się błędem „komenda nie została wykonana”.

**Wyjaśnienie:** urządzenie wraca do sieci Thread samo, ale kontroler odnawia sesję dopiero po ok. 1–2 minutach. W tym czasie komendy padają. Kanał nie przechodzi od razu w offline, bo subskrypcja kontrolera wygasa dopiero po ok. 1 min 44 s.

**Rozwiązanie:** odczekać 2 minuty. Ponowne parowanie nie jest potrzebne. (Przejście kanału w offline po dłuższym odłączeniu nie było sprawdzane.)

## `supla` pisze „czekam na /etc/supla-device/supla-device.yaml”

To stan normalny do czasu, aż istnieje konto SUPLA **i** pierwszy kanał. Wpisz konto w panelu i sparuj urządzenie obsługiwanego typu.

## Żółty komunikat w panelu o koncie lub konflikcie kanałów

- „Nie podano konta SUPLA…” — wpisz serwer i e-mail w sekcji „Konto SUPLA”.
- „zmiana złamałaby trwałość kanałów SUPLA…” — w `config/devices.yaml` usunięto urządzenie albo zmieniono jego typ. Przywróć poprzednią treść pliku (np. z kopii zapasowej). Wyjaśnienie i procedura resetu: [jak-to-dziala.md](jak-to-dziala.md#trwałość-kanałów-supla).

## Urządzenie nie pojawia się w SUPLA Cloud

- Sprawdź logi: `docker compose logs supla`.
- Czy w SUPLA Cloud włączone jest dodawanie (rejestracja) urządzeń.
- Czy adres serwera i e-mail w panelu są poprawne.
- Niesprawdzone: czy dopisanie kolejnego kanału do już zarejestrowanego urządzenia wymaga ponownego włączenia rejestracji urządzeń w Cloud.

## Łącznik nie startuje po ustawieniu `web.host`

W logach `bridge`: „panel dostępny z sieci wymaga hasła — ustaw M2S_WEB_PASSWORD w .env”. Ustaw hasło i uruchom `docker compose up -d --force-recreate bridge`.

## Kontenery nie mogą zapisać danych

`matter-server`, `bridge` i `supla` pracują jako uid 1000:

```bash
sudo chown -R 1000:1000 data/matter data/supla config
```

## Niesprawdzone wskazówki dla VM

Zapora Proxmox (conntrack) i `multicast_snooping` mostu — patrz [proxmox-vm.md](proxmox-vm.md#zapora-i-multicast).

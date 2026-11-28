# matter2supla-linux-device

Most **Matter over Thread → SUPLA** na Linuksie. Urządzenia Matter (gniazdka, światła) są widoczne w SUPLA Cloud jako kanały jednego wirtualnego urządzenia SUPLA „Matter2Supla”, ze sterowaniem w obie strony.

```text
GRILLPLATS / KAJPLATS / czujniki
   │ Matter over Thread (802.15.4)
ESP32-C5 (firmware ot_rcp) ──USB── host Linux (Raspberry Pi)
                                    ├─ otbr           OpenThread Border Router
                                    ├─ matter-server  kontroler Matter (matterjs-server, WebSocket)
                                    ├─ bridge         łącznik Matter ⇄ MQTT + panel WWW (ten projekt)
                                    ├─ mqtt           Mosquitto
                                    └─ supla          supla-device dla Linuksa ⇄ SUPLA Cloud
```

Wszystkie usługi działają w kontenerach (`docker compose`) w sieci hosta - Matter wymaga IPv6, multicastu i mDNS. ESP32-C5 pracuje jako radio Thread na USB, z niezmienionym firmware `ot_rcp` z ESP-IDF.

## Wymagania

- **Host:** Raspberry Pi z 64-bitowym systemem i wbudowanym Bluetooth (sprawdzone na RPi 4B, Raspberry Pi OS / Debian 13 arm64), podłączony do LAN. Inny Linux też się nadaje, ale nie WSL2.
- **Radio Thread:** płytka ESP32-C5 z firmware `ot_rcp`, podłączona portem **UART** (mostek USB-UART) przez przedłużacz do portu USB 2.0.
- **Bluetooth w hoście** - potrzebny do parowania urządzeń bezpośrednio do własnej sieci Thread.
- Konto SUPLA z włączonym dodawaniem urządzeń (cloud.supla.org → „Moja SUPLA” → rejestracja urządzeń).

## Szybki start

Skrót dla Raspberry Pi; wersja krok po kroku, z wyjaśnieniami: [docs/instalacja-raspberry-pi.md](docs/instalacja-raspberry-pi.md).

1. **Dongle.** Wgraj firmware radia Thread (`ot_rcp`) na płytkę ESP32-C5 prosto z przeglądarki: **<https://matter2supla.zjawa.tech>** - podłącz płytkę kablem USB, kliknij „Wgraj firmware” i wybierz port (Chrome lub Edge na komputerze). Uwaga: wgranie kasuje dotychczasowy firmware płytki. Samodzielna budowa i wgranie przez esptool: [docs/dongle-esp32-c5.md](docs/dongle-esp32-c5.md).

2. **Instalacja.** Podłącz dongle do Raspberry Pi i w katalogu projektu uruchom jedno polecenie:

   ```bash
   sudo ./host/install.sh --lan              # bez --lan panel działa tylko lokalnie (127.0.0.1)
   ```

   Skrypt instaluje Dockera, ustawia system, rozpoznaje dongle, tworzy sieć Thread, uruchamia usługi i włącza codzienną kopię zapasową. Można go uruchamiać ponownie - niczego istniejącego nie nadpisuje. Na końcu wypisuje adres panelu.

3. **Panel WWW** - wpisz konto SUPLA i sparuj urządzenie kodem lub zdjęciem kodu QR: [docs/panel-www.md](docs/panel-www.md), [docs/parowanie.md](docs/parowanie.md).

   ![Panel WWW - kanały SUPLA (przykładowe dane)](docs/img/panel-kanaly.png)

   Jak te urządzenia wyglądają w SUPLA (Cloud i aplikacja mobilna): [docs/widok-w-supla.md](docs/widok-w-supla.md).

4. **Kopia zapasowa** powstaje codziennie o 03:30; pobierz jedną od razu po pierwszym parowaniu i trzymaj poza urządzeniem: [docs/kopie-zapasowe.md](docs/kopie-zapasowe.md).

5. **Aktualizacja** - panel informuje o nowym wydaniu; aktualizuje się ręcznie: `sudo ./host/update.sh` ([docs/aktualizacja.md](docs/aktualizacja.md)).

## Najważniejsze ostrzeżenia

- **Kanały SUPLA są trwałe.** Wolno je tylko dopisywać; usunięcie kanału lub zmiana typu kończy się „Channel conflict” i usunięciem urządzenia z SUPLA Cloud razem z historią. Szczegóły: [docs/jak-to-dziala.md](docs/jak-to-dziala.md).
- **Kopie zapasowe zawierają sekrety** (klucze sieci Thread, parowania Matter, tożsamość urządzenia SUPLA) - trzymaj je w bezpiecznym miejscu.
- **Panel nie szyfruje połączenia** (HTTP). Udostępniaj go tylko w zaufanej sieci LAN i nigdy do Internetu; to samo dotyczy REST OTBR, WebSocket `matter-server` i brokera MQTT (domyślnie nasłuchują tylko na `127.0.0.1`).

## Stan projektu

Co obsługuje łącznik:

| Funkcja | Stan |
| --- | --- |
| Gniazdko / przekaźnik (klaster OnOff): stan, sterowanie, dostępność | działa na sprzęcie (GRILLPLATS) |
| Panel WWW: konto SUPLA, parowanie kodem i zdjęciem QR, kanały ze szczegółami i identyfikacją urządzenia („Znajdź”), lokalne nazwy, odnośniki do SUPLA Cloud, logi, diagnostyka, kopie | działa na sprzęcie |
| Wersjonowane wydania i ręczna aktualizacja `host/update.sh` z kopią i wycofaniem | działa na sprzęcie; wycofanie sprawdzone tylko na atrapach |
| Światła z regulacją bieli (KAJPLATS) jako kanał SUPLA „ściemniacz CCT”: jasność i temperatura bieli | działa na sprzęcie; wymaga protokołu SUPLA 28 na serwerze |
| Światła kolorowe (KAJPLATS CWS) jako kanał SUPLA „ściemniacz CCT + RGB”: jasność, biel i kolor | kanał rejestruje się w SUPLA i działa jako „ściemniacz CCT i RGB”, kolory z panelu docierają do żarówki, stan wraca do Cloud ([zrzuty](docs/widok-w-supla.md)); sterowanie bielą i kolorem z aplikacji SUPLA działa (potwierdzone przez użytkownika) |
| Grupy świateł: jeden kanał SUPLA dla kilku świateł tego samego rodzaju, obok zwykłych kanałów pojedynczych ([opis](docs/panel-www.md#grupy-świateł)) | działa na sprzęcie (cztery KAJPLATS GU10 w jednej grupie): parowanie prosto do grupy, stan w SUPLA, sterowanie z panelu; sterowanie grupą z aplikacji SUPLA do potwierdzenia |
| Licznik energii gniazdka (napięcie, prąd, moc, energia) - kanał jednofazowy | działa na sprzęcie (GRILLPLATS) |
| Czujnik zalania (KLIPPBOK) jako kanał SUPLA `BinaryParsed`, z poziomem baterii | działa na sprzęcie: kanał i poziom baterii widoczne w SUPLA Cloud ([zrzut](docs/widok-w-supla.md)); po zamoczeniu czujnik piszczy, a stan zmienia się w SUPLA (potwierdzone przez użytkownika) |
| Czujnik temperatury i wilgotności (TIMMERFLOTTE) jako kanał SUPLA `ThermHygroMeterParsed`, z poziomem baterii | działa na sprzęcie: odczyty widoczne w aplikacji SUPLA, z baterią (zrzuty: [widok-w-supla.md](docs/widok-w-supla.md)) |
| Monitor jakości powietrza (ALPSTUGA): trzy kanały - temperatura i wilgotność, CO2, PM2.5 | działa na sprzęcie: temperatura, wilgotność, CO2 i PM2.5 widoczne w aplikacji SUPLA (zrzuty: [widok-w-supla.md](docs/widok-w-supla.md)); bez indeksu jakości powietrza |
| Inne czujniki i typy urządzeń | nieobsługiwane - można sparować i pobrać zrzut diagnostyczny |

Jeszcze niesprawdzone na sprzęcie: instalacja `host/install.sh` na czystym systemie, odtworzenie kopii zapasowej na drugim urządzeniu, płytki ESP32-C6 i ESP32-H2 jako dongle.

## Dokumentacja

Spis treści: [docs/README.md](docs/README.md).

| Dokument | Zawartość |
| --- | --- |
| [docs/dongle-esp32-c5.md](docs/dongle-esp32-c5.md) | sprzęt, budowa i wgranie firmware `ot_rcp` |
| [docs/instalacja-raspberry-pi.md](docs/instalacja-raspberry-pi.md) | instalacja krok po kroku: system, Docker, sysctl, udev, `.env`, sieć Thread, Bluetooth, start |
| [docs/proxmox-vm.md](docs/proxmox-vm.md) | wariant z maszyną wirtualną na Proxmox i jego ograniczenia |
| [docs/panel-www.md](docs/panel-www.md) | panel: dostęp i hasło, konto SUPLA, kanały, diagnostyka urządzeń nieobsługiwanych |
| [docs/parowanie.md](docs/parowanie.md) | parowanie urządzeń: tryb parowania, reset, ponawianie |
| [docs/kopie-zapasowe.md](docs/kopie-zapasowe.md) | kopie zapasowe i odtwarzanie na innym urządzeniu |
| [docs/aktualizacja.md](docs/aktualizacja.md) | wersje, powiadomienie o nowym wydaniu w panelu, ręczna aktualizacja `host/update.sh` i cofanie |
| [docs/rozwiazywanie-problemow.md](docs/rozwiazywanie-problemow.md) | znane problemy i ich rozwiązania |
| [docs/jak-to-dziala.md](docs/jak-to-dziala.md) | tematy MQTT, dostępność, trwałość kanałów SUPLA, pliki i porty |
| [docs/cli.md](docs/cli.md) | polecenia `m2s-bridge` |
| [docs/rozwoj.md](docs/rozwoj.md) | testy i dopisywanie obsługi nowych urządzeń |

## Komponenty zewnętrzne

- [openthread/ot-br-posix](https://github.com/openthread/ot-br-posix) - obraz `openthread/border-router`
- [matter-js/matterjs-server](https://github.com/matter-js/matterjs-server)
- [SUPLA/supla-device](https://github.com/SUPLA/supla-device) - `extras/examples/linux`
- ESP-IDF, przykład `examples/openthread/ot_rcp`

## Licencja

Copyright © 2026 Zjawa.IT - Jarosław Zjawiński, <kontakt@zjawa.it>.

Projekt jest wolnym oprogramowaniem na licencji **GNU General Public License w wersji 2 lub (do wyboru) dowolnej późniejszej** (`GPL-2.0-or-later`) - pełny tekst w pliku [LICENSE](LICENSE). W skrócie: wolno używać, kopiować, zmieniać i rozpowszechniać, pod warunkiem zachowania informacji o autorze i licencji oraz udostępniania zmienionych wersji na tej samej licencji, razem z kodem źródłowym. Oprogramowanie jest dostarczane bez jakiejkolwiek gwarancji.

Obraz `matter2supla-supla-device` zawiera [supla-device](https://github.com/SUPLA/supla-device) (AC SOFTWARE SP. Z O.O., `GPL-2.0-or-later`) zbudowany z commita przypiętego w `supla-device/Dockerfile`, z rozszerzeniami z katalogu `supla-device/extensions/`. Pozostałe komponenty (OTBR, matterjs-server, Mosquitto) są używane jako osobne, niezmienione obrazy na własnych licencjach.

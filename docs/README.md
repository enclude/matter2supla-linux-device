# Dokumentacja

Przegląd projektu, wymagania i stan prac: [../README.md](../README.md).

## Wdrożenie — w tej kolejności

1. [Dongle ESP32-C5](dongle-esp32-c5.md) — sprzęt, budowa firmware `ot_rcp` w Dockerze, wgranie przez esptool.
2. [Instalacja na Raspberry Pi](instalacja-raspberry-pi.md) — system, Docker, sysctl, udev, `.env`, sieć Thread, Bluetooth, start stosu.
   - Zamiast RPi: [maszyna wirtualna na Proxmox](proxmox-vm.md) — różnice i znane ograniczenia.
3. [Panel WWW](panel-www.md) — dostęp i hasło, konto SUPLA, kanały, diagnostyka.
4. [Parowanie urządzeń](parowanie.md) — tryb parowania, kod i zdjęcie QR, ponawianie.
5. [Kopie zapasowe i odtwarzanie](kopie-zapasowe.md) — harmonogram, zawartość kopii, przeniesienie na inne urządzenie.

## Eksploatacja

- [Aktualizacja](aktualizacja.md) — wersje i tagi obrazów, powiadomienie w panelu, ręczna aktualizacja `host/update.sh`, cofanie.
- [Rozwiązywanie problemów](rozwiazywanie-problemow.md) — objawy i rozwiązania z rzeczywistych uruchomień.
- [Jak to działa](jak-to-dziala.md) — tematy MQTT, dostępność i offline, trwałość kanałów SUPLA, pliki, porty.
- [Polecenia CLI łącznika](cli.md) — `m2s-bridge` i jego opcje.

## Dla rozwijających projekt

- [Rozwój i testy](rozwoj.md) — testy, zrzuty diagnostyczne jako węzły testowe, budowa obrazów.

## Zrzuty ekranu panelu

Wykonane na przykładowych danych skryptem [`img/make_screenshots.py`](img/make_screenshots.py); opis sekcji w [panel-www.md](panel-www.md).

| Widok | Plik |
| --- | --- |
| Cały panel (komputer) | [img/panel.png](img/panel.png) |
| Cały panel (telefon) | [img/panel-telefon.png](img/panel-telefon.png) |
| Logowanie | [img/panel-logowanie.png](img/panel-logowanie.png) |
| Kanały SUPLA | [img/panel-kanaly.png](img/panel-kanaly.png) |
| Urządzenia Matter bez kanału | [img/panel-bez-kanalu.png](img/panel-bez-kanalu.png) |
| Konto SUPLA | [img/panel-konto-supla.png](img/panel-konto-supla.png) |
| Parowanie urządzenia | [img/panel-parowanie.png](img/panel-parowanie.png) |
| Parowanie w toku | [img/panel-parowanie-trwa.png](img/panel-parowanie-trwa.png) |
| Kopie zapasowe | [img/panel-kopie.png](img/panel-kopie.png) |

## Konwencje w tej dokumentacji

- Polecenia uruchamia się w katalogu projektu na hoście (tam, gdzie leży `docker-compose.yml`), o ile nie napisano inaczej.
- `<adres-rpi>`, `<hasło>`, `svrXX.supla.org`, `<VMID>` itp. to symbole zastępcze — wstaw własne wartości.
- Przy krokach zaznaczono, czy były **uruchomione na sprzęcie** (i na jakim), czy są **nieuruchamiane** / **niesprawdzone**.

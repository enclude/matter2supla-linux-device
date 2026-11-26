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

- [Rozwiązywanie problemów](rozwiazywanie-problemow.md) — objawy i rozwiązania z rzeczywistych uruchomień.
- [Jak to działa](jak-to-dziala.md) — tematy MQTT, dostępność i offline, trwałość kanałów SUPLA, pliki, porty.
- [Polecenia CLI łącznika](cli.md) — `m2s-bridge` i jego opcje.

## Dla rozwijających projekt

- [Rozwój i testy](rozwoj.md) — testy, zrzuty diagnostyczne jako węzły testowe, budowa obrazów.

## Konwencje w tej dokumentacji

- Polecenia uruchamia się w katalogu projektu na hoście (tam, gdzie leży `docker-compose.yml`), o ile nie napisano inaczej.
- `<adres-rpi>`, `<hasło>`, `svrXX.supla.org`, `<VMID>` itp. to symbole zastępcze — wstaw własne wartości.
- Przy krokach zaznaczono, czy były **uruchomione na sprzęcie** (i na jakim), czy są **nieuruchamiane** / **niesprawdzone**.

# Webflasher - wgrywanie firmware dongla z przeglądarki

Statyczna strona z przyciskiem „Wgraj firmware”: przeglądarka (Chrome lub Edge na komputerze) łączy się z płytką przez Web Serial i wgrywa `ot_rcp`. Publiczna instancja: <https://matter2supla.zjawa.tech>.

## Co jest w obrazie

- `ot_rcp` z ESP-IDF (`espressif/idf:release-v5.5`, bez zmian w kodzie) dla ESP32-C5, ESP32-C6 i ESP32-H2, każdy w dwóch wariantach: przez mostek USB-UART (UART0, 460800 bodów) i przez wbudowany port USB układu (USB Serial/JTAG). Buduje je `build-firmware.sh` w pierwszym etapie `Dockerfile`; wynik to scalone obrazy wgrywane od adresu 0 i `manifest.json`.
- [ESP Web Tools](https://github.com/esphome/esp-web-tools) w przypiętej wersji, pobierane z rejestru npm ze sprawdzeniem sumy kontrolnej - strona nie ładuje niczego z zewnętrznych serwerów.
- nginx ze stroną z katalogu `site/`.

Na sprzęcie sprawdzony jest tylko wariant UART dla ESP32-C5 (ten sam, którego używa bramka).

## Uruchomienie

```bash
cd webflasher
docker compose up -d --build          # pierwsza budowa pobiera obraz ESP-IDF (kilka GB) i trwa kilkanaście minut
```

Strona nasłuchuje na porcie 3001 (zmiana: `WEBFLASHER_PORT=8080 docker compose up -d`).

Web Serial działa tylko w bezpiecznym kontekście, więc przed kontenerem musi stać serwer pośredniczący z certyfikatem HTTPS. Bez niego strona zadziała wyłącznie pod `http://localhost:3001`.

## Aktualizacja firmware

Zmień `IDF_IMAGE` (argument budowy w `Dockerfile`) i przebuduj obraz. Wersja ESP-IDF, z której powstał firmware, jest widoczna na stronie obok przycisku oraz w `firmware/idf-version.txt`; sumy kontrolne plików w `firmware/SHA256SUMS`.

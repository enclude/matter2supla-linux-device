# Dongle ESP32-C5 - radio Thread

Płytka ESP32-C5 pracuje jako **RCP** (Radio Co-Processor): samo radio 802.15.4, którym przez port szeregowy steruje border router (`otbr`) na hoście. Wgrywa się na nią niezmieniony przykład `examples/openthread/ot_rcp` z ESP-IDF - w tym repozytorium nie ma własnego firmware.

## Sprzęt

- Płytka z ESP32-C5. Sprawdzona: ESP32-C5 N16R8 (układ ESP32-C5HR8, flash 16 MB, PSRAM 8 MB) z mostkiem USB-UART **CH340** (`1a86:7523`).
- Kabel do portu **UART** płytki (tego z mostkiem USB-UART), nie do natywnego portu USB. Firmware `ot_rcp` domyślnie komunikuje się przez UART0 z prędkością **460800 bodów**.
- Przedłużacz USB i port **USB 2.0** hosta - porty i urządzenia USB 3.0 zakłócają pasmo 2,4 GHz.

Inne mostki USB-UART (np. CP2102N `10c4:ea60`, CH343 `1a86:55d3`) wymagają tylko zmiany identyfikatorów w regule udev - nie były sprawdzane.

## Ostrzeżenie

**Wgranie `ot_rcp` kasuje całą pamięć płytki** - dotychczasowy firmware i jego dane (np. parowania, jeśli płytka pracowała wcześniej jako samodzielna bramka). Tej operacji nie da się cofnąć.

## Wgranie z przeglądarki (najprostsze)

Strona **<https://matter2supla.zjawa.tech>** wgrywa gotowy firmware jednym kliknięciem - bez Dockera, ESP-IDF i esptoola:

1. Otwórz stronę w Chrome albo Edge na komputerze (Firefox, Safari i telefony nie mają dostępu do portu szeregowego).
2. Podłącz płytkę kablem USB do portu **UART** płytki.
3. Kliknij „Wgraj firmware”, wybierz port z listy i poczekaj na komunikat o zakończeniu. Jeśli połączenie się nie udaje, przytrzymaj przycisk BOOT na płytce podczas startu wgrywania.

Strona sama rozpoznaje układ. Firmware to ten sam niezmieniony `ot_rcp` z ESP-IDF v5.5, budowany dla ESP32-C5, ESP32-C6 i ESP32-H2, w wariancie dla mostka USB-UART (UART0, 460800 bodów) i dla wbudowanego portu USB układu. Wszystko dzieje się w przeglądarce; strona niczego nie wysyła na serwer.

Stan sprawdzenia: strona działa i udostępnia firmware, a obraz dla ESP32-C5 ma poprawny nagłówek; **samo wgrywanie przez stronę nie było jeszcze wykonane na płytce**. Na sprzęcie sprawdzony jest ten sam firmware (ESP32-C5, wariant UART) wgrany esptoolem - opis niżej. ESP32-C6, ESP32-H2 i wariant dla wbudowanego portu USB nie były sprawdzane w bramce; przy wbudowanym porcie USB dongle zgłasza się na hoście jako `/dev/ttyACM0`, a nie `/dev/ttyUSB0`.

Kod strony i sposób jej uruchomienia u siebie: [`webflasher/`](../webflasher/README.md).

## Budowa firmware w Dockerze (sprawdzone)

Nie wymaga instalowania ESP-IDF; potrzebny jest tylko Docker. Można to zrobić na dowolnym komputerze, niekoniecznie na docelowym hoście.

```bash
./host/build-rcp-docker.sh rcp-build
```

Skrypt uruchamia obraz `espressif/idf:release-v5.5`, buduje `ot_rcp` dla celu `esp32c5` i zapisuje w katalogu `rcp-build/` (jest w `.gitignore`) trzy pliki: `bootloader.bin`, `partition-table.bin`, `esp_ot_rcp.bin`. Na końcu wypisuje polecenia do wgrania.

Sprawdzone: build z ESP-IDF v5.5.6.

## Wgranie przez esptool

Potrzebny `esptool` w wersji 5 lub nowszej (`pip install esptool`). Wgrywać można z dowolnego systemu, w którym płytka ma port szeregowy: w Linuksie zwykle `/dev/ttyUSB0`, w Windows `COMx` (po instalacji sterownika mostka, np. CH340).

W katalogu z plikami `.bin`:

```bash
esptool --chip esp32c5 --port <PORT> erase-flash
esptool --chip esp32c5 --port <PORT> --baud 460800 write-flash --flash-mode dio --flash-freq 80m \
    --flash-size 2MB 0x2000 bootloader.bin 0x8000 partition-table.bin 0x10000 esp_ot_rcp.bin
```

Sprawdzone: wgranie z Windows przez port `COM`; po wgraniu RCP odpowiadał na zapytanie o wersję przy 460800 bodów. Wgrywanie z Linuksa tymi samymi poleceniami - nieuruchamiane.

## Alternatywa: zainstalowane ESP-IDF (nieuruchamiane)

Jeśli masz ESP-IDF 5.5.x, skrypt buduje i wgrywa jednym poleceniem:

```bash
source ~/esp/esp-idf/export.sh
./host/flash-rcp.sh /dev/ttyUSB0
```

Skrypt wykonuje `idf.py set-target esp32c5`, `idf.py build` i `idf.py -p <port> erase-flash flash`. Nie był uruchamiany.

## Sprawdzenie po podłączeniu do hosta

Po wpięciu dongla do hosta i uruchomieniu border routera (patrz [instalacja](instalacja-raspberry-pi.md)):

```bash
docker exec otbr ot-ctl rcp version
```

Polecenie powinno zwrócić wersję firmware RCP. Jeśli `otbr` nie widzi radia - patrz [rozwiązywanie problemów](rozwiazywanie-problemow.md#border-router-nie-widzi-dongla-spinel_driver--init-failure).

# Parowanie urządzeń

Są dwie drogi. Sprawdzona jest pierwsza.

| Droga | Wymaga | Stan |
| --- | --- | --- |
| **Bezpośrednio do własnej sieci Thread** | Bluetooth w hoście, dataset Thread zapisany w kontrolerze | sprawdzone na RPi 4B (GRILLPLATS, KAJPLATS) |
| **Urządzenie jest już w sieci** (udostępnione z innej aplikacji, np. IKEA) | kod wygenerowany w tamtej aplikacji | **niesprawdzone**; urządzenie zostaje w sieci Thread tamtego huba, dongle nie bierze udziału |

## Przed pierwszym parowaniem

Sprawdź w panelu albo poleceniem:

```bash
docker compose run --rm bridge nodes
```

Pierwszy wiersz powinien zawierać `dataset Thread ustawiony: True` i `Bluetooth: True`. Jeśli nie:

- dataset — [instalacja, krok 10](instalacja-raspberry-pi.md#10-dataset-thread-dla-kontrolera);
- Bluetooth — [instalacja, kroki 5 i 7](instalacja-raspberry-pi.md#5-wi-fi-i-bluetooth) oraz [rozwiązywanie problemów](rozwiazywanie-problemow.md#kontroler-nie-ma-bluetootha).

Warto też mieć już wpisane konto SUPLA ([panel-www.md](panel-www.md#konto-supla)) — bez niego kanał nie powstanie do czasu jego zapisania.

## Tryb parowania urządzenia

Urządzenie musi być w trybie parowania i **nie może być sparowane z innym systemem** (wtedy najpierw reset fabryczny), a podczas parowania powinno znajdować się blisko hosta — Bluetooth ma mały zasięg.

**IKEA GRILLPLATS** (wg instrukcji IKEA):

- reset: przytrzymanie przycisku ON/OFF przez ok. 10 s;
- po włożeniu do gniazdka urządzenie jest w trybie parowania przez 15 minut (w części wersji dopiero po naciśnięciu ON/OFF).

Dla innych urządzeń sposób wejścia w tryb parowania i resetu podaje instrukcja producenta.

## Parowanie w panelu

1. Otwórz panel i przejdź do sekcji „Parowanie urządzenia”.
2. Podaj kod: wpisz `MT:…` / kod numeryczny albo użyj „Zrób zdjęcie kodu QR” (telefon; panel musi być dostępny z LAN — [panel-www.md](panel-www.md#z-sieci-lan--z-hasłem)). Kod QR jest na naklejce urządzenia.
3. Pole „Urządzenie jest już w sieci…” zostaw **odznaczone** (parowanie przez Bluetooth).
4. „Sparuj”. Parowanie trwa do kilku minut (limit: 5 minut); wynik pojawi się pod formularzem.

Po udanym parowaniu urządzenie obsługiwanego typu samo pojawia się w „Kanały SUPLA”, a `supla-device` restartuje się z nową konfiguracją. Sprawdź działanie przyciskami Włącz/Wyłącz, potem ustaw funkcję kanału w SUPLA Cloud.

## Parowanie z wiersza poleceń

```bash
docker compose run --rm bridge commission
```

Polecenie zapyta o kod (dzięki temu kod nie trafia do historii powłoki). Wariant bez Bluetootha, dla urządzenia udostępnionego z innej aplikacji:

```bash
docker compose run --rm bridge commission --network-only
```

## Gdy parowanie się nie uda

Parowanie przez Bluetooth Raspberry Pi 4 bywa zawodne. W testach połączenie BLE z gniazdkiem udawało się w części prób (błąd `le-connection-abort-by-local`, kod HCI 0x3e); pierwsze parowanie nie powiodło się po trzech próbach kontrolera, drugie się udało. Żarówka sparowała się za pierwszym razem.

Co robić:

1. **Ponów.** Kliknij „Sparuj” jeszcze raz z tym samym kodem — to zwykle wystarcza.
2. Sprawdź, czy urządzenie jest nadal w trybie parowania (GRILLPLATS: 15 minut od włożenia do prądu). Jeśli nie — wyjmij i włóż ponownie albo zresetuj.
3. Zbliż urządzenie do Raspberry Pi na czas parowania.
4. Jeśli błąd brzmi „No commissionable device was discovered” przy każdej próbie — kontroler w ogóle nie widzi urządzenia przez Bluetooth: [rozwiązywanie problemów](rozwiazywanie-problemow.md#no-commissionable-device-was-discovered).

Komunikat błędu jest w panelu oraz w logach: `docker compose logs bridge matter-server`.

## Sekrety

Kody parowania `MT:…` i dataset Thread są sekretami — nie zapisuj ich w repozytorium, zgłoszeniach ani zrzutach ekranu. Panel ukrywa oba pola jak hasła.

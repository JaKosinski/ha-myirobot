# iRobot Local

[English README](README.md) · Nieoficjalny projekt, niezwiązany z iRobot Corporation.

Integracja Home Assistant dla robotów iRobot Roomba (testowana pod i7), działająca **wyłącznie lokalnie** przez MQTT/TLS na porcie 8883. Bez chmury iRobot, bez konta, bez pollingu (`local_push`).

## Co daje

- `vacuum` — start / wznowienie, pauza, stop, powrót do bazy, lokalizacja, `send_command`
- sensory — bateria, faza misji, kod błędu (+ opis w atrybucie `message`), czas misji, posprzątana powierzchnia, liczba misji, sygnał Wi-Fi (domyślnie wyłączony)
- binary sensory — pojemnik pełny, brak pojemnika
- przycisk — opróżnij pojemnik do Clean Base (`evac`, tylko i7+)

Połączenie próbuje kolejno szyfrów `AES128-SHA256` i domyślnych OpenSSL (oraz TLS 1.3), z `SECLEVEL=0` i `OP_LEGACY_SERVER_CONNECT` — to omija problemy starego firmware z Pythonem 3.12+/OpenSSL 3.

## Wymagania

- Home Assistant 2025.1 lub nowszy
- Adres IP robota. BLID i hasło integracja pobierze sama (patrz niżej).
- HA musi mieć dostęp do robota na TCP 8883.

## Instalacja

Skopiuj katalog `custom_components/irobot_local` do `/config/custom_components/` na HA, np.:

```bash
scp -r custom_components/irobot_local <user>@<ha-host>:/config/custom_components/
```

Zrestartuj HA, potem: Ustawienia → Urządzenia i usługi → Dodaj integrację → **iRobot Local** i wybierz, skąd wziąć BLID i hasło:

- **Z robota (lokalnie, zalecane)** — podajesz IP, przytrzymujesz HOME ok. 2 s aż robot zagra, w ciągu 30 s klikasz Zatwierdź. BLID przychodzi po UDP 5678, hasło po TLS 8883.
- **Z konta iRobot (chmura)** — gdy lokalnie się nie uda (kreator sam to zaproponuje). E-mail i hasło konta służą tylko do jednorazowego odczytu hasła robota i **nie są zapisywane**. Konta z logowaniem Google/Apple nie mają hasła iRobot i nie zadziałają. Z wieloma robotami na koncie wybierasz właściwego z listy.
- **Ręcznie** — BLID i hasło robota. Hasło zaczyna się od `:1:` — **dwukropek na początku jest częścią hasła**.

Niezależnie od sposobu robot jest potem sterowany wyłącznie lokalnie.

Roboty widoczne dla HA przez DHCP (nazwy `iRobot-*` / `Roomba-*`) pojawiają się jako wykryte, a zmiana IP skonfigurowanego robota aktualizuje się sama. IP i hasło zmienisz też przez **Zmień konfigurację** w integracji, a gdy robot odrzuci hasło (np. po resecie fabrycznym), HA poprosi o nowe.

## Ważne

- **Robot przyjmuje tylko jedno lokalne połączenie MQTT.** Usuń/wyłącz wbudowaną integrację *iRobot Roomba and Braava* dla tego robota i zamykaj aplikację iRobot, gdy HA ma działać. Aplikacja w telefonie przejmuje połączenie i HA pokaże encje jako niedostępne do czasu ponownego połączenia (automatyczne, co 1–60 s).
- Robot bez dostępu do internetu działa lokalnie, ale część firmware okresowo restartuje Wi-Fi, gdy nie widzi chmury.

## Sprzątanie wybranych pokoi

Robot zna tylko numery pokoi ze swojej Smart Map — nazwy nadajesz sam.

1. Raz uruchom w aplikacji iRobot sprzątanie **wszystkich** pokoi, które chcesz mieć w HA (zaznacz je wszystkie). Robot zapamięta to w `last_command`.
2. W HA: Ustawienia → Urządzenia i usługi → iRobot Local → **Konfiguruj**. Id mapy i pokoi wypełnią się same (`5 = Room 5`). Zmień nazwy, np.:

   ```
   5 = Kuchnia
   12 = Salon
   zid:3 = Pod stołem
   ```

   Który numer to który pokój? Uruchamiaj z aplikacji po jednym pokoju i patrz na `last_command`.
3. Otwórz ustawienia encji odkurzacza i przypisz pokoje robota do obszarów (areas) HA.
4. Sprzątaj obszary natywną akcją HA — kolejność obszarów = kolejność sprzątania:

```yaml
action: vacuum.clean_area
target:
  entity_id: vacuum.roomba
data:
  cleaning_area_id:
    - kuchnia
    - salon
```

Według zgłoszeń użytkowników i7 sprząta wybrane pokoje także bez internetu, bo mapę nawigacyjną trzyma w sobie. Bez konfiguracji pokoi nadal działa surowe `send_command`:

```yaml
action: vacuum.send_command
target:
  entity_id: vacuum.roomba
data:
  command: start
  params:
    pmap_id: "<pmap_id>"
    user_pmapv_id: "<user_pmapv_id>"
    ordered: 1
    regions:
      - region_id: "1"
        type: rid
```

## Przejazd mapujący

Przycisk **Przejazd mapujący** wysyła komendę `train`: robot objeżdża dom bez sprzątania i buduje Smart Map. Zatwierdzenie mapy, podział na pokoje i ich nazwy w aplikacji iRobot dalej odbywają się w chmurze — lokalnie tego nie zrobisz.

## Nagrywanie wiadomości MQTT

Przełącznik **Nagrywaj wiadomości MQTT** (kategoria diagnostyczna) zapisuje każdą wiadomość od robota do `/config/irobot_local/<BLID>_<data>.jsonl`, jedna linia JSON na wiadomość. Atrybuty pokazują ścieżkę pliku i liczbę wiadomości. Nagrywanie zatrzymuje się samo po 50 MB i nie przetrwa restartu HA.

Plik zawiera dane sieci (SSID, IP, MAC) i BLID — przejrzyj go przed udostępnieniem.

## Testy

```bash
pip install -r requirements_test.txt
pytest
```

Testy klienta uruchamiają fałszywego robota (TLS 1.2 / AES128-SHA256 + MQTT 3.1.1) na localhost.

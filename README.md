# peugeot80

Automatisch stoppen met laden van een **Peugeot e-2008 (2022)** rond een
instelbaar accupercentage (standaard **80%**).

De e-2008 van deze generatie heeft géén ingebouwde laadlimiet — de auto laadt
altijd door tot 100%. Deze app lost dat lokaal op:

```
   ┌──────────────┐        SoC %        ┌──────────────┐   pauzeer/hervat   ┌──────────────┐
   │  SoC-bron    │ ──────────────────▶ │  controller  │ ─────────────────▶ │  laadpaal    │
   │ cloud / OBD  │                     │  (80% regel) │                    │  Mennekes    │
   └──────────────┘                     └──────────────┘                    └──────────────┘
```

De controller leest periodiek het accupercentage uit, en zodra de drempel
bereikt is stuurt hij "pauzeren" naar de laadpaal. Bij ontkoppelen wordt de
laadpaal weer vrijgegeven.

## Waarom deze opzet

* **Laden stoppen bij de laadpaal (Mennekes via Modbus TCP)** is betrouwbaar en
  volledig lokaal — geen afhankelijkheid van de wisselvallige Stellantis-cloud.
* **SoC uitlezen** kan via de Stellantis-cloud (geen extra hardware) of via een
  OBD-II dongle (realtime/nauwkeuriger). Zie [SoC-bron kiezen](#soc-bron-kiezen).

## Hardware-vereisten

| Onderdeel | Eis |
|---|---|
| Laadpaal | **AMTRON Compact 2.0s / Start 2.0s** → RS-485 **Modbus RTU** (zie hieronder). **AMTRON Xtra / Premium** (HCC3) → Modbus TCP. Geen van beide mogelijk → `cloud_delayed` fallback. |
| SoC-bron | Stellantis connected services (cloud) **of** een OBD-II dongle in de auto. |
| Host | Iets dat 24/7 lokaal draait: Raspberry Pi, mini-PC, NAS met Docker, of Home Assistant. |

## AMTRON Compact 2.0s / Start 2.0s (RS-485 Modbus RTU)

Deze paal heeft **geen netwerkaansluiting**; besturing gaat via **RS-485 Modbus
RTU**. Wat je nodig hebt:

1. **Modbus aanzetten**: met de **MENNEKES Configuration Tool** (via RJ45) én de
   DIP-schakelaar **S1-4 = ON**. (Open je de paal/wijzig je instellingen, betrek
   dan je installateur i.v.m. garantie.)
2. **RS-485-verbinding** naar je host, op één van twee manieren:
   - **USB-RS485-adapter** in een Raspberry Pi naast de paal → `transport: serial`.
   - **RS485↔WiFi-gateway** (bv. een **Elfin EW11**, ~€20) → `transport: tcp`,
     dan praat de app via het netwerk met de gateway.
3. **Besturingsmodel**: de app houdt een **heartbeat** (`0x55AA` → reg `0x0D00`,
   elke ≤8s) in de lucht en zet de **release** (`0x0D05`) + **stroom** (`0x0302`).

> **Belangrijk neveneffect (juist veilig):** zodra Modbus aanstaat, laadt de paal
> **alleen terwijl deze app draait en toestemt**. Valt de app of de host weg, dan
> stopt het laden binnen ~10s (heartbeat weg). Draai de app dus onder
> auto-restart (Docker `restart: unless-stopped` of systemd) en gebruik het
> `status_file`/webhook-alarm.

Registers bevestigen:

```bash
peugeot80 scan-rtu /dev/ttyUSB0            # serial
# of via een gateway:
peugeot80 scan 192.168.1.60                # tcp naar de Elfin EW11
```

## Welk AMTRON-model heb ik?

Weet je het model niet zeker? Draai de scan vanaf je thuisnetwerk:

```bash
peugeot80 scan 192.168.1.50        # IP van je laadpaal
```

De scan probeert verbinding te maken met de Modbus TCP-poort (502), leest de
firmware-/modelregisters en dumpt een registerbereik. Lukt de verbinding →
je hebt een Xtra/Premium en de lokale aanpak werkt. Geen verbinding → waarschijnlijk
een Start/Compact (gebruik dan `cloud_delayed`).

## ⚠️ Veiligheid (brandrisico) — lees dit eerst

De app is **fail-safe** ontworpen: als hij faalt of het netwerk wegvalt, laadt
de auto hooguit door tot 100% (accu-slijtage) — er gaat nooit te veel stroom
lopen. De echte brandrisico's zitten in *hoe* je de stroom onderbreekt:

* **Nooit** schakelen met een gewone slimme stekker/stopcontact (continu hoge
  stroom → oververhitting → brand). Daarom biedt deze app géén smart-plug aan.
* **Nooit** de voeding onder belasting hard wegschakelen. Stoppen gaat via het
  Control-Pilot-signaal (Mennekes HEMS-limiet → 0), zodat de auto netjes
  afbouwt — de manier die de norm voorschrijft.
* **`max_current` nooit hoger zetten dan je circuit/paal aankan.** De app
  weigert onveilige waarden en clamp't elke geschreven stroom.

De volledige analyse staat in **[SAFETY.md](SAFETY.md)**. Lees die voordat je
op echte hardware aansluit.

## Betrouwbaarheid: watchdogs tegen "mist net de 80%"

Naast de fail-safe veiligheid vangt de app ook de *functionele* faalgevallen af
(allemaal getest in `tests/test_watchdog.py`):

* **Stop-marge** (`stop_margin`) — compenseert de vertraging van de cloud-SoC
  zodat je rond 80% uitkomt i.p.v. erboven.
* **Stale-SoC watchdog** (`soc_max_age`, `on_soc_lost`) — als de SoC te lang
  niet ververst terwijl er geladen wordt: alarm, en optioneel preventief
  pauzeren.
* **Pauze-verificatie** (`pause_tolerance`) — blijft de SoC ná het pauzeren
  stijgen, dan werkt de pauze niet (bv. verkeerd Modbus-register) → alarm.
* **Heartbeat + notificaties** (`status_file`, `notify.webhook`) — schrijft elke
  tick de status weg en stuurt alarmen naar een webhook, zodat je merkt als de
  app eruit ligt.

Zie `config.example.yaml` voor alle opties.

## Veilig testen zonder auto of laadpaal

Voordat je iets op de echte auto/laadpaal aansluit kun je de hele logica
risicovrij uitproberen met de ingebouwde **simulator** (een virtuele accu +
virtuele Mennekes). De *échte* controller draait eroverheen; er gaat geen enkel
commando naar echte hardware.

```bash
peugeot80 simulate --start 76 --rate 4 --limit 80
```

Je ziet de SoC oplopen, het laden stoppen op de limiet, de SoC daarna vlak
blijven (paal staat echt uit) en bij "uitpluggen" de latch resetten:

```
SoC=79.8% ... charger=charging
SoC=80.0% ... reached limit -- pausing charge
--> Laden GEPAUZEERD bij 80.0%
--> Na pauze stijgt SoC niet verder (drift +0.00%)
--> Auto losgekoppeld (unplug) -- clearing pause latch
=== RESULTAAT: GESLAAGD ✅ ===
```

Opties: `--limit` drempel, `--start` begin-SoC, `--rate` laadsnelheid (%/s),
`--tick-seconds` snelheid van de demo. Met `-c config.yaml` leest hij je
`charge_limit`/`hysteresis`/`resume_below` uit je eigen config.

### Faalgevallen aantoonbaar testen

`peugeot80 selftest` speelt de **faal-scenario's** end-to-end af tegen de
simulator (met geïnjecteerde fouten) en laat live zien dat elke watchdog afgaat:

```
[1] Stop-marge -> stopt vroeg bij 78%                      ✅
[2] Kapotte pauze (verkeerd register) -> alarm             ✅ 🔔
[3] SoC-feed weg -> alarm + preventief stoppen             ✅ 🔔
[4] Korte hapering -> geen vals alarm                      ✅
[5] Onzin-SoC (150%) -> genegeerd                          ✅
=== SELFTEST RESULTAAT: 5/5 GESLAAGD ✅ ===
```

De geautomatiseerde tests (`pytest`) draaien dezelfde controller tegen de
simulator met een nep-klok, dus volledig deterministisch en zonder hardware.

## Installatie

```bash
pip install -e .
cp config.example.yaml config.yaml
# config.yaml aanpassen (zie hieronder)
peugeot80 run -c config.yaml
```

Of met Docker:

```bash
cp config.example.yaml config.yaml
docker compose up -d
```

## Configuratie

Zie `config.example.yaml` voor alle opties met uitleg. De belangrijkste:

```yaml
charge_limit: 80          # drempel in % waarbij het laden stopt
soc:
  provider: cloud         # cloud | obd
charger:
  provider: mennekes_modbus   # mennekes_modbus | cloud_delayed
```

### SoC-bron kiezen

* **cloud** — gebruikt een draaiende [`psa-car-controller`](https://github.com/flobz/psa_car_controller)
  die het percentage via de Stellantis-API ophaalt. Geen extra hardware, maar
  updates lopen enkele minuten achter; je kunt daardoor iets boven de drempel
  uitkomen (bv. 83% i.p.v. 80%). Prima voor "ongeveer 80%".
* **obd** — leest het percentage realtime via een OBD-II dongle. Nauwkeuriger en
  cloud-onafhankelijk, maar vereist een dongle in de auto en het juiste SoC-PID
  (zie commentaar in `config.example.yaml`).

### Laadregelaar kiezen

* **mennekes_modbus** — pauzeert lokaal via Modbus TCP (aanbevolen).
* **cloud_delayed** — fallback voor "domme" palen: zet de auto via de
  Stellantis-cloud in *uitgesteld laden*. Werkt zonder slimme paal, maar is
  minder betrouwbaar (afhankelijk van de Stellantis-servers).

## Status

Werkend skelet met `cloud` SoC + `mennekes_modbus`/`cloud_delayed` regelaars en
een `scan`-tool. De exacte Modbus-registers zijn **configureerbaar** met
verstandige defaults volgens de MENNEKES ECU Modbus TCP-spec; **bevestig ze met
`peugeot80 scan`** tegen jouw eigen paal voordat je hierop vertrouwt.

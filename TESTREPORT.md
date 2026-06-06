# Testrapport — peugeot80

| | |
|---|---|
| **Project** | peugeot80 — automatisch stoppen met laden bij ~80% (Peugeot e-2008 2022) |
| **Branch / commit** | `claude/gifted-lovelace-6UdH4` @ `313ab74` |
| **Datum** | 2026-06-06 |
| **Omgeving** | Python 3.11, pytest 9.0, pymodbus 3.13 (Linux) |
| **Uitslag** | **40 / 40 geautomatiseerde tests geslaagd** + **5 / 5 live faal-scenario's geslaagd** |
| **Hardware gebruikt** | Geen — alles tegen een in-process simulator (virtuele accu + virtuele Mennekes) |

---

## 1. Samenvatting

De volledige beslis- en bewakingslogica is getest zonder auto of laadpaal, via
een simulator die de échte controller aanstuurt. Alle 40 unittests en alle 5
geïnjecteerde faal-scenario's slagen. De code die met echte hardware/cloud
praat (Modbus-sockets, Stellantis-cloud, OBD) is bewust *niet* end-to-end
getest omdat dat fysieke hardware en accounts vereist — zie §5 (grenzen) en §6
(wat ik van jou nodig heb).

---

## 2. Geautomatiseerde tests (40 stuks)

| Testbestand | Aantal | Dekt |
|---|---|---|
| `test_controller.py` | 7 | Basislogica: pauzeren op limiet, niet eronder, geen geklapper, latch-reset, poll-intervallen |
| `test_scenarios.py` | 14 | Praktijk- en faalsituaties (zie §3) |
| `test_watchdog.py` | 11 | Stop-marge, stale-SoC watchdog, pauze-verificatie, alarm-ontdubbeling, heartbeat-bestand |
| `test_simulator.py` | 4 | End-to-end laden→stoppen→plateau→uitpluggen tegen de virtuele accu |
| `test_charger_safety.py` | 5 | Fysieke veiligheids-clamps (stroom nooit boven installatielimiet) |

Alle 40: **PASSED**, looptijd ~0,3 s.

---

## 3. Geteste scenario's (functioneel)

**Normale werking**
- Laden tot exact de limiet → pauze.
- Auto al boven de limiet bij inpluggen → meteen pauze.
- Idle sessie onder de limiet → laden starten.
- SoC blijft vlak na pauze (paal staat echt uit).

**Faalgevallen**
- Pauze-commando mislukt → wordt herhaald; latch pas na succes.
- Paal hervat uit zichzelf boven de limiet → pauze opnieuw gestuurd.
- SoC-uitlezing faalt → geen crash, geen onveilige actie, toestand behouden.
- Onzin-SoC (None / negatief / >100 / NaN) → genegeerd.
- Paalstatus onleesbaar → toch pauze bij de limiet.

**Latch-levenscyclus**
- Kleine drift na pauze → blijft gepauzeerd (geen onnodig bijladen).
- Grote SoC-daling (cloud, geen uitplug-detectie) → nieuwe sessie herkend.
- `resume_below` onderhoudsmodus.
- Uitpluggen → latch reset → volgende sessie laadt weer.
- Hysterese-band → geen geklapper.

**Configuratievalidatie**
- Ongeldige `charge_limit` / `stop_margin` → geweigerd bij start.

---

## 4. Live faal-demonstratie (`peugeot80 selftest`)

De echte controller, gedraaid tegen de simulator met geïnjecteerde fouten:

| # | Scenario | Verwacht | Resultaat |
|---|---|---|---|
| 1 | Stop-marge 2 bij limiet 80 | stopt bij 78% | ✅ |
| 2 | Kapotte pauze (verkeerd register), SoC stijgt door | 🔔 alarm "pauze werkt niet" | ✅ |
| 3 | SoC-feed weg > `soc_max_age`, `on_soc_lost=pause` | 🔔 alarm + preventief stoppen | ✅ |
| 4 | Korte SoC-hapering (< `soc_max_age`) | géén vals alarm | ✅ |
| 5 | Onzin-SoC (150%) | genegeerd, geen laadcommando | ✅ |

**5/5 geslaagd.**

---

## 5. Codedekking en de grenzen van deze test

| Module | Dekking | Toelichting |
|---|---|---|
| `controller.py` | **92%** | Kernlogica + watchdogs grondig gedekt |
| `simulator.py` | **93%** | Testgereedschap |
| `charger/mennekes_modbus.py` | 58% | Veiligheids-clamps + validatie getest; **echte Modbus-socket I/O niet** |
| `charger/cloud_delayed.py` | 0% | Praat met Stellantis-cloud — vereist account |
| `soc/cloud.py` | 0% | Praat met psa-car-controller — vereist account/VIN |
| `soc/obd.py` | 0% | Vereist OBD-dongle + juist PID |
| `scan.py`, `cli.py` | 0% / n.v.t. | I/O- en CLI-glue, handmatig gedraaid |

> **Wat een simulator principieel niet kan bewijzen:**
> 1. Of de **Modbus-registeradressen** van jouw specifieke AMTRON-firmware kloppen.
> 2. Of de **Stellantis-cloud** betrouwbaar SoC teruggeeft voor jouw VIN.
> 3. Of het **OBD-PID** voor de e-2008 het juiste percentage geeft.
> 4. Timing in de echte wereld (hoe snel de paal daadwerkelijk afbouwt).
>
> Vangnet hiervoor: faal-scenario 2 (pauze-verificatie) **detecteert** een fout
> register in de praktijk — als de pauze niet werkt gaat het alarm af in plaats
> van stilletjes te falen. En `peugeot80 scan` bevestigt de registers read-only.

---

## 6. Wat is nodig om dit realistisch te maken

Zie de hoofdtekst / README §"Wat ik van je nodig heb". Kort:
1. Exact **AMTRON-model + firmwareversie** en of Modbus TCP aan staat.
2. `peugeot80 scan <IP>`-output (registers bevestigen).
3. **Installatie-rating** (max. laadstroom A) voor `max_current`.
4. Keuze SoC-bron: cloud (VIN + werkende psa-car-controller) of OBD (PID).
5. Eén echte test-laadsessie met `status_file` aan, zodat we de logs kunnen
   nakijken.

---

## 7. Reproduceren

```bash
pip install -e ".[obd]" pytest pytest-cov
pytest -v --cov=peugeot80          # 40 unittests
peugeot80 selftest                 # 5 live faal-scenario's
peugeot80 simulate --start 76 --rate 4 --limit 80   # happy-path demo
```

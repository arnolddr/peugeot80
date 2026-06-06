# Veiligheid — wat kan er in de echte wereld misgaan?

Korte samenvatting vooraf:

> **De software die *beslist wanneer* het laden stopt, is op zichzelf geen
> brandrisico.** Het ergste dat gebeurt als de app faalt, crasht of het netwerk
> wegvalt, is dat de auto gewoon doorlaadt tot 100% (slijtage van de accu) —
> niet dat er te veel stroom gaat lopen.
>
> Brandrisico ontstaat door **hoe** je de stroom fysiek onderbreekt en door het
> **commanderen van te hoge stroom**. Daar zijn deze app en deze documentatie
> op gericht.

Deze app is een **gemakslaag**, geen veiligheidssysteem. De echte beveiliging
zit in de auto (BMS), de laadkabel en de laadpaal (aardlek-, temperatuur- en
overstroombeveiliging). Laat de installatie altijd door een erkend installateur
uitvoeren.

---

## 1. Fail-safe ontwerp

De controller is bewust *fail-safe*:

* Is de SoC onbekend/onzin (sensor weg, cloud down, waarde <0 of >100/NaN) →
  de app doet **niets** dat onveilig is en verhoogt nooit de stroom.
* Is de laadpaal onbereikbaar → de app blijft **proberen te stoppen** bij de
  limiet en geeft nooit een hogere stroom vrij.
* Mislukt het pauzeer-commando → het wordt elke tick **opnieuw geprobeerd**;
  de "gepauzeerd"-vlag wordt pas gezet als het écht is gelukt.
* Ziet de app dat de paal uit zichzelf weer is gaan laden boven de limiet →
  het pauzeer-commando wordt **opnieuw gestuurd**.

Worst case bij een bug/uitval = doorladen tot 100%. Dat is slecht voor de
accu-levensduur, maar geen brandgevaar (auto en paal beveiligen zichzelf).

Deze gedragingen zijn allemaal afgedekt met tests in
`tests/test_scenarios.py`.

---

## 2. De échte fysieke brandrisico's (en hoe we ze vermijden)

### 2a. NOOIT schakelen met een gewone slimme stekker / stopcontact
Een EV trekt **urenlang continu hoge stroom** (vaak dicht tegen de maximale
belasting van het stopcontact). Gewone "slimme stekkers" en huishoudstopcontacten
zijn **niet gemaakt voor continue EV-belasting**. De contacten lopen warm →
contactweerstand loopt op → smelten → **brand**.

> Dit is precies waarom de "granny-kabel in een gewoon stopcontact + slimme
> plug"-oplossing gevaarlijk is. Deze app biedt daarom **bewust geen
> smart-plug-aansturing** aan.

### 2b. NOOIT de voeding onder belasting wegschakelen (relais/contactor)
Een actieve laadstroom (bv. 16 A) hard onderbreken met een relais/contactor,
zónder de auto eerst te laten afbouwen, geeft **vlambogen** aan de contacten →
contactlassen of brand.

De **juiste** manier om te stoppen is via het **Control-Pilot-signaal**: de
laadpaal vraagt de auto om af te bouwen naar 0 A, en pas dán opent de auto haar
contactor — bij nagenoeg geen stroom (IEC 61851). Dat is precies wat de Mennekes
intern doet als wij de **HEMS-stroomlimiet op 0** zetten via Modbus.

> Daarom: stoppen via de **Modbus HEMS-limiet** van de Mennekes, **niet** via
> een domme stroomonderbreker aan de voedingskant.

### 2c. NOOIT een stroom commanderen boven de installatielimiet
Dit is het **enige brandrisico dat de software zélf kan introduceren**: als
`max_current` hoger staat dan de bekabeling/zekering/paal aankan, zou hervatten
een te hoge stroom commanderen → oververhitte kabel → brand.

Borging in de code (`charger/mennekes_modbus.py`):

* De app **weigert te starten** als `max_current` buiten 1..32 A valt of niet
  bij je installatie past — je krijgt een fout, geen risico.
* Elke geschreven stroom wordt **geclamped** naar maximaal `max_current`
  (`_clamp_current`), dus er kan nooit meer gevraagd worden.
* Een HEMS-limiet kan technisch alleen **verlágen** ten opzichte van het eigen
  maximum van de paal (de DIP-switch/instelling in de Mennekes), nooit verhogen.
  Dat is een tweede, onafhankelijke beveiliging.

> **Stel `max_current` altijd in op de werkelijke rating van je circuit/paal,
> nooit hoger.** Bij twijfel: vraag je installateur.

### 2d. Verkeerd Modbus-register beschrijven
Als een registeradres niet klopt (firmware verschilt per AMTRON), doet een
schrijfactie vrijwel altijd ofwel **niets** (laden gaat door tot 100% → geen
brand) ofwel hij **faalt** met een foutmelding. De paal valideert intern altijd
haar eigen veiligheidsgrenzen, ongeacht wat er via Modbus binnenkomt.

> Borging: **verifieer de registers met `peugeot80 scan`** tegen jouw eigen paal
> vóór je hierop vertrouwt. De `scan` is read-only.

---

## 3. Wat de app NIET doet / niet vervangt

* Het vervangt **geen** beveiliging van auto, kabel of paal.
* Het is **geen** vervanging voor een correcte, door een installateur
  aangelegde laadinstallatie met aardlekbeveiliging (type B / 6 mA DC).
* Het garandeert geen exact stoppunt: bij de **cloud-SoC** kan de waarde enkele
  minuten achterlopen, waardoor je iets boven 80% kan uitkomen. Dat is een
  accu-levensduur-kwestie, geen veiligheidskwestie.

---

## 4. Aanbevolen, veilige opstelling

1. Stoppen via de **Mennekes Modbus HEMS-limiet** (Control-Pilot), niet via
   een stroomonderbreker of slimme stekker.
2. `max_current` = de werkelijke rating van je circuit/paal.
3. Registers eerst bevestigen met `peugeot80 scan`, daarna `peugeot80 status`.
4. Laat de elektrische installatie door een erkend installateur uitvoeren.

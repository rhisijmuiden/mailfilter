# DKIM-sleutelrotatie

[English](../dkim-rotation.md) · [Alle documentatie](README.md)

Elk domein (transport en relay) ondertekent uitgaande mail met een
DKIM-sleutel. Mailfilter vervangt elke sleutel volgens een vast schema, zonder
moment waarop mail niet door DKIM komt. Instellingen en status:
**DNS-toolbox → DKIM-sleutels**.

## Zo gaat één rotatie

1. **Nieuwe sleutel gepubliceerd.** Naast de huidige sleutel komt een nieuwe
   2048-bit sleutel met een nieuwe selector (`mfJJJJMM`, bijv. `mf202611`).
   Staat de zone van het domein in Cloudflare, dan wordt het record
   `mf202611._domainkey.<domein>` daar aangemaakt (zichtbaar in het
   DNS-wijzigingslog en terug te draaien). De huidige sleutel blijft
   ondertekenen.
2. **Gezien in DNS.** Mailfilter vraagt de eigen nameservers van het domein
   (geen cache) totdat het record er staat.
3. **Ondertekenen met de nieuwe sleutel** na *wachten na publiceren*
   (standaard 48 uur), zodat elke DNS-cache het nieuwe record heeft.
4. **Oude sleutel weg** na *oud record bewaren* (standaard 7 dagen): het oude
   record gaat uit Cloudflare (alleen het record met precies de oude publieke
   sleutel) en de oude privésleutel wordt van de server verwijderd.

De timer `mailfilter-dkim-rotate.timer` draait elke zes uur; **Nu controleren**
doet dezelfde stappen meteen. Vanaf de commandoregel: `mailfilter-admin dkim-rotate`.

## Welke domeinen automatisch roteren

* de sleutel is ouder dan *vervangen na* (standaard 6 maanden), en
* de huidige sleutel staat in DNS (een domein dat nog niet live is, blijft met rust), en
* de zone van het domein staat in Cloudflare, tenzij **Ook domeinen buiten
  Cloudflare** aan staat. Dan toont Mailfilter het nieuwe record en wacht tot je
  het zelf hebt gepubliceerd; tot die tijd blijft de oude sleutel ondertekenen.
  Haal na de overlap het oude record weg bij je DNS-provider (de pagina zegt welk).

**Nu vervangen** start meteen een rotatie voor elk domein. Een rotatie kan
worden geannuleerd zolang de nieuwe sleutel nog niet ondertekent.

## Cluster

Eén server roteert: de eerste op naam. De nieuwe sleutel gaat direct na de
wissel via de normale configuratiesynchronisatie naar de andere servers; het
oude record blijft tijdens de overlap in DNS, dus een server die iets later
synchroniseert ondertekent nog steeds met een sleutel die klopt. De
instellingen worden gesynchroniseerd; op de andere servers staat welke server
de rotatie doet.

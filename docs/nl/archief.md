# Mailarchief

[English](../archive.md) · [Alle documentatie](README.md)

Het archief bewaart een **onveranderbare kopie van alle mail per domein**, inkomend
en uitgaand, tot 10 jaar lang. Het is doorzoekbaar, elke dag wordt verzegeld en je
kunt aantonen dat er niets is aangepast of verwijderd. Te vinden onder
**Mailverkeer › Archief**.

## Wat er in komt

Zet het archief per domein aan (tab *Domeinen*, alleen beheerders). Vanaf dan:

- komt alle mail die echt is **afgeleverd** bij het domein of **verstuurd** vanaf het
  domein in het archief, ook mail die uit de quarantaine is vrijgegeven;
- komen geweigerde spam en virussen er **niet** in, en ook geen mail die je in de
  quarantaine verwijdert.

Hoe: Postfix voegt één extra ontvanger toe, `<domein>@archive.mailfilter.invalid`
(`sender_bcc_maps` / `recipient_bcc_maps`, bestand `archive_bcc`). Die kopie reist
mee met het bericht en wordt afgeleverd bij de `mfarchive`-pipe, dus alleen als het
bericht zelf wordt afgeleverd. De mail trace toont deze extra ontvanger nooit.

## Onveranderbaar

- **Bestanden**: elke mail wordt één keer opgeslagen, gecomprimeerd en alleen-lezen,
  in `/var/lib/mailfilter-archive/<domein>/<bewaar-tot-maand>/<dag>/`. Elke nacht
  vergrendelt de root-helper de afgelopen dagen (`chattr +i`).
- **Hash-keten**: elke mail krijgt een SHA-256-vingerafdruk en een hash die vastzit
  aan die van de vorige mail (per domein).
- **Dagzegels**: elke nacht wordt per domein de dag verzegeld en ondertekend met de
  Ed25519-archiefsleutel van deze server. De publieke sleutel staat op de tab
  *Domeinen* en in elke export.
- **Database**: triggers weigeren wijzigingen aan verzegelde mail, verwijderen vóór
  de bewaartermijn of onder juridische bewaarplicht, en elke wijziging aan zegels.
- **Controle**: *Integriteit controleren* en de nachtelijke taak rekenen alles na
  (op zondag ook elk bestand). Bij problemen komt er een melding.

## Bewaartermijn en juridische bewaarplicht

- 1 tot 10 jaar per domein (standaard 7), tot het einde van de maand. Daarna wordt
  mail automatisch verwijderd en komt dat in het auditlog.
- Een kortere termijn geldt alleen voor mail die daarna gearchiveerd wordt.
- **Juridische bewaarplicht** stopt elk verwijderen voor een domein, ook na de termijn.

## Zoeken en exporteren

Zoek op woorden in onderwerp, tekst en bijlagenamen, op adressen (ook bcc), richting,
domein en datum. **Exporteren** geeft een zip met de `.eml`-bestanden en een
overzicht met vingerafdrukken, keten, dagzegels en publieke sleutel, plus uitleg om
het zonder Mailfilter te controleren. Klanten zien alleen hun eigen domeinen; elke
zoekopdracht, weergave, download en export komt in het auditlog.

## Back-ups en cluster

De archiefmap gaat mee in elke volledige en incrementele back-up (ook op afstand).
Na een restore worden de bestanden opnieuw vergrendeld. In een cluster worden de
instellingen per domein gedeeld; elke server archiveert de mail die via hem loopt.

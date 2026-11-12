# Mailtrap

Mailtrap vangt mail van testomgevingen op (staging- en acceptatieservers, de
laptop van een ontwikkelaar), zodat die **nooit wordt doorgestuurd**. De mail komt
in een Mailtrap-postvak, waar je hem in de webinterface of in een mailprogramma
leest en controleert of hij er goed uitziet en werkt.

## Aanzetten

Mailtrap is een module en staat na installeren of updaten **uit**. Een beheerder
zet hem aan met de schakelaar bovenaan **Mailverkeer › Mailtrap**. Zolang hij uit
staat:

- ziet een klant het menu-item niet;
- wordt mail van de adressen en logins van Mailtrap-postvakken **geweigerd**
  (`5.7.1 Mailtrap is switched off`), nooit doorgestuurd;
- is IMAP (poort 993) dicht.

## Een postvak

**Postvak toevoegen** vraagt om:

- **Naam** en eventueel de **klant** (domein). Gebruikers van die klant kunnen
  het postvak openen; geef andere gebruikers toegang onder *Wie mag kijken*.
- **Welke mail wordt opgevangen**: IP-adressen of netwerken (IPv4 en IPv6) van
  de testomgeving. Een adres staat óf bij Relay-toegang óf in de Mailtrap, nooit
  allebei, en één adres hoort bij één postvak. Netwerken groter dan /16 (IPv4)
  of /32 (IPv6) worden geweigerd.
- Eventueel **ook mail opvangen die met de login van het postvak wordt
  verstuurd**, voor testomgevingen zonder vast adres: die versturen via poort 587
  met de login `trap-<naam>` en het wachtwoord dat één keer wordt getoond.
- **Wat er met de mail gebeurt**: *Alles opvangen*, of *Alles opvangen, behalve
  naar testers*: adressen of hele domeinen (`@voorbeeld.nl`) op de testerslijst
  krijgen de mail ook echt, zodat je hem in Gmail of Outlook ziet. Er blijft een
  kopie in de Mailtrap.
- **Inloggen met een mailprogramma (IMAP)**: IMAP over TLS op poort 993,
  gebruiker `trap-<naam>`, hetzelfde wachtwoord. SMTP-accounts kunnen niet
  inloggen op IMAP.
- Hoe lang mail bewaard blijft (standaard 7 dagen) en hoeveel berichten
  maximaal (standaard 1000); oudere mail wordt verwijderd zodra er nieuwe binnenkomt.

## Lezen en controleren

Het postvak toont elk bericht met labels voor gevonden problemen. Een bericht
openen toont:

- **Weergave**: de HTML zoals de ontvanger hem ziet (desktop- of
  telefoonbreedte). Afbeeldingen van internet worden niet geladen.
- **Controle**: spamscore, links naar testomgevingen (privé-adressen, `.local`,
  `.test`, `staging.`, andere poorten), links zonder HTTPS, niet ingevulde
  sjabloonvelden (`{{ order.id }}`), DKIM-handtekening, From gelijk aan de
  envelope-afzender, HTML- en tekstdeel, afbeeldingen zonder alt-tekst, grootte
  (Gmail knipt af boven 102 KB), onderwerp, datum en Message-ID,
  afmeldheaders bij bulkmail, bijlagen.
- **Tekstversie**, **Headers** en **Bron** (downloaden als `.eml`).

**Doorsturen naar mij** stuurt een kopie als bijlage naar je eigen adres.
**Toch versturen** (alleen beheerders) stuurt het bericht naar de oorspronkelijke
ontvangers, zonder de Mailtrap-headers; dit komt in het auditlog.

In Mailtrace heeft een opgevangen bericht de status **In Mailtrap**.

## Technische details

- Postfix: een trap-adres of -login krijgt een restriction class `mft_<naam>`
  die de mail aan de `mftrap`-pipe (master.cf) geeft, die
  `mailfilter-trap-deliver` draait als gebruiker `mailfilter`. De controles
  (`/etc/mailfilter/postfix/mailtrap.cidr`, `mailtrap_sasl`) staan op elke
  listener vóór `permit_mynetworks`, en trap-adressen staan niet in
  `mynetworks`, dus een trap-adres kan nooit relayen.
- Mail wordt als Maildir opgeslagen in
  `/var/lib/mailfilter/mailtrap/<login>/Maildir`. Mislukt het opslaan, dan houdt
  Postfix de mail in de wachtrij en probeert het later opnieuw; hij wordt nooit
  doorgestuurd.
- Rspamd behandelt trap-adressen als uitgaand (DKIM-ondertekening, geen
  greylisting), zodat spamscore en DKIM-controle de echte situatie tonen.
- IMAP: Dovecot laadt `/etc/mailfilter/dovecot-mailtrap.conf` (geschreven door
  de webinterface) met logins uit `/etc/mailfilter/mailtrap-users`. De firewall
  opent poort 993 alleen zolang een postvak IMAP toestaat.
- Mailtrap-postvakken worden niet gesynchroniseerd in een cluster: elke node
  houdt zijn eigen postvakken.
- Na het updaten van een bestaande server: voer de update uit via het
  Info-scherm (die draait de installer opnieuw) voordat je de module aanzet; de
  webinterface weigert de module aan te zetten zolang Postfix de
  Mailtrap-controles nog niet heeft.

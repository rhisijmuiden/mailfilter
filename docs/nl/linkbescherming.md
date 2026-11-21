# Linkbescherming

[English](../link-protection.md) · [Alle documentatie](README.md)

Phishingsites zijn vaak onschuldig als een bericht binnenkomt en een uur later
gevaarlijk. Met linkbescherming controleert Mailfilter een link **op het moment
dat iemand klikt**, niet alleen bij aflevering.

## Zo werkt het

1. **Aflevering.** Voor domeinen met de regel aan vervangt Rspamd elke
   http(s)-link in de tekst en HTML van binnenkomende mail door
   `https://<mailfilter-host>/l/<code>.<handtekening>`. De code bevat de echte
   link, de Message-ID, de ontvanger en de tijd; door de handtekening (BLAKE2b
   met `/etc/mailfilter/link-protection.key`) kan niemand zelf zulke links
   maken, dus de klikpagina is nooit een open doorverwijzing. Er wordt per link
   niets opgeslagen. Links naar je eigen domeinen en naar domeinen onder
   *Links niet aanpassen* blijven zoals ze zijn. Het bericht krijgt de header
   `X-Mailfilter-Links: protected; <n>`.
2. **Klik.** De web app controleert de bestemming op dat moment: je
   blokkeerlijst, Spamhaus DBL, SURBL en URIBL (via de lokale resolver),
   dezelfde controles als de phishingregels (kaal IP-adres,
   `www.bank.nl@evil.example`, domeinen die op die van jou lijken) en
   linkverkorters worden gevolgd naar hun echte bestemming. Een link die
   Rspamd of een phishingregel bij aflevering al noemde, of mail van een valse
   afzender, geeft ook een waarschuwing.
3. **Uitkomst.** Veilig: de ontvanger gaat direct door. Verdacht: een
   waarschuwingspagina met *Toch doorgaan* (uit te zetten). Gevaarlijk: een
   blokkadepagina zonder doorgaan. De pagina's zijn Engels en Nederlands, in de
   huisstijl.

Elke klik staat onder **Mailverkeer › Linkklikken**. Beveiligingsscanners en
linkvoorbeelden worden gemarkeerd en standaard verborgen. Beheerders kunnen daar
een domein blokkeren; dat stopt ook de links in mail die al is afgeleverd. Een
geblokkeerde klik, of iemand die na een waarschuwing toch doorgaat, kan een
melding geven (Instellingen › Meldingen, "Gevaarlijke link aangeklikt").
Klikken worden 180 dagen bewaard.

## Aanzetten

**Configuratie › Aangepaste regels › Phishing › Links controleren bij het
klikken.** Standaard uit; aan voor iedereen of per domein. Klanten kunnen het
voor hun eigen domeinen aan- of uitzetten.

## DKIM en ondertekende mail

Een link vervangen verandert het bericht, waardoor de DKIM-handtekening van de
afzender niet meer klopt. Daarom:

- Mailfilter controleert SPF, DKIM en DMARC **voordat** er iets verandert; de
  uitslag staat in `Authentication-Results`.
- Ondertekende of versleutelde mail (S/MIME, PGP) en doorgestuurde berichten
  als bijlage worden nooit aangepast.
- Aangepaste mail krijgt **geen ARC-zegel**: Rspamd zegelt de oorspronkelijke
  body, dus het zegel zou ongeldig zijn (getest met Rspamd).
- Zet linkbescherming alleen aan voor domeinen waarvan de mailserver achter
  Mailfilter DKIM/DMARC niet nog eens controleert, of zet afzenders waarvan de
  handtekening heel moet blijven onder *Afzenders overslaan*.
- Uitgaande mail en mail van je eigen domeinen wordt nooit aangepast.

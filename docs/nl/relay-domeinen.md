# Transport-domeinen en relay-domeinen

Mailfilter kent twee soorten domeinen (Configuratie › Domeinen):

| | Transport-domein | Relay-domein |
|---|---|---|
| Inkomende mail | Aangenomen, gefilterd en afgeleverd op de mailserver van het domein | Niet aangenomen (de MX blijft bij de eigen provider) |
| Uitgaande mail | Ondertekend met DKIM, gecontroleerd, begrensd | Ondertekend met DKIM, gecontroleerd, begrensd |
| Mailserver om op af te leveren | Verplicht | Geen |

Een relay-domein is voor webservers en applicaties die alleen mail versturen.

## Instellen

1. **Domeinen › Domein toevoegen**, vul de naam in en kies **Relay-domein**.
2. Bij opslaan maakt Mailfilter meteen een DKIM-sleutel (RSA 2048, selector `mfJJJJ`).
3. Koppel onder **Wie mag namens dit domein versturen** het IP-adres van de
   webserver en/of maak een SMTP-account. Het wachtwoord wordt één keer getoond;
   gebruik de hostnaam van de Mailfilter, poort 587 (STARTTLS) of 465 (SSL/TLS).
4. Controleer onder **DNS-records** DKIM, SPF en DMARC. Staat het domein in
   Cloudflare, dan publiceer je elk record met één knop (eerst getoond, ongedaan
   te maken onder DNS › Wijzigingslog). Anders kopieer je de records naar de
   DNS-provider en klik je op **Opnieuw controleren**.

SPF: standaard voegt Mailfilter `include:_spf.<mailfilter-host>` toe aan het
record dat er al staat. Kies "Alleen de Mailfilter" als alle mail van het domein
erdoor gaat, met `~all` of `-all`.

## Afzenderslot

"Gekoppelde servers en accounts mogen alleen als @domein versturen" (standaard
aan bij nieuwe relay-domeinen, ook te gebruiken bij transport-domeinen): mail
van de gekoppelde IP-adressen en SMTP-accounts met een andere envelope-afzender
wordt geweigerd met `5.7.1 This server may only send mail as @domein`.
Subdomeinen en bounces (lege afzender) mogen wel.

## Omzetten

Een relay-domein kan een transport-domein worden (vul de mailserver in) en
andersom (Mailfilter neemt dan geen inkomende mail meer aan). Naam, limieten,
gekoppelde IP-adressen en accounts blijven.

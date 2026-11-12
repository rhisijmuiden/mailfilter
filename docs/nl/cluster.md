# Cluster

Meerdere Mailfilter-servers kunnen dezelfde configuratie houden. Elke server blijft een complete gateway: valt er één uit, dan blijven de andere mail filteren met de configuratie die ze hebben, en de server die weg was loopt bij zodra hij terug is. Een enkele server heeft hier niets mee te maken en werkt precies zoals voorheen.

## Instellen

1. Op de eerste server: **Systeem > Cluster > Cluster aanmaken**, geef hem een naam (bijvoorbeeld `mailfilter01`).
2. Installeer Mailfilter op de nieuwe server met de installer, zoals altijd.
3. Op de eerste server: **Server toevoegen**, kopieer de koppelcode.
4. Op de nieuwe server: **Systeem > Cluster > Aansluiten bij een bestaand cluster**, plak de code en geef een naam (bijvoorbeeld `mailfilter02`).

De nieuwe server neemt de configuratie van het cluster over: zijn eigen domeinen, gebruikers en DKIM-sleutels worden vervangen. Daarna log je erop in met je clusteraccount.

Publiceer daarna in de DNS-toolbox het gedeelde SPF-record (`_spf.<eerste server>`) opnieuw, zodat de adressen van alle servers erin staan, en geef elk domein een MX-record voor elke server.

## Wat wordt gesynchroniseerd

| Gelijk op elke server | Apart per server |
| --- | --- |
| Domeinen en hun afleverinstellingen | Mailwachtrijen en quarantaine |
| Relay-toegang (IP-adressen, extra poorten) | Mailtrace, logs, auditlog |
| Throttling, adresroutes, TLS-regels | Backups en hun schema |
| Gebruikers met wachtwoord en domeinen | Updates en het productie-/testkanaal |
| Eigen regels, bescherming tegen gehackte afzenders | Gepauzeerde afzenders (elke server bewaakt zijn eigen mail) |
| E-mail verzenden (SMTP), Cloudflare-token | DMARC-rapporten, blacklistcontroles, MTA-STS-bestanden |
| Toegestane en geblokkeerde afzenders, instellingen quarantainerapport | |
| DKIM-sleutels | Hostnaam, IP-adressen, certificaat |

## Hoe het werkt

* Alle servers zijn gelijk. Een wijziging op welke server dan ook verhoogt de *revisie* van het cluster (tijdstip plus server) en gaat meteen naar de andere. De timer `mailfilter-cluster.timer` controleert elke minuut elke andere server en haalt op of verstuurt wat ontbreekt. De nieuwste revisie wint; zijn er binnen dezelfde minuut op twee servers wijzigingen gedaan terwijl ze elkaar niet konden bereiken, dan wint de laatste.
* Een server past een ontvangen configuratie toe met dezelfde controles als een wijziging in de webinterface (`mailfilter-config apply`). Weigert de mailserver hem, dan verandert er op die server niets en toont de Cluster-pagina waarom.
* Servers praten via HTTPS op poort 443 (het certificaat wordt gecontroleerd). Elk bericht is bovendien versleuteld en ondertekend met de gedeelde clustersleutel (Fernet: AES en HMAC), is vijf minuten geldig en wordt maar één keer geaccepteerd. De klokken van de servers moeten dus kloppen (NTP).
* **Uit cluster verwijderen** (voor een server die weg is) vervangt ook de clustersleutel, zodat de verwijderde server en oude koppelcodes niet meer werken. **Cluster verlaten** haalt de server eruit; hij houdt zijn configuratie en gaat zelfstandig verder.
* Op de commandline: `mailfilter-admin cluster-sync` en `mailfilter-admin cluster-leave` (als gebruiker `mailfilter`).

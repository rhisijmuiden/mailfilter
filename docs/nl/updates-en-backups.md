# Updates en backups

## Versies en branches

Het versienummer staat in het bestand `VERSION` (bijvoorbeeld `0.1.0`). De server draait een git-checkout in `/opt/mailfilter` op een van twee branches:

| Branch | Kanaal | Getoond als |
| --- | --- | --- |
| `main` | Productie | `0.1.0` |
| `test` | Test, elke wijziging komt hier eerst | `0.1.0-test+abc1234` (versie, branch en commit) |

## Updaten

Open in de webinterface **Info** en klik op **Controleren op updates**. Je ziet de nieuwste versie van beide branches en de wijzigingen sinds jouw versie. **Updaten** doet dan:

1. een backup maken (incrementeel, of volledig als die aan de beurt is);
2. de checkout naar de nieuwste commit van de branch zetten;
3. de installer opnieuw draaien met de opgeslagen instellingen (`install.sh --reconfigure`), die de configuratie bijwerkt en de diensten herstart;
4. als stap 3 mislukt, de vorige versie terugzetten en de installer opnieuw draaien, zodat de server blijft werken.

Wisselen tussen productie en test gaat op dezelfde manier. Een branch die deze updater nog niet bevat, kan niet vanuit de webinterface geïnstalleerd worden; je krijgt dan een melding en er verandert niets.

De update draait als achtergrondtaak onder systemd en loopt dus door als de webinterface herstart. Voortgang en log zie je in de webinterface (`mailfilterctl job status ID` en `mailfilterctl job log ID` op de server). Er draait steeds maar één taak tegelijk (update, backup of restore).

## Backups

Een backup bevat:

| Onderdeel | Waar het vandaan komt |
| --- | --- |
| Bestanden | `/etc/mailfilter` (alle instellingen, lijsten, wachtwoorden), `/var/lib/rspamd/dkim` (DKIM-sleutels), `/etc/letsencrypt` (certificaten), `/var/spool/postfix/hold` (quarantaine) |
| Database | PostgreSQL-database `mailfilter` (mailtrace, gebruikers, instellingen), altijd volledig |
| Leerdata spamfilter | Redis: Bayes, fuzzy hashes, greylisting |

Een **volledige** backup bevat alle bestanden. Een **incrementele** backup bevat alleen wat sinds de vorige backup is veranderd, dus klein en snel. Een volledige backup met zijn incrementele backups heet een keten. De nachtelijke backup maakt een volledige backup als de laatste ouder is dan `full_every_days` (standaard 7) en anders een incrementele. De oudste ketens worden verwijderd als er meer zijn dan `keep_full` (standaard 4).

Backups staan in `/var/backups/mailfilter/<keten>/<stap>/` als gewone `tar`-archieven (zstd), een `pg_dump`-bestand en een Redis-dump, met een `manifest.json` met controlegetallen. Een backup-id ziet eruit als `20261111T023000Z.003`: keten plus stapnummer.

De instellingen staan in `/etc/mailfilter/backup.json` en zijn aan te passen in de webinterface of met `mailfilterctl backup config SLEUTEL=WAARDE`:

| Instelling | Standaard | Betekenis |
| --- | --- | --- |
| `enabled` | `true` | Nachtelijke backup aan of uit |
| `time` | `02:30` | Tijdstip van de nachtelijke backup (servertijd) |
| `full_every_days` | `7` | Na zoveel dagen een nieuwe volledige backup |
| `keep_full` | `4` | Aantal volledige backups (met hun incrementele) dat bewaard blijft |
| `location` | `/var/backups/mailfilter` | Map voor de backups |

Externe locaties (FTP, FTPS, SCP) komen in een latere versie.

## Terugzetten

Kies in de webinterface een backup en klik op **Terugzetten**, of draai `mailfilterctl restore ID`. Terugzetten:

1. controleert de controlegetallen van de backup en van elke backup waarop hij voortbouwt;
2. maakt eerst een volledige veiligheidsbackup van de huidige situatie ("before restore");
3. stopt Postfix, Rspamd en de webinterface;
4. zet de bestanden terug: de volledige backup en daarna elke incrementele tot en met de gekozen, zodat bestanden die op dat moment verwijderd waren ook weer weg zijn;
5. zet de database en de leerdata van het spamfilter terug;
6. draait de installer met de teruggezette instellingen en start alles weer.

Met `--only files,database,redis` zet je alleen bepaalde onderdelen terug. Dit werkt ook op een vers geïnstalleerde server: installeer Mailfilter, kopieer de backupmap naar `/var/backups/mailfilter` en zet hem terug.

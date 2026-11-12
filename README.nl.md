<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="huisstijl/logo/rhis-logo-wit.svg">
    <img src="huisstijl/logo/rhis-logo.svg" alt="RHIS" height="56">
  </picture>
</p>

<h1 align="center">RHIS Mailfilter</h1>

<p align="center">
  <strong>Een moderne mailfilter-gateway en vertrouwde relay voor Debian 13.</strong><br>
  Spam- en virusfilter in beide richtingen, een mailtrace die in gewone taal vertelt wat er met elk bericht gebeurde, en een heldere webinterface voor jou en je klanten.
</p>

<p align="center">
  <a href="CHANGELOG.md"><img alt="Versie" src="https://img.shields.io/badge/versie-1.0.0-E8553D"></a>
  <img alt="Debian 13" src="https://img.shields.io/badge/Debian-13%20trixie-A81D33">
  <img alt="IPv4 en IPv6" src="https://img.shields.io/badge/IPv4%20%2B%20IPv6-ja-1F2937">
  <img alt="Talen" src="https://img.shields.io/badge/talen-English%20%7C%20Nederlands-1F2937">
</p>

<p align="center">
  <a href="README.md">English</a> · <b>Nederlands</b>
</p>

---

Mailfilter staat tussen het internet en je mailservers. Binnenkomende mail wordt op spam, virussen en phishing gecontroleerd voordat hij de mailbox bereikt; uitgaande mail van webservers, applicaties en mailservers wordt gecontroleerd, met DKIM ondertekend en begrensd voordat hij vertrekt. Het vervangt Proxmox Mail Gateway met dezelfde kern (Postfix, Rspamd, ClamAV) en een webinterface die de vraag beantwoordt die iedereen als eerste stelt: *is mijn mail aangekomen, en zo niet, waarom niet?*

De interface is standaard Engels en volledig in het Nederlands vertaald; iedere gebruiker kiest zijn eigen taal.

## Inhoud

- [Handleidingen](#handleidingen)
- [Functies](#functies)
- [Snel aan de slag](#snel-aan-de-slag)
- [Hoe het werkt](#hoe-het-werkt)
- [Updates, branches en backups](#updates-branches-en-backups)
- [Opdrachtregel](#opdrachtregel)
- [Ontwikkelen](#ontwikkelen)
- [Indeling van de repository](#indeling-van-de-repository)

## Handleidingen

| | Nederlands | English |
| --- | --- | --- |
| **Installatie**: van kale server tot eerste mail | [Installatiehandleiding](docs/nl/installatie.md) | [Installation guide](docs/installation.md) |
| **Gebruikershandleiding**: voor klanten en dagelijks gebruik | [Gebruikershandleiding](docs/nl/gebruikershandleiding.md) | [User manual](docs/user-guide.md) |
| **Beheerdershandleiding**: elke instelling uitgelegd | [Beheerdershandleiding](docs/nl/beheerdershandleiding.md) | [Administrator manual](docs/admin-guide.md) |
| Alle documentatie | [docs/nl/](docs/nl/README.md) | [docs/](docs/README.md) |

Uitgebreide naslagpagina's: [updates en backups](docs/nl/updates-en-backups.md), [relaydomeinen](docs/nl/relay-domeinen.md), [DKIM-sleutelrotatie](docs/nl/dkim-rotatie.md), [Mailtrap](docs/nl/mailtrap.md), [archief](docs/nl/archief.md), [cluster](docs/nl/cluster.md), [REST API](docs/nl/api.md).

## Functies

### Filteren in beide richtingen

- **Spamfilter** met Rspamd: reputatie, Bayes-leren, fuzzy hashes, DNS-blocklists (Spamhaus en andere, via een eigen resolver), greylisting en postscreen aan de deur.
- **Virusscan** met ClamAV op binnenkomende *en* uitgaande mail.
- **Authenticatie**: SPF-, DKIM- en DMARC-controle op binnenkomende mail, ARC voor doorgestuurde mail, DKIM-ondertekening van alle uitgaande mail per domein.
- **Phishingbescherming**: lookalike-domeinen (`rh1s.nl`), afzendernamen die zich voordoen als je eigen mensen (CEO-fraude), verdachte links en een optionele banner *externe afzender*.
- **Linkbescherming** (volgende release): links in binnenkomende mail worden opnieuw gecontroleerd op het moment dat erop wordt geklikt.
- **Bijlagebeleid**: gevaarlijke bestandstypen, Office-macro's en versleutelde archieven blokkeren of in quarantaine zetten.
- **Nieuwsbrieffilter**: herkent bulkmail en bestraft nieuwsbrieven zonder werkende afmeldlink.
- **Eigen regels** die je aan- of uitzet voor alle domeinen of per domein, elk met een gewone uitleg van wat hij doet.
- **Toestaan- en blokkeerlijsten** per mailbox, per domein of voor iedereen.
- **Ontvangercontrole**: mail voor adressen die niet bestaan wordt aan de deur geweigerd (vraag het aan de mailserver, LDAP / Active Directory of een vaste lijst), dus geen backscatter.
- **Bescherming tegen backscatter en mailloops**, en bouncemails met uitleg in het Engels en Nederlands.

### Zien wat er gebeurde

- **Mailtrace**: zoek elk bericht op afzender, ontvanger, message-ID, queue-ID of server. Een groen vinkje betekent afgeleverd; anders staat de reden er direct bij, in gewone taal, met *wie het kan oplossen*.
- **Routeplaatje**: bronserver → Mailfilter → bestemming, met spamscore en status per stap.
- **Spamcheckrapport** per bericht: een checklist van elke regel die raakte, gegroepeerd en uitgelegd.
- **Live dashboard**: mail per uur of dag, gestopte spam, problemen, wachtrij, topafzenders en een lijst *waar je naar moet kijken* (vastzittende mail, veel bounces, ongewoon verzendgedrag, gestopte services, blocklistvermeldingen).

### Quarantaine en terugkoppeling

- **Quarantaine** voor spam, virussen en riskante bijlagen. Vrijgeven of blokkeren met één klik; het spamfilter leert van elke keuze.
- **Dagelijks quarantainerapport** per e-mail, met een vrijgeefknop per bericht (geen login nodig).
- **Spammeldingen** vanuit Outlook, een telefoon of elke mailapp: doorsturen naar `spam@` of `notspam@` en Mailfilter leert ervan als het de melding kan vertrouwen.

### Domeinen en uitgaande mail

- **Transportdomeinen** (in- en uitgaand, afgeleverd bij je mailserver, met reserveservers) en **relaydomeinen** (alleen uitgaand, voor webservers en applicaties).
- **Relaytoegang**: één eenvoudig scherm om te zeggen *dit IP mag via Mailfilter versturen*. Servers van een klant mogen alleen versturen namens de eigen domeinen van die klant.
- **SMTP-accounts** voor afzenders zonder vast IP, op poort 587 en 465 plus extra poorten naar keuze.
- **Throttling** per server, per account en per afzenderdomein; vertraagregels voor grote ontvangende providers; **een eigen afleverwachtrij per domein**.
- **Gehackte afzenders automatisch pauzeren**: een server of account die plotseling spam of een vloed aan mail verstuurt wordt vastgehouden, er gaat niets verloren, en je geeft hem met één klik weer vrij.
- **TLS-beleid** per bestemming, uitgaand DANE.
- **Microsoft 365 en Google Workspace** achter Mailfilter, met een stap-voor-stapwizard die de PowerShell-opdrachten en DNS-records voor je schrijft.

### DNS makkelijk gemaakt

- **DNS-toolbox** met Cloudflare-koppeling: domein controleren, SPF en DMARC genereren, MX opzoeken, blocklists controleren, records met één knop publiceren en ongedaan maken vanuit het wijzigingslog.
- **Go live**-knop die de MX van een domein naar Mailfilter omzet.
- **DKIM-sleutels** met **automatische rotatie**.
- **MTA-STS, TLS-RPT en DANE**, gehost en gepubliceerd door Mailfilter.
- **BIMI**: upload je logo, Mailfilter zet het om naar SVG Tiny PS, controleert je VMC/CMC-certificaat en publiceert het record.
- **DMARC-rapporten**: Mailfilter ontvangt de rapporten en toont per domein de weg naar `p=reject`, met de afzenders die nog falen.
- **Blocklistbewaking** van Mailfilters eigen IP-adressen met een grote melding op het dashboard.

### Voor klanten en resellers

- **Logins voor beheerders, klanten en resellers**. Klanten zien alleen mail en instellingen van hun eigen domeinen.
- **Maandrapport** per klant per e-mail: ontvangen mail, gestopte spam en virussen.
- **Verbruik per klant** (domeinen, actieve mailboxen, verkeer, archiefopslag), prijzen, resellerkorting en CSV-export voor de facturatie.
- **Mailtrap**: vang alle mail van test- en acceptatieomgevingen op in virtuele mailboxen zodat hij nooit wordt verstuurd, met een ingebouwde controle van elk bericht (links naar testhosts, niet-ingevulde templatevelden, grootte, DKIM en meer). Ook via IMAP te lezen.
- **Mailarchief**: een onveranderbare, doorzoekbare kopie van alle mail per domein tot 10 jaar, met hashketen en elke nacht verzegeld, met juridische bewaarplicht (legal hold) en export.

### Beheer en beveiliging

- **Moderne interface** in het Engels en Nederlands, menu aan de linkerkant, licht en donker thema, werkt op een telefoon, **Ctrl+K** om overal te zoeken.
- **Tweestapsverificatie** (TOTP, herstelcodes), optioneel verplicht voor beheerders of voor iedereen.
- **Auditlog** van elke wijziging, te filteren en als CSV te exporteren.
- **REST API** met API-sleutels per onderdeel, IP-beperking, vervaldatum en ingebouwde OpenAPI-documentatie.
- **Meldingen** per e-mail, Telegram of webhook (JSON, Slack, Mattermost, Discord): service gestopt, wachtrij loopt vol, blocklist, certificaat, gepauzeerde afzender, backup- of updateprobleem.
- **Eigen SMTP-instellingen** voor mail die Mailfilter zelf verstuurt.
- **Cluster**: houd meerdere Mailfilter-servers op dezelfde configuratie; elke server blijft zelfstandig filteren als een andere uitvalt.
- **Importeren uit Proxmox Mail Gateway**: domeinen, relaynetwerken, adresroutes, TLS-regels, toestaan- en blokkeerlijsten en DKIM-sleutels uit een PMG-backupbestand.

### Beheer van de server

- **Installer met één opdracht** voor een kale Debian 13-server, direct aan het internet: HTTPS met Let's Encrypt, firewall (nftables), CrowdSec, IPv4 en IPv6, 's nachts beveiligingsupdates.
- **Info-knop** met de draaiende versie, *Controleren op updates*, *Nu updaten* en een schakelaar tussen het **productie**- (`main`) en **test**kanaal (`test`). Elke update maakt eerst een backup en draait zichzelf terug als er iets misgaat.
- **Backups**: volledig en incrementeel, 's nachts volgens schema, terugzetten vanuit de webinterface, en versleutelde kopieën naar **SCP/SFTP, FTPS of FTP** met terugzetten vanaf die externe locatie.

## Snel aan de slag

Je hebt een kale Debian 13-server nodig met een publiek IPv4-adres (en liefst IPv6), poort 25 open in beide richtingen, en een DNS-naam die ernaar wijst.

```bash
# 1. Laat A- (en AAAA-)records voor bijv. mailfilter01.voorbeeld.nl naar de server wijzen.
# 2. Als root op de server:
apt-get install -y git
git clone -b main https://github.com/rhisijmuiden/mailfilter.git /opt/mailfilter
/opt/mailfilter/install/install.sh
```

De installer vraagt om de hostnaam, een e-mailadres voor de beheerder en eventueel het eerste domein, en toont aan het eind de login voor de webinterface. Open `https://<hostnaam>/` en log in. De [installatiehandleiding](docs/nl/installatie.md) beschrijft alle opties, de DNS-records die je nodig hebt en hoe je een domein overzet.

Gebruik `-b test` in plaats van `-b main` voor een testserver die elke wijziging als eerste krijgt.

## Hoe het werkt

```
                 ┌──────────────────────────── Mailfilter ────────────────────────────┐
 Internet ──25──▶│ postscreen ▸ Postfix ▸ Rspamd (spam, SPF/DKIM/DMARC, regels) ▸ ClamAV│──▶ jouw mailserver
                 │                         │                                          │
 Webservers ─587/465/extra──▶ relaytoegang / SMTP-accounts ▸ zelfde controles ▸ DKIM  │──▶ Internet
                 │                         │                                          │
                 │   wachtrij per domein · quarantaine · archief · mailtrap           │
                 │   mailtrace-verzamelaar ▸ PostgreSQL ◂ webapp (FastAPI + React) ◂─┼── HTTPS 443 (Caddy, Let's Encrypt)
                 └────────────────────────────────────────────────────────────────────┘
```

| Onderdeel | Rol |
| --- | --- |
| Postfix | SMTP in en uit, postscreen, wachtrij per domein, relaypoorten, ontvangercontrole |
| Rspamd | Spamscore, SPF, DKIM ondertekenen en controleren, DMARC, ARC, greylisting, limieten, eigen regels |
| ClamAV | Virusscan in beide richtingen |
| Redis | Leergegevens van Rspamd, greylisting, tellers voor limieten |
| PostgreSQL | Database van de webapp: mailtrace, gebruikers, instellingen, rapporten, archiefindex |
| Dovecot | Alleen wachtwoordcontrole voor SMTP-accounts en IMAP voor Mailtrap; geen mailboxen |
| unbound | Eigen DNS-resolver voor blocklists en DNSSEC |
| Caddy + Let's Encrypt | Webinterface alleen via HTTPS, hosting van het MTA-STS-beleid; het certificaat wordt ook voor SMTP-TLS gebruikt |
| nftables + CrowdSec | Firewall (22, 25, 80, 443, 465, 587 en je extra relaypoorten) en automatisch blokkeren van aanvallers |
| `mailfilterctl` | Updates, kanaal wisselen, backups, terugzetten (roothulp die de webapp gebruikt) |

## Updates, branches en backups

| Branch | Kanaal | Doel |
| --- | --- | --- |
| `main` | Productie | Stabiele releases, zie [CHANGELOG.md](CHANGELOG.md) |
| `test` | Test | Elke wijziging komt hier eerst en draait op de testserver |

Klik in de webinterface op **Info** om de versie te zien, op updates te controleren, bij te werken of van kanaal te wisselen. Backups (volledig en incrementeel, lokaal en extern) staan onder **Systeem › Backups**. Details: [docs/nl/updates-en-backups.md](docs/nl/updates-en-backups.md).

## Opdrachtregel

Alles kan vanuit de webinterface, maar deze tools werken ook als root op de server:

| Opdracht | Wat het doet |
| --- | --- |
| `mailfilterctl info` | Versie, kanaal, commit en versies van de onderdelen |
| `mailfilterctl update check` / `update apply [--branch main\|test]` | Updates zoeken en installeren, of van kanaal wisselen |
| `mailfilterctl backup create --type full\|incremental\|auto` | Nu een backup maken |
| `mailfilterctl backup list` / `verify ID` | Backups tonen en controleren |
| `mailfilterctl restore ID [--from-remote] [--only files,database,redis]` | Een backup terugzetten |
| `mailfilterctl remote show\|set\|test\|list\|sync` | Externe backuplocatie |
| `/var/lib/mailfilter/venv/bin/mailfilter-admin set-password EMAIL` | Nieuw wachtwoord voor een weblogin |
| `/var/lib/mailfilter/venv/bin/mailfilter-admin reset-2fa EMAIL` | Tweestapsverificatie uitzetten bij een verloren telefoon |
| `/opt/mailfilter/install/install.sh --reconfigure` | De hele configuratie opnieuw toepassen |

## Ontwikkelen

```bash
# API (heeft een PostgreSQL-database nodig)
python3 -m venv .venv && .venv/bin/pip install -e "backend[test]"
export MAILFILTER_DB_URL=postgresql+psycopg://mailfilter:dev@127.0.0.1/mailfilter MF_COOKIE_SECURE=0 APP_SECRET=dev
.venv/bin/mailfilter-admin migrate && .venv/bin/mailfilter-admin create-admin jij@voorbeeld.nl
.venv/bin/uvicorn mailfilter.main:app --reload
# Interface, stuurt /api door naar de API op poort 8000
cd frontend && npm ci && npm run dev
```

Tests: `cd backend && pytest` (zet `MAILFILTER_TEST_DB_URL` op een lege testdatabase). CI rendert en controleert ook de configuratie van Postfix, Rspamd en Dovecot in een Debian 13-container. De teksten van de interface staan in `frontend/src/i18n/en.json` en `nl.json`; CI controleert dat beide dezelfde sleutels hebben.

## Indeling van de repository

| Pad | Inhoud |
| --- | --- |
| `backend/mailfilter/` | API van de webapp (FastAPI, SQLAlchemy, Alembic): routers in `api/`, modellen in `models/`, migraties in `migrations/versions/` |
| `backend/mailfilter_config/` | Zet de gewenste configuratie om naar Postfix- en Rspamd-bestanden, met controles en terugdraaien |
| `backend/mailfilter_dns/` | DNS-toolbox en Cloudflare-koppeling (ook als CLI: `python3 -m mailfilter_dns`) |
| `backend/mailfilter_tracer/` | Logverzamelaar die de mailtrace opbouwt |
| `backend/mailfilter_pmg/` | Lezer voor Proxmox Mail Gateway-backups |
| `frontend/` | Webinterface (React, TypeScript, Vite, Tailwind) |
| `install/` | Installer voor Debian 13 en de configuratiesjablonen |
| `ops/` | `mailfilterctl`: updates, backups, terugzetten |
| `docs/` | Handleidingen en naslag (Engels, Nederlands in `docs/nl/`) |
| `huisstijl/` | RHIS-huisstijl: tokens, Archivo-lettertype, logo's |
| `prototype/` | Het oorspronkelijke klikbare prototype van de interface |

## Licentie

[GNU General Public License v3.0](LICENSE).

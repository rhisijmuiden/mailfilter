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
  <a href="LICENSE"><img alt="Licentie AGPL-3.0" src="https://img.shields.io/badge/licentie-AGPL--3.0-1F2937"></a>
  <img alt="Talen" src="https://img.shields.io/badge/talen-English%20%7C%20Nederlands-1F2937">
</p>

<p align="center">
  <a href="README.md">English</a> · <b>Nederlands</b>
</p>

---

Mailfilter staat tussen het internet en je mailservers. Binnenkomende mail wordt op spam, virussen en phishing gecontroleerd voordat hij de mailbox bereikt; uitgaande mail van webservers, applicaties en mailservers wordt gecontroleerd, met DKIM ondertekend en begrensd voordat hij vertrekt. Het vervangt Proxmox Mail Gateway met dezelfde kern (Postfix, Rspamd, ClamAV) en een webinterface die de vraag beantwoordt die iedereen als eerste stelt: *is mijn mail aangekomen, en zo niet, waarom niet?*

De interface is standaard Engels en volledig in het Nederlands vertaald; iedere gebruiker kiest zijn eigen taal.

<p align="center">
  <img src="docs/screenshots/dashboard.png" alt="Dashboard van Mailfilter: berichten, afgeleverd, spam tegengehouden en problemen van de afgelopen 24 uur, met een live grafiek van de laatste 60 minuten" width="100%">
</p>

## Inhoud

- [In het kort](#in-het-kort)
- [Handleidingen](#handleidingen)
- [Rondleiding](#rondleiding)
  - [Dashboard](#dashboard) · [Mailtrace](#mailtrace) · [Quarantaine](#quarantaine) · [Filteren en bescherming](#filteren-en-bescherming) · [Lijsten en spammeldingen](#toestaan-en-blokkeren-en-spammeldingen) · [DMARC-rapporten](#dmarc-rapporten) · [Linkbescherming](#linkbescherming)
  - [Domeinen](#domeinen) · [Relay-toegang](#relay-toegang) · [Throttling en gehackte afzenders](#throttling-en-gehackte-afzenders) · [Microsoft 365 en Google Workspace](#microsoft-365-en-google-workspace) · [DNS-toolbox](#dns-toolbox)
  - [Mailtrap](#mailtrap) · [Archief](#archief) · [Maandrapport en verbruik](#maandrapport-en-verbruik) · [Klanten, gebruikers en beveiliging](#klanten-gebruikers-en-beveiliging) · [API](#api)
  - [Meldingen en e-mail verzenden](#meldingen-en-e-mail-verzenden) · [Backups](#backups) · [Updates en kanalen](#updates-en-kanalen) · [Cluster](#cluster) · [Huisstijl](#huisstijl) · [Import uit Proxmox Mail Gateway](#import-uit-proxmox-mail-gateway) · [Elk scherm, overal](#elk-scherm-overal)
- [Snel aan de slag](#snel-aan-de-slag)
- [Hoe het werkt](#hoe-het-werkt)
- [Updates, branches en backups](#updates-branches-en-backups)
- [Opdrachtregel](#opdrachtregel)
- [Ontwikkelen](#ontwikkelen)
- [Indeling van de repository](#indeling-van-de-repository)
- [Licentie](#licentie)

## In het kort

- **Filtert in beide richtingen**: spam, virussen, phishing en gevaarlijke bijlagen in binnenkomende mail; spamcontrole, DKIM-ondertekening en limieten op uitgaande mail van je webservers en mailservers.
- **Beantwoordt "waar is mijn mail?"** in gewone taal: elk bericht, elke ontvanger, de reden en wie het kan oplossen.
- **Gemaakt voor hostingbedrijven**: logins voor klanten en resellers die alleen hun eigen domeinen zien, maandrapporten, verbruik voor de facturatie, een REST API voor je panel.
- **DNS wordt voor je geregeld**: SPF, DKIM (met rotatie), DMARC, MTA-STS, DANE en BIMI, met één knop gepubliceerd in Cloudflare en terug te draaien.
- **Draait zichzelf**: installer met één opdracht voor Debian 13, updates vanuit de webinterface met automatisch terugdraaien, versleutelde backups naar SCP, FTPS of FTP, meldingen via e-mail, Telegram of webhook.
- **Engels en Nederlands**, licht en donker, werkt op een telefoon.

## Handleidingen

De handleidingen zijn in het Engels geschreven. Van elke handleiding is er een Nederlandse vertaling.

| Handleiding | Voor wie | Nederlands |
| --- | --- | --- |
| [Installation guide](docs/installation.md) | De server inrichten: vereisten, opties van de installer, eerste keer inloggen, een domein overzetten | [Installatiehandleiding](docs/nl/installatie.md) |
| [User manual](docs/user-guide.md) | Klanten en dagelijks gebruik: mailtrace, quarantaine, lijsten, spammeldingen, Mailtrap, archief, rapporten | [Gebruikershandleiding](docs/nl/gebruikershandleiding.md) |
| [Administrator manual](docs/admin-guide.md) | Elk scherm en elke instelling, hoe een bericht gefilterd wordt, naslag voor de server | [Beheerdershandleiding](docs/nl/beheerdershandleiding.md) |
| [All documentation](docs/README.md) | Overzicht van handleidingen en naslagpagina's | [Alle documentatie](docs/nl/README.md) |

Naslagpagina's in het Nederlands: [updates en backups](docs/nl/updates-en-backups.md), [relaydomeinen](docs/nl/relay-domeinen.md), [DKIM-sleutelrotatie](docs/nl/dkim-rotatie.md), [Mailtrap](docs/nl/mailtrap.md), [linkbescherming](docs/nl/linkbescherming.md), [archief](docs/nl/archief.md), [cluster](docs/nl/cluster.md), [REST API](docs/nl/api.md).

## Rondleiding

De schermafbeeldingen komen van een demoserver met verzonnen klanten en domeinen, en tonen de Engelse interface; alles is ook in het Nederlands beschikbaar (zie [Elk scherm, overal](#elk-scherm-overal)). Ze worden gemaakt met de scripts in [`tools/screenshots`](tools/screenshots/README.md), zodat ze na elke release ververst kunnen worden.

### Dashboard

Alles wat de afgelopen 24 uur of 30 dagen door de gateway ging: berichten in en uit, afgeleverd, spam tegengehouden en problemen, steeds vergeleken met de periode ervoor. Een live grafiek toont de laatste 60 minuten en een live feed toont elk bericht dat voorbijkomt. **Om naar te kijken** zet op een rij wat aandacht nodig heeft: mail die al meer dan een uur vastzit, een piek in bounces, een server die ineens veel meer verstuurt dan normaal, een gepauzeerde afzender, gestopte diensten, of een eigen IP-adres van Mailfilter op een blocklist. Topafzenders, top-ontvangende domeinen en de mailwachtrij maken het beeld compleet.

### Mailtrace

Zoek elk bericht op afzender, ontvanger, message-ID, queue-ID of server, en filter op status, richting en periode. Een groen vinkje betekent afgeleverd; bij alle andere berichten staat de reden er direct bij.

![Mailtrace: een lijst berichten met hun status](docs/screenshots/trace.png)

Open een bericht en je ziet de **route** (verzendende server → Mailfilter → bestemming), wat er per ontvanger gebeurde met het antwoord van de ontvangende server, een uitleg **in gewone taal** en **wie het kan oplossen**.

| Afgeleverd, met de spamcontrole | Deels afgeleverd: één mailbox is vol |
| --- | --- |
| ![Een afgeleverd bericht met de route en spamcontrole](docs/screenshots/trace-detail.png) | ![Een bericht waarbij de mailbox van één ontvanger vol was](docs/screenshots/trace-problem.png) |

De **spamcontrole** legt elke beslissing uit als checklist: is de afzender echt (SPF, DKIM, DMARC, lookalike-domeinen), reputatie en blocklists, links, bijlagen en virussen, inhoud en opmaak, met de punten die elke bevinding opleverde en waar de grenzen voor greylisting, spamlabel, quarantaine en weigeren liggen.

![Spamcontrole van een phishingbericht dat in quarantaine staat](docs/screenshots/spam-check.png)

### Quarantaine

Spam, virussen en riskante bijlagen wachten hier op een beslissing. **Vrijgeven** levert het bericht af en leert het spamfilter *dit is goede mail*; **Blokkeren en verwijderen** leert *dit is spam*. **Altijd toestaan** en **Altijd blokkeren** zetten de afzender met één klik op de lijst van die ontvanger. De inhoud wordt als platte tekst getoond, dus bekijken is veilig. Alleen beheerders kunnen een virus vrijgeven.

| Wachtende berichten en waarom ze zijn vastgehouden | Een geopend bericht |
| --- | --- |
| ![Quarantainelijst](docs/screenshots/quarantine.png) | ![Een bericht in quarantaine met knoppen om vrij te geven of te blokkeren](docs/screenshots/quarantine-detail.png) |

Het **dagelijkse quarantaine-overzicht** mailt elke ontvanger de berichten die voor hem zijn vastgehouden, met per bericht een knop om het vrij te geven, vrij te geven en de afzender toe te staan, of de afzender te blokkeren. Inloggen is niet nodig; de links werken 14 dagen en alleen voor dat bericht.

![Instellingen van het quarantaine-overzicht per domein](docs/screenshots/quarantine-report.png)

### Filteren en bescherming

- **Spamfilter** met Rspamd: reputatie, Bayes-leren, fuzzy hashes, DNS-blocklists (Spamhaus en andere via een lokale resolver), greylisting en postscreen aan de deur.
- **Virusscan** met ClamAV op binnenkomende *en* uitgaande mail.
- **Authenticatie**: controle van SPF, DKIM en DMARC op binnenkomende mail, ARC voor doorgestuurde mail, DKIM-ondertekening van alle uitgaande mail per domein.
- **Ontvangercontrole**: mail voor adressen die niet bestaan wordt aan de deur geweigerd (navragen bij de mailserver, LDAP / Active Directory of een vaste lijst), dus geen backscatter.
- **Bescherming tegen backscatter en lussen**, en foutmeldingen met uitleg in het Engels en Nederlands.

**Eigen regels** voegen bescherming toe die je voor alle domeinen of per domein aan- of uitzet, elk met een duidelijke uitleg: nieuwsbrieven (en nieuwsbrieven zonder afmeldlink), phishing en CEO-fraude (lookalike-domeinen, afzendernamen die zich voordoen als je eigen mensen, verdachte links, een banner *externe afzender*, linkbescherming), gevaarlijke bijlagen (bestandstypen, Office-macro's, versleutelde archieven) en ARC voor doorgestuurde mail.

![Eigen regels met een uitleg per regel](docs/screenshots/rules.png)

### Toestaan en blokkeren en spammeldingen

Afzenders die altijd doorkomen, of nooit, per mailbox, per domein of voor iedereen. Klanten beheren de lijsten van hun eigen mailboxen.

![Lijsten om toe te staan en te blokkeren](docs/screenshots/lists.png)

Gebruikers melden gemiste spam of onterecht tegengehouden mail vanuit **Outlook** (de ingebouwde knop Melden in Microsoft 365), een **telefoon** of **elke mailapp** door hem door te sturen naar `spam@` of `notspam@` de Mailfilter-host. Mailfilter leert alleen van meldingen die het kan vertrouwen: mail die het zelf afleverde, gemeld vanuit dat domein.

![Spammeldingen met hun status](docs/screenshots/spam-reports.png)

### DMARC-rapporten

Mailfilter ontvangt de DMARC-rapporten van je domeinen en laat zien wie er namens ze mailt, hoeveel er slaagt, en de volgende veilige stap op de **weg naar p=reject**. Met Cloudflare is elke stap één knop.

![DMARC-rapporten per domein](docs/screenshots/dmarc.png)

### Linkbescherming

Links in binnenkomende mail worden herschreven zodat ze opnieuw worden gecontroleerd **op het moment dat iemand klikt**: blocklists, lookalike-domeinen, links naar een IP-adres, verkorte URL's uitgepakt. Gevaarlijke links tonen een waarschuwingspagina. Elke klik staat in een lijst met wat Mailfilter besliste. Zie [linkbescherming](docs/nl/linkbescherming.md).

![Linkklikken: geblokkeerd, gewaarschuwd, toch doorgegaan en veilig](docs/screenshots/link-clicks.png)

### Domeinen

**Transport-domeinen** ontvangen mail via Mailfilter en leveren die af bij je mailserver (met reserveservers, TLS-beleid, een eigen afleverwachtrij en verzendlimieten). **Relay-domeinen** versturen alleen, voor webwinkels en applicaties, met DKIM, SPF en SMTP-accounts.

![Transport-domeinen met hun mailserver, ontvangercontrole en wachtrij](docs/screenshots/domains.png)

| Een geopend transport-domein | Relay-domeinen |
| --- | --- |
| ![Instellingen van één transport-domein](docs/screenshots/domain-detail.png) | ![Relay-domeinen met SMTP-accounts en DNS-status](docs/screenshots/relay-domains.png) |

### Relay-toegang

Eén eenvoudig scherm om te zeggen *deze server mag mail via Mailfilter versturen*. Een server die aan een klant gekoppeld is, kan alleen namens de eigen domeinen van die klant versturen, en zijn mail wordt nog steeds op spam en virussen gecontroleerd, met DKIM ondertekend en begrensd. Extra relay-poorten naast 587 en 465 voor oude software.

![Relay-toegang: toegestane servers en relay-poorten](docs/screenshots/relay.png)

### Throttling en gehackte afzenders

Limieten per server, per SMTP-account en per afzenderdomein, een eigen afleverwachtrij per domein, en vertragingsregels voor grote ontvangende providers. **Automatische bescherming** pauzeert een server of account die ineens spam of een stortvloed aan mail verstuurt: er gaat niets verloren, er wordt niets verstuurd, en je geeft hem met één klik weer vrij als het veilig is.

![Throttling: standaardlimieten en vertragingsregels](docs/screenshots/throttling.png)

### Microsoft 365 en Google Workspace

Een wizard van 8 stappen zet een Microsoft 365- of Google Workspace-domein achter Mailfilter: hij schrijft de PowerShell-opdrachten, controleert de connectors, past de DNS aan en sluit tot slot de tenant af zodat alleen Mailfilter nog kan afleveren. IP-reeksen van clouddiensten worden nooit blind vertrouwd: doorgestuurde mail moet de tenant-ID of een geldige DKIM-handtekening hebben.

![Cloud-mail: voortgang per omgeving](docs/screenshots/cloud.png)

### DNS-toolbox

Controleer een domein bij zijn eigen nameservers, maak SPF en DMARC, zoek MX op, controleer blocklists, en publiceer records met één knop in **Cloudflare**, altijd eerst getoond en terug te draaien vanuit het wijzigingslog. **Live zetten** zet de MX van een domein over naar Mailfilter. **DKIM-sleutels** worden automatisch vervangen, **MTA-STS, TLS-RPT en DANE** worden door Mailfilter gehost en gepubliceerd, en **BIMI** zet je logo om naar SVG Tiny PS en controleert je VMC/CMC-certificaat.

| SPF-generator: Mailfilter toevoegen aan een bestaand record | DKIM-sleutels met automatische rotatie |
| --- | --- |
| ![SPF-generator](docs/screenshots/dns-spf.png) | ![DKIM-sleutels en hun volgende rotatie](docs/screenshots/dns-dkim.png) |

![BIMI: wat er nog nodig is voordat je logo in Gmail en Apple Mail verschijnt](docs/screenshots/dns-bimi.png)

### Mailtrap

Vang alle mail van test- en stagingomgevingen op in virtuele mailboxen, zodat hij **nooit** naar echte mensen gaat. Elk bericht wordt gecontroleerd: links naar testhosts of zonder HTTPS, sjabloonvelden die niet zijn ingevuld, DKIM, grootte, alt-teksten, afmeldheaders en meer. Bekijk het op desktop- of telefoonbreedte, lees de bron, stuur het naar jezelf door, of lees de mailbox via IMAP. Zie [Mailtrap](docs/nl/mailtrap.md).

![Mailtrap: een opgevangen orderbevestiging met de controles](docs/screenshots/mailtrap.png)

### Archief

Een onveranderbare, doorzoekbare kopie van alle mail per domein, tot 10 jaar lang. Elk bericht zit in een hashketen, elke dag wordt verzegeld met de Ed25519-sleutel van de server en op schijf vergrendeld, en een juridische bewaarplicht (legal hold) voorkomt dat er iets wordt verwijderd. Zoek op woorden, adressen en namen van bijlagen, en exporteer met bewijs dat er niets is veranderd. Zie [archief](docs/nl/archief.md).

![Archief: zoeken in gearchiveerde mail](docs/screenshots/archive.png)

### Maandrapport en verbruik

Elke klant krijgt een **maandrapport** per e-mail: ontvangen mail, tegengehouden spam en virussen. **Verbruik** telt per klant de domeinen, actieve mailboxen, het verkeer en de archiefopslag, rekent met jouw prijzen en resellerkortingen, en exporteert CSV voor je boekhoudpakket.

| Maandrapport | Verbruik per klant |
| --- | --- |
| ![Maandrapport met 12 maanden mail](docs/screenshots/monthly-report.png) | ![Verbruik en bedragen per klant](docs/screenshots/usage.png) |

### Klanten, gebruikers en beveiliging

Beheerders zien alles; **klanten** zien alleen mail en instellingen van hun eigen domeinen; **resellers** zien alleen het verbruik van hun eigen klanten. Inloggen in twee stappen (TOTP met herstelcodes) kan verplicht worden voor beheerders of voor iedereen, en het **auditlog** legt elke wijziging vast, te filteren en te exporteren.

| Gebruikers en rollen | Auditlog |
| --- | --- |
| ![Gebruikers met hun rol, domeinen en status van inloggen in twee stappen](docs/screenshots/users.png) | ![Auditlog van elke wijziging](docs/screenshots/audit.png) |

| Beveiliging: inloggen in twee stappen verplichten | Mijn account |
| --- | --- |
| ![Beleid voor inloggen in twee stappen](docs/screenshots/security.png) | ![Mijn account met taal en inloggen in twee stappen](docs/screenshots/account.png) |

### API

Een REST API voor hostingpanels en scripts: domeinen toevoegen, een webserver laten relayen, SMTP-accounts maken, mail uit quarantaine vrijgeven, in de mailtrace zoeken. Sleutels hebben rechten per onderdeel, optionele IP-beperkingen en een vervaldatum; een sleutel van een klant werkt alleen op de domeinen van die klant. De documentatie met voorbeelden is ingebouwd. Zie [REST API](docs/nl/api.md).

| API-sleutels | Ingebouwde documentatie |
| --- | --- |
| ![API-sleutels met hun rechten](docs/screenshots/api.png) | ![API-documentatie met een voorbeeldaanroep](docs/screenshots/api-docs.png) |

### Meldingen en e-mail verzenden

Meldingen via **e-mail**, **Telegram** of **webhook** (JSON, Slack, Mattermost, Discord) als een dienst stopt, de wachtrij volloopt, een IP op een blocklist komt, het certificaat niet wordt verlengd, een afzender wordt gepauzeerd, of een backup of update mislukt. Mailfilters eigen mail gaat via zichzelf (met DKIM ondertekend) of via elke SMTP-server, met een testknop die fouten in gewone taal uitlegt.

| Meldingen | E-mail verzenden |
| --- | --- |
| ![Meldingskanalen en waarover een melding komt](docs/screenshots/notifications.png) | ![Hoe Mailfilter zijn eigen mail verstuurt](docs/screenshots/mail-sending.png) |

### Backups

Elke nacht volledige en incrementele backups, terugzetten vanuit de webinterface, en versleutelde kopieën (AES-256) naar **SCP/SFTP, FTPS of FTP**, met terugzetten direct vanaf die externe locatie, ook op een vers geïnstalleerde server.

![Backups: lijst, planning en externe kopie](docs/screenshots/backups.png)

### Updates en kanalen

**Info** toont de draaiende versie, het kanaal, de commit en de versies van alle onderdelen. **Controleren op updates** laat zien wat er veranderde; **Nu updaten** maakt een backup, installeert de nieuwe versie en draait zichzelf terug als er iets misgaat. Wissel met één klik tussen het kanaal **productie** (`main`) en **test** (`test`).

![Info: versie, beschikbare update, updatekanaal](docs/screenshots/info.png)

### Cluster

Houd meerdere Mailfilter-servers op dezelfde configuratie. Elke server blijft een complete gateway: valt er één uit, dan blijven de andere filteren, en hij loopt bij zodra hij terug is. Zie [cluster](docs/nl/cluster.md).

![Cluster met twee servers](docs/screenshots/cluster.png)

### Huisstijl

Je eigen productnaam, accent- en donkere kleur, logo (met een versie voor donkere modus) en browsericoon, gebruikt in de webapp, op de inlogpagina en in rapportmails. De RHIS-huisstijl is de standaard, en "Powered by RHIS" blijft zichtbaar (zie [Licentie](#licentie)).

![Huisstijl met een live voorbeeld](docs/screenshots/branding.png)

### Import uit Proxmox Mail Gateway

Upload een PMG-backup en neem domeinen, relay-netwerken, adresroutes, TLS-regels, lijsten om toe te staan en te blokkeren, en DKIM-sleutels over. Je ziet eerst alles en kiest wat je importeert.

![Import uit Proxmox Mail Gateway](docs/screenshots/import-pmg.png)

### Elk scherm, overal

Zoek alles met **Ctrl+K**, wissel tussen licht en donker, gebruik het op een telefoon, en geef klanten de interface in hun eigen taal: alles is er in het Engels en het Nederlands.

| Zoeken met Ctrl+K | Donker thema |
| --- | --- |
| ![Opdrachtpalet dat een domein en zijn berichten zoekt](docs/screenshots/search.png) | ![Mailtrace in het donkere thema](docs/screenshots/trace-detail-dark.png) |

| De weergave van een klant, in het Nederlands | Inloggen |
| --- | --- |
| ![Dashboard van een klantlogin in het Nederlands](docs/screenshots/dashboard-customer.png) | ![Inlogpagina](docs/screenshots/login.png) |

<p align="center">
  <img src="docs/screenshots/dashboard-phone.png" alt="Dashboard op een telefoon" width="280">
  &nbsp;&nbsp;
  <img src="docs/screenshots/quarantine-phone.png" alt="Quarantaine op een telefoon" width="280">
</p>

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

De schermafbeeldingen in deze README komen uit een demodatabase met verzonnen `.example`-domeinen; [tools/screenshots/README.md](tools/screenshots/README.md) legt uit hoe je ze opnieuw maakt na een wijziging aan de interface.

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
| `docs/` | Handleidingen en naslag (Engels, Nederlands in `docs/nl/`), schermafbeeldingen in `docs/screenshots/` |
| `tools/screenshots/` | Demodata en scripts die de schermafbeeldingen van deze README maken |
| `huisstijl/` | RHIS-huisstijl: tokens, Archivo-lettertype, logo's |
| `prototype/` | Het oorspronkelijke klikbare prototype van de interface |

## Licentie

Mailfilter is open source onder de [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0), met [aanvullende voorwaarden](NOTICE) volgens artikel 7 van die licentie:

- **"Powered by RHIS" blijft staan.** Het kleine "Powered by RHIS" met het RHIS-icoon rechtsonder op elke pagina moet in elke kopie en elke aangepaste versie zichtbaar blijven. Het is geen instelling en mag niet verwijderd of verborgen worden.
- **Je eigen huisstijl mag.** Naam, kleuren, logo en browsericoon pas je aan onder **Systeem › Huisstijl**; de RHIS-huisstijl is alleen de standaard.
- **Aanpassingen blijven open.** Wie een aangepaste versie via het netwerk voor anderen draait, moet hun de broncode daarvan aanbieden (AGPL artikel 13).
- Een aangepaste versie moet vermelden dat hij is aangepast, en de naam en logo's van RHIS mag je verder niet gebruiken.

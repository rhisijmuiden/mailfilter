# Installatiehandleiding

[English](../installation.md) · [Alle documentatie](README.md)

Deze handleiding brengt je van een kale Debian 13-server naar een werkende Mailfilter die de mail van je eerste domein filtert.

## 1. Wat je nodig hebt

| | |
| --- | --- |
| Server | Een verse installatie van **Debian 13 (trixie)**, 64-bit, verder niets erop. Minimaal 2 CPU-cores, 4 GB RAM (ClamAV gebruikt alleen al ongeveer 1,5 GB) en 40 GB schijf. Meer schijf als je het archief gebruikt. |
| Netwerk | Een publiek IPv4-adres, liefst ook IPv6, waarbij poort 25 de server bereikt. De webinterface kan direct op internet staan, achter een firewall of in een DMZ, of achter een reverse proxy (zie *Webinterface* hieronder). |
| Poort 25 | Open **in en uit**. Veel cloudproviders blokkeren uitgaand poort 25 standaard; vraag ze die open te zetten. |
| Hostnaam | Een naam zoals `mailfilter01.voorbeeld.nl` met een **A**-record (en **AAAA** voor IPv6) naar de server. |
| Reverse DNS | Een **PTR**-record voor elk IP-adres dat terugwijst naar die hostnaam. Dit stel je in bij je hostingprovider. Zonder PTR weigeren veel ontvangers je mail. |
| Toegang | Roottoegang via SSH. |

## 2. Installeren

Log in als root en voer uit:

```bash
apt-get install -y git
git clone -b main https://github.com/rhisijmuiden/mailfilter.git /opt/mailfilter
/opt/mailfilter/install/install.sh
```

Gebruik `-b test` in plaats van `-b main` voor een testserver: die volgt het testkanaal, waar elke wijziging als eerste komt. Je kunt later in de webinterface van kanaal wisselen.

De installer vraagt vier dingen:

1. **Hostnaam** van deze server, bijvoorbeeld `mailfilter01.voorbeeld.nl`.
2. **E-mailadres van de beheerder**: je login voor de webinterface; meldingen en berichten van Let's Encrypt gaan hierheen.
3. **Eerste domein** om te filteren (optioneel, je kunt domeinen later in de webinterface toevoegen) en de mailserver die de schone mail ontvangt.
4. **Hoe de webinterface bereikbaar is** (zie hieronder).

Daarna installeert en configureert hij alles; dat duurt ongeveer 10 tot 15 minuten. Je kunt meekijken op het scherm; het volledige log staat in `/var/log/mailfilter-install.log`.

### Opties

Alles kan ook op de opdrachtregel worden meegegeven, bijvoorbeeld om zonder vragen te installeren:

```bash
/opt/mailfilter/install/install.sh --non-interactive \
  --hostname mailfilter01.voorbeeld.nl --admin-email beheer@voorbeeld.nl \
  --domain voorbeeld.nl --destination mail.voorbeeld.nl \
  --relay-net 203.0.113.25 --relay-net 2001:db8:42::/64 \
  --relay-port 2525 --ssh-from 198.51.100.7
```

| Optie | Betekenis |
| --- | --- |
| `--hostname NAAM` | Volledige hostnaam van deze server |
| `--admin-email ADRES` | Login van de beheerder en adres voor meldingen en certificaatberichten |
| `--domain DOMEIN` | Eerste domein om te filteren (optioneel) |
| `--destination HOST` | Mailserver die schone mail voor `--domain` ontvangt |
| `--relay-net CIDR` | Server of netwerk dat mail via Mailfilter naar buiten mag sturen (IPv4 of IPv6). Herhaalbaar |
| `--relay-port POORT` | Extra relaypoort naast 587 en 465, bijvoorbeeld 2525. Herhaalbaar |
| `--ssh-from CIDR` | SSH alleen toestaan vanaf dit adres of netwerk. Herhaalbaar. Zonder deze optie blijft SSH open (CrowdSec bewaakt het wel) |
| `--web-mode MODUS` | Hoe de webinterface bereikbaar is: `public`, `internal`, `proxy` of `http` (zie hieronder) |
| `--trusted-proxy CIDR` | Adres van de reverse proxy (`proxy`). Herhaalbaar |
| `--web-url URL` | Adres dat gebruikers openen op de reverse proxy (`proxy`), standaard `https://<hostnaam>` |
| `--web-port POORT` | HTTP-poort voor `proxy` (standaard 8080) of `http` (standaard 80) |
| `--web-from CIDR` | Webinterface alleen bereikbaar vanaf hier (`internal`, `http`). Herhaalbaar |
| `--web-cert BESTAND`, `--web-key BESTAND` | Eigen certificaat en sleutel (PEM) voor `internal` in plaats van het zelfgemaakte |
| `--skip-certificate` | Nog geen Let's Encrypt-certificaat aanvragen (DNS nog niet klaar) |
| `--non-interactive` | Nooit vragen; stoppen als een verplichte waarde ontbreekt |
| `--reconfigure` | Alles opnieuw toepassen met de instellingen van de vorige installatie, zonder vragen |

De installer opnieuw draaien is veilig: hij past de configuratie opnieuw toe en houdt sleutels, wachtwoorden, lijsten en de database.

### Webinterface: op internet, in een DMZ of achter een proxy

| Modus | Wanneer | Wat er gebeurt |
| --- | --- | --- |
| `public` (standaard) | De server staat direct op internet | HTTPS met een Let's Encrypt-certificaat op poort 80 en 443, automatisch vernieuwd. Nodig voor MTA-STS en linkbescherming. |
| `internal` | Achter een firewall of in een DMZ | HTTPS met een certificaat dat Caddy zelf maakt (of je eigen met `--web-cert`/`--web-key`). **Geen Let's Encrypt-aanvraag.** Browsers waarschuwen tot je het vertrouwt. Eventueel alleen vanaf `--web-from`. |
| `proxy` | Achter nginx, HAProxy, Traefik of een vergelijkbare reverse proxy | Gewone HTTP op `--web-port` (8080), alleen bereikbaar vanaf `--trusted-proxy`. De proxy doet HTTPS; zijn `X-Forwarded-For` en `X-Forwarded-Proto` worden vertrouwd, zodat het auditlog de echte bezoeker toont. |
| `http` | Een afgesloten beheernetwerk | Gewone HTTP zonder versleuteling. De installer waarschuwt; beperk met `--web-from` wie mag verbinden. |

```bash
# In een DMZ, webinterface alleen vanaf het kantoornetwerk:
install.sh --web-mode internal --web-from 192.168.10.0/24
# Achter een reverse proxy op 10.0.0.5 die https://mail.voorbeeld.nl serveert:
install.sh --web-mode proxy --trusted-proxy 10.0.0.5 --web-url https://mail.voorbeeld.nl
```

Het filteren van mail is in elke modus gelijk; poort 25 moet de server nog steeds bereiken. Je kunt later omschakelen onder **Systeem › Webtoegang**. Buitengesloten? Op de server: `sudo /opt/mailfilter/install/web-access.sh --mode public` (of een andere modus).

### Wat er geïnstalleerd wordt

| Onderdeel | Wat het doet |
| --- | --- |
| Postfix | Inkomend op poort 25 met postscreen, relay op 587 (STARTTLS), 465 (TLS) en je extra poorten, een afleverwachtrij per domein, IPv4 en IPv6 |
| Rspamd | Spamscore, SPF, DKIM-ondertekening, DMARC, ARC, greylisting, blocklists, Bayes-leren, throttling, toestaan- en blokkeerlijsten |
| ClamAV | Virusscan in beide richtingen |
| Dovecot | Wachtwoordcontrole voor SMTP-accounts, IMAP voor de Mailtrap; geen mailboxen |
| unbound | Eigen DNS-resolver voor blocklists en DNSSEC |
| Caddy + Let's Encrypt | De webinterface (standaard via HTTPS); het Let's Encrypt-certificaat wordt ook voor SMTP-TLS gebruikt en wordt automatisch vernieuwd |
| nftables + CrowdSec | Firewall en automatisch blokkeren van aanvallers |
| PostgreSQL, Redis | Database van de webapp, opslag voor het spamfilter |
| Mailfilter-webapp | De webinterface en API, de mailtrace-verzamelaar en de timers voor backups, rapporten, meldingen en meer |
| unattended-upgrades | 's Nachts beveiligingsupdates van Debian |

### Firewall

Open vanaf het internet: **25** (mail in), de webinterface (**80** en **443** in modus `public` en `internal`, of de `--web-port` vanaf de proxy of de `--web-from`-netwerken), **465** en **587** (relay), je extra relaypoorten, **22** (SSH, of alleen vanaf `--ssh-from`) en **993** alleen zolang een Mailtrap-mailbox IMAP toestaat. Al het andere is dicht.

## 3. Eerste keer inloggen

Aan het eind toont de installer:

- de **DNS-records** die je moet toevoegen (A, AAAA, PTR, SPF en DKIM voor je eerste domein, en de MX voor later);
- de **login voor de webinterface**: je e-mailadres als beheerder en een gegenereerd wachtwoord. Het wachtwoord staat ook in `/etc/mailfilter/initial-admin-password`.

Open `https://<hostnaam>/` en log in. Daarna direct:

1. **Mijn account** (klik rechtsboven op je initialen): wachtwoord wijzigen en je taal kiezen (English of Nederlands). Verwijder `/etc/mailfilter/initial-admin-password` op de server.
2. **Mijn account › Tweestapsverificatie**: instellen met een authenticator-app en de herstelcodes bewaren.
3. Klik op **Info** (onderaan het linkermenu) om de versie te controleren en of het kanaal het juiste is.

Wachtwoord kwijt? Op de server: `/var/lib/mailfilter/venv/bin/mailfilter-admin set-password jij@voorbeeld.nl` geeft een nieuw wachtwoord.

## 4. De basis instellen

Loop deze stappen door in de webinterface. De [beheerdershandleiding](beheerdershandleiding.md) legt elk scherm uit.

1. **Systeem › E-mail verzenden**: hoe Mailfilter zijn eigen mail verstuurt (meldingen, rapporten). De standaard, *via deze Mailfilter*, is voor de meeste situaties goed. Druk op **Testbericht versturen**.
2. **Systeem › Meldingen**: kies waar meldingen heen gaan (e-mail, Telegram of webhook).
3. **Systeem › Backups**: controleer het nachtelijke schema en stel een **externe kopie** in (SCP aanbevolen). Bewaar het wachtwoord voor de versleuteling in je wachtwoordmanager.
4. **DNS-toolbox › Cloudflare**: staan je domeinen bij Cloudflare, vul dan een API-token in met *Zone:Read* en *DNS:Edit*. Mailfilter kan dan alle records voor je publiceren.
5. **Configuratie › Domeinen**: voeg je domeinen toe (of gebruik **Systeem › Import uit PMG** als je van Proxmox Mail Gateway komt).
6. **Configuratie › Relay-toegang**: voeg de webservers en mailservers toe die via Mailfilter mail naar buiten mogen sturen.

## 5. Een domein overzetten

Per domein:

1. **Voeg het toe** onder Configuratie › Domeinen als *transportdomein* met zijn mailserver, of als *relaydomein* als het alleen verstuurt.
2. **Controleer de DNS** in de DNS-toolbox (*Domein controleren*). Publiceer SPF (Mailfilter voegt `include:_spf.<hostnaam>` toe aan het bestaande record), DKIM en eventueel DMARC met rapporten naar Mailfilter.
3. **Laat je mailserver via Mailfilter versturen** (zet zijn IP onder Relay-toegang en stel Mailfilter in als smarthost), en controleer in de mailtrace dat uitgaande mail wordt afgeleverd en met DKIM is ondertekend.
4. **Zorg dat je mailserver mail van Mailfilter accepteert** en, zodra je live bent, alleen nog van Mailfilter.
5. **Ga live**: druk in de DNS-toolbox op **Go live** voor het domein (Cloudflare), of wijzig het MX-record bij je DNS-provider naar de hostnaam van Mailfilter. Binnenkomende mail loopt nu via Mailfilter. Volg het in de mailtrace.

Gebruik voor Microsoft 365 of Google Workspace in plaats daarvan **Configuratie › Cloud-mail**: de wizard leidt je door connectors, DNS en afsluiten.

## 6. Updaten

Klik op **Info › Controleren op updates › Nu updaten**. Mailfilter maakt een backup, installeert de nieuwe versie, past de configuratie opnieuw toe en draait zichzelf terug als dat misgaat. Op de opdrachtregel: `mailfilterctl update apply`. Zie [updates en backups](updates-en-backups.md).

## Problemen oplossen

| Probleem | Wat te doen |
| --- | --- |
| Let's Encrypt mislukt | Het A/AAAA-record wijst nog niet naar de server, of poort 80 is dicht. Herstel de DNS en draai `install.sh --reconfigure`. Gebruik `--skip-certificate` om eerst te installeren. |
| Uitgaande mail blijft in de wachtrij | Poort 25 uitgaand wordt door de provider geblokkeerd. De mailtrace toont "connection timed out". Vraag de provider hem te openen. |
| Mail wordt door Gmail of Microsoft geweigerd | Controleer PTR, SPF en DKIM in de DNS-toolbox (*Domein controleren*), en *Blacklistcontrole* voor je IP. |
| Een service draait niet | Het dashboard toont welke. Draai `install.sh --reconfigure`; het log in `/var/log/mailfilter-install.log` vertelt waarom het misging. |
| Buitengesloten door tweestapsverificatie | `mailfilter-admin reset-2fa jij@voorbeeld.nl` op de server. |

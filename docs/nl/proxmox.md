# Proxmox LXC-template

[English](../proxmox.md) · [Alle documentatie](README.md)

Mailfilter is ook beschikbaar als kant-en-klare **Proxmox CT-template**: een Debian 13-container waarin alles al geïnstalleerd is. Je downloadt hem in Proxmox, maakt er een container van, en de console toont het webadres met een gebruikersnaam en wachtwoord. De rest van de inrichting gaat via een wizard in de webinterface.

## 1. Wat je nodig hebt

| | |
| --- | --- |
| Proxmox VE | 8.4 of nieuwer (9 aanbevolen: die is ook op Debian 13 gebaseerd) |
| Container | 2 CPU-cores, **4 GB RAM** (ClamAV gebruikt alleen al zo'n 1,5 GB), 512 MB swap, **40 GB schijf** (meer als je het archief gebruikt) |
| Netwerk | Een vast IPv4-adres, liefst ook IPv6, met poort 25 in en uit naar de container. Internettoegang bij de eerste start (pakketten, virusdefinities). |

## 2. Template downloaden

Kies in Proxmox een opslag met containertemplates (meestal **local**), dan **CT Templates › Download from URL**:

| Kanaal | URL |
| --- | --- |
| Productie (aanbevolen) | `https://github.com/rhisijmuiden/mailfilter/releases/latest/download/mailfilter-production_amd64.tar.zst` |
| Test (elke wijziging eerst) | `https://github.com/rhisijmuiden/mailfilter/releases/download/ct-test/mailfilter-test_amd64.tar.zst` |

Klik op **Query URL** en dan **Download**. Vul eventueel de SHA-256-controlesom in uit het `.sha256`-bestand ernaast op de [releasespagina](https://github.com/rhisijmuiden/mailfilter/releases).

Op de commandoregel van de Proxmox-host doet dit hetzelfde:

```bash
cd /var/lib/vz/template/cache
wget https://github.com/rhisijmuiden/mailfilter/releases/latest/download/mailfilter-production_amd64.tar.zst
```

De URL geeft altijd de nieuwste versie. Een container die je al gemaakt hebt heeft geen nieuwe template nodig: die werkt zichzelf bij vanuit de webinterface (Info › Controleren op updates).

## 3. Container aanmaken

**Create CT** in Proxmox:

1. **General**: een hostnaam, bijvoorbeeld `mailfilter01` (of de volledige naam `mailfilter01.example.nl`), en een rootwachtwoord of SSH-sleutel voor de console. Laat *Unprivileged container* aan.
2. **Template**: de Mailfilter-template.
3. **Disks**: 40 GB of meer.
4. **CPU / Memory**: 2 cores, 4096 MB geheugen, 512 MB swap.
5. **Network**: het **IP-adres** van de container. Gebruik een vast adres (IPv4/CIDR en gateway, en IPv6 als je dat hebt). Het adres stel je hier in, in Proxmox, niet in de container.
6. **DNS**: laten staan. Mailfilter gebruikt na de eerste start zijn eigen resolver.
7. Vink **Start after created** aan en rond af.

Zorg dat *Options › Features › nesting* aan staat (Proxmox doet dat bij nieuwe unprivileged containers): Debian 13 heeft het nodig.

## 4. Eerste start

De eerste start duurt een paar minuten: de container maakt zijn eigen wachtwoorden en sleutels, zet de database klaar en downloadt de virusdefinities. Er wordt niets gevraagd. Open de **Console** van de container; als hij klaar is staat er:

```
  RHIS Mailfilter  (1.1.0)
  ------------------------------------------------------------
  Web interface:
    https://192.168.1.50/

  Username:  admin
  Password:  Xk3vR8...

  Log in and follow the setup wizard.
```

Druk in de console op Enter als er nog *being prepared* staat. Na inloggen als root zie je dezelfde tekst.

Open het adres in je browser. Het certificaat maakt de server zelf, dus de browser waarschuwt één keer; accepteer dat (of stap in de wizard later over op Let's Encrypt).

**Een andere poort dan 443?** Zet vóór de eerste start `WEB_PORT=8443` in `/etc/mailfilter/appliance.conf` van de container, op de Proxmox-host:

```bash
pct create ...           # zonder --start
pct mount 105 && echo WEB_PORT=8443 > /var/lib/lxc/105/rootfs/etc/mailfilter/appliance.conf && pct unmount 105
pct start 105
```

Later wijzigen kan ook onder **Systeem › Webtoegang** (modus *Achter een firewall of in een DMZ*, veld *Poort*).

## 5. Installatiewizard

Na het inloggen opent de wizard vanzelf. Elke stap is een gewone pagina van de webinterface, dus je kunt een stap overslaan en alles later in het menu wijzigen.

1. **Server en login**: de echte hostnaam (bijvoorbeeld `mailfilter01.example.nl`, met een A- en AAAA-record ernaartoe), **je e-mailadres**, dat je login wordt, en **je eigen wachtwoord**. Mailfilter richt daarna mailserver, webserver en certificaten opnieuw in met die naam (een minuut of twee).
2. **Webtoegang**: direct aan internet? Kies *Direct op internet* voor een Let's Encrypt-certificaat, zodra de hostnaam naar de container wijst en poort 80 bereikbaar is. Achter een firewall of reverse proxy kies je die modus.
3. **Domeinen**: de domeinen om te filteren, met de mailserver die de schone mail ontvangt.
4. **Relaytoegang**: de servers die via Mailfilter mail mogen versturen.
5. **E-mail versturen**: hoe Mailfilter meldingen en rapporten verstuurt; stuur een testbericht.
6. **Back-ups**: de nachtelijke back-up en een kopie op een andere server (SCP, FTPS of FTP).
7. **Klaar**: druk op *Instellen afronden*. Het eerste wachtwoord verdwijnt van de console en van de server.

Ga daarna verder met *Een domein overzetten* in de [installatiehandleiding](installatie.md), en zet tweestapsaanmelding aan onder *Mijn account*.

## Goed om te weten

| | |
| --- | --- |
| Updates | Zoals bij elke Mailfilter: Info › Controleren op updates › Nu bijwerken. Productie- of testkanaal wissel je daar ook. |
| Back-ups | De Mailfilter-back-ups (Systeem › Back-ups) bevatten configuratie, database, DKIM-sleutels en archief. Proxmox-back-ups (vzdump) van de container zijn een goede aanvulling. |
| Hostnaam | Mailfilter houdt de naam uit de wizard aan, ook als Proxmox een andere toont (`/etc/.pve-ignore.hostname`). Zet dezelfde naam in Proxmox om verwarring te voorkomen. |
| DNS | Mailfilter laat de container zijn eigen resolver gebruiken (nodig voor blocklists) en zorgt dat Proxmox die niet overschrijft (`/etc/.pve-ignore.resolv.conf`). |
| PTR | Vraag de eigenaar van het publieke IP-adres om een PTR-record naar de hostnaam. Achter NAT gebruik je het publieke adres in de DNS-toolbox. |
| Archiefvergrendeling | Een unprivileged container kan gearchiveerde mail niet onveranderbaar maken (`chattr +i`). Mailfilter waarschuwt hiervoor; het archief werkt wel en de hashketen laat elke wijziging nog steeds zien. Vergrendeling nodig? Gebruik een privileged container. |
| Poort 25 | Achter NAT stuur je poort 25, 465, 587 (en 80/443 voor Let's Encrypt) door naar de container. |
| Eerste start mislukt | Log op de console in als root, lees `/var/log/mailfilter-install.log`, los de oorzaak op (meestal geen internet) en voer `systemctl restart mailfilter-firstboot` uit. |
| Wachtwoord vergeten | `/var/lib/mailfilter/venv/bin/mailfilter-admin set-password <login>` geeft een nieuw wachtwoord. |

## Zelf de template bouwen

Op een machine met Docker:

```bash
git clone -b main https://github.com/rhisijmuiden/mailfilter.git && cd mailfilter
sudo appliance/build.sh --branch main          # appliance/out/mailfilter-production_amd64.tar.zst
sudo appliance/tests/smoke.sh appliance/out/mailfilter-production_amd64.tar.zst
```

`build.sh` start een Debian 13-container met systemd, draait daarin de gewone installer (`install.sh --image`), verwijdert alles wat per server uniek moet zijn (`appliance/generalize.sh`: wachtwoorden, sleutels, certificaatautoriteit, SSH-hostsleutels, machine-ID, CrowdSec-gegevens, logs) en pakt het bestandssysteem in. GitHub Actions doet precies dit bij elke wijziging op `main` en `test` (workflow *Proxmox template*) en publiceert het resultaat op de URL's hierboven.

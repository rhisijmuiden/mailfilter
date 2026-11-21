# Proxmox LXC template

[Nederlands](nl/proxmox.md) · [All documentation](README.md)

Mailfilter is also available as a ready-made **Proxmox CT template**: a Debian 13 container with everything already installed. You download it in Proxmox, create a container from it, and the console shows the web address with a user name and password. The rest of the setup happens in a wizard in the web interface.

## 1. What you need

| | |
| --- | --- |
| Proxmox VE | 8.4 or newer (9 recommended: it is based on Debian 13 too) |
| Container | 2 CPU cores, **4 GB RAM** (ClamAV alone uses about 1.5 GB), 512 MB swap, **40 GB disk** (more if you use the archive) |
| Network | A fixed IPv4 address, preferably also IPv6, with port 25 reaching the container in and out. Internet access at the first start (packages, virus definitions). |

## 2. Download the template

In Proxmox, select a storage that holds container templates (usually **local**), then **CT Templates › Download from URL**:

| Channel | URL |
| --- | --- |
| Production (recommended) | `https://github.com/rhisijmuiden/mailfilter/releases/latest/download/mailfilter-production_amd64.tar.zst` |
| Test (every change first) | `https://github.com/rhisijmuiden/mailfilter/releases/download/ct-test/mailfilter-test_amd64.tar.zst` |

Click **Query URL**, then **Download**. Optionally fill in the SHA-256 checksum from the `.sha256` file next to it on the [releases page](https://github.com/rhisijmuiden/mailfilter/releases).

On the command line of the Proxmox host this does the same:

```bash
cd /var/lib/vz/template/cache
wget https://github.com/rhisijmuiden/mailfilter/releases/latest/download/mailfilter-production_amd64.tar.zst
```

The URL always gives the newest version. A container you already made does not need a new template: it updates itself from the web interface (Info › Check for updates).

## 3. Create the container

**Create CT** in Proxmox:

1. **General**: a host name, for example `mailfilter01` (or the full name `mailfilter01.example.nl`), and a root password or SSH key for the console. Leave *Unprivileged container* on.
2. **Template**: the Mailfilter template.
3. **Disks**: 40 GB or more.
4. **CPU / Memory**: 2 cores, 4096 MB memory, 512 MB swap.
5. **Network**: the **IP address** of the container. Use a static address (IPv4/CIDR and gateway, and IPv6 if you have it). The address is set here, in Proxmox, not inside the container.
6. **DNS**: leave as is. Mailfilter uses its own resolver after the first start.
7. Tick **Start after created** and finish.

Make sure *Options › Features › nesting* is on (Proxmox does this for new unprivileged containers): Debian 13 needs it.

## 4. First start

The first start takes a few minutes: the container makes its own passwords and keys, sets up the database and downloads the virus definitions. Nothing is asked. Open the **Console** of the container; when it is ready it shows:

```
  RHIS Mailfilter  (1.1.0)
  ------------------------------------------------------------
  Web interface:
    https://192.168.1.50/

  Username:  admin
  Password:  Xk3vR8...

  Log in and follow the setup wizard.
```

Press Enter in the console if it still says *being prepared*. You see the same text after logging in as root.

Open the address in your browser. The certificate is made by the server itself, so the browser warns once; accept it (or later switch to Let's Encrypt in the wizard).

**Another port than 443?** Before the first start, put `WEB_PORT=8443` in `/etc/mailfilter/appliance.conf` of the container, on the Proxmox host:

```bash
pct create ...           # without --start
pct mount 105 && echo WEB_PORT=8443 > /var/lib/lxc/105/rootfs/etc/mailfilter/appliance.conf && pct unmount 105
pct start 105
```

You can also change it later under **System › Web access** (mode *Behind a firewall or in a DMZ*, field *Port*).

## 5. Setup wizard

After logging in, the wizard opens by itself. Each step is a normal page of the web interface, so you can skip a step and change everything later in the menu.

1. **Server and login**: the real host name (for example `mailfilter01.example.nl`, with an A and AAAA record pointing to it), **your e-mail address**, which becomes your login, and **your own password**. Mailfilter then sets up mail server, web server and certificates again with that name (a minute or two).
2. **Web access**: directly on the internet? Choose *Directly on the internet* for a Let's Encrypt certificate, once the host name points to the container and port 80 is reachable. Behind a firewall or reverse proxy, choose that mode.
3. **Domains**: the domains to filter, with the mail server that receives the clean mail.
4. **Relay access**: the servers that may send mail out through Mailfilter.
5. **E-mail sending**: how Mailfilter sends alerts and reports; send a test message.
6. **Backups**: the nightly backup and a copy on another server (SCP, FTPS or FTP).
7. **Ready**: press *Finish setup*. The first password disappears from the console and from the server.

Then continue with *Move a domain over* in the [installation guide](installation.md#5-move-a-domain-over), and turn on two-factor login under *My account*.

## Good to know

| | |
| --- | --- |
| Updates | Like any Mailfilter: Info › Check for updates › Update now. Production or test channel can be switched there. |
| Backups | The Mailfilter backups (System › Backups) hold the configuration, database, DKIM keys and archive. Proxmox backups (vzdump) of the container are a good extra. |
| Host name | Mailfilter keeps the name from the wizard, also when Proxmox shows another one (`/etc/.pve-ignore.hostname`). Set the same name in Proxmox to avoid confusion. |
| DNS | Mailfilter points the container at its own resolver (needed for blocklists) and tells Proxmox not to overwrite it (`/etc/.pve-ignore.resolv.conf`). |
| PTR | Ask the owner of the public IP address for a PTR record to the host name. Behind NAT, use the public address in the DNS toolbox. |
| Archive lock | An unprivileged container cannot make archived mail unchangeable (`chattr +i`). Mailfilter warns about it; the archive still works and its hash chain still shows any change. Need the lock? Use a privileged container. |
| Port 25 | Behind NAT, forward ports 25, 465, 587 (and 80/443 for Let's Encrypt) to the container. |
| First start failed | Log in on the console as root, read `/var/log/mailfilter-install.log`, fix the cause (usually no internet) and run `systemctl restart mailfilter-firstboot`. |
| Forgot the password | `/var/lib/mailfilter/venv/bin/mailfilter-admin set-password <login>` prints a new one. |

## Building the template yourself

On a machine with Docker:

```bash
git clone -b main https://github.com/rhisijmuiden/mailfilter.git && cd mailfilter
sudo appliance/build.sh --branch main          # appliance/out/mailfilter-production_amd64.tar.zst
sudo appliance/tests/smoke.sh appliance/out/mailfilter-production_amd64.tar.zst
```

`build.sh` starts a Debian 13 container with systemd, runs the normal installer in it (`install.sh --image`), removes everything that must be unique per server (`appliance/generalize.sh`: passwords, keys, certificate authority, SSH host keys, machine ID, CrowdSec credentials, logs) and packs the file system. GitHub Actions does exactly this on every change to `main` and `test` (workflow *Proxmox template*) and publishes the result at the URLs above.

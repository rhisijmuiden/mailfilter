# Cluster

Several Mailfilter servers can keep the same configuration. Each server stays a complete gateway on its own: when one is down, the others keep filtering mail with the configuration they have, and the server that was down catches up when it is back. A single server does not need any of this and works exactly as before.

## Setting it up

1. On the first server: **System > Cluster > Create cluster**, give it a name (for example `mailfilter01`).
2. Install Mailfilter on the new server with the installer, as usual.
3. On the first server: **Add a server**, copy the join code.
4. On the new server: **System > Cluster > Join an existing cluster**, paste the code and give it a name (for example `mailfilter02`).

The new server takes over the cluster's configuration: its own domains, users and DKIM keys are replaced. Afterwards you log in on it with your cluster account.

Then, in the DNS toolbox: publish the shared SPF record (`_spf.<first server>`) again so it lists the addresses of all servers, and give each domain an MX record for every server.

## What is synced

| The same on every server | Separate per server |
| --- | --- |
| Domains and their delivery settings | Mail queues and quarantine |
| Relay access (IP addresses, extra ports) | Mail trace, logs, audit log |
| Throttling, address routes, TLS rules | Backups and their schedule |
| Users with passwords and domains | Updates and the production/test channel |
| Custom rules, hacked-sender protection settings | Paused senders (each server watches its own mail) |
| E-mail sending (SMTP), Cloudflare token | DMARC reports, blocklist checks, MTA-STS policy files |
| Allow and block lists, quarantine report settings | |
| DKIM keys | Host name, IP addresses, certificate |

## How it works

* Every server is equal. A change on any server raises the cluster's *revision* (time of the change plus the server) and is pushed to the others right away. The timer `mailfilter-cluster.timer` checks every peer every minute and fetches or pushes whatever is missing. The newest revision wins; two changes made on two servers within the same minute while they could not reach each other: the later one wins.
* A server applies a received configuration through the same checks as a change in the web interface (`mailfilter-config apply`). If the mail server refuses it, nothing changes on that server and the Cluster page shows why.
* Servers talk over HTTPS on port 443 (the certificate is verified). Every message is also encrypted and signed with the shared cluster secret (Fernet: AES and HMAC), is valid for five minutes and is accepted once. The clocks of the servers must therefore be correct (NTP).
* **Remove from cluster** (for a server that is gone) also replaces the cluster secret, so the removed server and old join codes no longer work. **Leave cluster** takes the server out; it keeps its configuration and runs on its own.
* On the command line: `mailfilter-admin cluster-sync` and `mailfilter-admin cluster-leave` (as user `mailfilter`).

## Backup mode (primary and standby)

Instead of two servers that work side by side, you can make the second server a *backup* that takes over when the first one is down. Choose **Primary with a backup server** when you create the cluster, or switch an existing cluster under **System > Cluster > How the servers work together** (on the primary).

* The first server is the **primary**, the server that joins is the **standby**. The standby gets every change, but you cannot change anything on it: the web interface shows a banner with a link to the primary. If the primary is gone for good, use **Make this the primary** on the standby.
* **Incoming mail**: use **MX records for all domains** on the Cluster page (Cloudflare) to give every domain whose MX already points at a server an MX for the primary (priority 10) and the standby (20). Senders then use the standby by themselves when the primary does not answer, and the standby filters and delivers that mail with the same configuration.
* **Servers that send through Mailfilter** (web servers, scanners) use one host name, the *failover name* (for example `smtp.example.nl`), set under **Take over sending servers**. The standby checks the primary every minute. When the primary has answered neither over HTTPS nor on port 25 for the set number of minutes, the standby points the failover name's A/AAAA records in Cloudflare at itself (TTL 60 s), and back at the primary once that has answered again for the set number of minutes. The administrators get an e-mail at both moments, and every DNS change is in the DNS toolbox journal, so it can be undone there. **Take over now** and **Point the name at … now** do the same by hand.
* The failover name must not be the host name of one of the servers. Senders that check the certificate need the failover name in the certificate of both servers.

## For developers

`backend/mailfilter/cluster/`: `snapshot.py` lists what is synced (`SYNCED_SETTINGS`, `SYNCED_MODELS`) and how rows are matched (by name, never by database id). To have a new setting synced, add its key to `SYNCED_SETTINGS`; a new table needs an entry in `SYNCED_MODELS` plus export and import code. DKIM keys go through `mailfilter-config dkim --stdin --json` (root, sudo).

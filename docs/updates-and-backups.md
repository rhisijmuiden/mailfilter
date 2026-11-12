# Updates and backups

## Versions and branches

The version number is in the `VERSION` file (for example `0.1.0`). The server runs a git checkout in `/opt/mailfilter` on one of two branches:

| Branch | Channel | Shown as |
| --- | --- | --- |
| `main` | Production | `0.1.0` |
| `test` | Test, every change lands here first | `0.1.0-test+abc1234` (version, branch and commit) |

## Updating

In the web interface, open **Info** and press **Check for updates**. It shows the newest version of both branches and the changes since the version you run. **Update** then:

1. makes a backup (incremental, or full when one is due);
2. moves the checkout to the newest commit of the branch;
3. runs the installer again with the saved settings (`install.sh --reconfigure`), which updates the configuration and restarts the services;
4. if step 3 fails, puts the previous version back and runs the installer again, so the server keeps working.

Switching between production and test works the same way. A branch that does not yet contain this updater cannot be installed from the web interface; you get a message and nothing changes.

The update runs as a background task under systemd, so it keeps going when the web interface restarts. Its progress and log are shown in the web interface (`mailfilterctl job status ID` and `mailfilterctl job log ID` on the server). Only one task (update, backup or restore) runs at a time.

## Backups

In the web interface: **System > Backups**. There you see every backup, make a full or incremental backup right away, change the schedule, check a backup and restore it.

A backup contains:

| Part | Where it comes from |
| --- | --- |
| Files | `/etc/mailfilter` (all settings, lists, passwords), `/var/lib/rspamd/dkim` (DKIM keys), `/etc/letsencrypt` (certificates), `/var/spool/postfix/hold` (quarantine) |
| Database | PostgreSQL database `mailfilter` (mail trace, users, settings), always complete |
| Spam filter learning data | Redis: Bayes, fuzzy hashes, greylisting |

A **full** backup contains all files. An **incremental** backup contains only the files that changed since the previous backup, so it is small and quick. A full backup with its incrementals is called a chain. The nightly backup makes a full backup when the last one is older than `full_every_days` (default 7) and an incremental otherwise. The oldest chains are removed when there are more than `keep_full` (default 4).

Backups are stored under `/var/backups/mailfilter/<chain>/<step>/` as ordinary `tar` archives (zstd compressed), a `pg_dump` file and a Redis dump, with a `manifest.json` holding checksums. A backup id looks like `20261111T023000Z.003`: chain plus step number.

Settings live in `/etc/mailfilter/backup.json` and can be changed in the web interface or with `mailfilterctl backup config KEY=VALUE`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Nightly backup on or off |
| `time` | `02:30` | Time of the nightly backup (server time) |
| `full_every_days` | `7` | Make a new full backup after this many days |
| `keep_full` | `4` | Number of full backups (with their incrementals) to keep |
| `location` | `/var/backups/mailfilter` | Folder for the backups |

## Remote copies (SCP, FTPS, FTP)

Under **System > Backups > Remote copy** you set up a second server that receives a copy of every backup. After each backup (also the nightly one) the new full or incremental backup is uploaded; **Copy now** catches up on anything that failed earlier. Old chains are removed remotely after the number of full backups you choose. A failed copy does not fail the local backup: it shows on the Backups page and is sent as a `remote.failed` event (see below).

* **SCP / SFTP** (recommended): Mailfilter makes an SSH key pair in `/etc/mailfilter/backup-ssh/`. Add the public key shown in the settings to `~/.ssh/authorized_keys` on the backup server. The first **Save and test connection** stores the server's fingerprint; a changed key is refused until you press **Forget fingerprint**.
* **FTPS**: FTP with explicit TLS (TLS 1.2 or newer, encrypted data connections). The certificate is checked unless you switch that off for a self-signed one.
* **FTP**: only inside a trusted network, the password travels unencrypted.

Files are encrypted with AES-256 (GnuPG) before they leave the server; only `manifest.json` (dates, sizes, checksums) stays readable, so the list of remote backups works without the passphrase. **Store the passphrase in your password manager**: you need it to restore on a new server. Settings live in `/etc/mailfilter/backup-remote.json`, the FTP password and passphrase in `backup-remote.secret.json` (root only).

Remote layout: `<folder>/<chain>/<NNN>/` with `*.gpg` files and `manifest.json`, which is uploaded last and marks a complete backup.

Command line: `mailfilterctl remote show|set|test|list|sync|forget-hostkey`, for example `mailfilterctl remote set protocol=scp host=backup.example.org username=mf enabled=true`.

## Events and hooks

Every finished task emits an event: `backup.succeeded|failed`, `remote.succeeded|failed`, `restore.succeeded|failed`, `update.succeeded|failed`. They are kept in `/var/lib/mailfilterctl/events.jsonl` (`mailfilterctl events`, API `GET /api/system/events`) and given as JSON on stdin (name in `$MF_EVENT`) to every executable in `/etc/mailfilter/hooks.d/` that is owned by root and not writable by others. Hooks have 60 seconds and cannot make a task fail. This is where alerts plug in.

## Restoring

Choose a backup in the web interface and press **Restore**, or run `mailfilterctl restore ID`. Restore:

1. checks the checksums of the backup and every backup it builds on;
2. makes a full safety backup of the current state ("before restore");
3. stops Postfix, Rspamd and the web interface;
4. restores the files: the full backup and then every incremental up to the chosen one, so files that were deleted at that moment are removed again;
5. restores the database and the spam filter learning data;
6. runs the installer with the restored settings and starts everything again.

With `--only files,database,redis` you restore only some parts. This also works on a freshly installed server: install Mailfilter, copy the backup folder to `/var/backups/mailfilter` and restore it, or set up the remote location with the same passphrase and choose **Show remote backups > Restore** (`mailfilterctl restore ID --from-remote`).

## E-mail sending

Mailfilter sends its own messages (alerts, quarantine reports, password and backup notices). Set this up under **System > E-mail sending**:

- **Through this Mailfilter** (default): the gateway's own Postfix on 127.0.0.1:25, so the messages are DKIM-signed like all other mail.
- **Through another SMTP server**: host, port, STARTTLS (587) or TLS (465), username and password. TLS 1.2 or newer with certificate checks; the password is stored encrypted in the database.

**Send test message** tries the settings in the form before you save them and explains in plain words what went wrong (wrong password, certificate not valid, server unreachable, and so on).

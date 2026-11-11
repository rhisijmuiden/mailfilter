#!/bin/sh
# Managed by Mailfilter. Runs after Let's Encrypt renews the certificate.
set -e
chmod 0755 /etc/letsencrypt/live /etc/letsencrypt/archive
chgrp caddy /etc/letsencrypt/archive/*/privkey*.pem 2>/dev/null || true
chmod 0640 /etc/letsencrypt/archive/*/privkey*.pem 2>/dev/null || true
systemctl reload postfix
systemctl reload caddy

#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run with sudo: sudo /usr/local/sbin/set-moomoo-dashboard-password" >&2
    exit 1
fi

htpasswd -c /etc/nginx/.htpasswd-moomoo-quant quant
chown root:www-data /etc/nginx/.htpasswd-moomoo-quant
chmod 640 /etc/nginx/.htpasswd-moomoo-quant
nginx -t
systemctl reload nginx
echo "Dashboard password updated for user quant."

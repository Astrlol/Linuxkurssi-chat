#!/usr/bin/env bash
# Runs on the server, started by GitHub Actions after the code has been
# rsynced to /opt/chatapp/app:
#   bash /opt/chatapp/app/scripts/deploy.sh <git-sha>
#
# Touches ONLY this app's own files and service:
#   /opt/chatapp, /etc/nginx/snippets/chatapp.conf, chatapp.service.
# Other weeks' sites, services and databases are not changed.
set -euo pipefail

REVISION="${1:-unknown}"
APP_DIR=/opt/chatapp/app
VENV=/opt/chatapp/venv
ENV_FILE=/etc/chatapp/chatapp.env
SOCKET=/run/chatapp/gunicorn.sock
SNIPPET=/etc/nginx/snippets/chatapp.conf

cd "$APP_DIR"

echo "==> Environment file"
if ! sudo test -f "$ENV_FILE"; then
    echo "ERROR: $ENV_FILE is missing. Create it on the server first (see README)." >&2
    exit 1
fi

echo "==> Python virtual environment and dependencies"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r requirements.txt

echo "$REVISION" > REVISION

echo "==> Database schema"
DB_NAME="$(sudo grep -E '^DB_NAME=' "$ENV_FILE" | cut -d= -f2)"
sudo mysql "$DB_NAME" < deploy/sql/schema.sql

echo "==> Nginx snippet (/viikko5/)"
# The snippet must be included in an enabled site (a one-time manual step).
if ! sudo grep -REq '^\s*include\s+(/etc/nginx/)?snippets/chatapp\.conf\s*;' /etc/nginx/sites-enabled/; then
    cat >&2 <<'MSG'
ERROR: the chat snippet is not included in any Nginx site.
Add this line ONCE inside the existing site's server { } block
(e.g. /etc/nginx/sites-available/default):

    include snippets/chatapp.conf;

Then: sudo nginx -t && sudo systemctl reload nginx   and run the deploy again.
MSG
    exit 1
fi

# Nginx (www-data) needs access to Gunicorn's socket, which belongs to the chatapp group.
# This is safe because the environment file is root:root 600, not readable by the group.
NGINX_ACTION=reload
if ! id -nG www-data | grep -qw chatapp; then
    sudo usermod -aG chatapp www-data
    NGINX_ACTION=restart   # a new group membership only applies to newly started processes
fi

# Install the new snippet but keep the old one: if nginx -t fails, restore the old one,
# so Nginx and the other weeks' pages keep working.
sudo cp -p "$SNIPPET" "$SNIPPET.bak" 2>/dev/null || true
sudo install -m 644 deploy/nginx/chatapp.conf "$SNIPPET"
if ! sudo nginx -t; then
    echo "ERROR: the Nginx configuration is invalid, restoring the previous snippet." >&2
    if [ -f "$SNIPPET.bak" ]; then sudo mv "$SNIPPET.bak" "$SNIPPET"; fi
    exit 1
fi
sudo rm -f "$SNIPPET.bak"
sudo systemctl "$NGINX_ACTION" nginx

echo "==> systemd service"
sudo install -m 644 deploy/systemd/chatapp.service /etc/systemd/system/chatapp.service
sudo systemctl daemon-reload
sudo systemctl enable chatapp >/dev/null
sudo systemctl restart chatapp

echo "==> Health check through the socket (expecting version $REVISION)"
# Ask Gunicorn directly. The workflow checks the public /viikko5/health afterwards.
for _ in $(seq 1 20); do
    if sudo curl -fsS --unix-socket "$SOCKET" http://localhost/health 2>/dev/null | grep -qx "$REVISION"; then
        echo "OK: version $REVISION is running."
        exit 0
    fi
    sleep 2
done

echo "ERROR: the app did not answer with the right version." >&2
sudo journalctl -u chatapp -n 50 --no-pager >&2
exit 1
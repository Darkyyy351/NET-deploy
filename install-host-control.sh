#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ $EUID == 0 ]] || { echo 'Run with sudo; pass the deployment user as the first argument.' >&2; exit 1; }
USER_NAME="${1:-${SUDO_USER:-}}"
[[ "$USER_NAME" =~ ^[a-z_][a-z0-9_-]*$ && "$USER_NAME" != root ]] || { echo 'Invalid deployment user' >&2; exit 1; }
HOME_DIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
[[ "$HOME_DIR" == /home/* && -d "$HOME_DIR/apps/NET-serverBCEND/.git" && -d "$HOME_DIR/apps/NET-frontend/.git" ]] || { echo 'Expected NET checkouts in user home/apps' >&2; exit 1; }
for binary in /usr/bin/python3 /usr/sbin/runuser /usr/sbin/shutdown; do
  [[ -x "$binary" ]] || { echo "Missing $binary" >&2; exit 1; }
done
command -v flock >/dev/null
if [[ -f /var/lib/net-host-control/operation.json ]]; then
  python3 - <<'PY'
import json
from pathlib import Path

state = json.load(open('/var/lib/net-host-control/operation.json')).get('state')
if state == 'installing':
    raise SystemExit('Host action active. Finish or cancel it before reinstalling the helper.')
if state == 'scheduled':
    try:
        pending = 'NET administrator request' in Path('/run/systemd/shutdown/scheduled').read_text()
    except OSError:
        pending = False
    if pending:
        raise SystemExit('Host action active. Finish or cancel it before reinstalling the helper.')
PY
fi
runuser -u "$USER_NAME" -- docker info >/dev/null
# Preserve the admin credential on reinstall. Never put it in frontend build variables.
python3 - "$USER_NAME" "$HOME_DIR" <<'PY'
import json, os, pathlib, secrets, sys
path = pathlib.Path('/etc/net-host-control.json')
if path.exists():
    config = json.loads(path.read_text())
    if config['user'] != sys.argv[1] or config['home'] != sys.argv[2]:
        raise SystemExit('Existing helper belongs to another user')
else:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(dict(user=sys.argv[1], home=sys.argv[2], apps=sys.argv[2]+'/apps', adminKey=secrets.token_hex(32)), stream)
PY
runuser -u "$USER_NAME" -- touch "$HOME_DIR/apps/.net-update.lock"
install -D -m 0755 "$SCRIPT_DIR/host-control/net-host-control.py" /usr/local/lib/net/host-control/net-host-control.py
install -m 0755 "$SCRIPT_DIR/update.sh" /usr/local/lib/net/host-control/update.sh
install -m 0644 "$SCRIPT_DIR"/compose.*.override.yml /usr/local/lib/net/host-control/
install -D -m 0644 "$SCRIPT_DIR/host-control/net-host-control.service" /etc/systemd/system/net-host-control.service
install -D -m 0644 "$SCRIPT_DIR/host-control/net-host-control.tmpfiles.conf" /etc/tmpfiles.d/net-host-control.conf
systemd-tmpfiles --create /etc/tmpfiles.d/net-host-control.conf
systemctl daemon-reload
systemctl enable net-host-control.service
systemctl restart net-host-control.service
echo 'Host helper installed. Run the NET updater once to mount its socket into the backend.'
echo 'Administrator key: read /etc/net-host-control.json with sudo; never share it or commit it.'

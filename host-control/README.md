# NET release and host control

## Scope

This is a host-side privileged bridge, not a Docker socket mounted into the API.
GET status uses the existing API token. Mutating operations additionally require
a separate 64-character admin key, verified by the helper and never stored by the UI.
Anyone with this admin key and API access can initiate host operations. Use a trusted
LAN/VPN or TLS; plaintext HTTP exposes credentials to network interception.

The helper checks the fixed NET-deploy main `release.json` URL every 15 minutes.
Missing/invalid manifest or network failure is unavailable, never "up to date".
Only a release manifest is an update announcement; normal development commits are not.
Install runs as the configured deployment user, pins both SHAs, retains backups and
image rollback from update.sh, and survives replacing backend/frontend containers.
It cannot automatically upgrade its own privileged helper; protocol changes require
manual installer execution and should not be advertised as compatible releases.

## First installation on CM5

Merge and pull the tested deploy/backend/frontend changes first. Do not install while
an update or fan/host maintenance operation is in progress.

```bash
cd ~/apps/NET-deploy
sudo bash install-host-control.sh darkman
bash update.sh
sudo systemctl status --no-pager net-host-control
```

Read the admin key locally (do not send it in chat or commit it):

```bash
sudo python3 -c 'import json; print(json.load(open("/etc/net-host-control.json"))["adminKey"])'
```

The initial CLI update mounts the helper socket. It does not restart/power off the host.
The backend container currently runs as root; the socket is root-only (`0600`). Its
runtime directory is traverse-only (`0711`) so the unprivileged updater can detect the
fixed socket path without listing the directory or connecting to the socket. A future
non-root container migration must explicitly arrange a dedicated socket group, not
chmod 666.

## Publishing an update

1. Test and merge backend/frontend changes into their deployment branch (`main`).
2. Copy release.example.json to release.json in NET-deploy, set exact full SHAs,
   a release version and concise notes. Never put branch names in commit fields.
3. Validate with `validate_release` from net-host-control.py and review the diff.
4. Commit/push the manifest to NET-deploy main only when ready to offer installation.
5. CM5 discovers it within 15 minutes; the dashboard can request an earlier check
   (at most once per minute). The displayed version identifies the release pair.

No live release.json is included in this initial batch: its final merged SHAs do not
exist yet. This deliberately reports unavailable until the first real release is published.
The pinned release must descend from the local checkouts; no automatic downgrade.

## Power operations

Restart/poweroff requires a typed confirmation and admin key. The host schedules
shutdown in one minute; the dashboard can cancel NET's pending power operation.
It affects all services, including services outside NET. Poweroff requires a separate
physical/RTC wake mechanism to turn CM5 on again. No wake mechanism is configured here.
The updater and power actions share a host flock; avoid external shutdown commands
while using this interface. Cancelling must not be used to manage externally scheduled shutdowns.

## Troubleshooting and verification

- `/var/lib/net-host-control/update.log`: last installation output (root-only).
- `journalctl -u net-host-control`: helper service errors.
- A helper restart during an operation reports interrupted; inspect actual containers.
- Never interpret a lost browser response as failure and blindly repeat power actions.
- Run unit tests: `python3 host-control/test_host_control.py`.
- Before enabling on CM5: verify real socket permissions, manual-update lock exclusion,
  release check, unchanged-release status, build failure rollback, scheduled reboot/cancel.
- Windows tests mock flock and subprocess calls. Actual CM5 shutdown/reboot is not
  exercised by automated tests, and was not performed during development.

Device offline/recovery notifications are now implemented separately in backend and
frontend. Next separate task: storage and backup monitoring.

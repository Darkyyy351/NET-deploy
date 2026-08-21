# NET Deploy

Deployment and update tooling for the NET backend and frontend on the CM5.

This repository intentionally manages only NET. Existing services such as Uptime Kuma are not part of its Compose projects and are never modified by the update script.

## Expected Layout

```text
~/apps/
|-- NET-deploy/
|-- NET-frontend/
`-- NET-serverBCEND/
```

Both application repositories must be on a clean `main` branch. Their `.env` files remain in their respective directories and are not read or copied by NET Deploy itself; Docker Compose uses them during builds and startup.

## First Installation

```bash
cd ~/apps
git clone https://github.com/Darkyyy351/NET-deploy.git
chmod +x ~/apps/NET-deploy/update.sh
```

## Update NET

Update the deployment tool first, then run the controlled application update:

```bash
cd ~/apps/NET-deploy
git pull --ff-only origin main
./update.sh
```

The script:

1. checks Docker, both repositories, branches, local changes, `.env` files and current rollback images;
2. creates a timestamped backend data backup under `~/apps/net-backups/`;
3. fast-forwards backend and frontend from `origin/main`;
4. builds uniquely tagged images while the current containers keep running;
5. switches backend and frontend and waits for Docker healthchecks;
6. records the active commits and deployment time for the dashboard;
7. restores both previous images automatically if either service fails.

The script never edits `.env`, device/log data or Uptime Kuma. It writes only `data/deployment.json`, which contains non-secret image, commit, timestamp and status metadata for the dashboard. Old images are retained so they can be inspected or removed manually after a successful update.

## Custom Paths

The defaults can be changed without editing the script:

```bash
NET_APPS_DIR=/srv/net/apps \
NET_BACKUP_DIR=/srv/net/backups \
NET_HEALTH_TIMEOUT=120 \
./update.sh
```

Available variables:

- `NET_APPS_DIR`: parent directory containing both application repositories;
- `NET_BACKEND_DIR`: explicit backend repository path;
- `NET_FRONTEND_DIR`: explicit frontend repository path;
- `NET_BACKUP_DIR`: backup destination;
- `NET_DEPLOY_BRANCH`: deployed branch, default `main`;
- `NET_HEALTH_TIMEOUT`: maximum seconds per container healthcheck, default `90`.

## Important First-Run Check

The updater refuses to continue unless Docker can still inspect the images used by the currently running `net-backend` and `net-frontend` containers. This guarantees that automatic rollback is possible before either container is replaced.

If this check fails, do not force the update. Inspect the current deployment first:

```bash
docker inspect --format '{{.Config.Image}}' net-backend net-frontend
docker images
```

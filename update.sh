#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APPS_DIR="${NET_APPS_DIR:-$HOME/apps}"
BACKEND_DIR="${NET_BACKEND_DIR:-$APPS_DIR/NET-serverBCEND}"
FRONTEND_DIR="${NET_FRONTEND_DIR:-$APPS_DIR/NET-frontend}"
BACKUP_ROOT="${NET_BACKUP_DIR:-$APPS_DIR/net-backups}"
DEPLOY_BRANCH="${NET_DEPLOY_BRANCH:-main}"
HEALTH_TIMEOUT="${NET_HEALTH_TIMEOUT:-90}"
BACKEND_OVERRIDE="$SCRIPT_DIR/compose.backend.override.yml"
FRONTEND_OVERRIDE="$SCRIPT_DIR/compose.frontend.override.yml"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
DEPLOYMENT_STATUS_FILE="$BACKEND_DIR/data/deployment.json"

OLD_BACKEND_IMAGE=""
OLD_FRONTEND_IMAGE=""
BACKEND_SHA=""
FRONTEND_SHA=""
ROLLBACK_REQUIRED=0

log() {
  printf '[NET update] %s\n' "$*"
}

fail() {
  printf '[NET update] ERROR: %s\n' "$*" >&2
  return 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command is missing: $1"
}

require_clean_repo() {
  local directory="$1"
  local name="$2"
  local branch

  git -C "$directory" rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "$name is not a Git repository: $directory"
  branch="$(git -C "$directory" branch --show-current)"
  [[ "$branch" == "$DEPLOY_BRANCH" ]] || fail "$name must be on branch '$DEPLOY_BRANCH' (currently '$branch')."
  [[ -z "$(git -C "$directory" status --porcelain)" ]] || fail "$name contains local changes. Commit or remove them before updating."
}

backend_compose() {
  local image="$1"
  shift
  env \
    BACKEND_DEPLOY_IMAGE="$image" \
    NET_COMMIT_SHA="${BACKEND_SHA:-development}" \
    NET_BUILD_TIME="$BUILD_TIME" \
    NET_IMAGE_REF="$image" \
    docker compose \
    --project-directory "$BACKEND_DIR" \
    -f "$BACKEND_DIR/docker-compose.yml" \
    -f "$BACKEND_OVERRIDE" \
    "$@"
}

frontend_compose() {
  local image="$1"
  shift
  env \
    FRONTEND_DEPLOY_IMAGE="$image" \
    NET_COMMIT_SHA="${FRONTEND_SHA:-development}" \
    NET_BUILD_TIME="$BUILD_TIME" \
    NET_IMAGE_REF="$image" \
    docker compose \
    --project-directory "$FRONTEND_DIR" \
    -f "$FRONTEND_DIR/docker-compose.yml" \
    -f "$FRONTEND_OVERRIDE" \
    "$@"
}

write_deployment_status() {
  local status="$1"
  local backend_commit="$2"
  local backend_image="$3"
  local frontend_commit="$4"
  local frontend_image="$5"
  local temp_file="${DEPLOYMENT_STATUS_FILE}.tmp"

  printf '%s\n' \
    '{' \
    '  "schemaVersion": 1,' \
    "  \"status\": \"$status\"," \
    "  \"deployedAt\": \"$BUILD_TIME\"," \
    "  \"backend\": { \"commit\": \"$backend_commit\", \"image\": \"$backend_image\" }," \
    "  \"frontend\": { \"commit\": \"$frontend_commit\", \"image\": \"$frontend_image\" }" \
    '}' > "$temp_file"

  chmod 600 "$temp_file"
  mv -f "$temp_file" "$DEPLOYMENT_STATUS_FILE"
}

wait_for_healthy() {
  local container="$1"
  local deadline=$((SECONDS + HEALTH_TIMEOUT))
  local status

  while (( SECONDS < deadline )); do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)"
    case "$status" in
      healthy)
        log "$container is healthy."
        return 0
        ;;
      unhealthy|exited|dead)
        docker logs --tail 80 "$container" >&2 || true
        fail "$container entered state '$status'."
        return 1
        ;;
    esac
    sleep 2
  done

  docker logs --tail 80 "$container" >&2 || true
  fail "$container did not become healthy within ${HEALTH_TIMEOUT}s."
}

rollback() {
  local exit_code="$?"
  trap - ERR

  if (( ROLLBACK_REQUIRED == 1 )); then
    set +e
    log "Update failed. Restoring the previous backend and frontend images."
    backend_compose "$OLD_BACKEND_IMAGE" up -d --no-build app
    frontend_compose "$OLD_FRONTEND_IMAGE" up -d --no-build frontend
    wait_for_healthy net-backend
    wait_for_healthy net-frontend
    write_deployment_status "rolled_back" "" "$OLD_BACKEND_IMAGE" "" "$OLD_FRONTEND_IMAGE"
    log "Rollback finished. Device/log data and configuration were not changed."
  fi

  exit "$exit_code"
}

trap rollback ERR

for command_name in git docker tar date; do
  require_command "$command_name"
done

[[ "$HEALTH_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || fail "NET_HEALTH_TIMEOUT must be a positive integer."
[[ -d "$BACKEND_DIR" ]] || fail "Backend directory does not exist: $BACKEND_DIR"
[[ -d "$FRONTEND_DIR" ]] || fail "Frontend directory does not exist: $FRONTEND_DIR"
[[ -f "$BACKEND_DIR/.env" ]] || fail "Backend .env is missing."
[[ -f "$FRONTEND_DIR/.env" ]] || fail "Frontend .env is missing."
[[ -d "$BACKEND_DIR/data" ]] || fail "Backend data directory is missing."

docker info >/dev/null
docker compose version >/dev/null
require_clean_repo "$BACKEND_DIR" "Backend"
require_clean_repo "$FRONTEND_DIR" "Frontend"

OLD_BACKEND_IMAGE="$(docker inspect --format '{{.Config.Image}}' net-backend)"
OLD_FRONTEND_IMAGE="$(docker inspect --format '{{.Config.Image}}' net-frontend)"
docker image inspect "$OLD_BACKEND_IMAGE" >/dev/null || fail "Current backend image cannot be used for rollback: $OLD_BACKEND_IMAGE"
docker image inspect "$OLD_FRONTEND_IMAGE" >/dev/null || fail "Current frontend image cannot be used for rollback: $OLD_FRONTEND_IMAGE"

BACKUP_DIR="$BACKUP_ROOT/$TIMESTAMP"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_ROOT" "$BACKUP_DIR"
tar -czf "$BACKUP_DIR/backend-data.tar.gz" -C "$BACKEND_DIR" data
log "Backend data backed up to $BACKUP_DIR/backend-data.tar.gz"

log "Fetching $DEPLOY_BRANCH for backend and frontend."
git -C "$BACKEND_DIR" fetch origin "$DEPLOY_BRANCH"
git -C "$BACKEND_DIR" merge --ff-only "origin/$DEPLOY_BRANCH"
git -C "$FRONTEND_DIR" fetch origin "$DEPLOY_BRANCH"
git -C "$FRONTEND_DIR" merge --ff-only "origin/$DEPLOY_BRANCH"

BACKEND_SHA="$(git -C "$BACKEND_DIR" rev-parse --short=12 HEAD)"
FRONTEND_SHA="$(git -C "$FRONTEND_DIR" rev-parse --short=12 HEAD)"
NEW_BACKEND_IMAGE="net-backend:$BACKEND_SHA"
NEW_FRONTEND_IMAGE="net-frontend:$FRONTEND_SHA"

log "Building $NEW_BACKEND_IMAGE while the current backend remains online."
backend_compose "$NEW_BACKEND_IMAGE" build --pull app
log "Building $NEW_FRONTEND_IMAGE while the current frontend remains online."
frontend_compose "$NEW_FRONTEND_IMAGE" build --pull frontend

ROLLBACK_REQUIRED=1
log "Switching backend to $NEW_BACKEND_IMAGE."
backend_compose "$NEW_BACKEND_IMAGE" up -d --no-build app
wait_for_healthy net-backend

log "Switching frontend to $NEW_FRONTEND_IMAGE."
frontend_compose "$NEW_FRONTEND_IMAGE" up -d --no-build frontend
wait_for_healthy net-frontend

write_deployment_status "healthy" "$BACKEND_SHA" "$NEW_BACKEND_IMAGE" "$FRONTEND_SHA" "$NEW_FRONTEND_IMAGE"
ROLLBACK_REQUIRED=0
trap - ERR

log "Update completed successfully."
printf '  Backend:  %s\n' "$BACKEND_SHA"
printf '  Frontend: %s\n' "$FRONTEND_SHA"
printf '  Backup:   %s\n' "$BACKUP_DIR/backend-data.tar.gz"

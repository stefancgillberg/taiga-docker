#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  echo "Usage: $0 <db-backup.sql> <media-backup.tar.gz> [options]"
  echo ""
  echo "  <db-backup.sql>          Path to PostgreSQL dump file"
  echo "  <media-backup.tar.gz>    Path to media tar archive"
  echo ""
  echo "  --name <name>            Container/project name (default: taiga-backup-validate)"
  echo "  --port <port>            Port for the web UI (default: 9001)"
  echo "  --test                   Only validate the backup files, skip the full stack"
  echo "  --remove                 Remove the Docker instance after validation"
  exit 1
}

# Parse arguments
DB_BACKUP=""
MEDIA_BACKUP=""
REMOVE_CONTAINER=false
FULL_STACK=true
NAME="taiga-backup-validate"
PORT=9001
OVERRIDE_FILE=""

for arg in "$@"; do
  case "$arg" in
    --remove) REMOVE_CONTAINER=true ;;
    --test)   FULL_STACK=false ;;
    --name|--port) ;;  # value captured via PREV_ARG on next iteration
    --help|-h) usage ;;
    *)
      if [[ "${PREV_ARG:-}" == "--name" ]]; then
        NAME="$arg"
      elif [[ "${PREV_ARG:-}" == "--port" ]]; then
        PORT="$arg"
      elif [[ -z "$DB_BACKUP" ]]; then
        DB_BACKUP="$arg"
      elif [[ -z "$MEDIA_BACKUP" ]]; then
        MEDIA_BACKUP="$arg"
      else
        echo "Unexpected argument: $arg"
        usage
      fi
      ;;
  esac
  PREV_ARG="$arg"
done

[[ -z "$DB_BACKUP" || -z "$MEDIA_BACKUP" ]] && usage

[[ -f "$DB_BACKUP" ]]    || { echo "ERROR: DB backup not found: $DB_BACKUP"; exit 1; }
[[ -f "$MEDIA_BACKUP" ]] || { echo "ERROR: Media backup not found: $MEDIA_BACKUP"; exit 1; }

# Derive container names
if [[ "$FULL_STACK" == true ]]; then
  DB_CONTAINER="${NAME}-taiga-db-1"
  BACK_CONTAINER="${NAME}-taiga-back-1"
else
  DB_CONTAINER="$NAME"
fi

# ── Conflict check ─────────────────────────────────────────────────────────────

if docker inspect "$DB_CONTAINER" &>/dev/null; then
  echo ""
  if [[ "$FULL_STACK" == true ]]; then
    echo "WARNING: A Taiga stack named '$NAME' already exists."
    echo "         Proceeding will stop and remove all its containers and volumes."
  else
    echo "WARNING: A container named '$NAME' already exists."
    echo "         Proceeding will stop and remove it, then create a fresh one."
  fi
  read -r -p "         Overwrite? [y/N] " confirm
  case "$confirm" in
    [yY][eE][sS]|[yY]) ;;
    *) echo "Aborted."; exit 0 ;;
  esac
  if [[ "$FULL_STACK" == true ]]; then
    TAIGA_DOMAIN="localhost:$PORT" TAIGA_SCHEME=http WEBSOCKETS_SCHEME=ws \
      docker compose -p "$NAME" -f "$SCRIPT_DIR/docker-compose.yml" down -v >/dev/null 2>&1 || true
  else
    docker rm -f "$NAME" >/dev/null
  fi
fi

# ── Cleanup trap ───────────────────────────────────────────────────────────────

cleanup() {
  [[ -n "$OVERRIDE_FILE" ]] && rm -f "$OVERRIDE_FILE"

  if [[ "$REMOVE_CONTAINER" == true ]]; then
    echo ""
    echo "==> Removing test instance..."
    if [[ "$FULL_STACK" == true ]]; then
      TAIGA_DOMAIN="localhost:$PORT" TAIGA_SCHEME=http WEBSOCKETS_SCHEME=ws \
        docker compose -p "$NAME" -f "$SCRIPT_DIR/docker-compose.yml" down -v >/dev/null 2>&1 || true
    else
      docker rm -f "$NAME" >/dev/null 2>&1 || true
    fi
    echo "    Done."
  else
    echo ""
    if [[ "$FULL_STACK" == true ]]; then
      echo "==> Stack '$NAME' left running at http://localhost:$PORT"
      echo "    Connect to DB: docker exec -it $DB_CONTAINER psql -U taiga -d taiga"
      echo "    Tear down:     docker compose -p $NAME -f $SCRIPT_DIR/docker-compose.yml down -v"
    else
      echo "==> Container '$NAME' left running."
      echo "    Connect: docker exec -it $NAME psql -U taiga -d taiga"
      echo "    Remove:  docker rm -f $NAME"
    fi
  fi
}
trap cleanup EXIT

echo ""
echo "==> Taiga Backup Validation"
echo "    DB:    $DB_BACKUP ($(du -sh "$DB_BACKUP" | cut -f1))"
echo "    Media: $MEDIA_BACKUP ($(du -sh "$MEDIA_BACKUP" | cut -f1))"
echo ""

# ── Start instance ─────────────────────────────────────────────────────────────

if [[ "$FULL_STACK" == true ]]; then
  echo "==> Starting full Taiga stack (project: $NAME, port: $PORT)..."
  echo "    This may take a minute..."

  OVERRIDE_FILE="$(mktemp /tmp/taiga-restore-override-XXXXXX.yml)"
  cat > "$OVERRIDE_FILE" <<EOF
version: "3.5"
services:
  taiga-gateway:
    ports:
      - "${PORT}:80"
EOF

  TAIGA_DOMAIN="localhost:$PORT" TAIGA_SCHEME=http WEBSOCKETS_SCHEME=ws \
    docker compose -p "$NAME" \
      -f "$SCRIPT_DIR/docker-compose.yml" \
      -f "$OVERRIDE_FILE" \
      up -d >/dev/null 2>&1
else
  echo "==> Starting test container ($NAME)..."
  docker run -d --name "$NAME" \
    -e POSTGRES_USER=taiga \
    -e POSTGRES_PASSWORD=taiga \
    -e POSTGRES_DB=taiga \
    postgres:12.3 >/dev/null
fi

# ── Wait for PostgreSQL ────────────────────────────────────────────────────────

echo "    Waiting for PostgreSQL..."
for i in {1..30}; do
  docker exec "$DB_CONTAINER" pg_isready -U taiga -q 2>/dev/null && break
  [[ $i -eq 30 ]] && { echo "ERROR: PostgreSQL did not become ready in time."; exit 1; }
  sleep 2
done
echo "    Ready."

# ── Restore database ───────────────────────────────────────────────────────────

echo ""
echo "==> Restoring database..."
docker cp "$DB_BACKUP" "$DB_CONTAINER":/tmp/backup.sql
RESTORE_ERRORS=$(docker exec "$DB_CONTAINER" psql -U taiga -d taiga -f /tmp/backup.sql 2>&1 \
  | grep -iE "^psql.*ERROR" | grep -v "already exists" || true)

if [[ -n "$RESTORE_ERRORS" ]]; then
  echo "    ERRORS during restore:"
  echo "$RESTORE_ERRORS" | sed 's/^/    /'
  echo ""
  echo "RESULT: FAIL - database restore had unexpected errors."
  exit 1
fi
echo "    Restored successfully (no unexpected errors)."

# ── Verify data ────────────────────────────────────────────────────────────────

echo ""
echo "==> Verifying data..."
COUNTS=$(docker exec "$DB_CONTAINER" psql -U taiga -d taiga -t -A -F'|' -c "
SELECT
  (SELECT COUNT(*) FROM users_user)            AS users,
  (SELECT COUNT(*) FROM projects_project)      AS projects,
  (SELECT COUNT(*) FROM userstories_userstory) AS user_stories,
  (SELECT COUNT(*) FROM issues_issue)          AS issues,
  (SELECT COUNT(*) FROM tasks_task)            AS tasks,
  (SELECT COUNT(*) FROM milestones_milestone)  AS sprints;
")

IFS='|' read -r users projects user_stories issues tasks sprints <<< "$COUNTS"

echo "    Users:        $users"
echo "    Projects:     $projects"
echo "    User stories: $user_stories"
echo "    Issues:       $issues"
echo "    Tasks:        $tasks"
echo "    Sprints:      $sprints"

if [[ "$projects" -eq 0 && "$users" -eq 0 ]]; then
  echo ""
  echo "RESULT: FAIL - database appears empty after restore."
  exit 1
fi

echo ""
echo "==> Active users:"
docker exec "$DB_CONTAINER" psql -U taiga -d taiga -t -A -F' | ' -c "
  SELECT username, email, date_joined::date
  FROM users_user
  WHERE is_active = true
  ORDER BY date_joined;" | sed 's/^/    /'

echo ""
echo "==> Projects:"
docker exec "$DB_CONTAINER" psql -U taiga -d taiga -t -A -F' | ' -c "
  SELECT name, created_date::date, is_private
  FROM projects_project
  ORDER BY created_date;" | sed 's/^/    /'

# ── Verify (and optionally restore) media ─────────────────────────────────────

echo ""
echo "==> Verifying media archive..."
MEDIA_FILES=$(tar -tzf "$MEDIA_BACKUP" 2>&1)
MEDIA_COUNT=$(echo "$MEDIA_FILES" | wc -l | tr -d ' ')

if [[ $? -ne 0 ]]; then
  echo "RESULT: FAIL - media archive is corrupt or unreadable."
  exit 1
fi

echo "    Archive is valid. ($MEDIA_COUNT entries)"
echo "    Top-level directories:"
echo "$MEDIA_FILES" | grep -oE '^media/[^/]+/' | sort -u | sed 's/^/    /'

if [[ "$FULL_STACK" == true ]]; then
  echo ""
  echo "==> Restoring media files..."
  # Wait for taiga-back to be running before writing to its volume
  for i in {1..30}; do
    status=$(docker inspect --format='{{.State.Status}}' "$BACK_CONTAINER" 2>/dev/null || echo "missing")
    [[ "$status" == "running" ]] && break
    [[ $i -eq 30 ]] && { echo "ERROR: taiga-back container did not start in time."; exit 1; }
    sleep 2
  done
  docker cp "$MEDIA_BACKUP" "$BACK_CONTAINER":/tmp/media-backup.tar.gz
  docker exec "$BACK_CONTAINER" bash -c \
    "cd /taiga-back && tar xzf /tmp/media-backup.tar.gz && rm /tmp/media-backup.tar.gz"
  echo "    Restored."

  # Wait for the web UI to respond
  echo ""
  echo "==> Waiting for web interface..."
  for i in {1..60}; do
    status=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$PORT/" 2>/dev/null || echo "000")
    [[ "$status" =~ ^(200|301|302)$ ]] && break
    [[ $i -eq 60 ]] && { echo "    WARNING: Web UI did not become ready within 2 minutes."; break; }
    sleep 2
  done

  echo ""
  echo "==> RESULT: PASS"
  echo "    Both backups restored. Full stack is running."
  echo ""
  echo "    Open in browser: http://localhost:$PORT"
else
  echo ""
  echo "==> RESULT: PASS"
  echo "    Both the database and media backups are valid and restorable."
fi

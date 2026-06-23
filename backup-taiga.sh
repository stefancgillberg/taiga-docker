#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${1:-$HOME/taiga-backups}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DB_BACKUP="$BACKUP_DIR/taiga-db-$TIMESTAMP.sql"
MEDIA_BACKUP="$BACKUP_DIR/taiga-media-$TIMESTAMP.tar.gz"

mkdir -p "$BACKUP_DIR"

echo "==> Backing up database..."
docker exec taiga-docker-taiga-db-1 pg_dump -U taiga taiga > "$DB_BACKUP"
echo "    Saved: $DB_BACKUP"

echo "==> Backing up media files..."
docker exec taiga-docker-taiga-back-1 tar czf /tmp/taiga-media-backup.tar.gz media
docker cp taiga-docker-taiga-back-1:/tmp/taiga-media-backup.tar.gz "$MEDIA_BACKUP"
docker exec taiga-docker-taiga-back-1 rm /tmp/taiga-media-backup.tar.gz
echo "    Saved: $MEDIA_BACKUP"

echo "==> Backup complete."
echo "    DB:    $DB_BACKUP ($(du -sh "$DB_BACKUP" | cut -f1))"
echo "    Media: $MEDIA_BACKUP ($(du -sh "$MEDIA_BACKUP" | cut -f1))"

echo "==> Removing backups older than 30 days..."
find "$BACKUP_DIR" -name "taiga-db-*.sql" -mtime +30 -delete
find "$BACKUP_DIR" -name "taiga-media-*.tar.gz" -mtime +30 -delete

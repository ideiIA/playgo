#!/usr/bin/env bash
# Backup do PlayGo: banco (pg_dump) + arquivos enviados (volume de uploads). Mantém os últimos N dias.
# Uso:   ./backup.sh                 (na pasta deploy/, no servidor)
# Cron:  0 3 * * *  cd /opt/playgo/deploy && ./backup.sh >> /var/log/playgo-backup.log 2>&1
set -euo pipefail
umask 077  # dump do banco e uploads só legíveis por quem roda o backup
cd "$(dirname "$0")"

DESTINO="${DESTINO:-/var/backups/playgo}"
DIAS="${DIAS:-14}"
CARIMBO="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DESTINO"

echo "[$(date -Is)] banco..."
docker compose --env-file .env exec -T db pg_dump -U playgo -d playgo --format=custom --no-owner > "$DESTINO/banco-$CARIMBO.dump"

echo "[$(date -Is)] arquivos enviados..."
docker compose --env-file .env run --rm --no-deps -T -v "$DESTINO:/backup" --entrypoint sh app \
  -c "tar -C /dados -czf /backup/uploads-$CARIMBO.tar.gz uploads"

find "$DESTINO" -type f \( -name 'banco-*.dump' -o -name 'uploads-*.tar.gz' \) -mtime +"$DIAS" -delete
echo "[$(date -Is)] pronto: $DESTINO"
ls -lh "$DESTINO" | tail -n 4

# Restaurar o banco (cuidado: substitui os dados):
#   docker compose --env-file .env exec -T db pg_restore -U playgo -d playgo --clean --if-exists --no-owner < banco-AAAAMMDD-HHMMSS.dump

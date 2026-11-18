#!/usr/bin/env bash
# Configura y levanta la plataforma en la VM.
#   ./scripts/setup.sh <DOMINIO> <AZURE_MAPS_KEY> [DIAS_PRECARGA]
set -euo pipefail
cd "$(dirname "$0")/.."
DOMAIN="$1"; KEY="$2"; DAYS="${3:-5}"
rnd() { openssl rand -hex "${1:-16}"; }
if [ ! -f .env ]; then
  cat > .env <<EOV
DOMAIN=$DOMAIN
AZURE_MAPS_KEY=$KEY
INFLUX_USER=admin
INFLUX_PASSWORD=$(rnd 12)
INFLUX_ORG=unab
INFLUX_BUCKET=iot
INFLUX_TOKEN=$(rnd 32)
GRAFANA_USER=admin
GRAFANA_PASSWORD=$(rnd 8)
ADMIN_TOKEN=$(rnd 12)
ALERT_TOKEN=$(rnd 16)
MQTT_BRIDGE_PASSWORD=$(rnd 12)
MQTT_BACKEND_PASSWORD=$(rnd 12)
MQTT_DEVICES_PASSWORD=$(rnd 12)
MQTT_REAL_PASSWORD=$(rnd 12)
EOV
  chmod 600 .env
fi
docker compose up -d --build
echo "Esperando a InfluxDB y al broker..."; sleep 40
if [ "$DAYS" != "0" ] && [ ! -f .precarga_hecha ]; then
  docker compose run --rm devices python run_cloud.py --backfill-days "$DAYS" --backfill-only && touch .precarga_hecha
fi
source .env
cat <<EOM

================ PLATAFORMA LISTA ================
Panel de control (front):  https://$DOMAIN/
Grafana:                   https://$DOMAIN/grafana/   (usuario $GRAFANA_USER / $GRAFANA_PASSWORD)
API (Swagger):             https://$DOMAIN/api/docs
Llave de administración:   $ADMIN_TOKEN
Broker para reales:        wss://$DOMAIN/mqtt  usuario real-device / $MQTT_REAL_PASSWORD

En tu computador (dispositivos reales):
  pip install paho-mqtt psutil
  python services/devices/real_devices.py --host $DOMAIN --password $MQTT_REAL_PASSWORD
===================================================
EOM

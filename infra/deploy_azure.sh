#!/usr/bin/env bash
# Crea la infraestructura en Azure y despliega la plataforma.
#   REPO=https://github.com/<usuario>/iot-capstone AZURE_MAPS_KEY=... ./infra/deploy_azure.sh
set -euo pipefail
RG=${RG:-rg-iot-capstone}; LOC=${LOC:-eastus2}; VM=${VM:-vm-iot-capstone}
DNS=${DNS:-iot-juanrojas-capstone}; SIZE=${SIZE:-Standard_B2s}
: "${REPO:?Define REPO con la URL del repositorio en GitHub}"
: "${AZURE_MAPS_KEY:?Define AZURE_MAPS_KEY}"
az account show >/dev/null 2>&1 || az login --use-device-code
az group create -n "$RG" -l "$LOC" -o none
sed "s#__REPO__#$REPO#" "$(dirname "$0")/cloud-init.yaml" > /tmp/cloud-init.yaml
az vm create -g "$RG" -n "$VM" -l "$LOC" --image Ubuntu2404 --size "$SIZE" \
  --admin-username azureuser --generate-ssh-keys --public-ip-sku Standard \
  --public-ip-address-dns-name "$DNS" --custom-data /tmp/cloud-init.yaml -o none
az vm open-port -g "$RG" -n "$VM" --port 80 --priority 1001 -o none
az vm open-port -g "$RG" -n "$VM" --port 443 --priority 1002 -o none
DOMAIN="$DNS.$LOC.cloudapp.azure.com"
echo "VM creada: $DOMAIN. Esperando cloud-init (Docker + repo)..."
az vm run-command invoke -g "$RG" -n "$VM" --command-id RunShellScript \
  --scripts "cloud-init status --wait; cd /opt/iot-capstone && ./scripts/setup.sh $DOMAIN $AZURE_MAPS_KEY 5" \
  --query "value[0].message" -o tsv | tee despliegue.txt
echo "Listo. Datos de acceso guardados en despliegue.txt (no lo subas al repositorio)."

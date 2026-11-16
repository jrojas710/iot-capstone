#!/usr/bin/env bash
# Borra toda la infraestructura (VM, disco, IP, red). Irreversible.
set -euo pipefail
RG=${RG:-rg-iot-capstone}
read -rp "Se eliminará el grupo $RG. Escribe SI: " ok; [ "$ok" = "SI" ] || exit 1
az group delete -n "$RG" --yes

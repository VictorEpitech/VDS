#!/usr/bin/env bash
# Déploiement à distance du VDS Player sur Raspberry Pi via SSH
set -euo pipefail

PI_HOST="${1:-}"
PI_USER="${2:-pi}"
SERVER_URL="${3:-https://allia-innovation.fr/espace/affichage}"

if [ -z "$PI_HOST" ]; then
    echo "Usage: $0 <PI_HOST> [PI_USER] [SERVER_URL]"
    echo "Exemple: $0 192.168.1.50 pi https://allia-innovation.fr/espace/affichage"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Déploiement distant VDS Player vers Raspberry Pi ($PI_USER@$PI_HOST) ==="

ssh "$PI_USER@$PI_HOST" "mkdir -p /tmp/vds-pi"
scp -r "$SCRIPT_DIR"/* "$PI_USER@$PI_HOST:/tmp/vds-pi/"
ssh -t "$PI_USER@$PI_HOST" "chmod +x /tmp/vds-pi/install.sh && sudo /tmp/vds-pi/install.sh '$SERVER_URL'"

echo "=== Déploiement distant terminé ==="

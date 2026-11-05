#!/usr/bin/env bash
# Installation et configuration automatique du VDS Player sur Raspberry Pi OS
set -euo pipefail

if [ "$EUID" -ne 0 ]; then
    echo "Erreur : ce script doit être exécuté en tant que root (sudo)."
    exit 1
fi

SERVER_URL="${1:-}"
if [ -z "$SERVER_URL" ]; then
    echo "Usage: sudo $0 <SERVER_URL>"
    echo "Exemple : sudo $0 https://allia-innovation.fr/espace/affichage"
    exit 1
fi

# Nettoyage du slash final
SERVER_URL="${SERVER_URL%/}"

# Extraction de l'origine (schéma + hôte + port optionnel)
ORIGIN=$(echo "$SERVER_URL" | grep -oE '^https?://[^/]+')
if [ -z "$ORIGIN" ]; then
    echo "Erreur : impossible d'extraire l'origine de $SERVER_URL"
    exit 1
fi

PLAYER_URL="${SERVER_URL}/player/"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Installation VDS Player sur Raspberry Pi ==="
echo "-> URL Serveur : $SERVER_URL"
echo "-> URL Player  : $PLAYER_URL"
echo "-> Origine     : $ORIGIN"

# 1. Création de l'utilisateur système 'vds' si absent
if ! id -u vds &>/dev/null; then
    echo "-> Création de l'utilisateur système 'vds'..."
    useradd -r -m -s /bin/false vds
fi

# Groupes nécessaires pour l'accélération GPU, le son et CEC
for grp in video render input audio dialout; do
    if getent group "$grp" &>/dev/null; then
        usermod -a -G "$grp" vds
    fi
done

# 2. Installation des paquets requis
echo "-> Vérification des paquets système..."
apt-get update -qq
CHROMIUM_PKG="chromium-browser"
if ! apt-cache show chromium-browser &>/dev/null; then
    CHROMIUM_PKG="chromium"
fi

apt-get install -y --no-install-recommends \
    "$CHROMIUM_PKG" \
    python3 \
    cec-utils \
    unclutter \
    xdotool \
    polkitd 2>/dev/null || apt-get install -y --no-install-recommends "$CHROMIUM_PKG" python3 cec-utils

# 3. Dossier applicatif /opt/vds
echo "-> Déploiement de l'assistant local dans /opt/vds..."
mkdir -p /opt/vds
cp "$SCRIPT_DIR/helper/vds_pi_helper.py" /opt/vds/vds_pi_helper.py
chmod 755 /opt/vds/vds_pi_helper.py
chown root:vds /opt/vds/vds_pi_helper.py

# 4. Configuration d'environnement /etc/vds/player.env
echo "-> Écriture de /etc/vds/player.env..."
mkdir -p /etc/vds
cat > /etc/vds/player.env <<EOF
# Configuration VDS Player pour Raspberry Pi
VDS_PLAYER_URL=${PLAYER_URL}
VDS_PI_ALLOWED_ORIGIN=${ORIGIN}
VDS_PI_ALLOW_REBOOT=0
EOF

chmod 0640 /etc/vds/player.env
chown root:vds /etc/vds/player.env

# 5. Déploiement des règles Polkit
echo "-> Configuration des règles Polkit..."
mkdir -p /etc/polkit-1/rules.d
cp "$SCRIPT_DIR/50-vds.rules" /etc/polkit-1/rules.d/50-vds.rules
chmod 0644 /etc/polkit-1/rules.d/50-vds.rules

# 6. Déploiement des unités systemd
echo "-> Déploiement des services systemd..."
cp "$SCRIPT_DIR/vds-pi-helper.service" /etc/systemd/system/vds-pi-helper.service
cp "$SCRIPT_DIR/vds-kiosk.service" /etc/systemd/system/vds-kiosk.service

chmod 0644 /etc/systemd/system/vds-pi-helper.service /etc/systemd/system/vds-kiosk.service

systemctl daemon-reload
systemctl enable vds-pi-helper.service
systemctl enable vds-kiosk.service

echo "-> Démarrage des services..."
systemctl restart vds-pi-helper.service
systemctl restart vds-kiosk.service

echo ""
echo "=== Installation terminée avec succès ==="
systemctl status vds-pi-helper.service --no-pager -n 3 || true
systemctl status vds-kiosk.service --no-pager -n 3 || true
echo "L'écran doit afficher l'interface du VDS Player."

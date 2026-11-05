#!/usr/bin/env bash
# Déploiement et enrôlement automatisé du VDS Player sur TCL Google TV (Android TV)
set -euo pipefail

TV_IP="${1:-}"
SERVER_URL="${2:-https://allia-innovation.fr/espace/affichage}"
DEVICE_OWNER="${3:-false}"

if [ -z "$TV_IP" ]; then
    echo "Usage: $0 <TV_IP> [SERVER_URL] [DEVICE_OWNER: true|false]"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APK_PATH="$SCRIPT_DIR/app/build/outputs/apk/debug/app-debug.apk"

echo "=== Déploiement VDS Player sur Android TV ($TV_IP) ==="

command -v adb >/dev/null 2>&1 || { echo "Erreur: adb introuvable dans PATH"; exit 1; }

if [ ! -f "$APK_PATH" ]; then
    echo "-> Compilation de l'APK debug..."
    (cd "$SCRIPT_DIR" && ./gradlew :app:assembleDebug)
fi

echo "-> Connexion ADB à $TV_IP:5555..."
adb connect "$TV_IP:5555"

echo "-> Installation de l'APK..."
adb install -r "$APK_PATH"

if [ "$DEVICE_OWNER" = "true" ]; then
    echo "-> Vérification Device Owner..."
    if adb shell dumpsys account | grep -q "Account {"; then
        echo "Attention: des comptes sont configurés sur la TV. Retirez-les pour activer le Device Owner."
        echo "Bascule en mode Kiosque doux..."
        DEVICE_OWNER="false"
    else
        echo "-> Activation de Device Owner..."
        adb shell dpm set-device-owner "app.vds.player/.AdminReceiver"
    fi
fi

if [ "$DEVICE_OWNER" != "true" ]; then
    echo "-> Configuration du Kiosque doux..."
    adb shell cmd package set-home-activity "app.vds.player/.MainActivity" || true
    adb shell appops set app.vds.player SYSTEM_ALERT_WINDOW allow || true
fi

echo "-> Désactivation de la veille d'inactivité..."
adb shell settings put secure attentive_timeout -1

echo "-> Provisioning de l'URL serveur ($SERVER_URL)..."
adb shell am start -n "app.vds.player/.ProvisionActivity" --es server_url "$SERVER_URL"

echo "-> Lancement du Player VDS..."
adb shell am start -n "app.vds.player/.MainActivity"

echo "=== Déploiement terminé avec succès ==="

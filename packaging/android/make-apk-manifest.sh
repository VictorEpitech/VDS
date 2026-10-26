#!/usr/bin/env bash
# Génère apk.json à côté de l'APK release signée. Usage : make-apk-manifest.sh <apk> <version_code> <version_name>
# Format lu par le serveur (docs/architecture-provision-android.md › Hébergement de l'APK).
set -euo pipefail
apk="$1"; code="$2"; name="$3"
dir="$(dirname "$apk")"; file="$(basename "$apk")"
sha="$(sha256sum "$apk" | cut -d' ' -f1)"
size="$(stat -c %s "$apk")"
apksigner="$(ls "${ANDROID_HOME:-$ANDROID_SDK_ROOT}"/build-tools/*/apksigner | sort -V | tail -1)"
signer="$("$apksigner" verify --print-certs "$apk" | sed -n 's/.*certificate SHA-256 digest: //p' | head -1)"
[ -n "$signer" ] || { echo "signature APK illisible" >&2; exit 1; }
printf '[{"package":"app.vds.player","version_code":%s,"version_name":"%s","sha256":"%s","size":%s,"signer_sha256":"%s","file":"%s"}]\n' \
  "$code" "$name" "$sha" "$size" "$signer" "$file" > "$dir/apk.json"

# power.adb — TCL Google TV 2023 (ADB réseau)

## Réglages sur la TV (une fois)
1. Paramètres > Système > À propos : appuyer 7 fois sur « Build » pour activer les options développeur.
2. Options pour les développeurs : activer « Débogage USB » puis « Débogage réseau / ADB sur réseau » (port 5555). Si seul le « débogage sans fil » (appairage par code, TLS) existe, le pilote ne fonctionne pas (voir limites).
3. Réserver l'IP de la TV (bail DHCP fixe). Préférer l'Ethernet.
4. Veille : désactiver les modes d'économie qui coupent le réseau en veille si possible.

## Test manuel
1. Config écran : `host` = IP de la TV. Lancer « Détecter » (commande `power.detect`) : la TV affiche « Autoriser le débogage USB ? », cocher « Toujours autoriser » puis OK. La clé RSA de l'agent est dans l'état chiffré de l'agent (`adb_private_key`).
2. `power.probe` : l'état doit être `on` TV allumée.
3. `power.set off` : la TV passe en veille (`KEYCODE_SLEEP`), l'état lu devient `standby` (ou injoignable, voir ci-dessous).
4. `power.set on` : réveil (`KEYCODE_WAKEUP`) en quelques secondes ; l'état revient à `on`.
5. Manuel équivalent : `adb connect <ip>:5555 && adb shell dumpsys power | grep mWakefulness`.

## Limites
- Pas de TLS ADB (STLS / débogage sans fil Android 11+) : code `power.adb.tls_required`.
- En veille profonde, beaucoup de TV coupent le réseau : l'état n'est alors plus lisible (`unknown`) et le réveil par ADB est impossible, il faut Wake-on-LAN (`power.wol`) ou CEC. Option `unreachable_means_off` pour considérer « injoignable » comme éteint.
- La clé ADB doit être acceptée sur la TV une fois ; refus ou absence de réponse : `power.adb.unauthorized`.
- Comportement à valider sur le firmware réel TCL (non testé sur matériel dans ce lot).

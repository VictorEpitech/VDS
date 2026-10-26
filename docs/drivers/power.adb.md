# power.adb 0.2.0 — TCL Google TV 2023 (ADB réseau) et provisioning Android

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

## Provisioning Android (capacité `provision.android`, 0.2.0)

Implémente `ScreenProvisioner` (plateforme `android`) pour l'assistant « Ajouter une TV Android » (`docs/architecture-provision-android.md`). Même transport et même clé RSA (`adb_private_key`) que l'alimentation : une seule invite RSA sur la TV. Permission `screens.provision`, core >= 1.3.

Étapes rapportées (`StepReport`, codes `provision.android.*`, clés i18n FR/EN du module) : `connect`, `authorize` (`waiting_user` pendant l'invite, 90 s max), `inspect` (SDK >= 24, comptes, profils, version installée, propriétaires), `download`/`verify` (sha256 + taille revérifiés par le module), `install` (flux `exec:cmd package install -r -S <taille>` via `exec_stream`, ignoré si même `version_code`), `device_owner`, `soft_kiosk`, `configure`, `inject` (`am start ProvisionActivity --es server_url --es pairing_ticket`), `launch` (`skipped` / `launch_by_app` en Device Owner, voir ci-dessous). Reprise par `from_step` ; `connect`/`authorize`/`inspect` sont toujours rejoués. Ticket jamais journalisé ni rapporté.

Device Owner : `dpm set-device-owner app.vds.player/.AdminReceiver` seulement si aucun compte, un seul profil, aucun autre propriétaire. Sinon l'étape est `skipped` avec `accounts_present` (+ `detail.accounts`), `extra_users`, `owner_exists` ou `dpm_refused`, et le kiosque doux est appliqué (Accueil par défaut + SYSTEM_ALERT_WINDOW) en annonçant ses limites (`soft_kiosk_active`). Résultat `kiosk` = `device_owner` | `soft` | `none`.

Restriction conservée par défaut : sous Device Owner l'app pose `DISALLOW_DEBUGGING_FEATURES`, donc ADB est coupé après le provisioning (`debugging_restricted` dans le rapport). `ProvisionActivity` lance déjà `MainActivity`, qui applique la restriction et coupe adbd : après `inject`, aucune connexion ADB n'est tentée en Device Owner. L'étape `launch` est donc `skipped` (`launch_by_app`) ; elle reste exécutée (`am start MainActivity`) hors Device Owner. L'issue réelle est confirmée par l'échange du ticket par le player (côté serveur, un échec rapporté après `inject` `ok` ne doit pas invalider le ticket). L'alimentation de cet écran doit passer par `power.cec` / WoL / le natif ; `power.adb` n'est utilisable qu'en kiosque doux ou en maintenance (réactivation manuelle des options développeur).

Kiosque doux : ADB TCP (5555) reste ouvert, l'invite RSA peut s'afficher sur l'écran public. Le résultat porte `code=provision.android.adb_open` et l'étape `soft_kiosk` `detail.adb_open=true, adb_port`. Pour fermer cette porte quand `power.adb` n'est pas le pilote retenu, `Driver.disable_network_debugging(req)` exécute `settings put global adb_enabled 0` (irréversible à distance, réactivation sur la TV). À n'appeler qu'après confirmation explicite de l'opérateur (« écran non public » / « alimentation hors ADB ») : le câblage de cette confirmation (champ de requête et étape finale de l'assistant) relève du SDK et du serveur, non faits ici.

### Test manuel sur vraie TV (provisioning)
1. TV sans compte Google ni profil supplémentaire (ou réinitialisée), « Débogage réseau » actif, IP notée.
2. Depuis l'admin : Ajouter une TV Android, saisir l'IP. Attendu : `waiting_user` ; accepter l'invite en cochant « Toujours autoriser » ; les étapes passent à `ok`.
3. Vérifier : `adb shell dpm list-owners` (Device Owner) et `pm list packages --show-versioncode app.vds.player`. Le player s'appaire sans code saisi et l'écran passe « En ligne ».
4. Cas compte présent : l'étape `device_owner` est `skipped` (`accounts_present`), kiosque doux annoncé ; retirer les comptes puis « Réessayer à cette étape » (`from_step=device_owner`).
5. Cas signature différente : `install_incompatible` ; désinstaller l'app puis relancer.
6. Relancer sur une TV déjà provisionnée : `install` `skipped`, aucune réinstallation.
7. Device Owner (I2) : `launch` doit être `skipped` (`launch_by_app`), sans `unreachable`, et le player doit s'appairer ; `adb connect` échoue ensuite (restriction active).
8. Kiosque doux (I3) : le rapport affiche l'avertissement `adb_open` ; `Driver.disable_network_debugging` doit faire échouer `adb connect` ensuite (vérifier sur le firmware TCL que `adb_enabled 0` coupe bien adbd).

### Limites (provisioning)
- Pas de STLS : TV en débogage sans fil seul => `tls_required` (réponse STLS détectée pendant la poignée de main).
- Le format de `dumpsys account` / `dpm list-owners` / `pm list users` varie selon les versions Android : parsing tolérant, à valider sur le firmware TCL réel (non testé sur matériel dans ce lot).
- Certains firmwares exigent une réinitialisation pour retirer le compte principal : Device Owner impossible, kiosque doux seul.
- ADB TCP circule en clair sur le LAN : le ticket (usage unique, 30 min, lié à l'écran) est interceptable par un tiers du LAN ; risque accepté et borné.
- Le kiosque doux n'empêche ni Accueil ni Assistant : réserver aux écrans non publics.
- Tests unitaires contre un faux appareil ADB (invite RSA en attente, flux d'installation, comptes présents, signature incompatible, déjà installé, reprise) ; aucune validation sur TV réelle.

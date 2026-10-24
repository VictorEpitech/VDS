# VDS Player pour Android (TCL Google TV)

Kiosque : une WebView plein écran sur le player, servi par un relais HTTP en boucle (`127.0.0.1:47800`) dans l'app.
Conception : `docs/architecture.md` › « Lecture hors ligne par plateforme » et « Empaquetage Android minimal ».

## Construire

Prérequis : JDK 17+, Android SDK (plateforme 34, build-tools 34). `local.properties` : `sdk.dir=/chemin/Android/Sdk`.

```
cd packaging/android
./gradlew :relay:test            # tests du relais, JVM seule (sans SDK Android)
./gradlew :app:assembleDebug     # app/build/outputs/apk/debug/app-debug.apk
```

Production signée : la CI (`.github/workflows/android.yml`) lit les secrets `ANDROID_KEYSTORE_BASE64`,
`ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`. En local, variables
`VDS_KEYSTORE_PATH`, `VDS_KEYSTORE_PASSWORD`, `VDS_KEY_ALIAS`, `VDS_KEY_PASSWORD`, puis `./gradlew :app:assembleRelease`.
Aucune clé dans le dépôt (`*.jks` ignorés).

## Installer sur la TCL

1. Sur la TV : Paramètres > Système > À propos, taper 7 fois sur « Build » ; Options pour les développeurs > Débogage USB et **Débogage réseau** (ADB sans fil).
2. Depuis le poste : `adb connect <ip-tv>:5555`, accepter l'empreinte sur la TV.
3. `adb install -r app-release.apk`
4. URL serveur, au choix :
   - saisie au premier lancement (écran « Adresse du serveur », vérifiée par `GET <url>/health`) ;
   - ADB : `adb shell am start -n app.vds.player/.ProvisionActivity --es server_url https://allia-innovation.fr/espace/affichage`
     (activité protégée par la permission signature `DUMP` : seul l'utilisateur shell peut la lancer, aucune app tierce ;
     `MainActivity` ignore désormais tout extra `server_url`) ;
   - configuration gérée (MDM / Device Owner) : restriction d'application `server_url`, lue au lancement.
   Le préfixe fait partie de l'URL ; en mode autonome : `https://vds.client.fr`. Changer l'URL purge le stockage web du player.

**HTTPS obligatoire.** Le trafic en clair n'est autorisé que vers `127.0.0.1` (WebView vers relais) par
`network_security_config`. Une URL `http://` hors boucle locale est refusée. Option LAN explicite (serveur autonome
sans certificat) : construire avec `./gradlew :app:assembleRelease -Pvds.lanHttp=true` ; l'app accepte alors `http://`
et le player remonte `player.insecure_upstream` dans le heartbeat (visible de l'admin, jamais du public).

### Device Owner (kiosque verrouillé, recommandé)

`dpm set-device-owner` n'est accepté que s'il n'existe **aucun compte** (Google, ou compte caché d'une app
constructeur : `adb shell dumpsys account`) et **qu'un seul utilisateur** (les profils Google TV sont des
utilisateurs Android). La contrainte ne vaut qu'à l'instant du provisioning ; on peut donc garder un compte
Google (Play Store, mises à jour) en respectant l'ordre :

1. Retirer le(s) compte(s) : Paramètres > Comptes et connexion > supprimer ; supprimer les profils supplémentaires.
   Selon le firmware, retirer le compte principal peut imposer une réinitialisation d'usine (recommencer alors
   l'assistant en passant la connexion Google).
2. `adb shell dumpsys account` ne doit lister aucun compte ; `adb shell pm list users` n'en montre qu'un.
3. `adb install -r app-release.apk`, puis `adb shell dpm set-device-owner app.vds.player/.AdminReceiver`.
4. **Réajouter le compte Google** (l'app ne pose pas `DISALLOW_MODIFY_ACCOUNTS`). Play Store sur un appareil géré
   par un DPC hors EMM : à vérifier en recette. Supprimer tout sélecteur de profil au démarrage.
5. `adb shell settings put secure attentive_timeout -1` et régler la veille automatique constructeur.

Le kiosque est **verrouillé par défaut** : l'app active `startLockTask()` et devient l'écran d'accueil persistant.
Le player applique ensuite le mode servi par le serveur (réglage groupe/écran, maintenance temporaire depuis l'admin) :
`unlocked` ou maintenance = `stopLockTask()` et écran d'accueil persistant retiré (Retour/Accueil normaux) ; retour
automatique à `locked` à l'échéance, **décidé par l'app elle-même** (minuterie native, bornée à 240 min), même si la
page ou le réseau sont morts. Au boot et à chaque redémarrage de l'app : verrouillé, puis le player rétablit le mode.
Retrait : `adb shell dpm remove-active-admin app.vds.player/.AdminReceiver` (ou réinitialisation d'usine).

### Sans Device Owner (kiosque doux)

Démarrage au boot (`BOOT_COMPLETED`), relance si l'app passe en arrière-plan, Retour neutralisé, plein écran
immersif, écran maintenu. À régler une fois par ADB :

```
adb shell cmd package set-home-activity app.vds.player/.MainActivity
adb shell appops set app.vds.player SYSTEM_ALERT_WINDOW allow     # relance depuis l'arrière-plan (Android 10+)
adb shell settings put secure attentive_timeout -1                # pas d'extinction d'inactivité Android 11+
```

Limites : Accueil, Assistant, raccourcis (YouTube/Netflix), source/réglages constructeur ne sont pas bloquables ;
le public peut voir quelques secondes une autre app avant le retour automatique. L'épinglage d'écran est écarté
(inutilisable sur TV). À réserver aux écrans non publics ; l'admin signale `soft` quand le mode demandé est `locked`.
Aucune mise à jour silencieuse de l'APK sans Device Owner (ADB). Play Protect peut avertir à l'installation d'une
APK hors Play Store : clé de signature stable.

### Pont natif `VdsNative`

Installé par `WebViewCompat.addWebMessageListener` limité à l'origine du relais (`http://127.0.0.1:47800`), jamais par
`addJavascriptInterface` : les iframes `content.url` n'ont pas le pont, et le natif refuse tout message qui ne vient pas
de la frame principale du player. Messages : `ping`, `reload`, `cec:<id>:<action>`, `kiosk:<id>:<mode>:<restant_ms>`
(tests JVM : `BridgeRouterTest`, `KioskDispatcherTest`, `CecDispatcherTest`).

## Recette sur matériel (à faire, non exécutée en développement)

- Le player s'affiche, `navigator.serviceWorker.controller` présent, hors ligne : lecture continue (couper le réseau).
- Redémarrage de la TV : l'app revient seule ; mise en veille/réveil.
- Retour, Accueil, appui long : aucun effet visible ; aucune URL, aucun code visible du public (sauf `E_RELAY_PORT` si le port est pris).
- Changement d'URL par ADB : appairage à refaire (stockage purgé).
- Kiosque : verrouillé par défaut (Accueil/Retour/Assistant sans effet en Device Owner). Maintenance de 5 min depuis
  l'admin : Accueil et Retour fonctionnent ; couper le réseau : retour verrouillé à l'échéance ; tuer l'app : relance verrouillée.
- Compte Google réajouté après `set-device-owner` : Play Store fonctionne, le kiosque tient (à constater sur la TCL).
- Vidéo de centaines de Mo : lecture et saut (Range) sans hausse mémoire notable.

## Contraintes

Voir `docs/architecture.md` › « Contraintes découvertes (L2-T9) » (cleartext, port ouvert aux autres apps, WebSocket lot 3, limites du lancement en arrière-plan).

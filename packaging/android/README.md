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

`dpm set-device-owner` n'est accepté que si **aucun compte Google/utilisateur n'existe sur l'appareil**.
Sur une TV Google TV neuve : ne pas se connecter à un compte Google à l'assistant de première configuration
(choisir « Configurer sur une TV Android » / passer la connexion), ou faire une réinitialisation d'usine.
Puis :

```
adb shell dpm set-device-owner app.vds.player/.AdminReceiver
```

Au lancement, l'app active `startLockTask()` : plus de barre système, plus d'accès au lanceur ; l'app devient
écran d'accueil persistant. Retrait : `adb shell dpm remove-active-admin app.vds.player/.AdminReceiver`
(ou réinitialisation d'usine).

### Sans Device Owner (kiosque doux)

Démarrage au boot (`BOOT_COMPLETED`), relance si l'app passe en arrière-plan, Retour neutralisé. Pour que la touche
Accueil ramène à l'app : `adb shell cmd package set-home-activity app.vds.player/.MainActivity`
(ou choisir VDS Player comme écran d'accueil si la TV le propose). L'utilisateur d'une télécommande peut encore ouvrir
les réglages système par certaines touches : seul le mode Device Owner l'interdit.

## Recette sur matériel (à faire, non exécutée en développement)

- Le player s'affiche, `navigator.serviceWorker.controller` présent, hors ligne : lecture continue (couper le réseau).
- Redémarrage de la TV : l'app revient seule ; mise en veille/réveil.
- Retour, Accueil, appui long : aucun effet visible ; aucune URL, aucun code visible du public (sauf `E_RELAY_PORT` si le port est pris).
- Changement d'URL par ADB : appairage à refaire (stockage purgé).
- Vidéo de centaines de Mo : lecture et saut (Range) sans hausse mémoire notable.

## Contraintes

Voir `docs/architecture.md` › « Contraintes découvertes (L2-T9) » (cleartext, port ouvert aux autres apps, WebSocket lot 3, limites du lancement en arrière-plan).

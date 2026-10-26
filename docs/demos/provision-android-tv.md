# Recette : assistant « Ajouter une TV Android » (TCL Google TV)

Durée : ~1 h 30. Aucun secret ici : clés, jetons et mots de passe sont saisis par vous.
Référence : `docs/architecture.md` › « Android TV : compte Google et kiosque géré », `packaging/android/README.md`, `docs/demos/kiosque-android-tv.md` (recette du kiosque une fois la TV provisionnée).
Automatisé (vert) : `make lint`, `make test`, `make e2e` (dont `e2e-agent` : serveur réel + agent réel + faux ADB, aux deux `VDS_BASE_PATH`), `make api-types-check`. Ici : tout ce qui dépend de la vraie TCL.

## 0. Prérequis serveur

Variables du serveur (fichier `.env`, jamais commité) :

| Variable | Rôle | Valeur |
| --- | --- | --- |
| `VDS_SECRET_KEY` | chiffre les tickets d'appairage ; **sans elle, l'assistant est indisponible** (« serveur non configuré ») | 32 caractères minimum, ex. `openssl rand -base64 36` |
| `VDS_PUBLIC_ORIGIN` | origine publique vue par la TV, ex. `http://192.168.1.182:8002` ; injectée dans l'app, doit être joignable depuis la TV | schéma + hôte + port, sans chemin |
| `VDS_BASE_PATH` | `/` puis `/espace/affichage` pour le second passage | |
| `VDS_PROVISION_LAN_CHECK` | défaut `true` : le serveur refuse (401, ticket non consommé) un appairage venant d'une adresse privée différente de l'IP de la TV de la session ; mettre `false` derrière un NAT Docker | `true` / `false` |
| `VDS_APK_SIGNER_SHA256` | empreinte attendue du certificat de signature ; une entrée de `apk.json` au `signer_sha256` différent est rejetée (côté CI : variable de dépôt, obligatoire pour `images.yml`) | SHA-256 hex, hors dépôt |
| `VDS_APK_DIR` | dossier `apk.json` + `.apk` signés ; défaut `/opt/vds/apk` (celui de l'image) | voir ci-dessous |

APK à fournir :
- Normal : la CI (`android.yml`) signe l'APK (secrets keystore) et `images.yml` l'embarque dans l'image serveur (`/opt/vds/apk`). Sans secrets de signature, l'image n'a aucune APK et l'assistant dit « application non disponible ».
- À la main : dossier hôte contenant `apk.json` (`[{package, version_code, version_name, sha256, size, signer_sha256, file}]`, généré par `packaging/android/make-apk-manifest.sh`) et l'APK release. Le monter avec `docker/compose.apk.yaml` :
  `VDS_APK_HOST_DIR=/srv/vds-apk docker compose -f docker/compose.yaml -f docker/compose.apk.yaml --env-file .env up -d`.

Rebuild docker (code serveur, admin et module `power.adb` 0.2.0 changés) :
- [ ] `docker compose -f docker/compose.yaml --env-file .env up -d --build` (ou `make dev`), puis `/health` répond.
- [ ] Agent de site (Pi/hôte du LAN de la TV) reconstruit : `docker compose -f docker/compose.agent.yaml --env-file docker/agent.env up -d --build`. Il doit être en `vds-core` 1.3 ; sinon l'assistant affiche « Mettez à jour l'agent de site ».
- [ ] Admin : module `power.adb` activé (Modules) ; agent « en ligne » sur le site de la TV.
- [ ] Admin > Écrans : bouton « Ajouter une TV Android » visible pour un admin, absent ou refusé pour un éditeur.

## 1. Préparer la TCL

- [ ] Paramètres > Système > À propos : 7 appuis sur « Build ». Options développeur : « Débogage USB » et « Débogage réseau » (port 5555) activés.
- [ ] Relever l'IP de la TV, la réserver dans le routeur. L'agent et la TV sont sur le même LAN.
- [ ] Noter la version Android/firmware de la TCL.

## 2. Parcours A : avec compte Google (cas réel) -> kiosque doux

- [ ] Garder le compte Google. Assistant : étape 1 choisir/créer l'écran, étape 3 saisir l'IP, Device Owner « Essayer », kiosque doux autorisé.
- [ ] Étape « Autorisation » : statut « Action sur la TV ». Invite RSA sur la TV : cocher « Toujours autoriser » puis accepter.
- [ ] Étapes dans l'ordre : connexion, autorisation, vérification, téléchargement, contrôle du fichier, installation, verrouillage complet (échec attendu : comptes présents), kiosque doux, réglage de la veille, transmission de l'appairage, lancement.
- [ ] Erreur « comptes présents » lisible ; choix « Continuer en kiosque doux » proposé ; bandeau kiosque doux avec la procédure de verrouillage complet.
- [ ] L'app démarre seule, sans saisie de code ni d'URL, l'écran passe « En ligne » ; l'assistant affiche « validée ».
- [ ] Kiosque doux : l'assistant avertit que le port ADB reste ouvert (`adb_open`) et demande la confirmation « écran non public » ; sans case cochée, pas de coupure. Cochée : étape finale après lancement, message `adb_closed`, `adb connect` échoue ensuite (alimentation `power.adb` alors indisponible : attendu). L'action est auditée (acteur).
- [ ] Écran admin : enforcement `soft`, alerte « Kiosque doux » (demandé `locked`, appliqué `soft`).
- [ ] `adb shell settings get secure attentive_timeout` = `-1` ; `adb shell cmd package resolve-activity -a android.intent.action.MAIN -c android.intent.category.HOME` pointe l'app.
- [ ] Alimentation : « Éteindre » puis « Allumer » depuis l'admin via `power.adb` (verified), toujours fonctionnels après l'installation.
- [ ] Rejouer l'assistant sur la même TV (déjà installée) : l'installation est sautée si `version_code` identique, pas de doublon d'écran (`reprovision` si déjà appairé).

## 3. Parcours B : sans compte Google -> Device Owner

- [ ] Retirer le compte Google puis les profils supplémentaires (`adb shell dumpsys account` : aucun compte ; `adb shell pm list users` : un seul utilisateur). Noter si une réinitialisation d'usine est exigée par la TCL.
- [ ] Assistant, Device Owner « Essayer ». Étape « Verrouillage complet » : OK (`dpm set-device-owner app.vds.player/.AdminReceiver` accepté).
- [ ] Bandeau « Verrouillage complet actif » ; admin : « Verrouillé », enforcement `lock_task`, aucune alerte kiosque doux.
- [ ] Réajouter le compte Google après coup : Play Store s'ouvre, le kiosque tient.
- [ ] Reprise : provoquer un échec (débrancher le réseau pendant le téléchargement) puis « Réessayer à cette étape » reprend sans tout refaire.
- [ ] Device Owner « Passer » : kiosque doux sans tenter `dpm`.

## 4. Refus et erreurs actionnables

- [ ] Refus de l'invite RSA (« Refuser » ou délai écoulé) : échec « Autorisation » avec message actionnable, rien installé, aucun ticket affiché ; « Réessayer à cette étape » OK après acceptation.
- [ ] Débogage réseau coupé : « TV injoignable : activez le débogage réseau… ».
- [ ] Adresse publique (ex. 8.8.8.8) ou `127.0.0.1` : refusée dès le formulaire. Mauvaise IP LAN : injoignable, message lisible.
- [ ] Agent arrêté : assistant « agent hors ligne ». Module `power.adb` désactivé : message dédié.
- [ ] Ticket d'appairage : usage unique, valable 10 min ; passé ce délai (ou après une première consommation) l'app est refusée. Appairage depuis une autre machine du LAN que la TV : 401, ticket intact (`VDS_PROVISION_LAN_CHECK=true`).
- [ ] APK dont le signataire diffère de `VDS_APK_SIGNER_SHA256` : « application non disponible ».
- [ ] « Annuler l'installation » : session annulée ; le ticket ne permet plus de s'appairer.
- [ ] Repli : « Utiliser le code d'appairage manuel » fonctionne toujours.

## 5. Les deux `VDS_BASE_PATH`

- [ ] Rejouer A ou B avec `VDS_BASE_PATH=/` puis `/espace/affichage` (avec `VDS_PUBLIC_ORIGIN` adapté, proxy Dokploy ou compose.subpath) : l'app appaire sur l'URL avec préfixe, schedule et médias OK.
- [ ] FR et EN, thèmes clair et sombre, assistant lisible à 375 px.

## 6. Limites connues

- L'ADB réseau et le TLS : la TV doit accepter ADB en clair (port 5555) sur le LAN ; une TV en `stls`/TLS-only affiche « tls_required » (non pris en charge, voir l'erreur dédiée).
- Un compte Google (ou un compte constructeur caché) ou un second profil Android bloque `dpm set-device-owner` : kiosque doux seulement (Accueil/Assistant/source ouvrent d'autres écrans puis l'app revient ; les touches ne sont pas bloquées). À réserver aux écrans non publics.
- L'invite RSA doit être acceptée avec « Toujours autoriser » ; une OTA peut désactiver le débogage réseau ou reprendre le rôle HOME (à revérifier).
- Pas de mise à jour silencieuse de l'APK sans Device Owner ; Play Protect peut avertir pour une APK hors Play Store (clé de signature stable).
- Contrôle LAN inopérant si le serveur est distant (IP publique du site) ; ARP-spoof de la TV possible : segmenter le réseau des écrans.
- Un seul provisioning actif par écran et par IP cible ; durée max bornée (délai global de la commande).
- Les tests automatiques utilisent un faux ADB : le dialogue réel, `dpm` réel, les délais TCL et Play Protect ne sont validés que par cette recette.
- Android uniquement (Tizen/Pi hors périmètre).

## Résultat à noter

- Version Android/firmware TCL, durée totale du parcours, étape ayant demandé une action manuelle.
- Message exact de `dpm set-device-owner` avec compte, et besoin éventuel de réinitialisation.
- Écarts à ouvrir dans `docs/roadmap.md` (K6/P8) ; compléter `docs/demos/kiosque-android-tv.md` §1.

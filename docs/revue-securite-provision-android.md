# Revue de sécurité — assistant « Ajouter une TV Android » (lot P)

Périmètre : commits `9eb779a` à `ed8bb76` (vds-core 1.3.0, `screen.provision`, `power.adb` 0.2.0, `ProvisionActivity` + coffre à ticket, assistant admin, CI APK). Revue en lecture seule, le 26 octobre 2026. Référence : `docs/architecture-provision-android.md`.

**Verdict : non bloquant pour le lot**, à condition de traiter I1 et I2 avant la mise en production (I1 avant toute ouverture du rôle admin à un tiers, I2 avant la recette sur une vraie TV en Device Owner).

Bilan : 0 bloquant · 5 importants · 10 mineurs.

## Points vérifiés et conformes

- **Ticket** : `secrets.token_urlsafe(32)` (256 bits), sha256 en base (`ticket_hash`), clair seulement dans `remote_command.args` chiffré Fernet (HKDF de `VDS_SECRET_KEY`, contexte dédié), déchiffré uniquement à la construction de la trame (`agents/service.py:205-210`), effacé à la clôture (`provisioning/service.py:85-94`). Usage unique sous `SELECT … FOR UPDATE` (`service.py:468-487`), TTL 30 min, erreur unique 401. Absent de l'audit, des événements du bus, des logs serveur/agent (`SecretStr`, `repr=False`), de l'URL (corps POST), de l'UI admin.
- **`/player/pair` exempté du contrôle d'origine** : acceptable. La route est anonyme, n'utilise aucun cookie (pas de CSRF possible), est limitée à 10/min/IP, et le schéma est strict (`extra=forbid`, regex base64url).
- **APK** : servie uniquement depuis `apk.json` (lookup par `version_code` entier borné), nom de fichier par regex sans `/` ni `..`, `resolve()` puis `is_relative_to`, taille ≤ 200 Mo, sha256 recalculé au démarrage (`provisioning/apk.py:88-109`). La route exige un jeton d'agent (`api/v1/provisioning.py:67`). Côté agent : URL construite localement, taille plafonnée pendant le flux, sha256 + taille vérifiés avant `rename`, cache nommé par sha256 validé (`vds_agent/provision.py:64-99`).
- **Adresse LAN** : validée au noyau (`parse_lan_address`, normalisation IPv4-mapped) et revalidée par l'agent (`ProvisionRequest._lan`, boucle locale et IP propre refusées). Verrou par IP côté agent, index unique partiel par écran côté serveur. Rôle `admin` exigé sur toutes les routes de l'assistant (`api/v1/provisioning.py:23`). La progression n'est acceptée que de l'agent du site de la session (`service.py:543`). Agent < 1.3 : assistant masqué (`capabilities`) et `unknown_command` sinon.
- **Shell ADB** : commandes constantes, entiers (`size`, `version_code`), `shlex.quote` sur l'URL et le ticket (déjà en base64url), aucune sortie shell brute remontée (`provision_android.py`).
- **Android** : `ProvisionActivity` protégée par `DUMP`, ticket validé, lié à l'URL, scellé AES-GCM avec une clé Android Keystore, remis une seule fois et seulement à la frame principale d'origine locale (`BridgeRouter.kt:21`, `VdsBridge.kt`). Aucun affichage ni logcat.
- **Admin** : aucun `dangerouslySetInnerHTML` ; les codes d'étape venus de l'agent ne sont affichés que s'ils correspondent à une clé i18n existante (`StepList.tsx:21`). Pas de XSS.
- **Secrets / image** : keystore décodé dans `RUNNER_TEMP`, jamais dans le dépôt. `docker/apk/*` est ignoré par git. L'image ne contient que l'APK et `apk.json`. Une `VDS_SECRET_KEY` vide vaut « non définie » et donne `server_unconfigured` (`settings.py:36-38`).

## Importants

### I1 — `screen.provision` reste envoyable par la route générique des commandes d'agent
`apps/server/src/vds_server/agents/service.py:152-185` et `:187-201` (`validate_command`) ; `packages/sdk-py/src/vds_sdk/wire/messages.py:160-161,204`.
`POST /sites/{site}/agents/{agent}/commands` valide `screen.provision` avec le seul schéma `ScreenProvisionArgs`. Dans ce schéma, `server_url` est n'importe quelle URL `^https?://` et `pairing_ticket` une valeur arbitraire.
**Scénario** : un admin (ou une session admin volée) envoie `screen.provision` avec `server_url=https://serveur-tiers` vers une TV du LAN. L'agent installe le player, tente le Device Owner et pointe la TV publique vers un serveur étranger, qui peut alors afficher n'importe quel contenu sur un écran du site. Cette voie contourne aussi :
- la session `screen_provision` et son verrou par écran ;
- l'audit `screen.provision.started` (adresse, version) ;
- la règle « https obligatoire » ;
- le chiffrement du ticket (stocké en clair dans `remote_command.args`).
**Correctif** : déclarer une liste de commandes réservées au noyau (`INTERNAL_AGENT_COMMANDS = {"screen.provision"}`) et la refuser dans `send_command` (422 `agents.command_internal`). En défense en profondeur, faire vérifier par l'agent que `server_url` a la même origine que son `http_base` ou que l'origine publique annoncée dans `welcome`.

### I2 — Device Owner : la restriction de débogage coupe ADB avant l'étape `launch`, et l'échec invalide le ticket
`modules/power.adb/agent/provision_android.py:158-161,309-325` ; `packaging/android/app/src/main/kotlin/app/vds/player/ProvisionActivity.kt:31` ; `AndroidKiosk.kt:73-74` ; `apps/server/src/vds_server/provisioning/service.py:579-586`.
À l'étape `inject`, `ProvisionActivity` démarre `MainActivity`. Celle-ci applique aussitôt le kiosque, donc `DISALLOW_DEBUGGING_FEATURES`, ce qui coupe adbd. La connexion ADB suivante (`launch`) échoue alors (`launch_failed`/`unreachable`). L'agent renvoie `failed`, et `on_cmd_result` appelle `finish("failed")`, qui efface `ticket_hash`. Si le player n'a pas encore échangé son ticket (cas probable : chargement du WebView), `POST /player/pair` répond 401 et la TV retombe sur le code manuel. Si le player a déjà échangé, la session passe quand même `failed` alors que l'écran est appairé. Les tests à faux ADB ne voient pas cette course.
**Correctif** :
1. Supprimer l'étape `launch` quand `inject` a réussi (`ProvisionActivity` lance déjà `MainActivity`), ou la rendre non bloquante.
2. Côté serveur, ne plus invalider le ticket sur un échec rapporté après une étape `inject` à `ok` : laisser l'expiration du ticket et `online_timeout` trancher.
3. Recette sur une vraie TV en Device Owner.

### I3 — Kiosque doux : le débogage réseau ADB (5555) reste ouvert en permanence sur l'écran
`provision_android.py:284-296` ; `docs/architecture-provision-android.md` › Risques.
Sans Device Owner, ADB TCP reste actif après le provisioning (c'est nécessaire pour `power.adb`).
**Scénario** : un poste du LAN (invité, Wi-Fi mal segmenté) ouvre une connexion ADB. L'invite RSA s'affiche sur l'écran public, et une personne qui a la télécommande, ou qui accepte par erreur, donne un shell complet : sortie du kiosque, réinstallation, lecture du jeton d'appareil. Cela contredit la règle 6 (kiosque) si l'écran est public.
**Correctif** :
- dans l'assistant, exiger une confirmation explicite « écran non public » avant d'accepter le kiosque doux ;
- tracer ce choix dans l'audit (`screen.provision.finished.kiosk=soft` existe déjà ; y ajouter l'acteur de l'accusé) ;
- documenter la segmentation réseau (VLAN écrans) ;
- à terme, désactiver `adb tcpip` en fin de provisioning quand `power.adb` n'est pas le pilote retenu pour l'écran.

### I4 — CI : APK signée avec la clé de production sur `pull_request`, choix d'artefact sans filtre d'événement ni contrôle du signataire — CORRIGÉ
`.github/workflows/android.yml:7-8,31-48` (préexistant pour la signature) ; `.github/workflows/images.yml:40-50`.
- L'étape de signature tourne aussi sur `pull_request`. Une PR depuis une branche du dépôt (pas un fork) dispose des secrets, et produit donc une APK de production signée à partir de code non relu. Android accepte cette APK comme mise à jour légitime des TV, puisque le signataire est le même.
- `images.yml` prend le dernier run `success` de la branche `main` sans `-e push`, et ne vérifie ni `signer_sha256` ni l'en-tête de l'artefact.
**Statut : corrigé.** `android.yml` ne signe plus sur `pull_request` ; `images.yml` filtre `-e push` et refuse l'APK si `signer_sha256` diffère de la variable de dépôt `VDS_APK_SIGNER_SHA256` (obligatoire, valeur hors dépôt) ou si sha256/taille/fichier de `apk.json` sont incohérents. Reste ouvert : *environment* protégé pour les secrets (réglage GitHub), `apksigner verify` direct dans `images.yml`.
**Correctif** :
- conditionner la signature à `github.event_name == 'push' || startsWith(github.ref, 'refs/tags/android-v')` et placer les secrets dans un *environment* protégé ;
- dans `images.yml`, ajouter `-e push` et vérifier, après téléchargement, `apksigner verify --print-certs` contre une empreinte épinglée (variable de dépôt `VDS_APK_SIGNER_SHA256`) ainsi que la cohérence sha256/taille avec `apk.json`.

### I5 — Ticket transmis en clair sur le LAN : un poste malveillant peut s'appairer à la place de la TV
`provision_android.py:309-319` ; `apps/server/src/vds_server/provisioning/service.py:468-487`.
C'est un risque documenté, mais sans mitigation dans le code. Un attaquant en position d'écoute ou d'usurpation ARP sur le LAN capte le ticket dans la trame ADB (non chiffrée), appelle `POST /player/pair` avant la TV et obtient un jeton d'appareil de l'écran : il lit le plan et les médias, et la session passe `done` dès son heartbeat. Un ARP-spoof suffit aussi à usurper la TV elle-même, car ADB n'authentifie pas l'appareil.
**Correctif** (au choix, cumulables) :
- n'accepter l'échange que depuis l'IP publique de sortie de l'agent du site (comparer `client_ip` au dernier `peer` de la connexion WS de l'agent, si `FORWARDED_ALLOW_IPS` est correctement réglé) ;
- afficher dans l'admin le modèle et la plateforme déclarés par l'appareil appairé, avec un bouton « Ce n'est pas ma TV » qui révoque l'appareil ;
- réduire le TTL du ticket à 10 min (le provisioning dure au plus 10 min côté agent).

## Mineurs

- **M1 — L'annulation n'arrête pas un provisioning déjà en cours sur l'agent.** `provisioning/service.py:434-461` : seules les commandes `queued`/`sent` passent `expired`. Une commande déjà prise par l'agent continue : installation, Device Owner, kiosque doux. Correctif : commande fermée `screen.provision_cancel {provision_id}`, ou vérification d'un jeton d'annulation entre deux étapes côté agent.
- **M2 — Oracle de balayage du LAN et de l'hôte Docker.** `vds_sdk/agent/provision.py:39-48` ; `vds_agent/provision.py:140-144` ; `vds_agent/runtime.py:50-57`. Le port est libre (1-65535) et seule l'IPv4 de sortie principale est exclue. La passerelle Docker (`172.17.0.1`), les autres interfaces de l'hôte et le serveur VDS en mode autonome restent joignables. Les codes `unreachable`/`protocol`/`unauthorized` révèlent quels ports sont ouverts (réservé à l'admin). Correctif : limiter `port` à 5555 ou ≥ 1024, exclure toutes les adresses locales (`socket.getaddrinfo`/`/proc/net/fib_trie`) et les réseaux des ponts Docker, et uniformiser les codes d'erreur réseau avant `authorize`.
- **M3 — Sortie ADB accumulée sans borne.** `modules/power.adb/agent/adb.py:190` (`out += data`). Un appareil hostile (IP usurpée) peut envoyer des WRTE en continu jusqu'au délai (300 s pour `install`) et épuiser la mémoire de l'agent. Correctif : plafonner `out` (64 Kio) et couper la session au-delà.
- **M4 — Données de l'agent stockées sans validation.** `provisioning/service.py:588` (`row.outcome = data` pour une session `done`, JSON brut) et `:551` (`code` sans limite de longueur). Correctif : valider avec `ProvisionOutcome` dans les deux branches et borner `code` (`^[a-z0-9_.]{1,96}$`).
- **M5 — Le chiffré du ticket et l'adresse sont exposés par la liste des commandes d'agent.** `agents/service.py:148-150` → `CommandOut.args`. C'est sans danger direct (Fernet), mais inutile. Correctif : masquer `pairing_ticket_enc` dans `CommandOut` (`"***"`).
- **M6 — Risque de ticket dans les logs de l'agent en cas d'arguments malformés.** `vds_agent/runtime.py:86,413-416` (`str(e)[:200]`) et `:433` (`log.exception`). Le message Pydantic inclut `input_value`, soit le dict complet pour une erreur `missing`. Correctif : `model_config = ConfigDict(hide_input_in_errors=True)` sur `ScreenProvisionArgs` et `ProvisionRequest`.
- **M7 — `VDS_SECRET_KEY` typée `str` et non `SecretStr`.** `settings.py:32-40`. En cas de clé trop courte, la `ValidationError` des réglages affiche la valeur dans les logs de démarrage. Correctif : `SecretStr` + `hide_input_in_errors`. Par ailleurs, la rotation de la clé invalide silencieusement les tickets en cours (repli `UNUSABLE_TICKET`, comportement sûr) : à documenter dans l'exploitation.
- **M8 — `VDS_PUBLIC_ORIGIN` non validé.** `settings.py:18`. Une valeur avec chemin, `http://` ou espace ne produit qu'un `args_invalid` tardif, ou une URL que la TV refuse. Correctif : valider au démarrage (schéma `https`, sauf build LAN, sans chemin, sans `/` final).
- **M9 — Signataire de l'APK jamais vérifié par le serveur.** `provisioning/apk.py:88-109`. `signer_sha256` est ignoré, alors qu'une première installation accepte n'importe quel signataire. Avec `VDS_APK_DIR`/`compose.apk.yaml`, une APK quelconque listée dans `apk.json` serait installée sur les TV. Correctif : variable `VDS_APK_SIGNER_SHA256` (défaut : empreinte de la CI) et rejet des entrées dont `signer_sha256` diffère (le contrôle cryptographique reste à la CI, voir I4).
- **M10 — `ProvisionActivity` accessible aux apps système privilégiées.** `AndroidManifest.xml:48-53` (préexistant). `DUMP` est `signature|privileged|development` : une app OEM préinstallée privilégiée peut repointer `server_url` et injecter un ticket. Correctif à terme : permission signature propre à VDS plus vérification de l'appelant (`Binder.getCallingUid() == SHELL_UID` via `getReferrer`/`ActivityManager`), ou blocage de `ProvisionActivity` dès que l'app est Device Owner et appairée.

## Dépendances

`cryptography>=43` ajoutée au serveur ; version épinglée par `uv.lock`. Aucune nouvelle dépendance npm ni Gradle dans le lot. `uv pip audit` et `pnpm audit` n'ont pas été lancés dans cette revue (lecture seule, sans réseau) : à exécuter en CI avant la clôture.

# VDS — Feuille de route V1

## Périmètre

| Bloc | V1 | Plus tard (module futur) |
| --- | --- | --- |
| Parc | Appairage, sites, groupes, tags, supervision, capture, actions | — |
| Contenus | Image, vidéo, PDF, URL ; météo, RSS | Autres sources, interactif |
| Mises en page | Grille + calques, paysage et portrait | Murs d'écrans |
| Diffusion | Playlists, alternance, override, planification par cible | Diffusion conditionnelle |
| Alimentation | Planning, exceptions, ADB, Samsung WS, WoL, CEC, écran noir | Cloud, IR, prise connectée |
| Utilisateurs | Admin, éditeur, groupes, validation, FR/EN ; connexion unique Studio Allia | SSO, LDAP, M365, Google |
| Hébergement | Autonome (Docker) et hébergé dans le Studio Allia (Dokploy) | — |
| Exploitation | Kiosque, mail, Docker par client, agent de site, mises à jour | Stats, API publique, catalogue |
| Alertes | — | Alertes prioritaires |

## Lots

Chaque lot se termine par une **démo sur un écran réel du campus**.

### Lot 1 — Socle
- [x] Monorepo, outillage (uv, pnpm, ruff, mypy, eslint), `Makefile`, `docker compose` (T1 ; eslint branché dans `apps/admin/eslint.config.js`, T8)
- [x] FastAPI, PostgreSQL 17, Alembic (T4 : modèles + migration 0001_socle ; `make test` lance un PG17 jetable)
- [x] `packages/sdk-py` : manifeste, registre, bus d'événements (T2 contrat, T5 registre + bus/outbox)
- [x] Configuration héritée + provenance (T6 : `apps/server/src/vds_server/config/`, résolveur pur + service + API ; branchement registre et auth en T5/suivant)
- [x] `VDS_BASE_PATH` respecté partout (API, admin, WebSocket, médias), testé à `/` et `/espace/affichage` (T3 : serveur ; admin/WS/médias à venir)
- [x] Interface auth-provider + module `auth.local`, rôles admin/éditeur, contrôle de même origine (T7 : pipeline `core/auth.py`, `modules/auth.local`, CRUD parc, audit)
- [x] `make api-types` / `api-types-check` : OpenAPI → `packages/sdk-ts/src/api.generated.ts`, openapi-typescript 7.13.0 épinglé (T9)
- [x] Modules de thème `theme.vtech` et `theme.allia` (T8 ; déclaratifs, tokens/polices/icônes/logo ; logos provisoires, couleurs d'état à valider)
- [x] Admin React : squelette, thème chargé depuis le module actif, i18n FR/EN (T8 partielle : serveur simulé `vite.mock-server.ts`, à rebrancher sur `config.js` réel et `/api` une fois T5 livrée ; pas d'auth ni de formulaire de module)
- [x] CI : build et publication des images (T10 : docker/*, ci.yml, images.yml, deploy/dokploy/README.md ; le player est servi sous `<base>/player/` depuis L2-T7)

### Lot 2 — Premier écran qui joue (jalon clé)
- [x] Contrat vds-core 1.1.0 : manifeste `content`, `ContentType`, `StorageBackend`, événements diffusion (L2-T1)
- [x] Migration `0002_diffusion`, modèles, API CRUD médias (métadonnées) / playlists / programmation, rôles, audit, tests aux deux `VDS_BASE_PATH` (L2-T2 ; reste : octets T3, résolution et `player/schedule`)
- [x] Module `storage.local` (`VDS_MEDIA_DIR`), envoi `POST /api/v1/media` (type par octets, taille `core.media_max_upload_mb`, sha256), service `<base>/media/<id>/<variant>` (Range, ETag, no-store, nosniff), crochet `rt.media_access.register_reader` pour le jeton d'appareil (L2-T3 ; jeton : T4)
- [x] Appairage (code 6 car. 15 min, claim admin, jeton `vds_dev_…` haché remis une fois, révocation 401 `device_revoked`, heartbeat 60 s, audit/événements, dépendance `rt.device_auth`) (L2-T4)
- [x] Player web : appairage, cache (SW + IndexedDB), lecture hors ligne, watchdog (L2-T7 : `apps/player`, serveur `<base>/player/` + CSP, content-types activés par défaut ; Playwright pairage → lecture → coupure → extinction/rallumage → révocation sur les deux `VDS_BASE_PATH` ; contraintes dans `architecture.md` › Contraintes de plateforme)
- [x] App Android (TCL Google TV) en kiosque : code, relais et CI livrés (L2-T9 : `packaging/android`, Kotlin/Gradle, relais Ktor CIO + OkHttp, 10 tests JVM verts, `assembleDebug` + lint verts, `.github/workflows/android.yml`) ; **non vérifié sur matériel** : WebView + Service Worker sur `127.0.0.1`, Device Owner, démarrage au boot, relance en arrière-plan (procédure de recette dans le README)
- [x] Modules content-type : image, vidéo, PDF, URL (L2-T6 : `modules/content.*`, `process_media` Pillow / ffprobe / pypdfium2, éléments `vds-content-*`, ffmpeg ajouté à l'image serveur ; tests `test_content_modules.py` et `content-modules.test.ts`)
- [x] Playlists + programmation simple (L2-T5 : résolveur pur `diffusion/resolver.py`, `GET /api/v1/player/schedule?format=1` ETag/304, révision du heartbeat réelle, lecture média par jeton limitée à la programmation de l'écran ; programmation sans règle = toute la journée)
- [x] Admin diffusion (L2-T8 : appairage code + choix d'écran, médiathèque avec progression et 409, éditeur de playlist à formulaire généré du schéma d'élément + ordre au clavier, programmation simple cible/créneaux HH:MM/priorité, révocation d'appareil ; schémas et i18n `content.*` embarqués au build faute d'endpoint de registre ; serveur simulé `vite.mock-diffusion.ts` ; vitest + Playwright 2 bases x 2 thèmes)
- [x] Registre exposé à l'admin : `GET /api/v1/registry/content-types` (content-types actifs : manifeste localisé, `item.schema.json`, schéma de config, i18n, lus à chaud ; rôle éditeur) et `ScreenOut.device` (présence `online|offline|unseen|revoked` sur 3 heartbeats, plateforme, modèle, version, dernier vu, révoqué, résumé du dernier heartbeat ; jamais le jeton) (L2-suite ; tests `test_player_registry_api.py` aux deux base paths)
- [x] E2E réel : `pnpm --filter @vds/player e2e:real` (`apps/player/e2e-real/`, `make e2e`) lance le vrai serveur + le vrai build du player + un PostgreSQL 17 dédié jetable par préfixe (`/` et `/espace/affichage`) : appairage, claim, programmation image, le player affiche le média servi avec son jeton. Corrige deux écarts révélés : `script` annoncé au player (`assets/assets/player.js` 404) et propriété d'élément `media` (schéma) lue comme `src` par `content.image` / `content.video`
- [ ] Admin : brancher `registry/content-types` à la place des schémas/i18n `content.*` embarqués au build, et `ScreenOut.device` dans la liste des écrans (apps/admin non touché)
- [ ] `content.pdf` : le player lit `props.pages` alors que le schéma d'élément ne porte que `media` (à résoudre côté `schedule.py`, avec les variantes de pages)

### Lot 3 — Multi-sites et alimentation
Conception et découpage L3-T0…T12 : `docs/architecture-lot3.md` (contrat vds-core 1.2.0).
- [x] Backlog sécurité mineur 6, 7, 8, 10, 11, 12 (L3-T0, en premier)
- [x] Agent de site : relais WebSocket sortant, cache média
- [x] Pilotes : ADB, Samsung WS, WoL, CEC, écran noir + assistant de détection
- [x] Planning d'alimentation + exceptions
- [x] Supervision + notifier mail
- [x] E2E réel serveur + agent (`make e2e-agent`, `apps/site-agent/e2e/`) : agent créé, `/ws/agent` TLS, plan via `agent/plan`, cascade Samsung (refus) → ADB → WoL, exécution hors ligne serveur coupé, rejeu au retour, `power.failed` → incident → mail (faux SMTP) → résolution, aux deux `VDS_BASE_PATH`
- [x] Écarts de l'E2E résolus (contrat non modifié) : (1) `power/state.py` (`PowerStateTracker`) écrit `power_state.desired` (plan résolu, recalculé toutes les 15 s et sur les événements de plan ; `desired_since` = début du créneau) puis `observed`/`power_action` depuis `power.changed|power.failed` (agent ou player, `reason` relu de la commande `power.set` par `request_id`, rejeu d'outbox dédoublonné) : `scheduled_off` se produit en conditions réelles ; (2) `PUT /api/v1/registry/modules/{id}/state` (`enabled|disabled`, admin, audité `module.enabled|disabled`, activation à chaud, codes `module.not_found` 404 / contrat 409) ; (3) arrêt propre : écoute LISTEN sur une connexion `engine.connect()` invalidée à l'annulation (plus de MissingGreenlet) et fermeture du pair (`WebSocketDisconnect`, dont 1011 à l'arrêt d'uvicorn) traitée par la session d'agent au lieu d'une exception ; l'E2E vérifie `power_state`/`power_action` et l'absence de `MissingGreenlet`/1011 au SIGTERM

### Lot 4 — Mises en page et diffusion complète
- [ ] Éditeur de grille + calques, paysage/portrait
- [ ] Alternance + override
- [ ] Widgets météo, RSS
- [ ] Groupes + validation avant diffusion

### Lot 4 bis — Hébergement dans le Studio Allia
- [ ] Module `auth.studio-allia` (vérification de session déléguée, correspondance des rôles, création du profil au premier accès)
- [ ] Côté Studio : route `/api/espace/vds/session`, entrée « Affichage » du menu, variable `VDS_STUDIO_SECRET` (guide 04 du Studio)
- [ ] Dokploy : application `vds` sur `allia-innovation.fr/espace/affichage`, base `vds-db`, volume `vds-media`, sauvegardes vers MinIO
- [ ] Respect des règles du Studio : noindex, no-store, 375 px, ton Allia, logos officiels
- [ ] Démo : un membre du Studio se connecte, publie un contenu, l'écran le joue

Ce lot peut être avancé avant le lot 3 si le Studio est le premier déploiement réel.

### Lot 5 — Durcissement et livraison
- [ ] Tizen (ou boîtier externe)
- [ ] Image Pi Chromium kiosque
- [ ] Installation / mise à jour en un clic, sauvegarde
- [ ] Déploiement sur les 4 premiers sites

## Décisions (22–23 octobre 2026)

Système maison (pas Xibo) · une instance Docker par client · perso, potentiellement commercial · noyau + modules, intégrés et à chaud, un format · FastAPI + React · player web unique 4 empaquetages · agent par site · alimentation en cascade · import de fichiers + grille/calques · alternance + override sur créneau · kiosque obligatoire · admin/éditeur + validation par groupe · FR/EN · notifications mail · pas de suivi conso · charte V-Tech (MD3) · nom de travail VDS.

### 23 octobre 2026 — Double mode
VDS se lance en autonome ET comme module complémentaire du Studio Allia · reste un service à part (FastAPI + React), même code dans les deux modes · accès hébergé sous `allia-innovation.fr/espace/affichage` depuis le menu du Studio, connexion unique · président et direction = admin, membre = éditeur, financeur = aucun accès · charte Allia en mode hébergé, V-Tech en autonome · base VDS séparée, jamais d'accès à `espace-db` · PostgreSQL 17.

### 23 octobre 2026 — Socle (architecte, lot 1)
Monorepo uv workspace + pnpm workspace, une seule image serveur (API + admin + player statiques), tests sur vrai PostgreSQL 17 · contrat de module **vds-core 1.0.0** : manifeste étendu (`name`, `description`, `entrypoints`, `requires`, `assets`, bloc `theme`), config avec `x-vds-level` obligatoire + `x-vds-scopes`, `x-vds-env`, `x-vds-secret`, catalogue de permissions fermé, événements `domaine.verbe_passé[@N]` avec outbox pour les critiques · l'auth locale devient le module `auth.local` (le noyau ne garde que utilisateurs, rôles et pipeline `Identity → Principal`) ; `AuthProvider.shell_links()` porte le « Retour au Studio » sans `if studio` · thème = module déclaratif sans code · `VDS_BASE_PATH` via `Mount` unique, `<base href>` injecté, `config.js`, médias en clés relatives, cookies `Path=<base>/`, en-têtes noindex/no-store/DENY et contrôle de même origine dans les deux modes · schéma `0001_socle` (organisation singleton, groupe rattaché à un site via FK composite, `config_override` à portée unique avec provenance, registre `module`, outbox, audit) · tables propres aux modules intégrés préfixées `m_<id>_` en branche Alembic · lot 1 : modules intégrés découverts au démarrage, activation/désactivation/quarantaine à chaud ; installation d'archive plus tard. Détail : `docs/architecture.md`.

### 23 octobre 2026 — Auth et secrets (architecte, lot 1, amende vds-core 1.0.0 avant publication, sans bump)
Permission `users.provision` ajoutée au catalogue 1.0, réservée au kind `auth-provider` (rejet au chargement du manifeste sinon) · rôles V1 = admin + éditeur seuls ; rôle externe inconnu = refus `role_unmapped` ; lecteur futur = ajout additif · limitation de débit de connexion dans le module auth (`auth.local`), le noyau n'expose que `ctx.core.http.client_ip()` · `x-vds-secret` : masqué dans l'API HTTP et les logs, mais remis au module propriétaire en `SecretStr`.

### 23 octobre 2026 — Arbitrages lot 1 (Victor)
Un groupe est rattaché à un seul site · `Cache-Control: no-store` aussi sur les médias et en mode autonome · registre d'images : GHCR · installation d'archive de module à chaud reportée après le lot 1.

### 23 octobre 2026 — Lot 2 diffusion et player (architecte, vds-core 1.1.0)
Contrat **vds-core 1.1.0** (mineur, additif : bloc manifeste `content`, `ContentType`, `StorageBackend`, `ctx.core.media`, événements `media.*`/`playlist.*`/`schedule.*`/`screen.paired|unpaired`, `sdk-ts` `content.ts` + `player.ts` ; modules 1.0 inchangés) · appairage : code 6 car. Crockford 15 min, réclamé par un admin, jeton `vds_dev_…` remis une seule fois au polling, stocké haché, un appareil actif par écran, révocation = 401 `device_revoked` → le player efface tout · programmation résolue `GET /api/v1/player/schedule?format=1` (ETag = révision, créneaux absolus J+J+1, URL relatives, layout implicite plein écran) · résolution lot 2 : cible la plus spécifique puis priorité ; alternance/override au lot 4 · migration `0002_diffusion` (screen_device, pairing_request, media, media_variant, playlist, playlist_item, playlist_item_media, schedule, schedule_rule) · octets via module `storage.local` (`VDS_MEDIA_DIR`) · content-types `content.image|video|pdf|url` en web components, PDF rendu en pages par `pypdfium2` · cache SW (shell + médias, Range servi depuis le cache) + IndexedDB, aucun flux en lecture · watchdog page + pont Android `VdsNative` · app Android Kotlin WebView, URL serveur saisie au 1er lancement, Device Owner sinon kiosque doux · **`/ws/player` reporté au lot 3** (heartbeat 60 s + `commands` vides en lot 2) · `tag`/`screen_tag` reportés au lot 4. Détail : `docs/architecture.md` › Diffusion et player.

### 23 octobre 2026 — Lecture hors ligne par plateforme (architecte, lot 2, sans changement de contrat)
Un seul cache (SW + IndexedDB du player web), chaque empaquetage garantit un contexte sécurisé · Android : relais HTTP en boucle `127.0.0.1:47800` dans l'app, toujours actif, la WebView charge le player par `localhost` (T9) · Pi : stratégie Chromium `OverrideSecurityRestrictionsOnInsecureOrigin` (lot 5) · Tizen et PWA : HTTPS à certificat public obligatoire, sinon boîtier externe · écartés : `shouldInterceptRequest` (corps des POST inaccessibles), cache natif via `VdsNative`, app en `file://` · HTTPS autonome documenté (ACME HTTP-01, DNS-01, `tls internal` admin seulement), profil Compose `https` au lot 5 · aucun changement noyau ni serveur. Détail : `docs/architecture.md` › Lecture hors ligne par plateforme.

## Risques

| Risque | Parade |
| --- | --- |
| Développeur seul, échéance rapide | Lots courts, démo par lot, modules futurs non codés |
| Modularité trop lourde pour la V1 | Contrat figé au lot 1, installation à chaud minimale |
| TV grand public instables | Cascade + vérification, tests sur vraies TV dès le lot 3 |
| Tizen mode dev contraignant | Repli boîtier externe |
| Écrans publics | Kiosque, déverrouillage par code |
| Réseaux partenaires | Agent sortant seul, check réseau par site |
| Charte incomplète | Couleurs d'état MD3 à valider, logo demandé |
| Mode hébergé qui fuit dans le code | Préréglage de modules uniquement ; tests systématiques avec les deux `VDS_BASE_PATH` |
| Changement côté Studio (cookie, rôles, menu) | Contrat de session minimal et versionné ; test d'intégration sur la route de session |
| Build sur VPS 1 qui fait tomber la production (déjà arrivé au Studio) | Images construites en CI, Dokploy ne fait que déployer |
| Pas de préproduction côté Studio | Migrations additives, déploiement VDS indépendant du Studio |

## Questions ouvertes

- [ ] Date cible V1
- [ ] Couleurs d'état + logo V-Tech
- [ ] Licence (fermé / source-available)
- [ ] Nom définitif
- [ ] Samsung : Tizen ou boîtier
- [ ] Cookie `allia_espace` posé avec `path=/` (à vérifier dans le Studio)
- [ ] Charte Allia : mode sombre et couleurs d'état
- [ ] Domaine des players en mode hébergé : même chemin `/espace/affichage` ou sous-domaine dédié
- [ ] Registre d'images (GitHub Container Registry ?) et accès Dokploy
- [ ] Calendrier de l'API publique
- [ ] Liste des 4 premiers sites et de leurs écrans
- [ ] Groupe d'écrans rattaché à un seul site (proposé ; le transversal passe par les tags) — à confirmer
- [ ] `Cache-Control: no-store` aussi sur les médias et en mode autonome (proposé : oui, le player et l'agent cachent eux-mêmes)
- **Contrat de thème côté admin (T8)** : `assets/theme.css` porte tokens `--vds-*`, polices auto-hébergées, icônes (`.vds-icon[data-icon=<nom sémantique>]`, masque CSS) et logo (`.vds-logo`) ; `config.theme.icons/logo` ne sont pas nécessaires. Les liens du mode (ex. « Retour au Studio ») viennent de `config.auth.links` (`{id, href}`, libellé `nav.links.<id>`). Config ESLint dans `apps/admin` (pas à la racine).

### 23 octobre 2026 — Admin initial via l'environnement (auth.local)
`VDS_ADMIN_EMAIL` + `VDS_ADMIN_PASSWORD` : si définis et qu'aucun compte n'existe, `auth.local` crée le premier admin au démarrage (idempotent, jamais d'écrasement, même politique de longueur, mot de passe jamais journalisé). Pas de jeton généré, `/bootstrap` répond 409. Variables absentes ou invalides : jeton d'amorçage inchangé. `auth.studio-allia` ne les utilise pas.

### T7 — Authentification (lot 1)
Rôles V1 = `admin` / `editor` (pas de « lecteur » : absent des docs et du CHECK en base) · lecture du parc et de la config : éditeur ; écriture : admin · `Identity.role = None` → rôle conservé en base, `editor` à la création · le compte noyau d'un provider est créé via la façade `ctx.core.users.provision` réservée au kind `auth-provider` (intégrée au catalogue comme permission `users.provision`, voir décision ci-dessous) · routes de module montées à chaud via `ModuleMux` · migrations : branches `core` et `<id module>`, `alembic upgrade heads` (`python -m vds_server.migrate`).

### 23 octobre 2026 — Revue sécurité : correctifs et backlog début du lot 3
Corrigés : (3) `content.pdf` rend dans un sous-processus jetable (`render_worker.py`, `python -I`, env vide, rlimits mémoire/CPU/fichier, délai ; échec ⇒ média `failed`, codes `content.pdf.invalid|too_many_pages|timeout|render_failed`) · (9) `ffprobe` : env minimal, rlimits, timeout · (4) `content.url` refuse toute URL de même origine que `VDS_PUBLIC_ORIGIN` (serveur) ou `location.origin` (player), quel que soit `VDS_BASE_PATH` ; plus de `allow-scripts allow-same-origin` possible sur une URL de même origine · médias servis avec `Content-Security-Policy: sandbox`.
L3-T0 corrigés (23 oct.) : (6) le Bearer n'exempte le contrôle de même origine que `player/heartbeat` et `agent/*` · (7) clé du limiteur IPv6 en /64, `app_factory` refuse `FORWARDED_ALLOW_IPS` contenant `*`, purge des `pairing_request` expirées depuis plus d'1 h · (8) `UploadLimitMiddleware` : `Content-Length` obligatoire (411) et borné par `media_max_upload_mb` (413) avant lecture, octets comptés · (11) jeton d'appareil à expiration glissante 90 j (prolongée par heartbeat) ; rotation avec période de grâce reportée (demande une colonne : migration lot 3) · (12) uv épinglé 0.13.5, actions épinglées par SHA, APK debug non publié.
Backlog (début du lot 3) : mineurs 6, 7, 8, 11, 12 de la revue sécurité ; 10 : l'admin n'a pas de CSP (à ajouter).

### 23 octobre 2026 — Lot 3 multi-sites et alimentation (architecte, vds-core 1.2.0, Victor absent : défauts sûrs)
Contrat **vds-core 1.2.0** (mineur, additif : `PowerDriver` agent + `PlayerPowerDriver` TS, `Notifier`, `StateStore` agent, `Event.origin`, `vds_sdk.wire`, permissions `power.control`/`power.calendars.write`/`incidents.read`, événements `agent.*`, `power.requested|changed|failed|plan_changed`, `screen.status_changed`, `incident.opened|resolved`, `cache.synced` ; modules 1.0/1.1 inchangés) · `<base>/ws/agent` : jeton `vds_agt_` 256 bits remis une fois, stocké sha256, en-tête `Authorization`, requête avec `Origin` refusée, hello/welcome, trames fiables seq/ack persistées des deux côtés, reprise, ping 30 s + heartbeat 60 s, commandes fermées (`power.set|probe|detect`, `plan.updated`, `config.updated`, `cache.sync`), événements relayés sur le bus avec `origin=agent:<site>` et contrôle de site · `<base>/ws/player` optionnel : jeton en premier message, `schedule.changed` (la programmation reste en HTTP), commandes `reload|identify|screenshot|power.set|power.probe`, polling conservé · le serveur planifie, l'agent (ADB, Samsung WS, WoL) et le player (CEC, écran noir) exécutent hors ligne le plan J+J+1 ; cascade générique avec vérification ; secrets d'appareil uniquement sur l'agent · plan d'alimentation et calendriers rattachés par la config héritée (`power.plan_id`, `power.calendars`) · migration `0003_sites_power` (site_agent, remote_command, power_plan(+rule), power_exception, power_calendar(+day), power_state, power_action, incident) · cache de site par contenu (sha256), service LAN signé HMAC **désactivé par défaut** [à confirmer par Victor : seul port en écoute de l'agent, LAN uniquement] · supervision noyau → incidents ; transport dans `notify.mail` (SMTP par variables d'environnement, mot de passe `x-vds-secret`, TLS vérifié) · backlog sécurité mineur traité en premier (L3-T0). Détail : `docs/architecture-lot3.md` (à fusionner dans `architecture.md` › Lot 3).

### 24 octobre 2026 — Revue sécurité lot 3 : importants corrigés
I1 : `agent_modules()`/`agent_events()` ne renvoient que les modules actifs ; les pilotes d'alimentation intégrés sont actifs par défaut (comme les content-types) ; `module.enabled|disabled|quarantined` ferme les sessions d'agent (1012), qui se reconnectent, et l'agent décharge (`teardown`) les pilotes absents de `modules_enabled`. I2 : heartbeat `/ws/player` validé par `Heartbeat` (4400 si invalide), au plus 1 écriture / 10 s, `device_state` tolérant. I3 : empreinte Samsung vérifiée sur la connexion TLS qui porte le jeton (aucun envoi avant vérification, `proxy=None`). I4 : code de fermeture **4411 `token_rotated`** (additif) : l'agent s'arrête sans effacer ses secrets ; `hello.out_seq` (optionnel) permet au serveur de réaligner `agent_seq` si l'état de l'agent a été perdu. Mineurs : M2 (`--ws-max-size 262144`), M5 (`pattern` sur `host`). 

Multi-workers (24 oct.) : tout signal local passe par `pg_notify` sur le canal `vds_command` (ou par la base) : `module.*` -> `modules:` (chaque hub ferme ses sessions en 1012), `PlanPusher` déduplique sur la dernière `plan.updated` en base (plus d'état mémoire), `PowerStateTracker` sérialise le recalcul par verrou consultatif, `/ws/player` unique par appareil (`device:<id>:<horodatage>`, la plus récente gagne, 4409). Mineurs M1, M3, M5 à M11 corrigés ; M4 en partie (reporté : empreinte comparée au `checksum` du registre, décision produit avec l'installation à chaud ; M3 : coffre externe de clé reporté, la clé locale reste le défaut avec avertissement).

### 24 octobre 2026 — Android TV avec compte Google, kiosque piloté depuis l'admin (architecte, sans bump vds-core)
Compte Google compatible avec Device Owner : retirer comptes et profils, `dpm set-device-owner`, puis réajouter le compte · sans Device Owner : kiosque doux (HOME + `SYSTEM_ALERT_WINDOW` + `attentive_timeout -1`), Accueil/Assistant/raccourcis non bloquables, épinglage d'écran écarté · kiosque **verrouillé par défaut** ; `core.player.kiosk` `locked|unlocked` réglable au groupe ou à l'écran seulement ; maintenance temporaire par écran 5 à 240 min (`POST /screens/{id}/kiosk/maintenance`), reverrouillage autonome à échéance ; champ optionnel `kiosk` dans la programmation (format 1) et le heartbeat ; pont natif par `addWebMessageListener` limité à l'origine du relais ; audit `screen.kiosk.*` ; aucune info au public. **À valider par Victor** : amender la règle 6 de `CLAUDE.md` (« kiosque obligatoire par défaut, relâchable par un admin, borné et audité »). Détail : `docs/architecture.md` › Android TV : compte Google et kiosque géré.
Tâches :
- [ ] K1 serveur (`builder`) : clés `core` (scopes, `readOnly`), endpoints maintenance, `kiosk` dans programmation et révision, heartbeat, `ScreenOut.kiosk`, audit. Fin : pytest aux deux `VDS_BASE_PATH` (PUT org/site refusé, PUT générique de l'échéance en 422, expiration, ETag change).
- [x] K2 player web (livré : `apps/player/src/kiosk.ts`, vitest et Playwright aux 2 bases verts) · (`builder`) : application du mode, message `vdsKiosk`, reverrouillage hors ligne à `until`, heartbeat `kiosk`. Fin : vitest + Playwright (maintenance expirée hors ligne = locked, rien d'affiché au public).
- [x] K3 Android (`builder`) : `addWebMessageListener` restreint, bascule Lock Task / HOME persistant / retour auto, borne 240 min, `onRenderProcessGone`, `enforcement` remonté. Fin : `./gradlew :relay:test :app:lint :app:assembleDebug` verts, iframe tierce sans effet.
- [ ] K4 admin (`builder`, skills `vds-ui-charte` + `vds-i18n`) : réglage groupe/écran, bouton maintenance (durée, motif, compte à rebours), badges, filtre, alerte kiosque doux. Fin : vitest + Playwright 2 bases x 2 thèmes, FR/EN.
- [ ] K5 doc installation (`grunt`) : README Android, procédure « Device Owner avec compte Google », commandes ADB du kiosque doux, Play Protect, recette. Fin : relu par `architecte`.
- [ ] K6 recette TCL avec compte (`qa-tests`) : provisioning, boot, touches Assistant/source/réglages en Lock Task, OTA, mise à jour WebView, 4 h sans télécommande. Fin : résultats consignés dans `architecture.md`.
- [ ] K7 `revue-securite` : pont natif, scopes, borne de durée, audit, absence de fuite au public. Fin : aucun point bloquant.

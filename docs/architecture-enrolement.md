# Refonte lot 1 — Enrôlement d'agent et agent local activable depuis l'admin

Statut : **décidé par l'architecte** le 30 octobre 2026, sur arbitrages de Victor (image agent privée, Windows ignoré, découverte au clic seulement, ordre enrôlement → `discovery.lan` → navigation). Rien n'est implémenté.
Contrat : **vds-core 1.4.0** (mineur, additif), limité à l'enrôlement. La découverte (`discovery.scan`, `network.discover`, `vds_sdk.agent.discovery`) est **hors de ce lot** : elle viendra en 1.5.0 au lot suivant.
Source : `docs/refonte/automatisation.md` (D1, D2, D3 amendée). À fusionner dans `docs/architecture.md` › Agent de site.

Exigence directrice : **aucune ligne de commande pour l'utilisateur sur l'hôte du serveur après le premier `docker compose up`**. Une machine distante (autre site, mode hébergé) demande forcément une action sur cette machine : une ligne `docker run` générée par l'admin, ou plus tard l'image Pi avec fichier déposé (lot 5).

## 1. Agent local : comment le serveur le fait tourner sans CLI ni `docker.sock`

### Option retenue : service Compose `agent` toujours présent, en sommeil, réveillé par un dépôt de code dans un volume partagé

- `docker/compose.yaml` déclare un service `agent` **par défaut** (pas de profil), `network_mode: host`, image `${VDS_AGENT_IMAGE:-vds-agent:local}` (même version que le serveur, construite en CI comme le serveur).
- Un volume nommé `local-agent` est monté dans **les deux** conteneurs : `/run/vds/local-agent`. C'est le seul canal local. Ni port, ni socket, ni secret permanent.
- Sans jeton ni code, l'agent démarre en **sommeil** : il n'ouvre aucune connexion, écrit toutes les 10 s un fichier de présence et surveille le dépôt d'un code.
- L'admin clique « Ce serveur pilote les écrans de ce réseau » sur un site : le serveur crée un enrôlement (même mécanisme que le distant, canal `local`, 10 min) et **écrit le code dans le volume**. L'agent le lit, l'efface, appelle `POST /api/v1/agent/enroll` par la boucle locale, range son jeton chiffré, se connecte à `/ws/agent`. L'admin voit l'agent arriver en direct.
- L'URL vue par l'agent local est `http://127.0.0.1:${VDS_PORT}${VDS_BASE_PATH}` (port publié du serveur, joint par la boucle locale grâce au réseau hôte), surchargeable par `VDS_LOCAL_AGENT_SERVER_URL` (serveur derrière un proxy TLS sans port publié). Le clair est accepté **sans** `VDS_AGENT_INSECURE_DEV` uniquement vers `127.0.0.0/8` et `::1` (rien ne sort de l'hôte).

Pourquoi le réseau hôte : Wake-on-LAN (broadcast), puis au lot suivant ARP/mDNS/SSDP, ne traversent pas le NAT d'un réseau bridge. ADB et Samsung WS marcheraient en bridge, pas le reste. Docker Desktop (macOS) gère mal `host` : non supporté en V1, comme Windows ; l'agent y reste en sommeil et l'admin affiche « agent local non détecté ».

Ce que ça coûte : une image de plus à tirer au premier `up` (image privée : mêmes identifiants GHCR que l'image serveur), un processus en sommeil (~30 Mo de RAM) sur les installations qui ne s'en servent pas, une permission de fichier partagée à régler (UID/GID communs, voir E5). Rien dans le noyau ne sait si on est sur un LAN : l'admin décide en cliquant, avec le texte « seulement si ce serveur est sur le même réseau que les écrans ».

Règles respectées : agent sans port entrant (règle 7 : il ne fait que des connexions sortantes vers la boucle locale) ; noyau minimal (le noyau ajoute un canal de dépôt de code générique, aucun code d'agent ni pilote dans le serveur) ; aucune hypothèse d'hébergeur (règle 11 : la disponibilité dépend uniquement de `VDS_LOCAL_AGENT_DIR`) ; même code dans les deux modes.

**Mode hébergé** : Dokploy ne définit ni `VDS_LOCAL_AGENT_DIR` ni service `agent`. `GET /api/v1/agent/local` répond `available: false`, `POST …/agent/local` répond 409 `agent.local.unavailable`, l'admin n'affiche que « Une autre machine du site ». Aucun `if studio`.

### Options écartées

- **Agent embarqué dans le processus ou le conteneur serveur** (tâche asyncio réutilisant `vds_agent`) : le serveur devrait passer en réseau hôte pour joindre le LAN (casse l'isolement, les ports Compose et Dokploy), chargerait les pilotes et leurs secrets (clé RSA ADB, jetons Samsung) dans le processus qui expose l'API publique, et se dupliquerait par worker. Contraire au noyau minimal.
- **Profil Compose `agent` + CLI `enroll-code`** (D3 initiale) : demande deux commandes sur l'hôte, contraire à l'exigence. La CLI `vds_server.cli agent enroll-code` reste utile aux scripts d'intégrateur mais n'est plus sur le chemin utilisateur : reportée, hors lot.
- **Serveur qui démarre l'agent via `docker.sock` ou SSH** : root sur l'hôte. Inchangé : refusé.
- **Secret d'enrôlement permanent partagé dans `.env`** : crédit long-vivant. Refusé.
- **Jeton `vds_agt_` écrit directement dans le volume** : un secret long-vivant au repos dans un volume partagé et un deuxième chemin d'émission de jeton. Le code à usage unique garde un seul mécanisme, testé une fois.

## 2. Contrat vds-core 1.4.0 (additif)

`CORE_API_VERSION = "1.4.0"`. Modules 1.0 à 1.3 chargent inchangés. Aucun changement de manifeste, de `x-vds-level`, ni du catalogue de permissions. Aucun changement de wire (`hello` porte déjà la version d'agent).

### 2.1 Migration `0008_agent_enrollment` (branche `core`, réversible)

```
agent_enrollment
  id                uuid pk
  site_id           uuid not null fk site(id) on delete cascade
  channel           text not null check (channel in ('remote','local'))
  code_sha256       bytea not null unique          -- 32 octets, jamais le code
  created_by        uuid null fk app_user(id) on delete set null
  created_at        timestamptz not null default now()
  expires_at        timestamptz not null
  consumed_at       timestamptz null
  consumed_ip       inet null
  consumed_hostname text null                      -- borné à 253, donnée non fiable, affichage seul
  agent_id          uuid null fk site_agent(id) on delete set null
  revoked_at        timestamptz null
index partiel (site_id) where consumed_at is null and revoked_at is null
check (consumed_at is null or revoked_at is null)
```

Une seule inscription en attente par site : en créer une révoque les précédentes (`reason=superseded`). Purge des lignes expirées depuis plus de 30 j (tâche de purge existante des `pairing_request`). Pas de nouvelle colonne sur `site_agent`.

### 2.2 Code

`vds_enr_` + 26 caractères base32 Crockford (128 bits, `secrets.token_bytes(16)`), stocké `sha256`, comparaison par recherche du hachage (pas d'énumération par préfixe). TTL : `remote` 60 min (réglable 10 à 1440 min à la création), `local` 10 min fixe. Jamais journalisé, jamais dans un événement ni un audit (seul `enrollment_id` y figure).

### 2.3 Modèles partagés : `vds_sdk.agent.enroll`

```python
ENROLL_CODE_PREFIX = "vds_enr_"

class EnrollRequest(BaseModel):            # extra="forbid"
    code: constr(pattern=r"^vds_enr_[0-9A-HJKMNP-TV-Z]{26}$")
    hostname: constr(max_length=253, pattern=r"^[A-Za-z0-9._-]+$")
    agent_version: constr(max_length=32)
    platform: constr(max_length=32)        # "linux/amd64", "linux/arm64"
    previous_token: SecretStr | None = None   # agent déjà enrôlé qui change de site

class EnrollResponse(BaseModel):
    agent_id: UUID
    site_id: UUID
    token: SecretStr                       # vds_agt_…, remis une seule fois
    ws_path: Literal["ws/agent"] = "ws/agent"

class LocalHandoff(BaseModel):             # fichier <dir>/enroll.json, écrit par le serveur
    v: Literal[1] = 1
    code: SecretStr
    enrollment_id: UUID
    expires_at: datetime

class LocalPresence(BaseModel):            # fichier <dir>/presence.json, écrit par l'agent, ≤ 4 Kio
    v: Literal[1] = 1
    hostname: str; agent_version: str; platform: str
    state: Literal["dormant", "enrolling", "active", "error"]
    agent_id: UUID | None = None
    error: str | None = None               # code i18n, jamais un message brut
    ts: datetime
```

### 2.4 API

| Méthode et chemin | Accès | Réponse |
| --- | --- | --- |
| `POST /api/v1/sites/{site_id}/agent/enrollments` `{ttl_min?}` | admin, cookie, même origine | 201 `{id, code, expires_at, server_url, image, commands: {docker_run, docker_login?}, files: {agent_env}}` : code remis une seule fois |
| `GET /api/v1/sites/{site_id}/agent/enrollments` | admin | `[{id, channel, created_at, expires_at, state: pending\|consumed\|expired\|revoked, consumed_ip, consumed_hostname, agent_id}]` sans code |
| `DELETE /api/v1/sites/{site_id}/agent/enrollments/{id}` | admin, même origine | 204 ; 409 `enroll.consumed` |
| `POST /api/v1/agent/enroll` (`EnrollRequest`) | public : ni cookie ni Bearer, requête avec `Origin` refusée (403), limitée par IP | 200 `EnrollResponse` ; 401 `enroll.invalid` ; 410 `enroll.expired` ; 409 `enroll.consumed` ; 429 `Retry-After` |
| `GET /api/v1/agent/local` | admin | `{available, detected: LocalPresence\|null, site_id\|null}` ; `detected` nul si présence absente, invalide ou vieille de plus de 30 s |
| `POST /api/v1/sites/{site_id}/agent/local` | admin, même origine | 202 `{enrollment_id, expires_at}` ; 409 `agent.local.unavailable` (canal non configuré) ou `agent.local.not_detected` |

`server_url` = `VDS_PUBLIC_ORIGIN` + `VDS_BASE_PATH` ; si `VDS_PUBLIC_ORIGIN` est vide, l'endpoint distant répond 409 `agent.public_origin_missing` (l'admin explique la variable à définir ; pas de déduction depuis l'en-tête `Host`). `image` = `VDS_AGENT_IMAGE` (défaut `ghcr.io/<owner>/vds-agent:<version serveur>`). Image privée : si `VDS_AGENT_PULL_USER` et `VDS_AGENT_PULL_TOKEN` (secret, portée `read:packages` seule) sont définis, `commands.docker_login` contient la ligne `docker login ghcr.io` à coller avant ; sinon l'admin indique que la machine doit déjà être authentifiée.

Consommation (`/agent/enroll`), dans une transaction avec `SELECT … FOR UPDATE` sur l'inscription :
1. hachage introuvable → 401 (compte un échec pour l'IP) ; expirée → 410 ; révoquée → 401 ; consommée → 409 ;
2. si `previous_token` désigne un `site_agent` valide d'un **autre** site, ce jeton est révoqué (session fermée par le code de révocation existant) ;
3. le site a déjà un `site_agent` → rotation du jeton (ancienne session fermée en 4411 `token_rotated`, existant) ; sinon création ;
4. `consumed_*`, `agent_id` remplis ; audit ; `agent.enrolled` dans l'outbox ; commit ; réponse.

Ajout au contrôle de même origine : `agent/enroll` est exempté comme `agent/*` (il n'accepte aucun cookie), mais une requête qui porte `Origin` est refusée, comme `/ws/agent`.

### 2.5 Canal local (serveur)

Réglage noyau `VDS_LOCAL_AGENT_DIR` (absent par défaut ⇒ canal indisponible). Écriture de `enroll.json` : fichier temporaire `0600` dans le même dossier, `fsync`, `rename` atomique ; remplace un dépôt précédent. Lecture de `presence.json` : `O_NOFOLLOW`, taille ≤ 4 Kio, validée par `LocalPresence`, traitée comme donnée non fiable (affichage seul, jamais d'autorisation). Le serveur ne lit rien d'autre dans ce dossier.

### 2.6 Événements (payload Pydantic `@1`)

| Nom | Payload | Fiabilité |
| --- | --- | --- |
| `agent.enrollment_created@1` | `enrollment_id, site_id, channel, expires_at, created_by` | bus simple |
| `agent.enrollment_revoked@1` | `enrollment_id, site_id, reason: admin\|superseded` | bus simple |
| `agent.enrolled@1` | `agent_id, site_id, enrollment_id, channel, hostname, platform, agent_version, replaced: bool, moved_from_site_id: UUID\|None` | **critique, outbox** |

`agent.connected` (existant) confirme ensuite la session. Ajoutés à `CORE_EVENTS_1_4` / `CRITICAL_EVENTS_1_4`.

Audit : `agent.enrollment.created`, `agent.enrollment.revoked`, `agent.enrollment.consumed` (acteur = l'agent, IP, hostname), `agent.local.activated`.

### 2.7 Agent (runtime, hors contrat de module)

Ordre de résolution des identifiants au démarrage :
1. `VDS_AGENT_TOKEN` (ou `_FILE`) : rétrocompatible, prioritaire, le canal local est alors ignoré ;
2. jeton rangé dans l'état chiffré (`Store`, clé `core/agent_token`) ;
3. `VDS_ENROLL_CODE` (ou `_FILE`) : échange, rangement, puis oubli ; ignoré (journal `agent.enroll_code_ignored`) si un jeton existe déjà ;
4. `VDS_AGENT_LOCAL_DIR` défini : sommeil, surveillance de `enroll.json` toutes les 2 s ;
5. sinon : erreur `agent.credentials_missing`, sortie 2.

En canal local, l'agent surveille le dépôt **même actif** : un nouveau `enroll.json` le réenrôle (avec `previous_token`) sur le site choisi, c'est le « déplacer l'agent local ». Révocation reçue (jeton refusé) : canal local ⇒ effacement du jeton et retour au sommeil ; sinon arrêt comme aujourd'hui. 4411 inchangé (arrêt sans effacement). `enroll.json` est supprimé dès lu, avant l'appel réseau. Échec réseau ou 5xx : 3 tentatives espacées, puis état `error` dans la présence ; 401/409/410 : abandon immédiat, état `error` avec le code.

## 3. Sécurité

| Risque | Parade |
| --- | --- |
| Code fuité (capture, historique, `docker inspect`) | 128 bits, usage unique, 60 min distant / 10 min local, lié à un site, révocable ; consommé il ne vaut plus rien ; seul le sha256 est stocké |
| Force brute sur `/agent/enroll` (exposé à Internet en hébergé) | espace de 128 bits ; limiteur par IP (clé /64 en IPv6, existant) : 10 échecs / 15 min ⇒ 429 ; réponse 401 identique pour code inconnu ou révoqué ; aucun identifiant de site dans l'erreur |
| Tiers qui consomme le code avant l'exploitant | l'admin voit `consumed_ip` + hostname de l'agent arrivé ; « Ce n'est pas mon agent » révoque l'agent et crée un nouveau code |
| Jeton d'agent exposé | jamais affiché ni mis dans une commande ; remis une fois dans la réponse d'enrôlement en TLS (ou boucle locale), chiffré dans l'état de l'agent |
| Volume partagé | monté seulement dans `server` et `agent` ; écrire dedans suppose déjà root sur l'hôte ; présence lue comme donnée non fiable, bornée, sans lien symbolique |
| Clair en local | accepté seulement vers loopback ; toute autre URL `http` exige toujours `VDS_AGENT_INSECURE_DEV=1` vers une IP privée |
| Permissions | enrôlement, révocation, canal local : rôle **admin** seul, contrôle de même origine ; aucune permission de module ajoutée (aucun module n'intervient) |
| Identifiants de registre privés | `VDS_AGENT_PULL_TOKEN` en `read:packages` seul, `x-vds-secret`, visible des seuls admins dans la commande générée, révocable côté GitHub |

## 4. Tâches

Chaque tâche serveur, agent et admin est testée aux deux `VDS_BASE_PATH` (`/` et `/espace/affichage`).

| Id | Agent | Contenu | Critère de fin vérifiable |
| --- | --- | --- | --- |
| E1 | `builder` | Contrat : `CORE_API_VERSION = "1.4.0"`, `vds_sdk.agent.enroll` (§2.3), payloads `agent.enrollment_created|revoked`, `agent.enrolled` critique, `CORE_EVENTS_1_4`, note de compatibilité | `test_compat_14.py` vert : modules 1.0–1.3 chargent inchangés ; motif de code rejette un code mal formé ; `LocalPresence` > 4 Kio refusée |
| E2 | `builder` | Serveur : migration `0008`, service d'enrôlement, endpoints §2.4 (hors local), limiteur, rotation 4411, `previous_token`, audit, événements, purge | pytest aux deux bases : rejoué 409, expiré 410, inconnu 401, 11ᵉ échec 429, éditeur 403, `Origin` présent 403, création révoque la précédente, ancienne session fermée en 4411, aucun code en clair en base ni dans `caplog`, `downgrade` puis `upgrade` OK |
| E3 | `builder` | Serveur, canal local : réglage `VDS_LOCAL_AGENT_DIR`, écriture atomique `0600`, lecture de présence bornée, `GET /agent/local`, `POST /sites/{id}/agent/local` | pytest (dossier temporaire) : réglage absent ⇒ `available:false` et 409 `agent.local.unavailable` ; présence de 31 s ⇒ `not_detected` ; lien symbolique et fichier de 5 Kio ignorés ; sha256 du code déposé = `code_sha256` en base |
| E4 | `builder` | Agent : ordre §2.7, enrôlement par code, jeton chiffré dans `Store`, sommeil et surveillance du dépôt, présence, réenrôlement, révocation ⇒ sommeil, clair autorisé vers loopback seul | pytest `apps/site-agent` : code échangé puis oublié ; redémarrage hors ligne réutilise le jeton rangé ; `enroll.json` supprimé avant l'appel ; révocation ⇒ jeton effacé + `dormant` ; `http://192.168.x.x` sans drapeau refusé, `http://127.0.0.1` accepté |
| E5 | `grunt` puis `builder` | Livraison : service `agent` + volume `local-agent` dans `docker/compose.yaml`, `docker/agent.Dockerfile` si absent, UID/GID communs serveur/agent sur le volume, image agent multi-arch privée dans `images.yml`, `VDS_AGENT_IMAGE`, `deploy/dokploy/README.md` (pas d'agent local en hébergé), `docker/agent.env` d'exemple sans jeton | `docker compose config` valide avec `VDS_BASE_PATH=/` et `/espace/affichage` ; `docker compose up` sur une machine vierge ⇒ agent en `dormant` visible par `GET /agent/local` sans autre commande |
| E6 | `builder` (skills `vds-ui-charte`, `vds-i18n`) | Admin, panneau Site › Agent : « Ce serveur » (si `available`, sinon masqué), « Une autre machine » (commande à copier, `agent.env` à télécharger, ligne de login si fournie), attente en direct (sondage 3 s), « Ce n'est pas mon agent », inscriptions en attente révocables, erreurs actionnables | vitest + Playwright (serveur simulé) 2 bases × 2 thèmes, FR/EN : aucun jeton `vds_agt_` dans le DOM, bouton « Ce serveur » absent quand `available:false` |
| E7 | `qa-tests` | E2E réel (extension de `make e2e-agent`) : canal local (dossier partagé) et distant (code en variable) | aux deux bases : clic d'activation ⇒ agent en ligne en moins de 15 s sans commande ; code rejoué refusé ; déplacement de l'agent local vers un autre site ⇒ ancien agent révoqué, nouveau en ligne |
| E8 | `revue-securite` | Enrôlement public, limiteur, volume partagé, clair loopback, identifiants de registre | aucun point bloquant |

Hors lot (suivant) : `discovery.lan`, commande `discovery.scan`, permission `network.discover`, adresse par MAC (D4, D5) ; assistant de mise en service (D6) et refonte de navigation (lot 3 de la refonte) ; CLI `enroll-code`.

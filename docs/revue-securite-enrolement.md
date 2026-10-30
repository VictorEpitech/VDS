# Revue de sécurité — Enrôlement d'agent et agent local (E8)

Date : 30 octobre 2026. Périmètre : commits `b1ab5d4..94c6391` (eaf6b5d, cbe04b6, 33d0124, 01f5e21, 490f543, 94c6391), spécification `docs/architecture-enrolement.md`. Revue en lecture seule ; l'état des correctifs est en fin de document (§ Suites).

Bilan : **0 bloquant, 5 importants, 9 mineurs**. Verdict : **non bloquant pour le lot**, à condition de traiter I1 à I3 avant toute installation chez un client et I4 avant de renseigner `VDS_AGENT_PULL_TOKEN`.

## Points vérifiés conformes

- Code `vds_enr_` : 128 bits (`secrets.token_bytes(16)`), seul le sha256 est stocké (`code_sha256` unique), recherche par hachage exact (pas d'énumération par préfixe ni de fuite de temps exploitable), motif strict côté Pydantic (`extra="forbid"`).
- Usage unique : `SELECT … FOR UPDATE` sur l'inscription, contrôles révoqué/expiré/consommé avant toute écriture, `IntegrityError` remontée en 409 sans consommation partielle (`service.py:267-345`).
- Code et jeton absents des audits, événements, listes (`EnrollmentOut` sans code) et journaux serveur et agent (codes i18n seulement) ; `Cache-Control: private, no-store` global (`middleware/headers.py:9`).
- `/agent/enroll` : exemption de même origine sur le chemin exact, refus de tout `Origin` avant le limiteur (`api/v1/enrollment.py:80-81`) ; aucun cookie lu. Routes admin : `require("admin")` partout, écritures sous `/sites/…` soumises au contrôle de même origine.
- `X-Forwarded-For` jamais lu directement (`security/client_ip.py`) ; uvicorn ne le croit que depuis `FORWARDED_ALLOW_IPS` (défaut `127.0.0.1`), un seul worker.
- Canal local côté serveur : temporaire aléatoire `O_EXCL|O_NOFOLLOW` 0600, `fsync`, `os.replace`, `fsync` du dossier ; présence lue `O_NOFOLLOW|O_NONBLOCK`, `S_ISREG`, ≤ 4 Kio, validée, fraîcheur 30 s, affichage seul.
- Agent : `follow_redirects=False`, `verify=ssl_context()` ; clair accepté seulement vers une IP littérale de boucle (`localhost`, `127.0.0.1.nip.io`, `http://127.0.0.1@hôte` refusés) ; `enroll.json` lu `O_NOFOLLOW`, borné, supprimé avant l'appel réseau ; UID 10001 non root dans les deux images.
- Génération de commande : `shlex.quote` sur toutes les valeurs interpolées.
- Aucune dépendance ajoutée ni lockfile modifié dans la plage ; pas de `.env` suivi.

## Bloquant

Aucun.

## Important

### I1. Jeton d'agent en clair sur la boucle locale : capturable par n'importe quel utilisateur local de l'hôte
- `apps/site-agent/src/vds_agent/settings.py:43` et `:117` ; `docker/compose.yaml:48` (port publié par `docker-proxy`).
- Scénario : l'agent local joint `http://127.0.0.1:8000` sans authentifier le serveur. Pendant un redémarrage ou une mise à jour du conteneur `server`, `docker-proxy` libère le port 8000 (> 1024) ; un utilisateur local non privilégié de l'hôte (compte de service, autre application compromise) écoute sur `127.0.0.1:8000`. L'agent, qui se reconnecte en boucle, lui envoie son `Authorization: Bearer vds_agt_…` (et, s'il enrôle à ce moment, le code). L'attaquant rejoue ensuite le jeton contre le vrai serveur et se fait passer pour l'agent du site (événements d'alimentation, réception des commandes et des secrets de pilotes).
- Correctif proposé : authentifier le serveur sur la boucle. Option simple : le serveur génère au démarrage un certificat auto-signé pour `127.0.0.1` (ou un port loopback dédié en TLS) et dépose son empreinte SHA-256 dans le volume partagé (écriture root ou UID 10001 seulement) ; l'agent l'épingle. Alternative minimale : à défaut d'épinglage, documenter le risque et exiger `VDS_LOCAL_AGENT_SERVER_URL` en `https` sur les hôtes multi-utilisateurs.

### I2. Limiteur d'échecs : une seule clé pour tous les clients derrière un proxy de l'hôte, verrouillage de l'enrôlement par un tiers
- `apps/server/src/vds_server/security/client_ip.py:13-14`, `docker/compose.yaml:43`, `apps/server/src/vds_server/core/runtime.py` (`FailureLimiter(limit=10, window_s=900)`).
- Scénario : installation autonome derrière un proxy TLS sur l'hôte (Caddy, nginx) qui joint `127.0.0.1:8000`. Le serveur voit toutes les requêtes depuis la passerelle Docker (`172.x.0.1`), qui n'est pas dans `FORWARDED_ALLOW_IPS=127.0.0.1` : l'en-tête du proxy est ignoré et tous les clients partagent la même clé. Un anonyme envoie 10 codes bien formés toutes les 15 min : l'agent légitime distant **et** l'agent local (qui arrive lui aussi par la passerelle) reçoivent 429. Déni de service de l'enrôlement pour 10 requêtes.
- Correctif proposé : (a) documenter et valider au démarrage la cohérence `FORWARDED_ALLOW_IPS` / proxy (journal d'avertissement si toutes les requêtes viennent d'une IP de passerelle) ; (b) donner à l'agent local un chemin distinct, par exemple un défi lié au dépôt (`enrollment_id` présent dans `enroll.json` et exigé avec le code pour le canal `local`), et compter les échecs du canal `local` à part ; (c) ajouter un compteur global d'échecs avec événement d'audit au-delà d'un seuil, pour que l'admin voie l'attaque.

### I3. Limiteur contournable par concurrence et absence de plafond de requêtes : force de frappe illimitée sur la base
- `apps/server/src/vds_server/api/v1/enrollment.py:82-89`, `apps/server/src/vds_server/player/ratelimit.py:51-63`.
- Scénario : `check()` est appelé avant `await s.consume(...)` et `fail()` après. Un client envoie 2 000 requêtes simultanées : toutes passent `check()` avant que le premier échec soit compté. Chaque requête ouvre une transaction avec `SELECT … FOR UPDATE` : épuisement du pool de connexions PostgreSQL, toute l'API (admin, players, agents) répond en erreur. En mode hébergé, l'endpoint est public sur Internet. La force brute du code reste infaisable (128 bits) : le risque est la disponibilité.
- Correctif proposé : réserver la tentative avant l'appel (compter un « en vol » dans `check`, le retirer en cas de succès) ou passer à un `RateLimiter` par IP sur toutes les requêtes (ex. 20 / min) en plus du `FailureLimiter` ; plafonner la concurrence de la route (sémaphore `asyncio` de quelques unités) ; poser un `statement_timeout` court sur la transaction.

### I4. `VDS_AGENT_PULL_TOKEN` : secret de l'éditeur remis aux admins clients, portée réelle plus large que `read:packages` « seule »
- `apps/server/src/vds_server/enrollment/service.py:250-256`, `docker/compose.yaml` (variables `VDS_AGENT_PULL_*` du service `server`), `docs/architecture-enrolement.md` §3.
- Scénario : un PAT GitHub classique `read:packages` donne la lecture de **tous** les paquets privés visibles du compte (et de ses organisations), pas seulement `vds-agent`. Il est renvoyé en clair à chaque admin de chaque instance cliente (et aux admins VDS du Studio en mode hébergé), recopié dans `echo <jeton> | docker login` (historique du shell), puis stocké en base64 dans `~/.docker/config.json` de chaque machine distante, sans expiration. Un admin d'un client ou un poste de site compromis peut tirer l'image serveur et toute autre image privée du compte, et ce jeton n'est révocable que globalement (tous les clients cassent en même temps).
- Correctif proposé : compte machine GitHub dédié n'ayant accès en lecture qu'au paquet `vds-agent` (le PAT ne voit alors que lui), un jeton par client, expiration fixée ; dans la commande, préférer `docker login ghcr.io -u <user> --password-stdin` avec saisie interactive ou fichier plutôt que `echo` ; corriger la phrase « portée read:packages seule » dans la spec ; à terme, image agent publique (le code n'est pas un secret) ou registre proxy authentifié par jeton d'agent.

### I5. Service `agent` en réseau hôte sans durcissement, actif par défaut sur toutes les installations
- `docker/compose.yaml:64-79`, `docker/site-agent.Dockerfile`, commande générée `service.py:240-247`.
- Scénario : le conteneur dort mais tourne partout, dans l'espace réseau de l'hôte, avec les capacités Docker par défaut (dont `NET_RAW`, `NET_BIND_SERVICE`), système de fichiers inscriptible, sans `no-new-privileges`. Une faille dans l'agent ou dans un module d'agent (pilotes tiers chargés dans le même processus) donne : écoute et usurpation ARP sur le LAN de l'hôte (`NET_RAW`), accès à tous les services en écoute sur `127.0.0.1` de l'hôte (API Docker TCP éventuelle, consoles d'administration, base si publiée en local). La commande `docker run` distante reproduit le même profil.
- Correctif proposé : dans `compose.yaml` et dans la commande générée, `cap_drop: [ALL]` (Wake-on-LAN en UDP broadcast n'exige aucune capacité ; rajouter `NET_RAW` seulement quand la découverte ARP du lot suivant l'exige, et la documenter), `security_opt: [no-new-privileges:true]`, `read_only: true` + `tmpfs: /tmp`, `user: "10001:10001"` explicite, limites `mem_limit`/`pids_limit`.

## Mineur

### M1. « Chiffré » est trompeur : clé Fernet rangée à côté du jeton
- `apps/site-agent/src/vds_agent/store.py:25-36`, `docker/compose.yaml:64-79` (aucun `VDS_AGENT_STATE_KEY`).
- Scénario : `state.key` et `agent.db` sont dans le même volume `agent-data` ; quiconque lit le volume (sauvegarde de l'hôte, `docker cp`) récupère le jeton `vds_agt_`. Déjà connu (M3), mais la spec §3 présente ce chiffrement comme une parade.
- Correctif : documenter que la protection réelle est la permission du volume ; fournir `VDS_AGENT_STATE_KEY_FILE` via secret Compose hors volume dans `compose.yaml` et la commande générée, ou retirer la mention « chiffré » de §3.

### M2. Déplacer l'agent local vers un site qui a déjà un agent évince silencieusement l'agent existant
- `apps/server/src/vds_server/enrollment/service.py:288-316` (rotation sur le `site_agent` du site cible), `api/v1/enrollment.py:63-75`.
- Scénario : un admin clique « Ce serveur pilote les écrans » sur un site déjà équipé d'un agent distant ; l'agent local reprend l'identité (`agent_id`) et le jeton de l'agent distant est remplacé (4411), sans avertissement. Même effet quand un code distant fuité est consommé : le tiers hérite de l'identité de l'agent en place.
- Correctif : `POST …/agent/local` et la création de code répondent 409 `agent.site_has_agent` sauf confirmation explicite (`replace: true`), tracée dans l'audit.

### M3. « Ce n'est pas mon agent » ne crée pas de nouveau code et repose sur un hostname déclaratif
- `apps/admin/src/features/sites/EnrollPanel.tsx:149-157`, `packages/sdk-py/src/vds_sdk/agent/enroll.py:19,27`.
- Scénario : le hostname est choisi par l'agent ; un tiers qui consomme le code peut annoncer le hostname attendu. L'IP est le seul indice fiable. Après rejet, l'admin doit relancer à la main (écart à la spec §3 : « révoque l'agent et crée un nouveau code »).
- Correctif : mettre `consumed_ip` en avant et le libeller « IP vue par le serveur » ; enchaîner la création d'un nouveau code après révocation, ou corriger la spec.

### M4. `agent_version` et `platform` sans motif
- `packages/sdk-py/src/vds_sdk/agent/enroll.py:28-29`.
- Scénario : chaînes libres (≤ 32) propagées dans `agent.enrolled` (outbox, modules abonnés) et l'admin ; React échappe, mais un module ou un notifier qui les insère dans un courriel ou un message markdown hérite de données non fiables.
- Correctif : motifs `^[0-9A-Za-z.+_-]{1,32}$` et `^[a-z0-9]+/[a-z0-9_]+$`.

### M5. Course de lecture du dépôt côté agent
- `apps/site-agent/src/vds_agent/enroll.py:111-126`.
- Scénario : l'agent ouvre `enroll.json`, puis le supprime **par chemin**. Si le serveur remplace le fichier entre-temps (double clic, deux admins), l'agent consomme l'ancien code (déjà révoqué `superseded`, 401) et supprime le nouveau : l'activation échoue sans trace. Pas d'élévation, mais erreur difficile à diagnostiquer.
- Correctif : `os.rename(enroll.json, .enroll.<random>.taken)` atomique puis lecture et suppression de la copie renommée.

### M6. Limiteur : entrées créées par `check()` et purge en O(n) à chaque échec
- `apps/server/src/vds_server/player/ratelimit.py:45-63`.
- Scénario : `check()` crée une entrée par nouvelle clé (via `defaultdict`), la purge n'a lieu que dans `fail()` au-delà de 10 000 clés et parcourt tout le dictionnaire à chaque échec. Un attaquant disposant de nombreux /64 IPv6 maintient > 10 000 clés actives : chaque échec coûte un parcours complet (CPU sur l'unique worker).
- Correctif : `_trim` sans création (`self._fails.get`), purge périodique bornée (au plus toutes les N secondes), ou `cachetools.TTLCache` de taille fixe.

### M7. Proxy de confiance en mode hébergé défini comme un sous-réseau partagé
- `deploy/dokploy/README.md:23` (`FORWARDED_ALLOW_IPS` = sous-réseau Docker de Dokploy).
- Scénario : tout conteneur du réseau Dokploy (autres applications du Studio) peut joindre `vds` directement avec un `X-Forwarded-For` arbitraire : contournement du limiteur (I3) et `consumed_ip` falsifiée, ce qui affaiblit « Ce n'est pas mon agent ».
- Correctif : IP fixe de Traefik seule, ou réseau Docker dédié Traefik ↔ `vds`.

### M8. Image de la commande distante non qualifiée et non épinglée
- `apps/server/src/vds_server/enrollment/service.py:238`, `docker/compose.yaml:39,65`.
- Scénario : défaut `vds-agent:<version>` (au lieu de `ghcr.io/<owner>/vds-agent:<version>` annoncé en §2.4) ; tag mutable, pas de digest. Une étiquette repoussée sur le registre est tirée telle quelle par les sites.
- Correctif : défaut pleinement qualifié, et référence `@sha256:` produite par la CI et injectée dans `VDS_AGENT_IMAGE`.

### M9. Codes d'erreur distincts selon l'état d'un code valide
- `apps/server/src/vds_server/enrollment/service.py:276-281`.
- Scénario : 410 / 409 / `site.archived` 409 révèlent l'état d'un code à qui le détient déjà (ex. code récupéré dans un historique de shell : le tiers apprend qu'il est expiré plutôt que de réessayer). Impact faible, conforme à la spec.
- Correctif : accepter tel quel, ou répondre 401 uniforme et ne garder le détail que dans l'audit.

## Suites (30 octobre 2026)

Corrigés : I1, I2, I3, I5, plus gratuitement M6 (purge du limiteur). Reportés : I4 (hors périmètre demandé : décision éditeur sur un compte machine GitHub et des jetons par client), M1, M2, M3, M4, M5, M7, M8, M9 (mineurs non traités, aucun ne bloque).

### I1. Corrigé : le serveur prouve son identité avant tout envoi de secret
- Le serveur crée `proof.key` (32 octets aléatoires, 0600, sans écrasement : `link` atomique, `O_NOFOLLOW`) dans le volume `local-agent`. Seuls les deux conteneurs (UID 10001) et root l'atteignent.
- L'agent local (dossier partagé + serveur en clair) envoie un nonce frais à `GET /api/v1/agent/local/proof` et exige `HMAC-SHA256(clé, contexte || nonce)` avant : l'ouverture du WebSocket (jeton), toute requête HTTP de l'agent (hook httpx, preuve valable 5 s), la consommation de `enroll.json` (le dépôt reste en place tant que le serveur n'est pas prouvé). Échec fermé : clé absente, réponse invalide, serveur injoignable.
- Le canal distant (TLS vérifié) n'est pas concerné ; le secret ne circule jamais.
- Limite connue : un attaquant local qui occupe le port 8000 et relaie les requêtes vers le vrai serveur par une autre route (IP du conteneur sur le pont Docker) obtient une preuve valide, la preuve n'étant pas liée au flux TCP. Ce modèle ferme le scénario décrit (écouteur qui usurpe le serveur) mais pas un relais actif ; sur un hôte multi-utilisateurs non maîtrisé, utiliser `VDS_LOCAL_AGENT_SERVER_URL` en `https`.

### I2. Corrigé : seaux séparés, IP fiable seulement via le proxy déclaré
- L'agent local prouve la détention de `proof.key` par `X-VDS-Local-Auth` (HMAC du code) : ses requêtes comptent dans un seau `local-agent` distinct des IP. Un anonyme qui épuise le seau de la passerelle ne bloque plus l'agent local, et les échecs du canal local ne bloquent personne. Un en-tête forgé ne donne aucun traitement de faveur (traité comme anonyme).
- L'IP client reste celle du pair, réécrite par uvicorn depuis `X-Forwarded-For` uniquement si le pair figure dans `FORWARDED_ALLOW_IPS` (test avec un vrai uvicorn). Pour un proxy TLS installé sur l'hôte, la passerelle Docker (ex. `172.18.0.1`) doit figurer dans `FORWARDED_ALLOW_IPS`, sinon tous les clients distants partagent un seau : à renseigner dans le `.env` de l'installation.
- Reporté : avertissement de démarrage sur la cohérence proxy, compteur global d'échecs avec audit (aucune surface d'alerte en V1).

### I3. Corrigé : tentative réservée avant le travail, concurrence bornée
- `FailureLimiter.reserve` compte la tentative avant l'accès à la base (une rafale ne dépasse jamais la limite) ; `release` rend la place pour tout ce qui n'est pas un échec compté (succès, 422, panne). `check`/`fail` supprimés.
- Sémaphore de 8 requêtes d'échange simultanées par worker ; au-delà, 429 immédiat sans toucher la base.
- Reporté : `statement_timeout` sur la transaction (le plafond de concurrence et le verrou par ligne suffisent à protéger le pool).

### I5. Corrigé : agent durci
- `docker/compose.yaml` (service `agent`) et commande `docker run` générée : `user 10001:10001`, `cap_drop ALL`, `no-new-privileges`, système de fichiers en lecture seule avec `/tmp` en tmpfs (les volumes `agent-data` et `local-agent` restent inscriptibles), `mem_limit` 512m, `pids_limit` 256. Wake-on-LAN (UDP broadcast) n'exige aucune capacité ; la découverte ARP du lot suivant devra ajouter `NET_RAW` explicitement et le documenter.
- Le panneau d'admin affiche la commande fournie par le serveur : aucun changement côté React.

### Procédure manuelle (hôte réel)
1. `docker compose up -d`, activer l'agent local sur un site depuis l'admin : l'agent passe `dormant`, `enrolling`, `active`.
2. I1 : arrêter `server`, écouter `nc -l 127.0.0.1 8000` en utilisateur non privilégié ; relancer l'agent, vérifier qu'aucun en-tête `Authorization` n'arrive (journal `agent.server_unverified`).
3. I5 : `docker inspect vds-agent-1 --format '{{.HostConfig.CapDrop}} {{.HostConfig.ReadonlyRootfs}}'` doit afficher `[ALL] true`.

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
- [x] CI : build et publication des images (T10 : docker/*, ci.yml, images.yml, deploy/dokploy/README.md ; le serveur ne sert pas encore le player, placeholder embarqué dans /app/web/player)

### Lot 2 — Premier écran qui joue (jalon clé)
- [ ] Player web : appairage, cache (SW + IndexedDB), lecture hors ligne, watchdog
- [ ] App Android (TCL Google TV) en kiosque
- [ ] Modules content-type : image, vidéo, PDF, URL
- [ ] Playlists + programmation simple

### Lot 3 — Multi-sites et alimentation
- [ ] Agent de site : relais WebSocket sortant, cache média
- [ ] Pilotes : ADB, Samsung WS, WoL, CEC, écran noir + assistant de détection
- [ ] Planning d'alimentation + exceptions
- [ ] Supervision + notifier mail

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

### T7 — Authentification (lot 1)
Rôles V1 = `admin` / `editor` (pas de « lecteur » : absent des docs et du CHECK en base) · lecture du parc et de la config : éditeur ; écriture : admin · `Identity.role = None` → rôle conservé en base, `editor` à la création · le compte noyau d'un provider est créé via la façade `ctx.core.users.provision` réservée au kind `auth-provider` (intégrée au catalogue comme permission `users.provision`, voir décision ci-dessous) · routes de module montées à chaud via `ModuleMux` · migrations : branches `core` et `<id module>`, `alembic upgrade heads` (`python -m vds_server.migrate`).

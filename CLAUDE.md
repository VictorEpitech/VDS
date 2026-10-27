# VDS — Vtech Digital Signage

Système d'affichage dynamique maison, multi-sites. Développeur unique (Victor). Objectif : V1 rapide, architecture modulaire extensible.

**Deux modes, un seul code** :
- **Autonome** : une instance Docker par client (`docker compose up`).
- **Hébergé dans le Studio Allia** : application Dokploy `vds`, servie sous `allia-innovation.fr/espace/affichage`, connexion unique avec le Studio, charte Allia.

Le mode hébergé est un **préréglage de modules** (`auth.studio-allia` + `theme.allia` + `VDS_BASE_PATH`), jamais une branche du code.

Spécifications détaillées (à lire avant toute tâche de conception) :
- `docs/architecture.md` — noyau, modules, players, agent de site, alimentation, sécurité
- `docs/integration-studio-allia.md` — mode hébergé : connexion unique, rôles, routage Dokploy, ajouts côté Studio
- `docs/design.md` — principes UX, écrans, parcours, composants, états
- `docs/charte-graphique-v-tech.md` — charte graphique V-Tech (MD3)
- `docs/roadmap.md` — périmètre V1, lots, backlog, risques, questions ouvertes

## Règles non négociables

1. **Noyau minimal, tout le reste est un module.** Le noyau connaît : écrans, sites, groupes, utilisateurs, configuration, planification, registre de modules, bus d'événements. Tout comportement métier (pilote d'alimentation, type de contenu, notifier, auth…) est un module.
2. **Les modules ne s'appellent jamais directement.** Ils communiquent par le bus d'événements et l'API du noyau.
3. **Un seul format de module** (manifeste + schéma JSON de config), qu'il soit intégré ou installé à chaud. Voir le skill `vds-module`.
4. **Configuration héritée** organisation → site → groupe → écran. Chaque niveau ne stocke que ses surcharges ; l'API renvoie la valeur résolue **et sa provenance**.
5. **Le player est autonome** : il reçoit une programmation déjà résolue (journée + lendemain), la met en cache et la joue hors ligne, y compris les rallumages programmés.
6. **Kiosque verrouillé par défaut, déverrouillable par un admin** : les écrans sont accessibles au public. Aucune voie de sortie du player pour le public, aucune info technique affichée au public ; seul un admin peut déverrouiller (action tracée).
7. **L'agent de site n'ouvre aucun port entrant** : connexion sortante WebSocket TLS vers le serveur.
8. **Interface FR/EN** : aucun libellé en dur dans le code UI. Voir le skill `vds-i18n`.
9. **Charte V-Tech** pour toute UI : tokens MD3, pas d'ombres portées, police mono réservée aux données techniques. Voir le skill `vds-ui-charte`.
10. **Modules futurs = points d'accroche, pas de code.** Ne pas implémenter alertes, SSO générique (LDAP, M365, Google), diffusion conditionnelle, tactile, murs d'écrans, statistiques en V1 ; seulement préserver leur point d'extension.
11. **Aucune hypothèse sur l'hébergeur dans le noyau.** Tout ce qui dépend du mode passe par la configuration et les modules : `VDS_BASE_PATH` (toutes les URL, assets, API et WebSocket en tiennent compte), `VDS_AUTH_PROVIDER`, `VDS_THEME`. Tester chaque fonctionnalité avec `VDS_BASE_PATH=/` ET `VDS_BASE_PATH=/espace/affichage`.
12. **VDS ne touche jamais à la base du Studio.** En mode hébergé, la seule interaction est l'appel de vérification de session (`docs/integration-studio-allia.md`).
13. **Écritures sous cookie = contrôle de même origine** (Origin / Sec-Fetch-Site), dans les deux modes.

## Stack

| Partie | Techno |
| --- | --- |
| Serveur | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic, PostgreSQL 17 (aligné sur le Studio) |
| Agent de site | Python 3.12, même SDK de module que le serveur |
| Admin | React 18 + TypeScript, Vite, TanStack Query, React Router, i18next, thème MD3 V-Tech |
| Player | TypeScript, web standard (aucun framework lourd), Service Worker + IndexedDB pour le cache |
| Empaquetage player | Android (WebView, kiosque) · Tizen web app · Chromium kiosque sur Pi · PWA |
| Outillage | uv, ruff, mypy, pytest · pnpm, eslint, vitest, Playwright · Docker Compose |

Les choix marqués « proposé » restent à confirmer par l'agent `architecte` au lot 1.

## Arborescence cible (monorepo)

```
apps/
  server/        # FastAPI : noyau + API + modules serveur
  site-agent/    # agent de site : relais, pilotes d'alimentation, cache média
  admin/         # interface React
  player/        # player web unique
packaging/
  android/  tizen/  pi/
modules/         # modules intégrés, un dossier par module (même format que les tiers)
                 # dont : auth.local, auth.studio-allia, theme.vtech, theme.allia
packages/
  sdk-py/        # contrat de module Python (manifeste, bus, config)
  sdk-ts/        # contrat content-type côté player + types API générés
docker/          # compose autonome, images, scripts d'installation
deploy/dokploy/  # mode hébergé Studio : variables, routage, sauvegardes (documentation)
.github/workflows/ # build et publication des images (jamais de build sur le VPS)
docs/
```

## Commandes (à créer au lot 1, garder cette section à jour)

```
make dev        # docker compose up : postgres + server + admin + player en dev
make test       # pytest + vitest
make lint       # ruff + mypy + eslint + tsc
make e2e        # Playwright sur admin + player
make api-types  # regénère packages/sdk-ts depuis l'OpenAPI du serveur
```

## Façon de travailler

- Travailler **lot par lot** (`docs/roadmap.md`). Utiliser le skill `vds-lot` pour démarrer un lot.
- Avant de coder une fonctionnalité qui touche le noyau ou le contrat de module : demander l'avis de l'agent `architecte`.
- Toute fonctionnalité livrée a des tests ; l'agent `qa-tests` les écrit ou les vérifie.
- Avant de clore un lot : passer l'agent `revue-securite` (kiosque, auth, modules tiers, agent de site).
- Mettre à jour `docs/` quand une décision change. Ajouter les décisions dans `docs/roadmap.md` › Décisions.
- Commits courts et en français, préfixés par la zone : `server:`, `admin:`, `player:`, `agent:`, `module(<id>):`, `docs:`.

## Vocabulaire (identique code / UI)

| FR (UI) | EN (UI et code) |
| --- | --- |
| Écran | screen |
| Site | site |
| Groupe | group |
| Mise en page | layout |
| Playlist | playlist |
| Programmation | schedule |
| Forçage | override |
| Hors service prévu | scheduled_off |
| Pilote d'alimentation | power_driver |
| Agent de site | site_agent |

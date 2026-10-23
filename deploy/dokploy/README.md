# Déploiement Dokploy (mode hébergé Studio Allia)

Documentation uniquement, aucun secret. Détail : `docs/integration-studio-allia.md`.

## Application `vds`

- Source : image **GHCR** `ghcr.io/<owner>/vds-server:<tag>` construite par `.github/workflows/images.yml`. Aucun build sur le VPS ; déployer un tag (`vX.Y.Z`) ou `sha-<court>`.
- Port interne : `8000`. Migrations Alembic lancées au démarrage du conteneur.
- Service `vds-db` : `postgres:17`, séparé de `espace-db`. VDS ne touche jamais à la base du Studio.
- Volume `vds-media` monté sur `/data/media`.

## Variables (Dokploy › Environment)

| Variable | Valeur |
| --- | --- |
| `VDS_BASE_PATH` | `/espace/affichage` |
| `VDS_AUTH_PROVIDER` | `auth.studio-allia` |
| `VDS_THEME` | `theme.allia` |
| `VDS_DEFAULT_LOCALE` | `fr` |
| `VDS_PUBLIC_ORIGIN` | `https://allia-innovation.fr` |
| `VDS_DATABASE_URL` | `postgresql+asyncpg://<user>:<mot de passe>@vds-db:5432/vds` |
| `VDS_SECRET_KEY` | secret généré (Dokploy) |
| `FORWARDED_ALLOW_IPS` | IP ou CIDR du proxy de confiance (Traefik) autorisé à fixer `X-Forwarded-*`, ex. le sous-réseau du réseau Docker de Dokploy. Défaut `127.0.0.1` : aucun en-tête de proxy n'est cru. Ne jamais mettre `*` |
| `VDS_BOOTSTRAP_TOKEN` | mode autonome seulement (`auth.local`) : jeton à usage unique du premier admin ; absent, il est généré et écrit une fois dans les journaux tant qu'aucun admin n'existe |
| `STUDIO_SESSION_URL` | `http://<service-website-interne>/api/espace/vds/session` |
| `STUDIO_SHARED_SECRET` | secret Dokploy, identique à `VDS_STUDIO_SECRET` côté `website` |

Les valeurs secrètes ne sont jamais écrites dans le dépôt.

## Routage (Traefik)

- Règle : `Host(allia-innovation.fr) && PathPrefix(/espace/affichage)`, priorité supérieure à la route `website`.
- Le préfixe n'est **pas** retiré (pas de `stripPrefix`) : VDS sert tout sous `VDS_BASE_PATH` (admin, API, WebSocket `/ws/*`, médias).
- WebSocket : aucun middleware ne doit couper les connexions longues. Supervision : `<base>/health` (Uptime Kuma).

## Sauvegardes

- `vds-db` : `pg_dump` planifié vers MinIO, rétention à définir.
- `vds-media` : sauvegarde du volume vers MinIO.
- Restauration testée avant toute mise à jour de schéma ; migrations additives et réversibles.

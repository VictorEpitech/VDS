---
name: vds-studio-allia
description: Travailler sur le mode hébergé de VDS dans le Studio Allia - connexion unique (auth.studio-allia), correspondance des rôles Studio, chemin /espace/affichage, déploiement Dokploy, ajouts à faire dans le dépôt du Studio. Utiliser dès qu'une tâche mentionne le Studio, Allia, Dokploy, /espace/affichage ou le double mode autonome/hébergé.
---

# VDS hébergé dans le Studio Allia

Référence : `docs/integration-studio-allia.md`. Lis-la en entier avant de toucher à ce mode.

## Les 5 règles

1. **Préréglage, pas branche** : le mode hébergé = `VDS_BASE_PATH=/espace/affichage` + `VDS_AUTH_PROVIDER=auth.studio-allia` + `VDS_THEME=theme.allia`. Aucun `if studio` dans le noyau ou l'admin. Si tu en as besoin, il manque un point d'extension : demande l'agent `architecte`.
2. **Le Studio reste seul juge de la session** : `auth.studio-allia` transmet le cookie `allia_espace` à `GET /api/espace/vds/session` (réseau interne, en-tête `x-vds-secret`) et utilise la réponse. VDS ne décode, ne stocke et ne journalise jamais ce cookie.
3. **Jamais la base du Studio** : aucune connexion à `espace-db`, aucune donnée du Studio copiée dans VDS hormis `{id, nom, email, role}` de l'utilisateur connecté.
4. **Rôles** : président / direction → admin ; membre → éditeur ; financeur, compte inactif, mot de passe provisoire → aucun accès.
5. **Règles du Studio** sur tout `/espace/affichage` : `noindex` + `X-Robots-Tag`, `Cache-Control: private, no-store`, `X-Frame-Options: DENY`, contrôle de même origine sur les écritures, 375 px, ton Allia, logos officiels.

## Module `auth.studio-allia`

```python
class StudioAlliaAuth(AuthProvider):
    async def authenticate(self, request) -> Principal | None:
        # 1. lire le cookie allia_espace ; absent -> None (redirection /espace/connexion?retour=<url>)
        # 2. cache 60 s par empreinte SHA-256 du cookie
        # 3. GET {STUDIO_SESSION_URL} avec Cookie + x-vds-secret, délai 2 s
        # 4. 200 -> mapper le rôle, créer/mettre à jour le profil VDS (référence = id Studio)
        # 5. 401 -> None ; erreur réseau -> 503 lisible, jamais d'accès accordé par défaut
    def login_url(self, return_to: str) -> str: ...   # /espace/connexion?retour=...
    def logout_url(self) -> str: ...                  # déconnexion du Studio
```

Tests obligatoires : chaque rôle Studio, 401, délai dépassé, secret erroné, cookie absent, financeur refusé, cache expiré.

## Ajouts côté Studio (dépôt du Studio, guide « 04 — Ajouter un module »)

- `app/api/espace/vds/session/route.ts` (GET, secret, `sessionOuNull()`, `{id, nom, email, role}`, no-store)
- Entrée « Affichage » dans `LIENS` de `components/espace/Nav.tsx`, rôles président / direction / membre, navigation complète (`<a>`)
- `VDS_STUDIO_SECRET` dans Dokploy → `website`
- Ligne « Affichage » dans `docs/05-modules-existants.md`

Ces changements se font dans un autre dépôt : prépare-les sous forme de patch ou de PR séparée, ne les mélange pas au code VDS.

## Déploiement Dokploy

Application `vds`, domaine `allia-innovation.fr`, chemin `/espace/affichage` (préfixe conservé), base `vds-db` postgres:17, volume `vds-media`, sauvegardes vers MinIO VPS 2, image construite en CI. Documenter la configuration dans `deploy/dokploy/README.md` (sans aucun secret).

## Vérification de fin de tâche

- `make test` avec `VDS_BASE_PATH=/` puis `VDS_BASE_PATH=/espace/affichage`
- Parcours e2e : connexion Studio simulée (faux service de session) → admin VDS → publication → déconnexion
- Agent `revue-securite` si la tâche touche à la session ou aux en-têtes

# Droits — périmètre par site et groupe (lot J)

Statut : décision d'architecture du 2026-11-03 (architecte), questions Q1-Q3 tranchées par Victor le 2026-11-03 (§11). Aucun code. Fondement : décision Victor **D2** (`docs/refonte/xibo-ecarts.md` §5) : périmètre par site ou par groupe, **jamais d'ACL par objet**.

Contexte : `ContentScope` (`apps/server/src/vds_server/diffusion/scope.py`) n'a que `OrganizationScope` (tout vrai). `PlanningService`, `ScheduleService`, `OverrideService`, `LayoutService` reçoivent déjà `actor` + `scope`. Le lot J remplace l'implémentation sans changer les appelants, puis étend la couverture aux écrans, sites, groupes, supervision et aux écrans de gestion des utilisateurs.

## 1. Décisions

| # | Décision |
| --- | --- |
| J-D1 | **Deux rôles inchangés** (`admin`, `editor`). `admin` = toute l'organisation, périmètre ignoré. `editor` = union des **nœuds de périmètre** qui lui sont attribués. Aucun nouveau rôle en V1 (voir §9, admin de site). |
| J-D2 | **Nœud de périmètre** = `organization` \| `site` \| `group` — la même hiérarchie que la configuration héritée. Pas de nœud `screen` (pas d'ACL par objet). |
| J-D3 | **Les contenus (médias, playlists, mises en page) ont un propriétaire-nœud** : `owner_site_id` / `owner_group_id`, tous deux NULL = organisation (« partagé »). C'est une partition, pas une ACL. |
| J-D4 | **Règle de lecture** : un objet est visible si son nœud est **ancêtre, égal ou descendant** d'un nœud du périmètre. **Règle d'écriture** : nœud **égal ou descendant** uniquement. Les règles de diffusion d'un ancêtre (planning organisation, forçage site) restent visibles en lecture seule : elles touchent mes écrans. |
| J-D5 | **Invariant de référence** (vaut pour tous, admin compris) : un objet ne référence que du contenu dont le propriétaire est **égal ou ancêtre** de son propre nœud (playlist → média, mise en page → playlist/média, planning/forçage → contenu vs nœud cible ; pour une cible écran, le nœud est son groupe, sinon son site). Erreur `scope.reference_out_of_scope` (422). Conséquence : jamais de nom hors périmètre dans ce qu'un éditeur peut lire. |
| J-D6 | **Hors périmètre = 404** (pas de révélation d'existence) ; visible mais non modifiable = **403 `scope.forbidden`**. |
| J-D7 | **Le périmètre est toujours stocké dans VDS**, dans les deux modes. Le provider donne au plus le rôle (`Identity.role`) ; jamais le périmètre en V1. Aucun `if studio` : la seule différence est `user_account.role_source`, alimentée par l'upsert générique. |
| J-D8 | **Rétrocompatibilité** : la migration attribue `organization` à chaque éditeur existant et tous les contenus existants restent « partagés » : comportement V1 strictement identique après migration. |
| J-D9 | Périmètre résolu **une fois par requête** dans `AuthPipeline.resolve` et porté par `Principal` (une requête SQL, pas de cache inter-requêtes : un retrait est effectif à la requête suivante). |
| J-D11 | **Contenu partagé** (propriétaire NULL/NULL) : créable et modifiable uniquement par un `admin` ou un éditeur dont le périmètre contient le nœud `organization` (`unrestricted`). Un éditeur limité à des sites/groupes le lit (J-D4) mais ne le crée ni ne le modifie : sinon il changerait ce qui est visible d'autres périmètres. **Règle à confirmer par Victor.** |
| J-D12 | **Nouvel éditeur : aucun périmètre** (moindre privilège). Seuls les éditeurs existants reçoivent `organization` à la migration (J-D8). Pas d'admin de site en V1 (§9). |
| J-D10 | Les **acteurs module/système** (`created_by_module`, tâches planifiées, agents, players) ne sont pas soumis au périmètre : il ne s'applique qu'aux principaux humains de l'API admin. Players et agents sont inchangés (aucun message WebSocket modifié). |

Option écartée : propriétaire au seul niveau site (sans `owner_group_id`). Plus simple, mais un éditeur de groupe modifierait des playlists diffusées sur d'autres groupes du même site (élévation de fait). Coût de l'option retenue : une colonne et une FK composite de plus par table de contenu, et la règle J-D5 à vérifier à chaque écriture de référence.

## 2. Modèle de données (migration `0018_perimetre`, additive)

```sql
CREATE TABLE user_scope (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid NOT NULL REFERENCES user_account(id) ON DELETE CASCADE,
  organization_id  uuid NULL REFERENCES organization(id) ON DELETE CASCADE,
  site_id          uuid NULL REFERENCES site(id) ON DELETE CASCADE,
  group_id         uuid NULL REFERENCES screen_group(id) ON DELETE CASCADE,
  scope_type       text GENERATED ALWAYS AS (CASE WHEN group_id IS NOT NULL THEN 'group'
                     WHEN site_id IS NOT NULL THEN 'site' ELSE 'organization' END) STORED,
  scope_id         uuid GENERATED ALWAYS AS (COALESCE(group_id, site_id, organization_id)) STORED,
  created_by       uuid NULL REFERENCES user_account(id) ON DELETE SET NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT one_scope CHECK (num_nonnulls(organization_id, site_id, group_id) = 1),
  CONSTRAINT uq_user_scope UNIQUE (user_id, scope_id)
);
CREATE INDEX ix_user_scope_user_id ON user_scope(user_id);

ALTER TABLE user_account ADD COLUMN role_source text NOT NULL DEFAULT 'vds'
  CONSTRAINT role_source CHECK (role_source IN ('vds','provider'));

-- pour media, playlist, layout (même motif) :
ALTER TABLE media ADD COLUMN owner_site_id uuid NULL REFERENCES site(id) ON DELETE RESTRICT;
ALTER TABLE media ADD COLUMN owner_group_id uuid NULL;
ALTER TABLE media ADD CONSTRAINT fk_media_owner_group
  FOREIGN KEY (owner_group_id, owner_site_id) REFERENCES screen_group(id, site_id)
  ON DELETE SET NULL (owner_group_id);           -- PG ≥ 15 : groupe supprimé → contenu remonte au site
ALTER TABLE media ADD CONSTRAINT owner CHECK (owner_group_id IS NULL OR owner_site_id IS NOT NULL);
CREATE INDEX ix_media_owner ON media(owner_site_id, owner_group_id);

-- rétrocompatibilité (J-D8)
INSERT INTO user_scope(user_id, organization_id)
  SELECT u.id, o.id FROM user_account u CROSS JOIN organization o WHERE u.role = 'editor';
```

Le motif `num_nonnulls = 1` + colonnes calculées reprend `config_override` (cascade native à la suppression d'un site/groupe). Plafond applicatif : 100 nœuds par utilisateur. Downgrade : suppression des colonnes et de la table (perte des périmètres, assumée).

`users.upsert_identity` : quand `identity.role` est non nul, positionne `role_source='provider'` (sinon inchangé). Un nouvel éditeur (premier passage d'un membre Studio, nouvel utilisateur local) est créé **sans aucun nœud** (décision Q1) : pas de clé de configuration, seule la migration 0018 attribue `organization`, et uniquement aux éditeurs déjà présents. Un éditeur sans périmètre voit `Perimeter.empty` (aucun contenu, 404 partout, état vide en UI) jusqu'à attribution par un admin.

## 3. Noyau : interfaces

```python
# vds_server/core/perimeter.py
NodeType = Literal["organization", "site", "group"]

@dataclass(frozen=True)
class Node:
    type: NodeType
    id: UUID

@dataclass(frozen=True)
class Perimeter:
    unrestricted: bool                 # admin, ou éditeur avec un nœud organization
    sites: frozenset[UUID]             # sites attribués directement
    groups: frozenset[UUID]            # groupes attribués directement
    sites_closure: frozenset[UUID]     # sites ∪ sites parents des groupes  (lecture : ancêtres)
    groups_closure: frozenset[UUID]    # groupes ∪ groupes des sites attribués (descendants)
    version: str                       # empreinte des nœuds, entre dans les ETag

    @property
    def empty(self) -> bool: ...

async def load_perimeter(session: AsyncSession, user_id: UUID, role: Role) -> Perimeter: ...
def node_of_owner(owner_site_id: UUID | None, owner_group_id: UUID | None) -> Node | None: ...  # None = organisation
async def covers(session: AsyncSession, ancestor: Node | None, node: Node | None) -> bool: ...  # J-D5
```

`Principal` gagne `perimeter: Perimeter` (champ ajouté, dataclass toujours gelée).

```python
# vds_server/diffusion/scope.py — signatures existantes conservées, ajouts en fin
Access = Literal["read", "write"]

class ContentScope(Protocol):
    def media_filter(self, principal: Principal) -> ColumnElement[bool]: ...      # lecture
    def playlist_filter(self, principal: Principal) -> ColumnElement[bool]: ...
    def layout_filter(self, principal: Principal) -> ColumnElement[bool]: ...
    def screen_filter(self, principal: Principal) -> ColumnElement[bool]: ...
    async def can_write_media(self, principal: Principal, media_id: UUID) -> bool: ...
    async def can_write_playlist(self, principal: Principal, playlist_id: UUID) -> bool: ...
    async def can_write_layout(self, principal: Principal, layout_id: UUID) -> bool: ...
    # lot J
    def owner_filter(self, principal: Principal, site_col: ColumnElement[UUID | None],
                     group_col: ColumnElement[UUID | None], access: Access) -> ColumnElement[bool]: ...
    def target_filter(self, principal: Principal, type_col: ColumnElement[str],
                      id_col: ColumnElement[UUID], access: Access) -> ColumnElement[bool]: ...
    def site_filter(self, principal: Principal) -> ColumnElement[bool]: ...       # sites_closure
    def group_filter(self, principal: Principal) -> ColumnElement[bool]: ...      # groups_closure
    def can_own(self, principal: Principal, owner: Node | None) -> bool: ...      # création / changement de propriétaire
    def default_owner(self, principal: Principal) -> Node | None | Literal["ask"]: ...
    async def can_target(self, principal: Principal, target_type: str, target_id: UUID) -> bool: ...

class OrganizationScope: ...   # conservé (tests, outils), tout vrai
class PerimeterScope: ...      # implémentation lot J, injectée par défaut
```

Sémantique SQL (éditeur non `unrestricted`) :

| Filtre | Lecture | Écriture |
| --- | --- | --- |
| `owner_filter` | `site IS NULL` ∨ (`group IS NULL` ∧ `site ∈ sites_closure`) ∨ `group ∈ groups_closure` | (`group IS NULL` ∧ `site ∈ sites`) ∨ `group ∈ groups_closure` |
| `target_filter` (schedules, playback_overrides) | `organization` ∨ (`site` ∧ id ∈ sites_closure) ∨ (`group` ∧ id ∈ groups_closure) ∨ (`screen` ∧ id ∈ écrans visibles) | (`site` ∧ id ∈ sites) ∨ (`group` ∧ id ∈ groups_closure) ∨ (`screen` ∧ id ∈ écrans visibles) |
| `screen_filter` | `site_id ∈ sites` ∨ `group_id ∈ groups` | idem (les gardes de rôle existantes restent) |

`media_filter` = `owner_filter(Media.owner_site_id, Media.owner_group_id, "read")`, idem playlist/layout. Les identifiants sont passés en littéraux `IN (...)` (≤ 100 nœuds). `Perimeter.empty` → `false()` partout.

Points d'application (liste fermée, chaque route y est classée, voir §6) : médiathèque (liste, détail, `usage`, `bulk`, tags et autocomplétion), playlists, mises en page (+ aperçu), planning (règles, aperçu, alternances, ETag), forçages (liste, aperçu plafonné, arrêt), écrans, sites et groupes (lecture), supervision / incidents / `fleet/summary` / onboarding (comptes), journal d'audit (filtré par cible), recherche transverse future. `usage` d'un contenu : lignes hors périmètre remplacées par un compteur `hidden_count`, jamais de nom. Suppression d'un contenu référencé hors périmètre : 409 `scope.used_out_of_scope` (compteur seul).

Création de contenu : `owner` optionnel dans la requête (`{"type": "site", "id": ...}` ou `null` = partagé). Absent → `default_owner` : le nœud unique du périmètre ; `null` (partagé) pour un `unrestricted` ; sinon 422 `scope.owner_required`. `owner: null` (partagé) par un éditeur non `unrestricted` → 403 `scope.shared_forbidden` ; `can_own(principal, None)` vaut vrai si et seulement si `Perimeter.unrestricted` (J-D11), et `owner_filter` en écriture n'inclut jamais `site IS NULL` pour un éditeur non `unrestricted` (déjà le cas dans le tableau ci-dessus). Un contenu partagé existant reste modifiable/supprimable seulement par `unrestricted` (403 `scope.forbidden` sinon, il est visible). Changement de propriétaire (`PATCH ... owner`) : `can_own` sur l'ancien et le nouveau nœud, puis J-D5 vérifié pour tous les référents (409 `scope.owner_narrower_than_references`, compteur seul).

## 4. Événements et contrat

- **Nouvel événement** `user.scope_changed`, payload v1 :
  ```python
  class UserScopeChangedV1(BaseModel):
      version: Literal[1] = 1
      user_id: UUID
      scopes: list[NodeRef]        # NodeRef = {type: NodeType, id: UUID}
      actor_id: UUID | None
  ```
- `user.role_changed` (aujourd'hui action d'audit seulement) devient aussi un événement, payload v1 `{user_id, from_role, to_role, source: "vds"|"provider"}`.
- Les payloads `media.*`, `playlist.*`, `layout.*` gagnent `owner: NodeRef | None` (champ additif optionnel).
- **Contrat** : catalogue d'événements étendu de façon additive → **bump mineur de vds-core** + note de compatibilité « additif, aucun module existant impacté ». `Identity` (SDK) inchangé. Aucune permission de module nouvelle.

## 5. API admin

| Méthode | Route | Rôle | Corps / réponse |
| --- | --- | --- | --- |
| GET | `/api/v1/me` | tous | ajoute `perimeter: {unrestricted, can_share, nodes: [{type, id, name}]}` (`can_share` = `unrestricted`) et `role_source` |
| GET | `/api/v1/scope-tree` | editor | sites → groupes visibles (`site_filter`/`group_filter`), drapeau `writable` par nœud et `can_share` (option « Partagé » proposée ou non) ; alimente sélecteurs de propriétaire et de cible |
| GET | `/api/v1/users` | admin | `UserPage` : `id, display_name, email, role, role_source, is_active, last_seen_at, scopes[]`, filtres `q`, `role`, `site_id` |
| PATCH | `/api/v1/users/{id}` | admin | `{role?, is_active?}` ; 409 `user.role_managed_by_provider` si `role_source='provider'` ; 409 `user.last_admin` ; 422 `user.self_demotion` |
| PUT | `/api/v1/users/{id}/scopes` | admin | `{scopes: [NodeRef]}` (remplacement, ≤ 100, `If-Match`) ; 422 `scope.node_unknown` ; audit `user.scope_changed` + événement |

La création de comptes reste du ressort de l'auth-provider (`auth.local` : invitation existante ou à venir ; Studio : création au premier passage). Écritures sous cookie : contrôle de même origine existant. Toutes les routes sous `VDS_BASE_PATH`.

## 6. Tests de non-fuite (obligatoires, aux deux `VDS_BASE_PATH`)

- **Jeu de données** : sites A, B ; groupes A1, A2 (A), B1 (B) ; contenus partagés, A, A1, A2, B ; plannings et forçages sur org, A, A1, écran de A2, B. Utilisateurs : `admin`, `ed_org`, `ed_A`, `ed_A1`, `ed_B`, `ed_none`.
- **Matrice paramétrée** utilisateur × route × objet : liste (présence/absence), détail (200/404), écriture (2xx/403/404), `usage` (`hidden_count`, aucun nom étranger dans le corps : recherche des noms des objets B dans tout le JSON renvoyé à `ed_A`).
- **Garde de classement** : test qui parcourt l'OpenAPI et échoue si une route `/api/v1/*` n'est pas déclarée dans `SCOPE_ROUTES` (`scoped`, `admin_only`, `scope_neutral`). Toute nouvelle route doit être classée.
- **Contenu partagé (J-D11)** : `ed_org` et `admin` créent/modifient/suppriment du partagé (2xx) ; `ed_A`, `ed_A1` : création avec `owner: null` → 403 `scope.shared_forbidden`, PATCH/DELETE d'un partagé → 403, lecture 200, PATCH d'un contenu A vers `owner: null` → 403 ; `ed_none` : liste vide, 404 partout. Aucun nom B dans les réponses.
- **Nouvel éditeur (Q1)** : création d'un éditeur (local et provider) → aucun `user_scope`, `/me.perimeter.nodes = []`, 404 sur tout contenu ; migration : éditeurs existants = `organization`.
- **Invariant J-D5** : playlist A1 → média A2 refusée ; planning site A → playlist A1 refusé ; changement de propriétaire vers un nœud plus étroit qu'un référent refusé.
- **Rétrocompatibilité** : après `0018`, toute la suite existante passe sans modification ; aller-retour `alembic upgrade/downgrade`.
- **Mode provider** : identité avec rôle → `role_source='provider'`, PATCH du rôle 409 ; périmètre modifiable ; aucune requête vers l'extérieur hors vérification de session.
- **ETag** : deux éditeurs de périmètres différents n'obtiennent jamais le même ETag de planning pour des vues différentes.

## 7. UI admin

- **Réglages › Utilisateurs** (admin) : liste (cartes à 375 px), fiche utilisateur : rôle (désactivé avec « Rôle géré par le service de connexion » si `role_source='provider'`), actif, **Périmètre** : « Toute l'organisation » ou arbre sites → groupes à cases (clavier : flèches + espace), résumé « 2 sites, 1 groupe ». État vide d'un éditeur : « Aucun périmètre — un administrateur doit vous en attribuer un ».
- **Contenus** : badge propriétaire sur cartes/lignes (« Partagé » ou nom du site/groupe), sélecteur « Propriétaire » à la création seulement s'il y a plus d'un choix (l'option « Partagé » n'existe que si `can_share`), actions d'écriture masquées (non désactivées) si non modifiable, filtre « Propriétaire ».
- **Diffusion / Écrans** : sélecteurs de cible alimentés par `scope-tree` (`writable`) ; règles d'ancêtres affichées « héritée de … », lecture seule.
- Menu : visibilité par rôle (`/me`), entrées Réglages réservées admin. i18n `users.*`, `scope.*`, FR/EN, aucune branche de thème.

## 8. Mode hébergé

Rien de spécifique : `auth.studio-allia` fournit le rôle (président/direction → admin, membre → éditeur) → `role_source='provider'`. Le périmètre d'un membre est attribué dans VDS par un admin VDS (président/direction). VDS n'écrit rien chez le Studio. Les routes, assets et `scope-tree` respectent `VDS_BASE_PATH`.

## 9. Points d'accroche (non implémentés)

- **Admin de site** (décision Q2 : hors V1, point d'accroche conservé ; gestion agents, alimentation, appairage limitée à ses sites) : nouveau rôle ajouté à `Role`/`RANK`, mêmes `Perimeter` et `site_filter` appliqués aux routes aujourd'hui `admin_only`. Rien à coder en V1 ; le classement `SCOPE_ROUTES` le rend possible.
- **Validation avant diffusion** (rôle « valideur ») : même périmètre, statut porté par la publication.
- **SSO générique** (LDAP, M365, Google) : un futur champ `Identity.scope_claims` + module de correspondance groupes → nœuds ; bump SDK à ce moment-là, pas en V1.
- **Lecteur** (lecture seule) : rôle supplémentaire, aucune donnée nouvelle.

## 10. Découpage

| Tâche | Agent | Contenu | Critère de fin |
| --- | --- | --- | --- |
| J0 | architecte | ce document | décision consignée, ligne roadmap |
| J1 | grunt + builder | migration `0018_perimetre`, modèles, `role_source` dans `upsert_identity`, `load_perimeter`, `Principal.perimeter` | `alembic upgrade/downgrade` OK ; suite existante verte sans modification ; éditeurs existants = `organization` |
| J2 | builder | `PerimeterScope` (filtres §3), `covers`, propriétaire sur médias/playlists/mises en page (création, PATCH, `usage` masqué, 404/403, 409) | tests unitaires des filtres + invariant J-D5 ; pytest aux deux `VDS_BASE_PATH` |
| J3 | builder | planning, forçages, écrans, sites/groupes, supervision, `fleet/summary`, onboarding, audit via `target_filter`/`screen_filter` ; ETag avec `perimeter.version` ; `SCOPE_ROUTES` | toutes les routes classées ; garde OpenAPI verte |
| J4 | builder (+ grunt DTO) | `/me`, `/scope-tree`, `/users`, `PATCH`, `PUT scopes`, événements `user.scope_changed` / `user.role_changed`, owner dans payloads, bump vds-core mineur + note de compatibilité, `make api-types` | tests API (dernier admin, rôle provider, If-Match, ≤ 100) |
| J5 | qa-tests | matrice de non-fuite §6 + jeu de données | matrice verte, aucun nom de B dans les réponses à `ed_A`, deux `VDS_BASE_PATH` |
| J6 | builder (admin) | Réglages › Utilisateurs, sélecteur de périmètre, badge et sélecteur de propriétaire, cibles via `scope-tree`, visibilité menu, i18n FR/EN | vitest + Playwright (admin, `ed_A`) à 375 px et desktop, aux deux `VDS_BASE_PATH` |
| J7 | revue-securite | revue périmètre, 404 vs 403, IDOR, ETag, mode provider | aucun constat bloquant |
| J8 | grunt | mise à jour `architecture.md` (renvoi vers ce document), `design.md` (écran Utilisateurs), `integration-studio-allia.md` (§ périmètre côté VDS) | docs cohérentes |

Ordre : J1 → (J2 ∥ J4) → J3 → J5 ∥ J6 → J7 → J8.

## 11. Questions pour Victor

Décisions de Victor, 2026-11-03.

- **Q1 (tranchée)** Périmètre par défaut d'un nouvel éditeur = **aucun**. Les éditeurs existants reçoivent `organization` à la migration 0018. Pas de clé `access.new_editor_scope`.
- **Q2 (tranchée)** **Pas d'admin de site en V1** : admin = organisation entière ; point d'accroche conservé (§9).
- **Q3 (tranchée, contraire à la recommandation initiale)** Un éditeur peut créer du **contenu partagé** (`owner_site_id` et `owner_group_id` NULL). Règle retenue (J-D11, **à confirmer par Victor**) : seul un éditeur dont le périmètre contient le nœud `organization` (ou un admin) crée/modifie du partagé ; un éditeur limité à site/groupe ne le peut pas, car il modifierait ce qui est visible par d'autres périmètres.

## Ligne à ajouter dans `docs/roadmap.md` › Décisions

`2026-11-03 — Lot J droits (Victor : nouvel éditeur sans périmètre, pas d'admin de site en V1, contenu partagé créable par un éditeur au périmètre organisation seulement — règle à confirmer) : périmètre éditeur = nœuds organisation/site/groupe (table user_scope), contenus partitionnés par owner_site_id/owner_group_id (NULL = partagé), lecture ancêtres+descendants / écriture nœud et descendants, invariant de référence vers un nœud égal ou ancêtre, hors périmètre = 404, périmètre toujours stocké dans VDS (role_source pour le rôle fourni par le provider), migration 0018 rétrocompatible, événement user.scope_changed (vds-core mineur). Détail : docs/architecture-droits.md.`

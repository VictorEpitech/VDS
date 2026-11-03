# VDS — Architecture : validation de publication (chantier B)

Décision du 2026-11-03 (architecte, Victor absent : option recommandée la plus simple et sûre). **vds-core 1.13.0, additif** (à fusionner après le chantier A, 1.12.0). Migration : une table noyau `publication`.

## 1. Périmètre V1 (borné)

- **Objets soumis à validation** : playlists, mises en page, programmations. Rien d'autre.
- **Hors workflow, avec garde-fou** :
  - Médias : un média seul ne joue jamais. Mais **remplacer le fichier d'un média utilisé par un objet publié** est réservé à l'admin pour un éditeur quand la validation est active (`403 publication.media_in_use_requires_admin`), sinon ce serait un contournement.
  - Forçages : non soumis à validation (urgence). Un forçage **de contenu** (playlist éphémère publiée d'office) est réservé à l'admin tant que `diffusion.review_required` est actif (`403 override.content_requires_admin`) ; la playlist éphémère n'est pas accessible via `/playlists`. Un forçage ne peut viser qu'une playlist ou une mise en page **publiée** (`409 publication.not_published`) et joue sa version publiée.
- **Pas en V1** (points d'accroche) : diff visuel entre versions, historique des versions publiées, « annuler mes modifications » (retour à la version publiée), validation à plusieurs niveaux, admin de site, notifications externes.

## 2. Décisions

1. **Copie de travail + instantané publié.** Les tables actuelles restent la copie de travail, éditée par les API existantes **sans changement d'UX d'édition**. La publication copie l'état dans un instantané JSON (`publication.snapshot`). **Le résolveur de programmation ne lit que les instantanés** : le player ne reçoit que du publié, par construction.
2. **Règle de publication** :
   - Clé de config noyau `diffusion.review_required` (booléen, niveau organisation seulement, défaut `true`, `x-vds-level: simple`).
   - **Admin** (ou validation désactivée) : tout enregistrement publie automatiquement la copie de travail entière, dans la même transaction (« ce que l'admin enregistre est publié »). Si une révision était en revue, elle est considérée comme approuvée.
   - **Éditeur** : l'enregistrement laisse l'instantané intact et passe l'objet en `draft`. L'éditeur soumet (`in_review`) ; l'admin approuve ou refuse.
3. **États** de la copie de travail : `draft` (modifications non publiées), `in_review`, `published` (copie = instantané). « Jamais publié » = `draft` avec `published_rev = null`.
4. **Révisions** : `working_rev` incrémenté à chaque écriture de l'objet ou de ses enfants (éléments de playlist, zones/calques). L'approbation exige `expected_rev == working_rev` (`409 publication.stale`) : l'admin approuve exactement ce qu'il a vu. Un éditeur qui modifie un objet `in_review` le repasse en `draft` (resoumission nécessaire).
5. **Dépendances** : publier une programmation ou une mise en page exige que les playlists et mises en page référencées aient un instantané publié (`409 publication.dependency_unpublished {deps:[{entity_type, entity_id}]}`). L'admin peut les approuver dans la même action (`include_dependencies: true`). Lors d'une publication automatique admin bloquée par une dépendance, l'enregistrement réussit mais l'objet reste `draft` et la réponse porte `publication.blocked_by`.
6. **Suppression** : un éditeur ne supprime pas un objet qui a un instantané publié (`403 publication.delete_requires_admin`) ; un objet jamais publié est supprimable. La suppression d'un objet supprime sa ligne `publication`.
7. **Médias référencés par un instantané** : comptés dans l'usage du média (purge, expiration, suppression), comme les références de la copie de travail.
8. **Rôles et périmètre (lot J)** : soumettre/retirer = éditeur dans son périmètre (contrôles de propriété existants) ou admin ; approuver/refuser = admin uniquement. Le serveur décide, l'admin masque seulement les boutons.
9. **Audit** : chaque transition explicite (`submit`, `withdraw`, `approve`, `reject`) écrit une ligne via `vds_server.core.audit.record` dans la transaction (`target_type` = type d'objet, `details` = `{rev, comment?}`). Les publications automatiques ne sont pas auditées en plus de l'écriture qui les cause.
10. **Pas de notification externe** : événements sur le bus uniquement ; aucun notifier abonné par défaut.
11. **Pas de `if studio`** : même workflow dans les deux modes ; les rôles viennent de l'`auth-provider` actif.
12. Option écartée : « demande de modification » (l'écriture de l'éditeur est stockée comme un état complet en attente et rejouée à l'approbation). Elle évite de toucher au résolveur mais impose une API « état complet » par objet et un double chemin d'enregistrement dans tous les éditeurs de l'admin.

## 3. Données

Table `publication` :

```
entity_type     text  check in ('playlist','layout','schedule')
entity_id       uuid
primary key (entity_type, entity_id)
state           text  check in ('draft','in_review','published')
working_rev     int   not null default 1
submitted_rev   int   null,  submitted_by uuid null,  submitted_at timestamptz null
published_rev   int   null,  published_by uuid null,  published_at timestamptz null
snapshot        jsonb null      -- {"v":1, ...} = DTO d'entrée du résolveur, voir §4
review_comment  text  null      -- ≤ 500 car., dernier refus
updated_at      timestamptz not null
index (state) where state = 'in_review'
```

Reprise de l'existant : au démarrage, avant de servir les players, une tâche idempotente crée une ligne `published` (instantané de l'état courant) pour chaque objet sans ligne. Aucune lecture « repli sur la copie de travail » dans le résolveur.

## 4. Résolveur

- Refactor préalable : l'entrée du résolveur devient des DTO Pydantic (`PlaylistSnapshotV1`, `LayoutSnapshotV1`, `ScheduleSnapshotV1`) construits aujourd'hui depuis l'ORM. Le résolveur ne consomme plus que ces DTO.
- `snapshot` = ces DTO sérialisés. Publier = construire le DTO depuis la copie de travail et l'écrire.
- Résolution pour un écran : programmations dont l'instantané existe → instantanés des playlists/mises en page référencées. Les médias restent lus en direct (expiration, suppression) comme aujourd'hui.
- Après une publication (manuelle ou automatique), déclencher le même recalcul et la même poussée vers les écrans qu'un enregistrement admin aujourd'hui.

## 5. API (`/api/v1`, sous `VDS_BASE_PATH`, écritures sous contrôle de même origine)

```
GET  /publications?state=in_review&entity_type=…          liste (admin : tout ; éditeur : son périmètre)
GET  /publications/{entity_type}/{entity_id}              PublicationOut
POST /publications/{entity_type}/{entity_id}/submit       {comment?: str≤500}            éditeur|admin
POST /publications/{entity_type}/{entity_id}/withdraw     {}                             auteur|admin
POST /publications/{entity_type}/{entity_id}/approve      {expected_rev: int, include_dependencies?: bool}  admin
POST /publications/{entity_type}/{entity_id}/reject       {comment: str 1..500}          admin
```

```python
class PublicationOut(BaseModel):
    entity_type: Literal["playlist", "layout", "schedule"]
    entity_id: UUID
    state: Literal["draft", "in_review", "published"]
    working_rev: int
    published_rev: int | None
    submitted_by: UserRef | None; submitted_at: datetime | None
    published_by: UserRef | None; published_at: datetime | None
    review_comment: str | None
    blocked_by: list[EntityRef] = []
```

`PlaylistOut`, `LayoutOut`, `ScheduleOut` gagnent `publication: PublicationOut` (additif). Erreurs typées : `publication.stale`, `publication.invalid_transition`, `publication.dependency_unpublished`, `publication.not_published`, `publication.delete_requires_admin`, `publication.media_in_use_requires_admin`, `auth.forbidden` (existant).

## 6. Événements (`CORE_EVENTS_1_13`, payloads Pydantic `v=1`)

- `publication.submitted` : `{v, entity_type, entity_id, rev, actor_id, comment}`
- `publication.withdrawn` : `{v, entity_type, entity_id, rev, actor_id}`
- `publication.approved` : `{v, entity_type, entity_id, rev, actor_id, author_id, auto: bool}`
- `publication.rejected` : `{v, entity_type, entity_id, rev, actor_id, author_id, comment}`

## 7. Admin (charte V-Tech, FR/EN)

- Badge d'état sur les listes et en tête des éditeurs : Brouillon / En revue / Publié / Modifications non publiées (icône + libellé, jamais la couleur seule).
- Éditeur : bouton « Soumettre pour validation » (commentaire facultatif), « Retirer » ; motif de refus affiché.
- Admin : page « Publications en attente » dans Diffusion (liste, aperçu de la copie de travail avec l'aperçu existant, « Valider », « Refuser » avec motif obligatoire, proposition « Valider aussi les dépendances »).
- Erreur `publication.stale` : « Le contenu a changé depuis votre ouverture. Recharger ». Réglage `diffusion.review_required` via le formulaire de config généré.

## 8. Tâches

| # | Tâche | Agent | Critère de fin testable |
| --- | --- | --- | --- |
| V1 | SDK 1.13.0 : `CORE_EVENTS_1_13` + payloads, `CORE_API_VERSION`, note de compatibilité | grunt | `pytest packages/sdk-py` vert, événements validés par `EVENT_NAME_RE` |
| V2 | Refactor résolveur : DTO d'entrée `*SnapshotV1`, construits depuis l'ORM | builder | Tests existants du résolveur verts sans modification de leurs attentes ; test d'équivalence ORM → DTO → JSON → DTO |
| V3 | Migration `publication` + reprise idempotente au démarrage | builder | `alembic upgrade/downgrade` OK ; après reprise, chaque objet existant a une ligne `published` ; relancer ne crée rien |
| V4 | Service `publication` : `working_rev` (hooks dans les services playlist/layout/schedule et enfants), transitions, auto-publication admin, dépendances, suppression, audit, événements | builder | Tests unitaires par transition (autorisée / refusée par rôle et périmètre), `stale`, dépendances, auto-publication bloquée, une ligne d'audit par transition |
| V5 | Résolveur sur instantanés seulement ; recalcul et poussée après publication | builder | Test : un éditeur modifie une playlist programmée → la programmation résolue de l'écran est inchangée ; après approbation → elle change et la poussée part |
| V6 | Garde-fous hors workflow : forçage sur publié seulement, remplacement de média utilisé réservé admin, usage média incluant les instantanés | builder | Tests : forçage sur playlist jamais publiée → 409 ; éditeur remplace un média utilisé → 403 ; purge ne supprime pas un média référencé seulement par un instantané |
| V7 | API `/publications` + champs `publication` dans les `*Out` ; `make api-types` | builder | Tests API avec `VDS_BASE_PATH=/` et `/espace/affichage`, contrôle de même origine sur les POST |
| V8 | Admin : badges, actions éditeur, page « Publications en attente », messages d'erreur, i18n FR/EN | builder | vitest : boutons selon rôle, badge par état ; aucune chaîne en dur (lint i18n) |
| V9 | e2e : éditeur crée et soumet, admin refuse, éditeur corrige et resoumet, admin valide, l'écran joue la nouvelle version | qa-tests | Playwright vert sur les deux `VDS_BASE_PATH` |
| V10 | Revue sécurité (contournements : forçage, médias, suppression, API directe, périmètre) | revue-securite | Rapport sans bloquant ni important ouvert |
| V11 | Docs : `architecture.md` (diffusion), `design.md` (états), ligne roadmap | grunt | Fichiers à jour |

Ordre : V1 → V2 → V3 → V4 → V5–V6–V7 → V8 → V9 → V10.

## 9. Risques

- V2 (refactor du résolveur) est le point dur : tout écart change ce que jouent les écrans. Le test d'équivalence est le filet.
- Auto-publication admin : un admin qui enregistre un objet en brouillon d'éditeur publie aussi les modifications de l'éditeur (il les a sous les yeux dans l'éditeur). Documenté dans l'aide.
- Défaut `review_required = true` : sans effet pour un admin seul ; change le comportement des éditeurs existants (voulu).
- Pas de diff ni de retour arrière en V1 : l'admin juge sur la copie de travail.

## 10. Ligne à ajouter dans `docs/roadmap.md` › Décisions

- 2026-11-03 — Validation de publication : playlists, mises en page et programmations ont une copie de travail (tables actuelles) et un instantané publié (`publication.snapshot`, DTO d'entrée du résolveur) ; le résolveur ne lit que les instantanés ; admin = auto-publication à l'enregistrement, éditeur = brouillon → soumission → approbation/refus admin (`expected_rev`, dépendances publiées exigées) ; `diffusion.review_required` (org, défaut true) ; forçage sur publié seulement, remplacement de média utilisé réservé admin ; audit des transitions ; événements `publication.submitted|withdrawn|approved|rejected` (hook, aucun notifier) ; vds-core 1.13.0 additif, après 1.12.0 (widgets). Voir `architecture-validation.md`.

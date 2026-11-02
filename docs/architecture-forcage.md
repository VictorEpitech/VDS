# Forçage avec fin automatique (lot 1 de la refonte Xibo)

Statut : décidé le 2026-11-02 (architecte, sur arbitrages de Victor consignés dans `docs/refonte/xibo-ecarts.md` §5). Aucun code. Contrat : **vds-core 1.7.0, additif**.
Sources lues : `apps/server/src/vds_server/diffusion/resolver.py` (résolveur pur J/J+1), `diffusion/schemas.py`, `config/resolver.py`, `docs/architecture-actions-rapides.md` (`/fleet/playback`, `source: "override"` réservé).

## 1. Décisions

1. **Le forçage est une primitive de la planification, donc du noyau** (le noyau connaît « planification »). Ce n'est pas un module : c'est le point d'entrée commun que les modules futurs (`alerts.*`) utiliseront via l'API et le bus, sans code noyau supplémentaire.
2. **Nom de code : `PlaybackOverride`** (table `playback_overrides`, API `/api/v1/overrides`). Le préfixe évite la collision avec `ConfigOverride` / `StoredOverride` (surcharges de configuration héritée), déjà présents. Vocabulaire UI inchangé : « Forçage » / « override ».
3. **Une seule fonction de résolution.** Le forçage s'insère dans `diffusion.resolver.resolve` (comme l'annonce son docstring) ; le player, `/fleet/playback` et l'aperçu consomment le même résultat. Aucune logique de résolution dans le front ni dans le player.
4. **Le forçage gagne toujours sur la programmation**, quelle que soit la spécificité de la cible. Entre forçages actifs sur un même écran : `priority` décroissante, puis **le plus récent gagne** (`created_at`), puis l'id. Règle affichée à l'utilisateur : « le dernier forçage l'emporte ».
5. **Fin obligatoire.** Pas de forçage sans fin. `ends_at` est un instant UTC absolu stocké ; l'UI propose des durées, le serveur convertit. Plafond configurable (défaut 7 jours).
6. **Le player n'a rien à « savoir » du forçage pour le jouer.** Un forçage produit des créneaux ordinaires dont la fin est `ends_at` : le player hors ligne passe seul au créneau suivant à l'heure dite (règle 5). Les champs ajoutés au créneau (`source`, `override_id`) sont informatifs.
7. **Contenu forcé = toujours une playlist côté player.** Forcer « un média / un contenu » crée une playlist système éphémère (`playlists.owner_override_id`), masquée des listes, purgée avec le forçage. Aucun nouveau format côté player.
8. **Interaction avec `scheduled_off`** : par défaut, le forçage **remplace aussi les créneaux hors service** (l'écran est rallumé pendant le forçage puis rendu à sa programmation, extinction comprise). Option Avancé `respect_scheduled_off=true` : le forçage ne s'applique que là où la programmation affiche quelque chose. Option Avancé `kind="off"` : forcer l'extinction (réservé `admin`).
9. **Arrêt anticipé = en ligne seulement.** « Arrêter » pose `ended_at` et repousse la programmation ; un écran hors ligne continue jusqu'à `ends_at`. L'UI l'annonce (« 2 écrans hors ligne l'afficheront jusqu'à 14:30 »). Accepté : c'est le prix de l'autonomie du player.
10. **Priorités en 3 niveaux visibles** (décision Victor) : sur une programmation, `normal` = 0, `important` = 100 ; `forçage` = cette entité. La priorité numérique (−1000..1000) n'apparaît qu'en Avancé. Seul le mapping est fixé ici ; l'écran de programmation est au lot H.
11. **Pas de dépendance au mode** : aucune URL absolue, tout sous `VDS_BASE_PATH`, écritures sous cookie soumises au contrôle de même origine existant.

## 2. Contrat vds-core 1.7.0 (additif) et note de compatibilité

Ajouts :
- Créneau de programmation résolue (trame serveur → player et serveur → agent) : `source: "schedule" | "override" | "off"`, `override_id: UUID | null`.
- Bus : `override.created`, `override.stopped`, `override.expired` (payload v1, §4).
- `ScreenPlaybackOut.source` accepte `"override"` + champ `override_id` (API admin, `make api-types`).

Compatibilité : un player ou un agent < 1.7.0 ignore les champs inconnus et joue le créneau forcé comme un créneau ordinaire (playlist + fin) ; un forçage `off` lui apparaît comme un créneau hors service. Aucun module existant n'est touché. **Pré-requis à vérifier (backend)** : le décodage des trames côté player et agent tolère les champs inconnus ; sinon, ne pas émettre `source`/`override_id` aux clients qui annoncent < 1.7.0.

### Écarts d'implémentation backend (2026-11-02)

- **`source` sur la trame reste un objet** : un créneau `play` porte `source: {schedule_id: UUID | null, kind: "schedule" | "override", override_id: UUID | null}` ; un créneau `off` forcé porte `override_id`. La forme chaîne (`"schedule" | "override" | "off"`) existe côté `Slot.source` (résolveur) et `ScreenPlaybackOut.source`. Raison : `source` était déjà un objet (`{schedule_id}`) dans le format 1 ; le transformer en chaîne casserait les clients 1.6. Pydantic : aucun `extra="forbid"` ajouté, les clients < 1.7 ignorent les clés inconnues.
- **Événements** : ajout additif de `override.updated` (prolongation) ; `override.stopped` et `override.expired` portent aussi `target_type` / `target_id` (nécessaire à l'agent de site pour retrouver le site et recalculer son plan). Permission de module `override.write` ajoutée au catalogue.
- **Alimentation côté serveur** : le plan `power` (player et agent) intègre les forçages (`power.resolver.apply_overrides`) : un forçage de playlist émet un créneau `on` qui remplace tout `off` recouvrant (sauf `respect_scheduled_off`), un forçage `off` émet un `off`. Aucun changement requis côté agent (`current_slot` « premier off » ne voit plus de `off` recouvrant le forçage).
- `respect_scheduled_off` est nullable en base (null = valeur de config résolue par écran). L'arrêt anticipé tronque `ends_at` à `ended_at` dans le résolveur ; les forçages terminés restent visibles dans la fenêtre J/J+1 jusqu'à la purge (révision stable à l'expiration).
- Balayeur : toutes les 30 s (`override.expire`, audit système) ; purge (`override.purge`) après `core.override.retention_days`. Idempotence : `request_id` unique (index partiel).
- Compat vds-core 1.7.0 : `CORE_API_VERSION = "1.7.0"` (sdk-py), événements `CORE_EVENT_PAYLOAD_1_7`, types TS `player.ts` alignés.

## 3. Modèle de données (Alembic, migration 0015)

```
playback_overrides
  id                    uuid pk
  name                  varchar(120)        -- défaut généré : « Forçage <cible> <heure> »
  kind                  varchar(16)         -- 'playlist' | 'off'
  playlist_id           uuid fk playlists null   -- null si kind='off'
  target_type           varchar(16)         -- organization | site | group | screen
  target_id             uuid
  starts_at             timestamptz         -- défaut now()
  ends_at               timestamptz         -- obligatoire, > starts_at
  ended_at              timestamptz null    -- arrêt manuel ou expiration constatée
  end_reason            varchar(16) null    -- 'stopped' | 'expired'
  priority              int default 0       -- -1000..1000, Avancé
  respect_scheduled_off bool default false
  note                  varchar(500) null
  created_by            uuid fk users null  -- null = système / module
  created_by_module     varchar(120) null   -- id de module (ex. alerts.cap) si créé par un module
  ended_by              uuid fk users null
  created_at, updated_at timestamptz
  index (target_type, target_id), index (ends_at) where ended_at is null

playlists
  + owner_override_id   uuid fk playback_overrides null on delete cascade
                        -- non null = playlist système éphémère, exclue de GET /playlists
```

Purge : un job serveur supprime les forçages terminés depuis plus de `core.override.retention_days` (et leur playlist éphémère) ; les médias restent régis par la future expiration/purge des médias (décision Victor, lot médiathèque). L'audit n'est pas purgé par ce job.

## 4. Interfaces

### Résolveur (pur, `diffusion/resolver.py`)

```python
@dataclass(frozen=True)
class OverrideSpec:
    id: UUID
    target_type: str
    target_id: UUID
    starts_at: datetime        # UTC
    ends_at: datetime          # UTC, déjà tronqué à ended_at si arrêté
    playlist_id: UUID | None   # None = forçage d'extinction
    priority: int
    created_at: datetime
    respect_scheduled_off: bool = False

@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime
    schedule_id: UUID | None
    playlist_id: UUID | None
    override_id: UUID | None = None          # ajout additif

    @property
    def source(self) -> Literal["schedule", "override", "off"]: ...

def resolve(chain, schedules, tz, day, overrides: Collection[OverrideSpec] = ()) -> list[Slot]
```

Algorithme : les bornes des forçages s'ajoutent aux points de découpe ; sur chaque segment, gagnant programmation calculé comme aujourd'hui, puis si un forçage ciblant la chaîne est actif → `max(actifs, key=(priority, created_at, id))` remplace le gagnant, sauf `respect_scheduled_off` et gagnant programmation = off. La fusion de créneaux contigus compare `(schedule_id, override_id)`. Les forçages arrêtés/expirés ne sont pas chargés (le service ne passe que `ended_at is null and ends_at > lo`).

### API REST admin

```
POST   /api/v1/overrides                 → 201 OverrideOut        (admin, editor ; kind=off ou cible organization : admin)
POST   /api/v1/overrides/preview         → 200 OverridePreviewOut (mêmes droits ; aucun effet)
GET    /api/v1/overrides?active=true&site_id=&target_type=&target_id=  → list[OverrideOut]
GET    /api/v1/overrides/{id}            → OverrideOut
PATCH  /api/v1/overrides/{id}            → OverrideOut   (prolonger/raccourcir ends_at, note ; actif seulement)
POST   /api/v1/overrides/{id}/stop       → OverrideOut   (idempotent)
```

```python
class OverrideContentIn(_In):            # « forcer un contenu » → playlist éphémère d'un élément
    content_type: str = Field(min_length=1, max_length=120)
    props: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int | None = Field(default=None, gt=0)

class OverrideCreate(_In):
    target_type: TargetType
    target_id: UUID
    kind: Literal["playlist", "off"] = "playlist"
    playlist_id: UUID | None = None      # exactement un de playlist_id / content si kind=playlist
    content: OverrideContentIn | None = None
    starts_at: datetime | None = None    # Avancé ; défaut now
    ends_at: datetime | None = None      # exactement un de ends_at / duration_minutes / until
    duration_minutes: int | None = Field(default=None, gt=0)
    until: Literal["end_of_day"] | None = None   # minuit du fuseau du site cible (organisation : fuseau org), cf. Q-F2
    priority: int = Field(default=0, ge=-1000, le=1000)
    respect_scheduled_off: bool | None = None    # None = valeur de config résolue
    name: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)
    request_id: UUID | None = None       # idempotence (double clic, fan-out admin)

class OverrideOut(BaseModel):
    id: UUID; name: str; kind: str; playlist_id: UUID | None; ephemeral: bool
    target_type: TargetType; target_id: UUID; target_name: str
    starts_at: datetime; ends_at: datetime; ended_at: datetime | None; end_reason: str | None
    status: Literal["scheduled", "active", "ended"]
    priority: int; respect_scheduled_off: bool; note: str | None
    created_by: UUID | None; created_by_module: str | None; created_at: datetime

class OverridePreviewOut(BaseModel):
    screens_total: int
    screens_offline: int          # l'arrêt anticipé ne les atteindra pas
    screens_in_scheduled_off: int # seront rallumés (ou ignorés si respect_scheduled_off)
    ends_at: datetime             # fin calculée
    superseded_override_ids: list[UUID]   # forçages actifs qui seront masqués
```

Erreurs (clés i18n) : `override.end_required`, `override.end_in_past`, `override.duration_too_long`, `override.content_ambiguous`, `override.target_not_found`, `override.forbidden_kind`, `override.already_ended` (PATCH seulement ; `stop` reste idempotent).

`GET /api/v1/fleet/playback` : `source` gagne `"override"`, ajout `override_id`, `until` = fin du créneau forcé.

### Configuration héritée (clés `core`, provenance via `ConfigService` existant)

| Clé | Défaut | Niveau | Portées |
| --- | --- | --- | --- |
| `core.override.default_duration_minutes` | 60 | simple | org, site, group |
| `core.override.duration_presets` | `[15, 60, 240, "end_of_day"]` | advanced | org, site, group |
| `core.override.max_duration_hours` | 168 | advanced | org, site |
| `core.override.respect_scheduled_off` | false | advanced | org, site, group, screen |
| `core.override.retention_days` | 90 | advanced | org |

La valeur appliquée est résolue **pour la cible** du forçage (pour `respect_scheduled_off` : par écran, au moment de la résolution si le champ du forçage est `null`). L'UI affiche la provenance (« 1 h, défini sur le site Campus Nord »).

### Bus (payloads Pydantic, `version: 1`)

```python
class OverrideCreatedV1(BaseModel):
    version: Literal[1] = 1
    override_id: UUID; target_type: str; target_id: UUID
    kind: Literal["playlist", "off"]; playlist_id: UUID | None
    starts_at: datetime; ends_at: datetime
    created_by: UUID | None; created_by_module: str | None

class OverrideStoppedV1(BaseModel):     # arrêt manuel ou raccourci à maintenant
    version: Literal[1] = 1
    override_id: UUID; ended_at: datetime; ended_by: UUID | None

class OverrideExpiredV1(BaseModel):     # constaté par le balayeur serveur à ends_at
    version: Literal[1] = 1
    override_id: UUID; ended_at: datetime
```

Permission de module à déclarer pour publier un forçage (point d'accroche `alerts.*`, non implémenté en V1) : `override.write`. Aucun module V1 ne l'utilise.

### Audit

`override.create`, `override.update`, `override.stop` (acteur utilisateur), `override.expire` (acteur système). Détail : cible, fin, playlist, nombre d'écrans touchés.

### Diffusion vers les écrans

À la création, la modification ou l'arrêt : recalcul de la programmation des écrans de la cible et poussée par le canal existant (même chemin qu'une modification de programmation), regroupée (anti-rafale, une poussée par écran). Le balayeur d'expiration ne pousse **rien** (le player a déjà la fin) ; il pose `ended_at`, émet `override.expired`, écrit l'audit.

## 5. Comportement player

- Aucun changement fonctionnel requis : créneau = playlist + fin, déjà joué hors ligne avec bascule à l'heure de fin.
- À vérifier/garantir : la bascule de créneau est déclenchée par minuterie sur `slot.end` (pas seulement à la réception d'une trame) ; une nouvelle programmation reçue en cours de créneau remplace immédiatement le créneau courant (arrêt anticipé).
- Rien n'est affiché au public (pas de badge « forcé »). Le player remonte `override_id` du créneau courant dans son état (optionnel, pour la carte écran).

## 6. Agent de site

- L'alimentation suit les créneaux résolus : un créneau `override` pendant une plage `scheduled_off` doit rallumer l'écran, puis l'extinction reprend à la fin. Si l'agent consomme déjà les créneaux résolus (`schedule_id is None` = off), **aucun code** : seulement des tests. Si l'agent recalcule ses propres heures d'extinction, il doit consommer les créneaux résolus à la place (sinon le forçage est éteint par l'agent).

## 7. UI admin (simple par défaut)

- Points d'entrée : bouton « Forcer » sur la carte écran, dans la barre de sélection multiple (fan-out = un forçage par écran, ou proposer « cibler le groupe » si la sélection = un groupe entier), sur la page groupe/site, et bloc « Forçages en cours » sur l'Accueil.
- Formulaire, 3 champs visibles : **Quoi** (playlist ou média, recherche), **Où** (prérempli), **Combien de temps** (puces issues de `duration_presets`, défaut résolu avec provenance). Compteur « 12 écrans, dont 2 hors ligne, 3 seront rallumés » depuis `/overrides/preview`.
- Volet Avancé : début différé, fin à une date précise, priorité numérique, respecter le hors service, forcer l'extinction (admin), nom, note.
- Carte écran : état `overridden` dans `displayState`, « Forcé jusqu'à 14:30 », actions Arrêter / Prolonger (+1 h). Arrêter : confirmation qui annonce les écrans hors ligne.
- i18n FR/EN, charte V-Tech, cibles ≥ 44 px, `aria-live` sur le compte à rebours de fin (mise à jour à la minute, pas à la seconde).

## 8. Découpage par agent et critères de fin

**backend** — migration 0015, modèle, `OverrideSpec` + résolveur, service, routes, preview, balayeur d'expiration + purge, événements, audit, clés de config, `/fleet/playback`, vds-core 1.7.0 + note, `make api-types`.
Fin : tests purs du résolveur (forçage > programmation à toute spécificité ; chevauchement de forçages ; `respect_scheduled_off` ; fin en cours de créneau ; traversée de minuit et changement d'heure ; fin au-delà de J+1 ; forçage `off`) ; tests API (rôles, `kind=off` admin seul, même origine, validations, idempotence `request_id`, PATCH sur forçage terminé) ; pytest aux deux `VDS_BASE_PATH` ; contrôle que les trames restent décodables par un client 1.6.

**player** — Fin : vitest avec horloge simulée : forçage reçu puis réseau coupé → bascule sur la programmation à `ends_at` ; nouvelle programmation sans le forçage → arrêt immédiat ; créneau `override` pendant une plage hors service → affiche ; aucun texte technique à l'écran.

**agent** — Fin : test que l'alimentation suit les créneaux résolus (rallumage pendant un forçage en plage hors service, extinction à la fin, y compris sans connexion serveur) ; aucune modification si c'est déjà le cas, sinon bascule vers les créneaux résolus. Version de l'agent annoncée ≥ 1.7.0.

**frontend-admin** — Fin : forcer une playlist sur un groupe en ≤ 3 interactions sans ouvrir Avancé ; carte écran en état « forcé » avec fin ; arrêter / prolonger ; bloc Accueil ; navigation clavier ; FR/EN ; Playwright aux deux `VDS_BASE_PATH`.

**qa-tests / e2e** — un forçage d'1 h sur un groupe s'affiche sur l'écran réel en < 30 s, se termine seul hors ligne, est visible dans la carte et l'audit.

**revue-securite** — droits (editor ne force pas l'extinction ni l'organisation), même origine, plafond de durée, aucune fuite d'info au public.

## 9. Risques

- **Horloge de l'appareil** : la fin hors ligne dépend de l'horloge locale ; une dérive décale la fin. Mitigation : remonter l'écart d'horloge dans l'état player (lot I) si ce n'est pas déjà fait.
- **Arrêt anticipé non reçu hors ligne** : assumé et affiché (décision 9).
- **Rafale de poussées** sur un forçage d'organisation : regroupement par écran obligatoire.
- **Forçage oublié** : plafond de durée + bloc Accueil « Forçages en cours ».
- **Confusion `ConfigOverride` / `PlaybackOverride`** dans le code : nommage imposé (décision 2).
- **Tolérance des trames aux champs inconnus** non confirmée : à vérifier avant d'émettre `source`/`override_id`.
- **Agent qui recalcule ses propres extinctions** : casserait le rallumage pendant un forçage ; à vérifier en premier.

## 10. Questions ouvertes

- Q-F1 : défaut `respect_scheduled_off=false` (le forçage rallume les écrans) — à confirmer par Victor ; réversible par la config.
- Q-F2 : « fin de journée » = minuit du fuseau du **site cible** (organisation : fuseau de l'organisation), pour garder un `ends_at` unique. Alternative (minuit propre à chaque écran) écartée : multi-fuseaux rares, complexité réelle.

## 11. Durcissement (revue sécurité, 2026-11-02)

- Droits : `PATCH` et `stop` appliquent `_check_rights` à la ligne en base (kind `off` ou portée organisation = admin seul), comme la création.
- Priorité : un forçage `off` (créable par un admin seul) bat toujours un forçage de playlist, quelle que soit sa `priority` (rang `(is_off, priority, created_at, id)`) ; entre forçages de même nature, `priority` puis dernier créé gagne.
- Anti-DoS : `core.override.max_active_per_target` (défaut 20, portées org/site/groupe, provenance standard) -> 409 `override.too_many_active` ; début différé <= 30 j -> 422 `override.start_too_far` ; `GET /overrides` paginé `limit` (1-200, défaut 100) / `offset`.
- `preview` calcule les écrans une fois (plus de N+1). La purge audite `count` et `ids` (100 max).

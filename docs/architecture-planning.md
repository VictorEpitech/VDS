# Planning : vue calendrier, aperçu, simulation, conflits (lot « refonte du planning », refonte UX lot H)

Statut : décision architecte du 2 novembre 2026, à valider par Victor sur les points « Questions ouvertes ». Aucun code.
Base : `docs/refonte/ux.md` (§1 Planning, §2.3 Programmer, §4.8), `docs/design.md` (parcours 3 et 4, états), `docs/architecture-mediatheque.md` (expiration), `apps/server/src/vds_server/player/schedule.py` (`ScheduleBuilder`, résolveur unique), `apps/server/src/vds_server/diffusion/schedules.py`, `apps/admin/src/routes/Schedules.tsx`, `features/schedule/*`, `features/overrides/*`.
Coordination : les mises en page sont spécifiées en parallèle dans `docs/architecture-mises-en-page.md`. Ce document ne suppose rien de leur modèle : un segment porte un `content` générique (`{kind: "playlist" | "layout", id, name}`) ; seul `playlist` existe aujourd'hui.

## 1. Décisions

1. **Un seul résolveur, côté serveur.** L'admin n'interprète jamais règles hebdomadaires, priorités, héritage, forçages ni expirations. Il affiche des segments déjà résolus et expliqués par le serveur. Les seuls calculs front autorisés sont de présentation (position en pixels, regroupement par jour, libellé du niveau de priorité renvoyé par le serveur).
2. **Le résolveur est extrait, pas dupliqué.** `ScheduleBuilder` est découpé en une fonction pure de résolution sur une fenêtre, appelée par : le document player (`GET /player/schedule`, inchangé octet pour octet), la timeline admin, l'aperçu « à l'instant », la simulation d'un brouillon. Option écartée : un second calcul SQL/TS « approximatif » pour l'admin (dérive garantie entre ce que voit l'admin et ce que joue l'écran).
3. **Mode « explication »** : le même calcul, avec un drapeau `explain=True`, conserve pour chaque segment le gagnant, les candidats perdants et la raison. Le document player ne l'active jamais (coût et taille nuls pour lui).
4. **Aucune migration en P0.** `schedule.priority` (entier −1000..1000) existe ; les 3 niveaux sont des **bandes d'affichage** calculées par le serveur, la valeur numérique reste éditable dans « Avancé ». Aucune donnée existante ne change de niveau de façon inattendue (0 → Normale).
5. **Pas de bump vds-core.** Endpoints admin REST additifs et champ de sortie additif ; ni manifeste, ni événement, ni trame WS, ni document player ne changent (même règle que le résumé de parc du 30 octobre). Si un module venait à consommer la timeline, elle serait alors versionnée (`format=1` déjà prévu dans la réponse).
6. **Lecture d'abord.** V1 du lot : calendrier en lecture + création/édition par l'assistant existant pré-rempli depuis la grille (clic sur un créneau vide). Pas de glisser-déposer pour déplacer un créneau (P3, avec alternative clavier obligatoire).

## 2. Règles de résolution exposées (référence, comportement actuel conservé)

Le comportement du résolveur **ne change pas** dans ce lot. Ordre exact implémenté (relu dans `diffusion/resolver.py`, figé par `tests/test_resolver_gold.py`) ; **il diffère de l'ordre attendu initialement** :

1. Le temps est découpé en créneaux (bornes de règles, de périodes, d'expirations, de forçages) sur J et J+1 locaux (minuit local à minuit local +2 jours).
2. Programmation candidate : `enabled`, cible dans la chaîne de l'écran (organisation, site, groupe, écran), jour dans `starts_on..ends_on`, jour de semaine et heure murale couverts par une règle (aucune règle = toute la journée), et créneau avant `until` (expiration de la playlist, qui coupe l'intervalle). Les playlists vides ne sont **pas** écartées par le résolveur (le player gère l'élément vide).
3. Gagnant parmi les candidats : **spécificité d'abord** (écran > groupe > site > organisation), puis `priority` décroissante, puis `updated_at` le plus récent, puis `id` le plus grand. La priorité ne remonte donc jamais au-dessus d'une cible plus spécifique.
4. Forçage actif sur la chaîne : il l'emporte sur toute programmation, **quelle que soit la spécificité de sa cible**. Entre forçages : extinction (sans playlist) d'abord, puis `priority` décroissante, puis `created_at` le plus récent, puis `id`. Un forçage `respect_scheduled_off` ne s'applique pas là où la programmation est hors plage (aucune programmation gagnante).
5. Aucun candidat : `off` (écran noir), sans message public.

L'extinction prévue du plan d'alimentation (`scheduled_off`) n'est **pas** une entrée du résolveur : elle voyage à part (bloc `power` du document player). Son seul effet ici est `respect_scheduled_off` ci-dessus. L'état `scheduled_off` de `Segment` sera donc posé par la couche timeline à partir du plan d'alimentation, jamais par `resolve`.

Mode `explain` : pour chaque créneau, `reason` du gagnant (`override_active`, `more_specific`, `higher_priority`, `tie_break`, ou `only_candidate` / `no_candidate`) et `losers` (programmations ou forçages de la chaîne avec leur raison : `disabled`, `outside_period`, `outside_rules`, `content_expired`, `override_active`, `scheduled_off`, `more_specific`, `higher_priority`, `tie_break`). Le découpage ajoute les minuits locaux.

Chaque branche reçoit un **code de raison** stable (énuméré §4.3), clé i18n côté admin.

## 3. Niveaux de priorité

| Niveau (`priority_level`) | FR / EN | Valeur créée par défaut | Bande |
| --- | --- | --- | --- |
| `background` | Fond / Background | −100 | `priority < 0` |
| `normal` | Normale / Normal | 0 | `0 ≤ priority < 100` |
| `high` | Prioritaire / High | 100 | `priority ≥ 100` |

- `ScheduleOut` gagne `priority_level: Literal["background","normal","high"]` (additif, calculé par le serveur, fonction unique `priority_level(p: int) -> PriorityLevel` dans `vds_server.diffusion.priority`). Bandes constantes en V1 (pas de réglage `core.*` : un réglage par organisation rendrait les badges instables entre deux vues).
- Création : le choix d'un niveau envoie la valeur par défaut du niveau. « Avancé » : champ numérique ; le badge suit la bande et affiche l'entier en sous-texte (« Prioritaire · 120 »).
- Affichage : badge couleur + icône + libellé (jamais la couleur seule). Icônes proposées : `low_priority` (fond), aucune icône distinctive pour normale (libellé seul), `priority_high` (prioritaire). Les forçages restent un état à part (`overridden`, Tertiary), jamais un « niveau 4 ».

## 4. API serveur (FastAPI, `vds_server.diffusion.planning`, routeur `/api/v1/planning`)

Toutes les routes sont sous `VDS_BASE_PATH` (même `Mount` que l'API), authentifiées par la session, lisibles par `admin` et `editor` (même périmètre que `GET /schedules`). `POST /simulate` est une écriture au sens HTTP : contrôle de même origine (règle 13) via le middleware existant, aucune écriture en base.

### 4.1 Extraction du résolveur (P0-1)

> Réalisé (écarts à la proposition ci-dessous) : le résolveur pur existait déjà (`vds_server.diffusion.resolver.resolve`) ; il est conservé, étendu de `explain=False` et complété par `ResolveInput(chain, schedules, overrides, tz, day)` + `resolve_input()`. Le chargement mutualisé est `vds_server.player.schedule.load_inputs(session, screens, now, timezone_of=, respect_of=)` (nombre de requêtes SQL constant). Pas de `Candidate`/`Window` : les `ScheduleSpec`/`OverrideSpec` existants jouent ce rôle ; `Window` et `empty_contents` seront ajoutés avec la timeline (P1-1). Conflits et marqueurs : `vds_server.diffusion.explain` (`conflicts`, `markers`), purs ; le nom des sources est résolu par l'API. Code de raison ajouté : `only_candidate`.

```python
# vds_server/player/resolve.py  (extrait de ScheduleBuilder, pur : pas d'I/O)
@dataclass(frozen=True)
class Window:
    start: datetime          # aware, UTC
    end: datetime            # exclusif ; end - start <= 42 jours (borne serveur)

@dataclass(frozen=True)
class Candidate:             # une programmation ou un forçage, déjà chargé
    kind: Literal["schedule", "override"]
    id: UUID | None          # None = brouillon de simulation
    name: str
    content: ContentRef      # {kind: "playlist"|"layout", id, name}
    target_type: TargetType
    target_id: UUID
    priority: int
    starts_on: date | None
    ends_on: date | None
    rules: tuple[Rule, ...]
    enabled: bool
    starts_at: datetime | None = None   # forçage
    ends_at: datetime | None = None     # forçage

@dataclass(frozen=True)
class ResolveInput:
    screen: ScreenCtx        # id, site_id, group_id, timezone (résolus)
    candidates: Sequence[Candidate]
    power_off: Sequence[Interval]           # plan d'alimentation résolu (scheduled_off)
    expiries: Mapping[UUID, datetime]       # contenu -> fin d'éligibilité (playlist et ses médias)
    empty_contents: frozenset[UUID]         # playlists sans élément jouable

def resolve(inp: ResolveInput, window: Window, *, explain: bool = False) -> list[Segment]: ...
```

- `ScheduleBuilder` devient : chargement SQL → `ResolveInput` → `resolve(explain=False)` → sérialisation du document player actuel. **Critère** : le document player (et donc `revision`) est identique avant/après sur le jeu de tests existant + un jeu d'or ajouté (chevauchements, héritage, forçage, expiration au milieu d'un créneau, minuit, changement d'heure fin octobre/fin mars).
- Le chargement SQL est mutualisé : `load_inputs(session, screen_ids, window) -> dict[UUID, ResolveInput]` en un nombre constant de requêtes (pas de N+1 par écran).
- **Point d'accroche (non implémenté)** : un filtre de candidats `CandidateFilter = Callable[[Candidate, ScreenCtx, datetime], bool]` est prévu dans la signature interne pour la diffusion conditionnelle future ; en V1 la liste de filtres est vide et aucun module ne peut s'y enregistrer. Raison réservée : `condition_unmet`.

### 4.2 `GET /api/v1/planning/timeline`

Query : `target_type=screen|group|site|organization`, `target_id`, `from` (date locale ISO), `to` (date locale incluse, ≤ 42 jours), `explain=true|false` (défaut `true` pour un écran, `false` pour un groupe/site), `cursor`, `limit` (écrans par page, défaut 50, max 100).

```python
class ContentRef(BaseModel):
    kind: Literal["playlist", "layout"]
    id: UUID
    name: str

class SourceRef(BaseModel):            # provenance d'un segment
    kind: Literal["schedule", "override", "power_plan", "none", "draft"]
    id: UUID | None
    name: str | None
    target_type: TargetType | None
    target_id: UUID | None
    target_name: str | None
    inherited: bool                    # cible != écran affiché
    priority: int | None
    priority_level: PriorityLevel | None

class Loser(BaseModel):
    source: SourceRef
    content: ContentRef | None
    reason: ReasonCode                 # pourquoi il ne joue pas sur ce segment

class Segment(BaseModel):
    start: datetime                    # UTC ; l'UI affiche dans `timezone`
    end: datetime
    state: Literal["play", "off", "scheduled_off", "overridden"]
    content: ContentRef | None
    source: SourceRef
    reason: ReasonCode                 # pourquoi ce gagnant
    losers: list[Loser] = []           # vide si explain=false

class Marker(BaseModel):               # instants remarquables dans la fenêtre
    at: datetime
    kind: Literal["playlist_expires", "media_expires", "override_ends", "schedule_starts", "schedule_ends"]
    ref_id: UUID
    name: str
    impact: Literal["gap", "fallback", "none"]   # ce que l'expiration provoque sur cet écran

class ScreenTimeline(BaseModel):
    screen_id: UUID
    screen_name: str
    timezone: str
    segments: list[Segment]
    markers: list[Marker]
    conflicts: list[Conflict]

class Lane(BaseModel):                 # candidats applicables à la cible (vue « couches »)
    source: SourceRef
    content: ContentRef
    occurrences: list[tuple[datetime, datetime]]   # où il serait éligible, avant arbitrage
    effective_ratio: float             # part de ses occurrences où il gagne sur au moins un écran

class TimelineOut(BaseModel):
    format: Literal[1] = 1
    target_type: TargetType
    target_id: UUID
    window: tuple[datetime, datetime]
    timezone: str                      # fuseau de la cible (site), sinon de l'organisation
    mixed_timezones: bool
    lanes: list[Lane]                  # programmations et forçages applicables, hérités compris
    screens: list[ScreenTimeline]      # page courante
    next_cursor: str | None
    computed_at: datetime
```

- Pour un écran : `lanes` + une seule `ScreenTimeline` expliquée.
- Pour un groupe/site : `lanes` (candidats propres + hérités) + une `ScreenTimeline` par écran (lignes de ressources), sans `losers` par défaut ; l'inspecteur d'instant (§4.4) donne l'explication à la demande.
- Coût borné : 42 jours × 100 écrans par requête ; au-delà `422 planning.window_too_large` / pagination. `ETag` = hachage des révisions des entrées (schedules, overrides, plan, playlists) : `304` en navigation répétée.

### 4.3 Conflits et codes de raison

```python
ReasonCode = Literal[
    "override_active", "scheduled_off", "higher_priority", "more_specific", "tie_break",
    "outside_rules", "outside_period", "disabled", "content_expired", "content_empty",
    "no_candidate",
    "condition_unmet",    # réservé, diffusion conditionnelle future, jamais émis en V1
]

class Conflict(BaseModel):
    kind: Literal[
        "never_plays",        # candidat jamais gagnant dans la fenêtre (masqué en totalité)
        "partially_masked",   # gagne une partie seulement
        "ambiguous_tie",      # même priorité ET même spécificité, chevauchement : départage arbitraire
        "content_expires",    # la playlist ou un média expire pendant une occurrence
        "content_empty",      # occurrence sans élément jouable (tout expiré/désactivé)
        "gap",                # trou de diffusion entre deux occurrences d'une même journée
    ]
    severity: Literal["info", "warning"]
    source: SourceRef
    other: SourceRef | None
    first_at: datetime
    occurrences: int
```

`ambiguous_tie`, `content_expires` (impact `gap`) et `content_empty` sont des avertissements ; le reste est informatif. Les conflits sont **calculés par le serveur** à partir des segments expliqués, jamais par l'admin.

### 4.4 `GET /api/v1/planning/at`

Query : `target_type`, `target_id`, `at` (ISO, défaut maintenant), `limit`/`cursor`. Réponse : `{at, screens: [{screen_id, screen_name, segment: Segment (explain=true), next_change_at, presence}]}`. Sert le bouton « Maintenant », le curseur « à tel instant », la fiche écran (« programmation active ») et le tableau de bord (« prochains changements »). Implémenté comme `timeline` sur la fenêtre `[at, at+1 min)` : aucun code de résolution propre.

### 4.5 `POST /api/v1/planning/simulate`

```python
class SimulateIn(_In):
    draft: ScheduleCreate | None = None        # nouvelle programmation
    edit: tuple[UUID, SchedulePatch] | None = None   # modification d'une existante
    remove: UUID | None = None                 # « que se passe-t-il si je supprime ? »
    target_type: TargetType                    # vue à simuler (souvent la cible du brouillon)
    target_id: UUID
    from_: date = Field(alias="from")
    to: date                                   # ≤ 14 jours

class ChangeOut(BaseModel):
    screen_id: UUID
    start: datetime
    end: datetime
    before: Segment
    after: Segment

class SimulateOut(BaseModel):
    after: TimelineOut                 # explain=true, sources "draft" marquées
    changes: list[ChangeOut]           # différences avant/après, fusionnées
    new_conflicts: list[Conflict]
    resolved_conflicts: list[Conflict]
    screens_touched: int
```

- Valide le brouillon avec les **mêmes** validateurs que `POST/PATCH /schedules` (erreurs identiques, `422`), puis injecte le candidat en mémoire dans `ResolveInput`. Aucune écriture, aucun audit, aucun événement.
- Phrases de l'assistant (« Remplace “Accueil” le mardi 8h–12h ») construites côté admin à partir de `changes` et de clés i18n, sans logique de résolution.
- L'aperçu existant des forçages (`OverridePreviewOut`) est ré-adossé à `resolve` en P2 (même source de vérité) sans changer sa forme.

### 4.6 Ajouts à des sorties existantes (additifs)

- `ScheduleOut.priority_level`.
- `ScheduleOut.next_occurrence_at: datetime | None` et `ScheduleOut.conflicts_count: int` (fenêtre glissante 7 jours, calculés par `resolve`) pour la vue liste ; facultatifs en P1, coûteux : uniquement si `?with=insights`.

## 5. Interface admin

Route : `/schedules` (entrée « Diffusion »), paramètres d'URL partageables, tous relatifs au `basename` du routeur (donc à `VDS_BASE_PATH`) : `?target=screen:<id>&view=week&date=2026-11-02&at=2026-11-02T10:30`. Données : TanStack Query, clés `["planning","timeline",target,from,to]`, `["planning","at",target,at]`, invalidées par toute mutation de schedule/override/playlist/média.

| Composant (`features/planning/`) | Rôle |
| --- | --- |
| `PlanningPage` | Assemble barre d'outils, vue, inspecteur ; remplace la liste actuelle (la liste devient la vue « Liste »). |
| `PlanningToolbar` | Sélecteur de cible (site/groupe/écran, compteur d'écrans), vues Jour / Semaine / Mois / Liste, navigation date, bouton « Maintenant », légende, fuseau affiché si différent du navigateur ou `mixed_timezones`. |
| `TimelineWeek` / `TimelineDay` | Colonnes de jours, axe horaire. Pour un écran : piste **Effectif** en tête (segments résolus) puis une piste par candidat (`lanes`), masqué en hachures + libellé « Masqué par X ». Les chevauchements ne s'empilent jamais en surimpression : une piste par source, donc lisibles à toute largeur. |
| `ResourceTimeline` | Groupe/site : une ligne par écran (segments effectifs), lignes virtualisées, pagination `cursor`. Au-dessus, les pistes des candidats de la cible. |
| `MonthGrid` | Cellules de jour : 3 segments effectifs max + « +N », badges conflits/expirations ; clic = vue Jour. |
| `SegmentBlock` | Bloc : nom du contenu, plage horaire, badge de niveau, icône de provenance (hérité), motif distinct pour `scheduled_off` (atténué Secondary), `overridden` (Tertiary + icône), `off` (vide bordé), brouillon (bordure pointillée). Bordures et aplats tonaux, **aucune ombre**. |
| `NowCursor` | Ligne verticale + étiquette « Maintenant 10:32 » (`aria-hidden`, l'info est aussi dans l'inspecteur). |
| `InstantInspector` | Panneau latéral (feuille basse à 375 px) : « Ce qui joue à <instant> » par écran, gagnant + chaîne d'explication (`losers` traduits : « Accueil (site Moulins, Normale) masqué par Promo (écran, Prioritaire) »), badge de provenance avec lien vers la programmation, forçage avec « Prolonger / Arrêter » (`OverrideControls` existant). Sélecteur date/heure accessible au clavier. |
| `ConflictPanel` | Liste des `conflicts` triés par gravité, chaque entrée = cause en clair + une action (« Modifier la priorité », « Prolonger l'expiration de la playlist », « Ouvrir la médiathèque »). |
| `ExpiryMarker` | Repère sur l'axe aux `markers` (icône `event_busy` + libellé), lien vers le média/la playlist. |
| `PriorityPicker` | 3 choix segmentés (radiogroup) + champ numérique dans le volet « Avancé » unique. |
| `ScheduleWizard` | Évolution de `ScheduleForm` (quoi, où, quand, aperçu) : étape aperçu = `POST /planning/simulate` avec anti-rebond 400 ms, `changes` + `new_conflicts`, région `aria-live="polite"`. Modèles de créneaux (« Tous les jours 8h–18h », « En semaine », « Une date ») = préréglages de formulaire, aucune donnée serveur. |

Accessibilité (WCAG 2.2 AA) :
- Toute vue graphique a une **alternative textuelle équivalente** : la vue Liste (tableau : début, fin, contenu, provenance, niveau, état) affiche les mêmes segments ; c'est la vue par défaut sous 600 px.
- Grille : rôle `grid`, navigation flèches (← → jours, ↑ ↓ segments), `Entrée` ouvre l'inspecteur sur le segment, `N` = maintenant ; tabindex itinérant ; focus visible (token de focus du thème).
- Jamais la couleur seule : niveau = badge texte + icône, masqué = hachures + texte, état = icône + libellé (`StatusBadge` existant).
- Cibles ≥ 44 px au toucher ; texte des blocs tronqué avec libellé complet dans `aria-label` et l'inspecteur.
- Heures en chiffres tabulaires (`font-variant-numeric: tabular-nums`) ; la police mono reste réservée aux données techniques (identifiants, révision).

i18n : clés sous `planning.*` (vues, raisons `planning.reason.<code>`, conflits `planning.conflict.<kind>`, niveaux `schedules.priority.level.<level>`), pluriels i18next (« +{{count}} »), dates et heures via `Intl.DateTimeFormat(i18n.language, { timeZone })`, premier jour de semaine selon la langue (lundi en FR). +30 % de longueur prévu : blocs tronqués, jamais de largeur fixe sur un libellé.

Charte V-Tech : uniquement `--vds-*` ; couleurs de contenu = rotation d'une palette tonale dérivée des tokens (Primary/Secondary/Tertiary container) indexée par l'id du contenu, toujours doublée du nom ; aucune ombre portée, séparation par bordure `--vds-outline-variant` ; thème `allia` sans modification de composant.

## 6. Points d'accroche (non implémentés)

- Diffusion conditionnelle : `CandidateFilter` (§4.1) et raison `condition_unmet` réservés ; l'UI affiche toute raison inconnue via une clé générique.
- Alertes : un forçage d'origine module (`SourceRef.kind` extensible, valeur future `alert`) sera rendu comme un forçage ; aucune valeur ajoutée en V1.
- Statistiques de diffusion réelle (preuve de lecture) : la timeline est **prévisionnelle** ; une piste « Réellement joué » viendra d'un futur module, hors de ce lot.
- Mises en page : `ContentRef.kind = "layout"` réservé, renseigné quand `docs/architecture-mises-en-page.md` sera implémenté.

## 7. Découpage en tâches

| Id | Tâche | Agent | Critère de fin |
| --- | --- | --- | --- |
| [x] P0-1 | Documenter l'ordre exact du résolveur (§2) puis extraire `resolve()` pur + `load_inputs()` mutualisé ; `ScheduleBuilder` l'appelle | builder (serveur) | Documents player identiques (révision égale) sur les tests existants + jeu d'or (chevauchement, héritage, forçage, expiration en cours de créneau, minuit, changements d'heure) ; mypy/ruff verts ; §2 mis à jour |
| [x] P0-2 | Mode `explain` : `losers`, `reason`, `Conflict`, `Marker` | builder (serveur) | Tests unitaires purs par code de raison et par type de conflit ; `explain=False` ne change pas le document player |
| [x] P0-3 | `priority_level` dans `ScheduleOut` + `vds_server.diffusion.priority` | grunt | Tests de bandes (−1, 0, 99, 100) ; `make api-types` regénéré |
| P1-1 | `GET /planning/timeline` et `GET /planning/at` (bornes, pagination, ETag/304, périmètre editor/admin) | builder (serveur) | Tests HTTP aux deux `VDS_BASE_PATH` (`/` et `/espace/affichage`) ; 100 écrans × 42 jours < 1 s sur la base de test ; nombre de requêtes SQL constant (test) |
| P1-2 | `POST /planning/simulate` | builder (serveur) | Validations identiques à `POST/PATCH /schedules` ; aucune ligne écrite (test) ; contrôle de même origine testé (403 sans Origin valide) aux deux chemins de base |
| P1-3 | `PlanningPage`, `PlanningToolbar`, vue Liste, `InstantInspector`, `PriorityPicker` | builder (admin) | Vitest sur rendu des raisons/niveaux ; aucune fonction de résolution dans `apps/admin` (revue) ; clés FR/EN complètes (`vds-i18n`) |
| P1-4 | `TimelineWeek`/`TimelineDay`, `SegmentBlock`, `NowCursor`, `ExpiryMarker`, `ConflictPanel` | builder (admin) | Navigation clavier complète ; axe AA (Playwright + axe) ; lisible à 375 px (Liste par défaut) ; aucune ombre (`vds-ui-charte`) |
| P2-1 | `ResourceTimeline` (groupe/site) + `MonthGrid` | builder (admin) | 100 écrans fluides (virtualisation) ; pagination ; `mixed_timezones` signalé |
| P2-2 | `ScheduleWizard` avec simulation et modèles de créneaux | builder (admin) | Parcours design n° 3 bout à bout ; annonces `aria-live` ; phrases FR/EN issues de `changes` |
| P2-3 | Aperçu des forçages adossé à `resolve` ; fiche écran et tableau de bord via `/planning/at` | builder (serveur + admin) | Forme `OverridePreviewOut` inchangée ; tests existants verts |
| P2-4 | E2E | qa-tests | Playwright : créer, voir masqué, simuler, forcer, expiration visible, aux deux `VDS_BASE_PATH`, FR et EN |
| P2-5 | Revue | revue-securite | Périmètre editor/admin, bornes anti-déni de service (fenêtre, pagination), simulate sans effet de bord |
| P3 | Glisser-déposer pour déplacer/étirer un créneau (alternative clavier obligatoire) | builder (admin) | Après validation de Victor |

## 8. Questions ouvertes (recommandation en premier)

1. **Bandes de priorité** : Fond < 0 ≤ Normale < 100 ≤ Prioritaire, valeurs par défaut −100 / 0 / 100. Recommandé (aucune migration, 0 reste Normale).
2. **Ordre priorité vs spécificité** : conserver l'ordre actuel du résolveur (à confirmer par P0-1) et seulement l'expliquer. Le changer modifierait ce que jouent des écrans déjà en service.
3. **Vue groupe/site** : une ligne par écran (vérité exacte, 100 écrans par page) plutôt qu'une synthèse agrégée (trompeuse dès que les écrans d'un groupe divergent). Recommandé.
4. **Glisser-déposer** : reporté en P3 ; V1 = clic sur créneau vide → assistant pré-rempli.
5. **Fuseau d'affichage** : celui de la cible (site) avec mention s'il diffère du navigateur. Recommandé.

## 9. Renvois à insérer (fichiers non modifiables par l'architecte lors de la rédaction)

`docs/architecture.md`, après la section « Contenus et diffusion » :

> ### Planning : calendrier, aperçu, simulation (décision 2 novembre 2026)
> Voir `docs/architecture-planning.md`. Résolveur unique extrait de `ScheduleBuilder` (`resolve()` pur, mode `explain`), endpoints admin `/api/v1/planning/{timeline,at,simulate}`, niveaux de priorité par bandes, aucune résolution dans l'admin. Sans bump vds-core.

`docs/roadmap.md` › Décisions :

> ### 2 novembre 2026 — Refonte du planning (architecte, sans bump vds-core, sans migration)
> Vue calendrier jour/semaine/mois/liste par écran, groupe ou site ; un seul résolveur serveur (`resolve()` extrait de `ScheduleBuilder`, documents player inchangés) servant la timeline, l'aperçu « à l'instant » et la simulation d'un brouillon ; conflits et provenance calculés côté serveur ; 3 niveaux de priorité par bandes (−100/0/100) + numérique en Avancé ; points d'accroche diffusion conditionnelle (`CandidateFilter`, `condition_unmet`) et alertes non implémentés. Détail : `docs/architecture-planning.md`.

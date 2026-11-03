# Mises en page et calques (lot 4 « Mises en page et diffusion complète »)

Statut : spécification architecte du 2 novembre 2026, aucun code. Contrat cible : **vds-core 1.9.0, additif**. Migration : `0017_mises_en_page`.
Prérequis livrés : forçage (vds-core 1.7.0), médiathèque et expiration (1.8.0, `docs/architecture-mediatheque.md`).

## 1. Décisions

1. **Une mise en page est une donnée du noyau, pas un module.** Le noyau connaît déjà la programmation et le `PlaySlot.layout.zones` du document servi au player (lot 2) ; on ne fait que rendre ce champ éditable. Aucun type de contenu nouveau, aucune logique métier par zone : une zone contient une playlist, point.
2. **Zone = calque.** Il n'y a qu'une seule notion : la zone, rectangle en coordonnées relatives (0..1) avec un ordre `z`. Un « calque » est une zone qui en recouvre une autre (bandeau, logo, incrustation). Pas de type distinct, pas de règle de superposition spéciale.
3. **Un seul résolveur.** Le résolveur de programmation existant reste l'unique endroit qui produit les `ScheduleSlot`. Une programmation (ou un forçage) cible **soit une playlist, soit une mise en page**. Une cible playlist est résolue en mise en page implicite plein écran (zone `main`, comportement actuel inchangé).
4. **Forçage d'écran ou forçage de zone.** Ordre de résolution du créneau inchangé : forçage d'écran > programmation > hors plage. Un forçage **d'écran** (`zone_ids` NULL, comportement 1.7) remplace toute la mise en page (il peut lui-même cibler une mise en page). Un forçage **de zone** (`zone_ids` renseigné, décision de Victor du 2 novembre 2026 : dans la V1) remplace seulement la playlist des zones citées ; le reste de la mise en page continue de jouer sans redémarrer. Il s'applique en dernier, sur tout créneau `play`, que la mise en page vienne d'une programmation ou d'un forçage d'écran. Détail §3 bis.
5. **Coordonnées relatives, orientation déclarée.** Une mise en page déclare `orientation` (`landscape` | `portrait`). Les zones sont en fractions de la surface de l'écran, donc indépendantes de la résolution. Discordance d'orientation avec un écran ciblé : avertissement à la programmation, pas de blocage (décision de Victor, §13 Q1).
6. **Durée et expiration par zone, cohérentes avec la médiathèque.** Une zone a une fenêtre optionnelle `active_from` / `active_until` (UTC) et une durée par défaut des éléments sans durée propre. L'expiration d'une zone est **projetée sur ses éléments** (`until = min(until média, until zone)`) : un player 1.8 qui ignore le champ de zone se comporte déjà correctement (éléments sautés, zone vide).
7. **Zones indépendantes.** Chaque zone boucle sa playlist avec son propre curseur, démarré au début du créneau. Aucune synchronisation entre zones ni entre écrans (murs d'écrans = point d'accroche).
8. **Pas de templates dans le noyau.** Les modèles (plein écran, 2 colonnes, bandeau bas, L, incrustation) sont des constantes de l'admin qui préremplissent un formulaire ; le serveur ne stocke que des mises en page.

## 2. Modèle de données (migration `0017_mises_en_page`, noyau, additive)

```
layout
  id               uuid PK
  org_id           uuid FK organization, NOT NULL
  name             text NOT NULL (1..120)
  orientation      text NOT NULL CHECK IN ('landscape','portrait')
  background_color text NULL CHECK ~ '^#[0-9a-fA-F]{6}$'      -- défaut noir côté player
  background_media_id uuid NULL FK media ON DELETE SET NULL    -- image uniquement (vérifié à l'écriture)
  tags             text[] NOT NULL DEFAULT '{}'                -- même règle que media.tags (médiathèque §2)
  revision         integer NOT NULL DEFAULT 1                  -- incrémenté à chaque écriture (layout ou zones)
  created_at, updated_at, created_by
  UNIQUE (org_id, name)

layout_zone
  id               uuid PK
  layout_id        uuid FK layout ON DELETE CASCADE, NOT NULL
  key              text NOT NULL CHECK ~ '^[a-z0-9_-]{1,32}$'  -- identifiant stable servi au player
  name             text NOT NULL (1..80)
  x, y, w, h       integer NOT NULL  -- en dix-millièmes (0..10000) ; CHECK w>0, h>0, x+w<=10000, y+h<=10000
  z                smallint NOT NULL DEFAULT 0 CHECK 0..99
  playlist_id      uuid NULL FK playlist ON DELETE SET NULL    -- zone vide autorisée (fond visible)
  fit              text NOT NULL DEFAULT 'contain' CHECK IN ('contain','cover','stretch')
  default_item_duration_ms integer NULL CHECK 1000..86400000
  active_from      timestamptz NULL
  active_until     timestamptz NULL  CHECK active_until IS NULL OR active_from IS NULL OR active_until > active_from
  UNIQUE (layout_id, key)
  INDEX (layout_id, z)

schedule.layout_id  uuid NULL FK layout ON DELETE RESTRICT       -- additif
schedule  CHECK (num_nonnulls(playlist_id, layout_id) = 1)        -- playlist_id devient NULLable
override.layout_id  uuid NULL FK layout ON DELETE RESTRICT       -- même règle XOR que schedule, si override porte un contenu
override.zone_ids   text[] NULL                                   -- additif ; NULL = forçage d'écran (1.7)
override  CHECK (zone_ids IS NULL OR cardinality(zone_ids) BETWEEN 1 AND 32)
override  CHECK (zone_ids IS NULL OR (playlist_id IS NOT NULL AND layout_id IS NULL))  -- zone = playlist seulement
override  CHECK (zone_ids IS NULL OR <colonne de nature du forçage> = <valeur « contenu »>) -- jamais de forçage d'alimentation par zone
```

`zone_ids` contient des **clés** de zone (`layout_zone.key`, = `LayoutZone.id` servi au player, `"main"` pour la mise en page implicite), pas des UUID : un forçage de zone vise « la zone `bandeau` » de toute mise en page qui en a une, sans FK ni dépendance à une mise en page précise. Format de chaque élément, unicité et borne `core.layout.max_zones` vérifiés par l'API (§8) ; la borne SQL 32 est le plafond absolu.

Stockage entier (dix-millièmes) pour éviter les comparaisons flottantes ; l'API et le document player exposent des **flottants 0..1 à 4 décimales**. Limite par mise en page : `core.layout.max_zones` (§7). Le builder L0 vérifie les noms réels des tables `schedule` / `override` et l'unité actuelle de `LayoutZone` (attendu : fractions 0..1, zone `main` = 0,0,1,1) avant d'écrire la migration.

## 3. Héritage et expiration

- **Cible** : une mise en page se programme sur organisation, site, groupe ou écran exactement comme une playlist (même résolution de cible, même priorité). Rien de nouveau dans la configuration héritée.
- **Durée d'un élément** (sans durée propre, ex. image) : `item.duration_ms` > `zone.default_item_duration_ms` > valeur résolue `core.default_item_duration_ms` (provenance par l'API de configuration existante). Le résolveur calcule la valeur et l'écrit dans `duration_ms` ; le player n'invente rien.
- **Expiration** :
  - média expiré : inchangé (médiathèque §3, élément sauté via `until`) ;
  - zone hors fenêtre : le résolveur **omet** la zone si `active_until <= slot.start` ou `active_from >= slot.end` ; sinon il émet `from` / `until` sur la zone **et** projette `until` sur chaque élément (`min`) ; un `active_from` futur dans le créneau est servi tel quel (le player démarre la zone à cette heure, hors ligne) ;
  - playlist vide ou toutes ses entrées expirées : la zone reste, transparente (fond ou calques inférieurs visibles), jamais d'erreur affichée au public.
- **Purge** : supprimer une playlist met `layout_zone.playlist_id` à NULL (zone vide) et publie `layout.updated` ; supprimer un média de fond met `background_media_id` à NULL. Supprimer une mise en page programmée est refusé (409 `layout_in_use`, avec l'usage), cohérent avec l'onglet Usage de la médiathèque.

## 3 bis. Forçage de zone

- **Portée** : mêmes cibles (organisation, site, groupe, écran), même fenêtre `début`/`fin` et même arrêt anticipé (`/overrides/{id}/stop`) que le forçage d'écran 1.7. Seule différence : `zone_ids` + `playlist_id`.
- **Application** : pour chaque créneau `play` d'un écran, après résolution de la mise en page (programmation ou forçage d'écran), le résolveur remplace, pour chaque zone dont la clé est citée, la playlist de la zone **pendant l'intersection** fenêtre du forçage ∩ créneau ∩ fenêtre de la zone. Géométrie, `z`, `fit` de la zone conservés. Le créneau n'est **pas découpé** : les autres zones gardent leur curseur.
- **Plusieurs forçages sur une même zone** : même règle de priorité que les forçages d'écran existants (cible la plus précise, puis le plus récent) ; le résolveur produit une frise sans chevauchement (`LayoutZone.overrides[]`, §4).
- **Zone absente de la mise en page résolue** (tranché) : le forçage est **ignoré pour ce créneau sur cet écran**, aucun calque inventé. À la création, l'API répond `warnings: ["override_zone_missing"]` avec le nombre d'écrans ciblés dont la mise en page courante ou programmée sur la fenêtre n'a pas la zone ; l'admin l'affiche avant validation. Justification : le player n'invente jamais de géométrie (un bandeau de 10 % servi en calque plein écran masquerait tout le contenu, ou une position arbitraire chevaucherait l'existant) ; le résultat reste prévisible et visible dans l'aperçu. Pour couvrir tout l'écran quelle que soit la mise en page, l'admin utilise un forçage d'écran. Calque d'alerte superposé = point d'accroche du module alertes (§11).
- **Hors plage, hors service prévu, extinction** : un forçage de zone n'allume pas un écran et ne crée pas de créneau `play` ; il n'agit que sur les créneaux `play` existants (sinon un forçage d'écran est requis).
- **Fin** : à `fin`, le player rend la playlist d'origine à la zone sur son horloge locale (hors ligne), en reprenant son curseur au début de la playlist d'origine. `/stop` ou suppression : recalcul, nouvelle `revision`, le player applique le document sans redémarrer les zones inchangées. Disparition de la zone (mise en page modifiée) : idem via `layout.updated`.
- **Purge** : la playlist d'un forçage de zone suit exactement la règle existante de la playlist d'un forçage d'écran (vérifiée par L0, pas de règle nouvelle).

## 4. Contrat vds-core 1.9.0 (additif) et note de compatibilité

`CORE_API_VERSION = "1.9.0"`, `SCHEDULE_FORMAT` reste `1`.

```ts
// packages/sdk-ts/src/player.ts — champs ajoutés, tous optionnels
export interface PlaySlot {
  layout: {
    zones: LayoutZone[];               // TOUJOURS triées par z croissant puis key (un player 1.8 empile dans l'ordre du tableau)
    id?: string | null;                // 1.9.0 : null = mise en page implicite (cible playlist)
    revision?: number;
    orientation?: "landscape" | "portrait";
    background?: { color?: string; asset?: string } | null;   // asset = clé de `assets`
  };
  source: { schedule_id: string | null; kind?: "schedule" | "override"; override_id?: string | null;
            layout_id?: string | null };  // 1.9.0
}

export interface LayoutZone {
  id: string;            // = layout_zone.key ("main" pour l'implicite)
  x: number; y: number; w: number; h: number;   // fractions 0..1
  playlist: string;      // clé de `playlists` ; playlist vide {items: []} si zone sans playlist
  z?: number;            // 1.9.0, défaut 0
  fit?: "contain" | "cover" | "stretch";        // 1.9.0, défaut "contain"
  from?: string | null;  // 1.9.0, ISO UTC : la zone apparaît à cet instant
  until?: string | null; // 1.9.0, ISO UTC : la zone disparaît à cet instant
  overrides?: ZoneOverrideWindow[];  // 1.9.0 : forçages de zone, triés par from, sans chevauchement, bornés au créneau
}

export interface ZoneOverrideWindow {  // 1.9.0
  override_id: string;
  playlist: string;      // clé de `playlists` (fusionnée comme les autres)
  from: string;          // ISO UTC, >= max(slot.start, zone.from)
  until: string;         // ISO UTC, <= min(slot.end, zone.until) ; toujours renseigné (borné au créneau)
}

// packages/sdk-ts/src/content.ts — ContentContext, additif
zone: { width: number; height: number; id?: string };   // 1.9.0 : id de zone (statistiques futures)
```

```python
# packages/sdk-py/src/vds_sdk/events.py
CORE_EVENT_DOMAINS_1_9 = CORE_EVENT_DOMAINS_1_8 | {"layout"}
CORE_EVENTS_1_9 = CORE_EVENTS_1_8 | {"layout.created", "layout.updated", "layout.deleted"}
CORE_EVENT_PAYLOAD_1_9 = CORE_EVENT_PAYLOAD_1_8 | {
    "layout.created": ("layout_id",),
    "layout.updated": ("layout_id", "revision"),
    "layout.deleted": ("layout_id",),
}
# permissions (vds_sdk.permissions) : "layouts:read", "layouts:write"
# forçage de zone : les payloads des événements override.* existants gagnent un champ optionnel
#   "zone_ids": list[str] | None   (None = forçage d'écran) — additif, aucun nouvel événement
# aucune nouvelle permission : forçage de zone = permission de forçage existante
```

Compatibilité forçage de zone : un player 1.8 ignore `LayoutZone.overrides` et joue le contenu programmé (dégradé sûr, rien d'inattendu au public). Le résolveur, la frise et le `revision` sont côté serveur ; seul le player doit être ≥ 1.9 pour afficher le forçage. L'admin signale « forçage non appliqué » sur les écrans dont la version de player connue est < 1.9.

Compatibilité : un player ou module 1.8 ignore les champs ajoutés. Garanties côté serveur pour les players 1.8 : zones déjà triées par `z`, expiration de zone projetée sur les éléments, `fit` par défaut identique au rendu actuel. Aucun module existant n'a à changer ; un module qui écoute `schedule.*` reçoit toujours `schedule_id`. Seule rupture potentielle : `schedule.playlist_id` devient NULLable dans l'API REST (lecture) ; les types générés (`make api-types`) le reflètent, l'admin est mis à jour dans le même lot.

## 5. Résolveur

Une seule fonction étendue, pas de second chemin :

```python
# apps/server : module de résolution existant
async def resolve_play_content(
    session: AsyncSession, *, org_id: UUID, playlist_id: UUID | None, layout_id: UUID | None,
    slot_start: datetime, slot_end: datetime, defaults: ResolvedDefaults,
    zone_overrides: Sequence[ActiveZoneOverride] = (),
) -> ResolvedLayout: ...
# ResolvedLayout = (layout dict pour PlaySlot.layout, playlists à fusionner, assets à fusionner,
#                   missing_zone_override_ids: set[UUID])  # pour l'indicateur admin / l'aperçu

@dataclass(frozen=True)
class ActiveZoneOverride:
    override_id: UUID; zone_ids: frozenset[str]; playlist_id: UUID
    starts_at: datetime; ends_at: datetime | None; priority: tuple  # clé de tri de la règle existante

async def select_zone_overrides(
    session: AsyncSession, *, screen_id: UUID, window_start: datetime, window_end: datetime,
) -> list[ActiveZoneOverride]: ...   # même requête de ciblage que les forçages d'écran, filtrée zone_ids IS NOT NULL
```

- Appelée pour chaque créneau `play`, qu'il vienne d'une programmation ou d'un forçage.
- `playlist_id` → zone implicite `{"id":"main","x":0,"y":0,"w":1,"h":1,"playlist":…}`, `layout.id = null` : document identique au comportement 1.8.
- `layout_id` → zones filtrées (§3), triées `(z, key)`, playlists et assets de **toutes** les zones et du fond fusionnés dans `playlists` / `assets` (clés existantes, dédupliquées). Le pré-cache du player et le cache de site couvrent donc tout sans changement.
- `revision` du document : inclut `layout.revision` de chaque mise en page servie ; `layout.updated` déclenche le recalcul des écrans concernés par le même mécanisme que `playlist.updated`.
- Discordance d'orientation : la zone est servie telle quelle (coordonnées relatives), aucune transformation ; l'avertissement est côté admin.
- Forçages de zone (§3 bis) : appliqués après la résolution de la mise en page, y compris implicite (`main`)  ; seuls les forçages d'écran (`zone_ids IS NULL`) découpent les créneaux, la requête existante est filtrée en conséquence ; un forçage de zone ne découpe jamais un créneau. Leurs playlists et assets sont fusionnés et pré-cachés. Leur `id` et leurs bornes entrent dans la `revision`.

## 6. Player

- **Scène** : un conteneur plein écran ; chaque zone est un conteneur absolu `left/top/width/height` en pourcentage, `z-index = z`, `overflow: hidden`. Valeurs numériques uniquement, jamais de chaîne CSS venue du serveur (couleur validée par regex avant usage).
- **Lecture** : une instance de lecteur de playlist par zone (le lecteur actuel, factorisé), curseur indépendant, démarrage au `start` du créneau ou à `from`, arrêt et retrait à `until` (horloge locale, hors ligne).
- **Fond** : couleur puis image (`fit: cover`) sous toutes les zones.
- **Ressources exclusives** : au plus `player.max_exclusive_elements` éléments `exclusive` (décodeur vidéo) actifs à la fois, toutes zones confondues (défaut 1, TV Samsung réelle) ; au-delà, l'élément est sauté comme un élément en erreur, sans message au public. Les vues web natives restent limitées à 2 (`docs/architecture-content-url-natif.md`) ; une 3e zone `content.url` retombe sur l'iframe.
- **Kiosque et public** : aucune information technique, aucune bordure ni nom de zone affichés ; zone vide = transparente.
- **Changement de mise en page** entre deux créneaux : bascule nette (pas de transition inter-zones en V1).
- **Forçage de zone** : le lecteur de la zone suit la frise `overrides[]` sur l'horloge locale ; à `from` il passe sur la playlist forcée (début de playlist), à `until` il revient à la playlist de la zone (début). Les autres zones ne sont pas touchées.
- **Nouveau document pendant un créneau** (`/stop`, `layout.updated`…) : réconciliation par `LayoutZone.id` ; une zone dont `(playlist, fit, géométrie, overrides)` est inchangée garde son lecteur et son curseur ; seules les zones modifiées sont recréées.

## 7. Configuration (`core`, provenance par l'API existante)

| Clé | Type | Défaut | `x-vds-level` |
| --- | --- | --- | --- |
| `core.layout.max_zones` | integer 1..32 | 12 | organization |
| `core.default_item_duration_ms` | integer | existant si déjà défini, sinon 10000 | organization, site, group, screen |
| `player.max_exclusive_elements` | integer 1..4 | 1 | organization, site, group, screen |

## 8. API REST admin

Toutes sous `{VDS_BASE_PATH}/api`, écritures sous cookie soumises au contrôle de même origine existant.

| Méthode | Chemin | Permission | Détail |
| --- | --- | --- | --- |
| GET | `/layouts?q=&tag=&orientation=` | `layouts:read` | liste paginée, avec `zone_count`, `usage_count` |
| POST | `/layouts` | `layouts:write` | corps `LayoutIn` (zones incluses), 201 |
| GET | `/layouts/{id}` | `layouts:read` | `LayoutOut` |
| PUT | `/layouts/{id}` | `layouts:write` | remplacement complet des zones, `If-Match: <revision>` obligatoire (412 si conflit) |
| POST | `/layouts/{id}/duplicate` | `layouts:write` | copie « Nom (copie) » |
| DELETE | `/layouts/{id}` | `layouts:write` | 409 `layout_in_use` si programmée ou forcée |
| GET | `/layouts/{id}/usage` | `layouts:read` | programmations, forçages, écrans touchés, écrans d'orientation différente |
| GET | `/layouts/{id}/preview` | `layouts:read` | `ScheduleDocument` à un seul créneau d'1 h, `screen` fictif, pour l'aperçu admin |

```python
class LayoutZoneIn(BaseModel):
    key: constr(pattern=r"^[a-z0-9_-]{1,32}$")
    name: constr(min_length=1, max_length=80)
    x: confloat(ge=0, le=1); y: confloat(ge=0, le=1)
    w: confloat(gt=0, le=1); h: confloat(gt=0, le=1)     # x+w<=1, y+h<=1 (validateur), arrondi 4 décimales
    z: conint(ge=0, le=99) = 0
    playlist_id: UUID | None = None                       # même organisation, sinon 422
    fit: Literal["contain", "cover", "stretch"] = "contain"
    default_item_duration_ms: conint(ge=1000, le=86_400_000) | None = None
    active_from: AwareDatetime | None = None
    active_until: AwareDatetime | None = None

class LayoutIn(BaseModel):
    name: constr(min_length=1, max_length=120)
    orientation: Literal["landscape", "portrait"]
    background_color: constr(pattern=r"^#[0-9a-fA-F]{6}$") | None = None
    background_media_id: UUID | None = None               # média image de l'organisation, sinon 422
    tags: list[str] = []
    zones: list[LayoutZoneIn]                              # 1..core.layout.max_zones, keys uniques

class LayoutOut(LayoutIn):
    id: UUID; revision: int; created_at: datetime; updated_at: datetime
```

Programmations et forçages : `ScheduleIn` / `OverrideIn` acceptent `layout_id` en alternative exclusive à `playlist_id` (422 `content_target_ambiguous` si les deux ou aucun).

Forçage de zone, ajout additif à `OverrideIn` / `OverrideOut` :

```python
ZoneKey = constr(pattern=r"^[a-z0-9_-]{1,32}$")
class OverrideIn(BaseModel):  # champs existants inchangés
    zone_ids: list[ZoneKey] | None = None   # 1..core.layout.max_zones, uniques ; None = forçage d'écran
    # validateur : zone_ids => playlist_id requis, layout_id interdit (422 override_zone_requires_playlist),
    #              nature « contenu » uniquement (422 override_zone_not_content)
```

- `POST /overrides/preview` (existant) renvoie en plus `zone_coverage: [{zone_id, screens_total, screens_missing}]` et la liste des clés de zone rencontrées dans les mises en page des écrans ciblés sur la fenêtre (`zone_candidates: [{key, name, layouts: int}]`), pour le sélecteur de l'admin.
- Création : `warnings: ["override_zone_missing"]` + `screens_missing` si au moins un écran n'a pas la zone ; jamais bloquant.
- `GET /overrides` et la fiche écran exposent `zone_ids` ; la liste des forçages actifs filtre `?scope=screen|zone`.
- `GET /layouts/{id}/usage` liste aussi les forçages de zone actifs ou futurs visant une clé de la mise en page. Réponse de création : champ `warnings: ["layout_orientation_mismatch"]` avec le nombre d'écrans concernés. Erreurs typées : `layout_zone_out_of_bounds`, `layout_too_many_zones`, `layout_zone_key_duplicate`, `layout_background_not_image`, `layout_in_use`, `layout_revision_conflict`.

## 9. Admin

- **Place** : Contenus › Mises en page (`docs/refonte/ux.md` §2.1), route `/content/layouts` et `/content/layouts/:id`, toutes via `VDS_BASE_PATH`.
- **Liste** : cartes avec vignette SVG des rectangles (couleurs tonales MD3, sans ombre), nom, orientation, nombre de zones, usage. Vide : « Aucune mise en page. Créer à partir d'un modèle ».
- **Création** : choix d'un modèle (constantes admin : plein écran, 2 colonnes, 2 lignes, bandeau bas, L, incrustation coin) + orientation.
- **Éditeur** : canevas au ratio 16:9 ou 9:16 ; déplacement et redimensionnement à la souris, pas de grille 1 % (Maj : libre), **alternative clavier complète** (flèches = déplacer, Maj+flèches = redimensionner) ; panneau Zones (ordre `z` par boutons « Monter / Descendre » et glisser-déposer), panneau Propriétés (nom, playlist, ajustement, durée par défaut, fenêtre d'affichage avec dates, coordonnées numériques en %). Avertissements actionnables : « 2 zones contiennent des vidéos : un seul décodeur sur la plupart des TV, la 2e vidéo sera sautée ». Aperçu : player en mode aperçu dans un `iframe` alimenté par `/layouts/{id}/preview`.
- **375 px** : pas d'édition au doigt du canevas ; liste des zones + champs numériques, canevas en lecture seule.
- **Programmer** (assistant ux §2.3) : l'étape « Quoi » propose playlist ou mise en page ; l'étape « Où » affiche l'avertissement d'orientation.
- **Formulaire de forçage** (existant) : choix « Tout l'écran » (défaut, 1.7) / « Certaines zones » ; dans le second cas, sélection multiple parmi `zone_candidates` (nom + clé en mono + nombre de mises en page), playlist seulement (pas de mise en page), couverture « s'applique à N écrans sur M » et avertissement `override_zone_missing` avant validation.
- **Indicateur de forçage de zone** : pastille tonale « Zone forcée : <nom> » dans la liste des forçages actifs (`ActiveOverrides`), sur la fiche écran et sur l'aperçu (contour de la zone, admin uniquement, jamais au public) ; mention « non appliqué » si zone absente ou player < 1.9. Bouton Arrêter identique au forçage d'écran.
- **Usage** : onglet sur la fiche mise en page, et la fiche playlist/média liste aussi les mises en page qui l'utilisent (même composant que la médiathèque).
- i18n : clés `layouts.*`, `layouts.templates.*`, `layouts.errors.<code>` FR/EN ; mono réservée aux coordonnées numériques et identifiants.

## 10. Sécurité

- Isolation organisation sur `layout`, `layout_zone.playlist_id`, `background_media_id`, et sur la référence `layout_id` des programmations et forçages (422, jamais de fuite d'existence : même code que « inconnu »).
- Bornes stricte des coordonnées, du nombre de zones et des chaînes (DoS du résolveur et du player).
- Aucune valeur libre injectée en CSS/HTML côté player : nombres bornés, couleur par regex, noms de zone jamais rendus au public.
- Aperçu : sous session admin, lecture seule, ne passe pas par le jeton d'appareil ; le Service Worker du player n'est pas enregistré en mode aperçu.
- Écritures : contrôle de même origine (Origin / Sec-Fetch-Site) dans les deux modes, `If-Match` contre l'écrasement concurrent.
- Kiosque : rien de nouveau exposé au public.
- Forçage de zone : `zone_ids` bornés (format, unicité, nombre) ; `playlist_id` isolé par organisation comme le forçage d'écran ; aucune nouvelle permission, aucune fuite (une clé de zone inexistante n'est jamais une erreur révélant les mises en page d'autres organisations : la couverture n'est calculée que sur les écrans de l'organisation) ; création et arrêt tracés dans le journal d'audit existant des forçages ; impossible d'éteindre ou d'allumer un écran par un forçage de zone (CHECK §2).

## 11. Points d'accroche (non implémentés)

| Futur module | Point d'accroche préservé |
| --- | --- |
| Tactile / interactif | `LayoutZone` accepte de futurs champs optionnels (`interaction`) ; le player ne gère aucune entrée en V1 |
| Murs d'écrans | `layout.id` + `revision` servis au player ; une future « toile » découpera une mise en page en `LayoutZone` par écran via le même résolveur. Aucune synchro en V1 |
| Statistiques | `ContentContext.zone.id` et `LayoutZone.id` permettront un futur événement de preuve de diffusion par zone ; aucun événement émis en V1 |
| Diffusion conditionnelle | un module pourra fournir un filtre sur `resolve_play_content` (zone ou élément gardé selon condition) ; aucun hook appelé en V1 |
| Alertes | le forçage de zone est livré en V1 ; le module alertes ajoutera un calque superposé au `z` maximal même sans zone prévue (géométrie fournie par l'alerte), déclenché par événement : `LayoutZone.overrides` et `source.kind` le permettent ; non implémenté |

## 12. Découpage en tâches

| Tâche | Agent | Contenu | Critère de fin |
| --- | --- | --- | --- |
| L0 | builder | Vérifier l'existant (unité de `LayoutZone`, tables schedule/override), migration `0017_mises_en_page` (dont `override.zone_ids` et ses CHECK), modèles SQLAlchemy ; relever la colonne de nature du forçage et la règle de purge de sa playlist | `alembic upgrade head` puis `downgrade -1` propres ; schedules et forçages existants intacts (`zone_ids` NULL) ; CHECK rejettent zone + layout et zone + alimentation |
| L1 | grunt | Contrat 1.9.0 : `CORE_API_VERSION`, `CORE_EVENTS_1_9`, payloads (dont `zone_ids` optionnel sur `override.*`), permissions, types TS `player.ts` (`ZoneOverrideWindow`) / `content.ts` | tests sdk-py (noms d'événements, compat 1.8) et `tsc` verts |
| L2 | builder | API `/layouts` (§8) + `layout_id` dans programmations et forçages, `zone_ids` dans `OverrideIn`, `zone_coverage`/`zone_candidates` dans `/overrides/preview`, événements `layout.*`, purge §3 | pytest : CRUD, bornes, isolation org, 409 `layout_in_use`, 412 `If-Match`, XOR playlist/layout ; 422 zone + layout, zone + alimentation, clé invalide, doublon, trop de zones ; `override_zone_missing` |
| L3 | builder | Résolveur `resolve_play_content` + `select_zone_overrides` (§5, §3 bis) | pytest : cible playlist = document 1.8 identique (snapshot) ; tri `z` ; zone expirée omise ; `until` projeté ; forçage d'écran prime ; `layout.updated` change la révision ; forçage de zone : créneau non découpé, autres zones identiques, frise sans chevauchement (priorité), zone absente ignorée et signalée, pas d'effet hors `play`, appliqué sur forçage d'écran, sur `main`, `/stop` change la révision |
| L4 | builder | Player : scène multi-zones, lecteur par zone, fond, `from`/`until`, limite `exclusive`, frise `overrides[]`, réconciliation par `LayoutZone.id` | vitest + Playwright : 3 zones dont un calque, hors ligne (réseau coupé) jusqu'à la bascule suivante ; portrait et paysage ; forçage de zone début et fin hors ligne sans redémarrer les autres zones ; nouveau document sans saut des zones inchangées |
| L5 | builder | Admin : liste, modèles, éditeur clavier/souris, aperçu, usage, assistant Programmer, formulaire de forçage « Certaines zones », indicateur de zone forcée | Playwright sous `VDS_BASE_PATH=/` et `/espace/affichage` ; FR et EN sans clé manquante ; 375 px ; forçage de zone créé, affiché, arrêté |
| L6 | grunt | `make api-types`, clés i18n, docs utilisateur | `make lint` vert |
| L7 | qa-tests | Recette transverse : TV Samsung (1 décodeur), Android, Pi | checklist de démo complétée |
| L8 | revue-securite | §10 | aucun point critique ouvert |

## 13. Décisions de Victor (2 novembre 2026)

- **Q1 Orientation discordante** : avertir sans bloquer (retenu).
- **Q2 Plusieurs vidéos simultanées** : `player.max_exclusive_elements = 1` par défaut, relevable par écran (retenu).
- **Q3 Forçage d'une zone** : **dans la V1** (la recommandation « hors V1 » est rejetée). Spécifié en §2, §3 bis, §4, §5, §6, §8, §9, §10. Zone absente : forçage ignoré sur l'écran concerné, avertissement à la création (arbitrage architecte, §3 bis).

Questions restantes : (a) un forçage de zone doit-il pouvoir agir hors plage / hors service prévu (bandeau d'urgence sur écran éteint) ? Proposé : non, utiliser un forçage d'écran. (b) Zone absente : confirmer « ignorer + avertir » plutôt qu'un calque plein écran de secours.

## 14. Notes L0 (vérification de l'existant, 3 novembre 2026)

- **Tables** : programmation = `schedule` (+ `schedule_rule`) ; forçage = `playback_overrides` (modèle `PlaybackOverride`, `db/models_override.py`), pas `override`. La migration `0017` ajoute `zone_ids` / `layout_id` sur `playback_overrides`.
- **Nature du forçage** : colonne `kind` (`'playlist'` | `'off'`, CHECK `kind`), plus CHECK `(kind='off') = (playlist_id IS NULL)`. Le CHECK « zone + alimentation » devient `zone_ids IS NULL OR kind='playlist'` ; « zone + layout » est à écrire sur la nouvelle colonne `layout_id`. Le forçage « alimentation » est `kind='off'` (pas de notion séparée).
- **Purge de la playlist d'un forçage** : le balayeur (`overrides/sweeper.py`, `purge`) supprime les forçages terminés depuis plus de `override.retention_days` (config noyau), la playlist éphémère part en cascade ; l'audit n'est pas purgé. La règle de §3 bis s'applique telle quelle.
- **LayoutZone actuel** (`player/schemas.py`, `player/schedule.py`) : fractions flottantes 0..1, zone implicite `{"id":"main",x:0,y:0,w:1,h:1}` ; seuls `id,x,y,w,h,playlist` existent (pas de `z`, `fit`, `from`, `until`) : conforme au contrat 1.9.0, additif. `Layout` serveur n'a que `zones` (id/revision/orientation/background à ajouter en L3).
- **Permissions** : le catalogue existant est en notation pointée (`media.read`, `override.write`) ; 1.9.0 ajoute `layouts.read` / `layouts.write` (et non `layouts:read|write`). Les « domaines d'événements » ne sont définis qu'en 1.1 et 1.2 ; `CORE_EVENT_DOMAINS_1_9` repart de `_1_2` (+ `override`, `layout`).
- **Contrat livré (L0)** : `vds-core 1.9.0` côté `sdk-py` et `sdk-ts` (L1 est donc absorbée) ; `zone_ids` sur `override.*` est documenté, non requis ; aucun code serveur ni migration.

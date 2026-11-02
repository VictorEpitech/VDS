# Médiathèque : vues, tags, expiration, purge, remplacement, usage (lot 4 de la refonte Xibo)

Statut : décidé le 2026-11-02 (architecte, sur arbitrages de Victor D1, D2, D4, D6 consignés dans `docs/refonte/xibo-ecarts.md` §5). Aucun code. Contrat : **vds-core 1.8.0, additif**. Migration : **0016**.
Sources lues : `db/models.py` (`Media`, `MediaVariant`, `Playlist`, `PlaylistItemMedia` en `RESTRICT`), `db/models_override.py` (`playlist_id` sans `ondelete`), `diffusion/media.py`, `diffusion/media_files.py` (envoi, `_VariantStore`), `diffusion/access.py`, `player/schedule.py` (`ScheduleBuilder`, `screen_media_ids`, `revision_of`), `agents/cache.py` (manifeste par sha256), `modules/storage.local/server/module.py` (`_KEY_RE`), `vds_sdk/server/storage.py`, `docs/architecture.md` › Diffusion et player, `docs/architecture-forcage.md`.

## 1. Décisions

1. **Tout reste dans le noyau existant « diffusion »** (médias et playlists y sont déjà) ; aucune nouvelle notion de noyau. Les miniatures sont produites par les **modules content-type** (variante `thumb`), le stockage par le module `storage` actif. Pas de dossiers (D1), pas de campagnes (D4).
2. **Expiration = instant UTC absolu** `expires_at` sur `media` et `playlist` (null = jamais). L'UI saisit date + heure (défaut 23:59) dans le fuseau `core.timezone` de l'organisation ; le serveur stocke l'instant.
3. **Arrêt de diffusion à l'instant exact, y compris hors ligne**, sans nouveau format :
   - playlist expirée : la programmation et les forçages qui la portent **se terminent à `expires_at`** dans le résolveur (point de découpe) ; le player passe seul au créneau suivant ;
   - média expiré : chaque élément de playlist porte un champ optionnel **`until`** (= min des `expires_at` de ses médias) ; le player saute l'élément à partir de cet instant (horloge corrigée par `server_time`).
4. **Purge différée** : expiration constatée par un balayeur (`expired_at`, événement, audit), puis purge base + stockage après `core.content.expired_retention_days` (défaut 30). Pendant la rétention, l'objet reste visible (badge « Expiré »), restaurable en repoussant `expires_at`. L'audit n'est jamais purgé.
5. **Remplacement de fichier = révision du même média** (même `media_id`, donc aucune playlist touchée). Nouveaux fichiers sur des clés physiques distinctes, bascule atomique en base, anciens fichiers supprimés après un délai de grâce. Pas d'historique restaurable en V1 (versions = backlog n°26).
6. **Identité de cache = (clé logique, sha256)**. Les clés d'assets de la trame restent `media/<id>/<variant>` (aucune réécriture, aucun module touché) ; un sha256 différent pour la même clé = asset manquant à retélécharger. Le cache de l'agent est déjà adressé par sha256.
7. **Tags = colonne `text[]`** sur `media` et `playlist` (index GIN), libres, sans hiérarchie. Distincts des futurs tags d'écran (`tag`/`screen_tag`, ciblage, table + FK).
8. **Suppression physique toujours via une file `storage_tombstone`** écrite dans la même transaction que la suppression en base : pas de fichier orphelin si le stockage échoue, aucune méthode `list()` ajoutée à `StorageBackend`.
9. **Droits (D2)** : en V1 inchangés (rôles `admin`/`editor`, organisation unique). Tout accès passe par un point unique `ContentScope` (§6) que le lot J remplira (périmètre site/groupe), jamais d'ACL par objet.
10. **Aucune dépendance au mode** : clés relatives uniquement, URL construites sous `VDS_BASE_PATH`, écritures sous cookie soumises au contrôle de même origine existant.

## 2. Modèle de données (migration `0016_mediatheque`, noyau, additive)

```
media
  + expires_at   timestamptz null
  + expired_at   timestamptz null        -- expiration constatée par le balayeur (idempotence)
  + revision     int not null default 1  CHECK (revision >= 1)
  + tags         text[] not null default '{}'  CHECK (cardinality(tags) <= 20)
  index GIN (tags)
  index (expires_at) WHERE expires_at IS NOT NULL AND expired_at IS NULL
  index (expired_at) WHERE expired_at IS NOT NULL

playlist
  + expires_at, expired_at, tags          -- mêmes règles ; pas de revision
  -- les playlists éphémères de forçage (owner_override_id non null) : expires_at toujours null

storage_tombstone
  id           bigserial pk
  backend      text not null              -- id du module storage (ex. storage.local)
  key          text not null              -- clé relative, toujours issue d'une ligne media_variant
  reason       text not null CHECK (reason IN ('replaced','purged','deleted','aborted'))
  media_id     uuid null                  -- informatif, sans FK
  not_before   timestamptz not null       -- now() ou now() + délai de grâce
  attempts     int not null default 0
  last_error   text null
  created_at   timestamptz
  UNIQUE (backend, key) ; index (not_before)
```

Tags : normalisés côté serveur (trim, minuscules, `^[\p{L}\p{N}][\p{L}\p{N} _-]{0,31}$`), dédoublonnés, triés. Pas de table de tags : l'autocomplétion lit `SELECT DISTINCT unnest(tags)`.

Clés physiques après remplacement : révision 1 inchangée (`<hh>/<id>/<variant>.<ext>`, aucune migration de données) ; révision N ≥ 2 : `<hh>/<id>/<variant>-r<N>.<ext>`. **`storage.local` 1.x (patch de module)** : `_KEY_RE` passe le nom à `[a-z0-9-]{1,48}` ; la contrainte de 40 caractères du nom de variante (`_VariantStore`) est inchangée. `MediaVariant.storage_key` pointe toujours la révision courante : le service `<base>/media/<id>/<variant>` ne change pas.

## 3. Comportement à l'expiration

| Cas | Effet |
| --- | --- |
| Média `expires_at` passé | Éléments qui le référencent : `until` dans la trame, sautés hors ligne ; omis de la trame dès qu'elle est reconstruite après l'instant. Exclu de `screen_media_ids` (manifeste agent, autorisation appareil) : un jeton d'appareil reçoit **404** sur ses variantes ; la session admin le lit encore (aperçu pendant la rétention). |
| Playlist `expires_at` passée | `ScheduleSpec.until = expires_at` : la programmation est inactive au-delà, le gagnant suivant (ou `off`) prend le relais dans le même calcul. Forçage : fin effective = `min(ends_at, playlist.expires_at)` (calculée dans `load_overrides`, résolveur des forçages inchangé). |
| Média expiré encore dans une playlist non expirée | Autorisé. L'éditeur de playlist affiche l'élément barré « Expiré, n'est plus diffusé ». « Où est-il utilisé » le signale. |
| Playlist dont tous les éléments sont expirés | Écran noir pour ce créneau (même règle qu'aujourd'hui quand tous les éléments sont sautés) ; pas de repli automatique sur une autre programmation. Signalé dans l'admin (« Playlist sans élément actif »), point d'accroche supervision. |
| Créer un forçage ou une programmation sur une playlist expirée | 422 `playlist.expired`. Aperçu de forçage : avertissement si la playlist expire avant `ends_at`. |
| Ajouter un média expiré à une playlist | 422 `media.expired`. |
| Prolonger pendant la rétention | `PATCH expires_at` futur ou null : `expired_at` remis à null, audit `media.restored` / `playlist.restored`, diffusion reprise à la reconstruction de trame. |

Purge (balayeur, après `expired_at + core.content.expired_retention_days`) :
- **Média** : dans une transaction, `SELECT … FOR UPDATE` puis revérification ; suppression des `playlist_item` qui le référencent (inactifs depuis toute la rétention), suppression de la ligne `media` (cascade `media_variant`), insertion des clés dans `storage_tombstone` (`reason=purged`, `not_before=now()`), audit `media.purged {title, sha256, bytes, removed_items:[{playlist_id,item_id}]}`, événements `media.purged` + `playlist.updated` par playlist touchée.
- **Playlist** : suppression des `schedule` qui la portent (inactifs depuis l'expiration) puis de la playlist (cascade des éléments) ; audit `playlist.purged {name, removed_schedules}`, événements `schedule.deleted {reason:"playlist_purged"}` + `playlist.purged`. **Reportée** tant qu'une ligne `playback_overrides` la référence (la purge des forçages, `core.override.retention_days`, passe d'abord).
- Lots de 100, verrou consultatif PostgreSQL (multi-workers), idempotent.

Balayeurs (même boucle que celui des forçages) :
- `content.expire` toutes les 60 s : `expires_at <= now() AND expired_at IS NULL` → `expired_at`, audit système, événement, invalidation des trames (même chemin que `playlist.updated` → `schedule.changed` WS + révision au heartbeat).
- `content.purge` toutes les heures (règles ci-dessus).
- `storage.gc` toutes les 5 min : tombstones `not_before <= now()` → `StorageBackend.delete(key)` du backend enregistré ; succès = ligne supprimée ; échec = `attempts+1`, backoff, au-delà de 10 : journal + audit `storage.delete_failed`. Backend désactivé : la ligne attend.

Note : la suppression manuelle existante (`DELETE /media/{id}`, 409 si utilisé) passe aussi par `storage_tombstone` (`reason=deleted`) ; à vérifier : aujourd'hui `MediaService.delete` ne supprime aucun fichier.

## 4. Remplacement de fichier

`POST <base>/api/v1/media/{id}/file` (multipart, mêmes règles que l'envoi : `UploadLimitMiddleware`, type par octets, `expected_sha256` optionnel, `If-Match: "<sha256 courant>"` facultatif (l'admin ne le pose pas encore ; à rendre obligatoire quand il le fera) → 412 si différent).
1. `kind` identique obligatoire (422 `media.replace_kind_mismatch`) ; MIME libre dans le kind. sha256 identique au courant → 200 sans effet.
2. Écriture de l'original sur la clé de révision N+1, `process_media` du module (nouvelles variantes sur clés `-r<N+1>-<hex8>`, suffixe aléatoire unique par tentative : une tentative ratée ne peut jamais viser la clé d'une suivante). Échec de traitement → fichiers N+1 en tombstone `aborted`, **révision courante intacte**, 422 `media.processing_failed`.
3. Transaction : `revision = N+1`, `sha256`, `bytes`, `mime`, dimensions, `original_filename` ; `media_variant` remplacé en bloc ; anciennes clés en tombstone `reason=replaced`, `not_before = now() + core.media.replaced_grace_hours` (défaut 48) pour les téléchargements en cours.
4. Audit `media.replaced {old_sha256, new_sha256, bytes, revision}`, événement `media.replaced`. `revision_of` change pour chaque écran concerné (sha dans `assets`) : rechargement au heartbeat ou par `schedule.changed`.
5. Player : clé identique, sha différent → asset manquant, téléchargé et vérifié avant activation de la révision (règle existante) ; l'ancien octet reste jouable jusque-là. Agent : nouveau sha dans le manifeste → nouveau fichier, l'ancien purgé par sa propre règle.

## 5. Contrat vds-core 1.8.0 (additif) et note de compatibilité

Ajouts :
- Trame player (format 1, champ optionnel) : `playlists.<pl>.items[].until: string | null` (ISO 8601 UTC). `packages/sdk-ts/src/player.ts` : `PlayerItem.until?: string`.
- `vds_sdk.events` (payload v1, `CORE_EVENT_PAYLOAD_1_8`) :

```python
class MediaUpdated(Event):   name = "media.updated";   media_id: UUID; fields: list[str]          # titre, tags, expires_at
class MediaReplaced(Event):  name = "media.replaced";  media_id: UUID; revision: int; sha256: str; previous_sha256: str
class MediaExpired(Event):   name = "media.expired";   media_id: UUID; expires_at: datetime
class MediaRestored(Event):  name = "media.restored";  media_id: UUID
class MediaPurged(Event):    name = "media.purged";    media_id: UUID; playlist_ids: list[UUID]; critical = True
class PlaylistExpired(Event):  name = "playlist.expired";  playlist_id: UUID; expires_at: datetime
class PlaylistRestored(Event): name = "playlist.restored"; playlist_id: UUID
class PlaylistPurged(Event):   name = "playlist.purged";   playlist_id: UUID; schedule_ids: list[UUID]; critical = True
# schedule.deleted : champ optionnel additif reason: Literal["user","playlist_purged"] | None
```
- Façade `ctx.core.media` (permission `media.read`) : `MediaInfo` gagne `revision: int = 1` et `expires_at: datetime | None = None` (champs optionnels). Aucune nouvelle permission, aucun nouveau kind, manifeste inchangé.
- Clés de config `core` (§7).

Compatibilité : modules 1.0 à 1.7 chargent inchangés (aucun champ requis ajouté). Player < 1.8 : ignore `until`, continue un média expiré **hors ligne** jusqu'à la prochaine trame (en ligne : arrêt ≤ 60 s, l'élément disparaît de la trame) ; le player web étant servi par le serveur, il est à jour avec lui (APK et Tizen chargent `<base>/player/`). Agent < 1.8 : aucun changement de wire. `CORE_API_VERSION = "1.8.0"`, `test_compat_18.py`.

## 6. API REST admin

```
GET    /api/v1/media?q=&kind=&status=&tags=&state=&unused=&sort=&limit=&offset=   → MediaPage
GET    /api/v1/media/tags?prefix=                           → list[str] (≤ 50)
GET    /api/v1/media/{id}                                   → MediaOut
PATCH  /api/v1/media/{id}          {title?, tags?, expires_at?}  → MediaOut
POST   /api/v1/media/{id}/file     multipart                 → MediaOut       (remplacement, §4)
GET    /api/v1/media/{id}/usage                             → MediaUsageOut
POST   /api/v1/media/bulk          MediaBulkIn               → MediaBulkOut
DELETE /api/v1/media/{id}                                   → 204 | 409 media.in_use
GET    /api/v1/playlists?q=&tags=&state=&sort=&limit=&offset=  → PlaylistPage   (même forme)
PATCH  /api/v1/playlists/{id}      {…, tags?, expires_at?}
GET    /api/v1/playlists/{id}/usage                         → PlaylistUsageOut
```

```python
MediaState = Literal["active", "expiring", "expired", "all"]   # expiring = expire dans core.media.expiring_soon_days
MediaSort = Literal["-created_at", "created_at", "title", "-title", "-bytes", "expires_at", "-updated_at"]

class MediaOut(_Out):                     # champs existants + :
    expires_at: datetime | None
    expired: bool                         # expired_at is not None
    revision: int
    tags: list[str]
    thumb: str | None                     # clé relative "media/<id>/thumb" si la variante existe, sinon None (icône du kind)
    usage_count: int                      # nombre de playlists (sous-requête indexée)

class MediaPage(_Out):
    items: list[MediaOut]
    total: int                            # limit 1..200 (défaut 50), offset ≥ 0

class UsagePlaylist(_Out):  id: UUID; name: str; expires_at: datetime | None; expired: bool
class UsageSchedule(_Out):  id: UUID; name: str; playlist_id: UUID; target_type: str; target_id: UUID; target_name: str; active: bool
class UsageOverride(_Out):  id: UUID; name: str; playlist_id: UUID; ends_at: datetime; active: bool

class MediaUsageOut(_Out):
    playlists: list[UsagePlaylist]
    schedules: list[UsageSchedule]        # via ces playlists
    overrides: list[UsageOverride]        # forçages non purgés, playlists éphémères comprises
    screens_now: int                      # écrans dont la trame courante le référence (borne supérieure, indicatif)

class PlaylistUsageOut(_Out):
    schedules: list[UsageSchedule]
    overrides: list[UsageOverride]

class MediaBulkIn(_In):
    ids: list[UUID] = Field(min_length=1, max_length=200)
    action: Literal["set_expiry", "add_tags", "remove_tags", "delete"]
    expires_at: datetime | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)

class MediaBulkOut(_Out):
    done: list[UUID]
    failed: list[dict[str, str]]          # {id, code} ; ex. media.in_use pour delete
```

- `GET /media` change de forme (liste → `MediaPage`) : seul consommateur = l'admin livré dans la même image ; régénérer `make api-types`. Les modules n'utilisent pas l'API HTTP mais la façade.
- Recherche `q` : titre et nom de fichier, `ILIKE` échappé (code existant). `tags` = tous requis (`@>`). `unused=true` = `usage_count = 0`.
- Grille et tableau = même endpoint, même page ; le mode d'affichage est un état d'UI (`localStorage`), pas un paramètre serveur.
- Droits : lecture `editor`+, écritures `editor`+ (comme l'envoi), `bulk delete` et `DELETE` comme aujourd'hui. Tout passe par :

```python
class ContentScope(Protocol):            # vds_server.diffusion.scope ; V1 : implémentation « organisation entière »
    def media_filter(self, principal: Principal) -> ColumnElement[bool]: ...
    def playlist_filter(self, principal: Principal) -> ColumnElement[bool]: ...
    async def can_write_media(self, principal: Principal, media_id: UUID) -> bool: ...
    async def can_write_playlist(self, principal: Principal, playlist_id: UUID) -> bool: ...
```
  Lot J remplace l'implémentation (périmètre site/groupe, D2) ; `usage` filtre ses lignes par le même scope (pas de fuite de noms d'autres sites).

## 7. Configuration (`core`, provenance par l'API existante)

| Clé | Défaut | Niveau | `x-vds-scopes` |
| --- | --- | --- | --- |
| `core.content.expired_retention_days` | 30 (0..365) | advanced | `organization` |
| `core.media.replaced_grace_hours` | 48 (1..720) | advanced | `organization` |
| `core.media.expiring_soon_days` | 7 (1..90) | simple | `organization` |

Clés d'organisation seulement : médias et playlists ne sont rattachés à aucun site en V1.

## 8. Aperçus et miniatures

- **Variante `thumb`** (WebP, plus grand côté 320 px, ≤ 30 Kio visé) produite par chaque module content-type dans `process_media` : `content.image` (Pillow, `MAX_IMAGE_PIXELS` borné), `content.video` (image à 10 % de la durée via ffmpeg, mêmes rlimits que ffprobe), `content.pdf` (page 1 dans le worker isolé existant). Le noyau ne génère rien ; une variante absente = icône du kind dans l'admin.
- Stockage par le module `storage` actif, clé `<hh>/<id>/thumb[-r<N>].webp` : aucune hypothèse d'hébergeur (disque, S3 futur).
- Servie par la route existante `<base>/media/<id>/thumb` (cookie de session), URL construite côté admin à partir de `apiBase`/`basePath` de `config.js` + clé relative `thumb`. Aucune URL absolue en base ni dans l'API.
- En-têtes inchangés (`private, no-store`, `nosniff`, CSP `sandbox`) : 50 vignettes de 30 Kio par page restent acceptables ; un cache navigateur ciblé ne sera envisagé que sur mesure.
- Rattrapage des médias existants : commande d'exploitation `python -m vds_server.media_reprocess --variant thumb` (relance `process_media` du module, sans changer la révision ni l'original) ; à défaut, icône.

## 9. Découpage en tâches

Ordre : M0 → M1 → {M2, M3, M4, M5} en parallèle → M8 ; M6 et M7 en parallèle dès M0 ; M9 dès que l'OpenAPI de M2 existe (mock puis réel) ; M10 → M11.

- [x] **M0 contrat** (`builder`) : événements 1.8 (§5), `MediaInfo.revision/expires_at`, `PlayerItem.until` (sdk-ts), `schedule.deleted.reason`, `CORE_API_VERSION = "1.8.0"`. Fin : `test_compat_18.py` (modules 1.0–1.7 chargent ; payloads exportés en JSON Schema), `make lint`.
- [x] **M1 migration + modèles** (`grunt` puis `builder` pour revue) : `0016_mediatheque` (§2), modèles, `storage.local` `_KEY_RE` à 48. Fin : `test_db_schema.py` (contraintes tags, unicité tombstone), migration descendante testée.
- [x] **M2 backend médiathèque** (`builder`) : `MediaPage`/`PlaylistPage`, filtres, tri, tags + autocomplétion, `PATCH` (expiration, tags, restauration), `usage` média et playlist, `bulk`, `ContentScope` V1, 422 `media.expired`/`playlist.expired` à l'ajout, clés de config §7. Fin : pytest aux deux `VDS_BASE_PATH` (tri stable, échappement `q`, 21e tag ou tag invalide 422, 422 bulk > 200, éditeur autorisé, origine croisée 403).
- [x] **M3 backend remplacement** (`builder`) : §4, `UploadLimitMiddleware` couvre `/media/{id}/file` (à vérifier, le filtre de chemin peut ne viser que `POST /media`). Fin : pytest (kind différent 422, traitement en échec ⇒ révision intacte + tombstones `aborted`, `If-Match` 412, sha identique sans effet, `revision_of` change, ancienne clé en tombstone à +48 h).
- [x] **M4 backend résolution** (`builder`) : `ScheduleSpec.until` (point de découpe dans le résolveur pur), fin effective des forçages, `until` par élément, omission des éléments expirés, `screen_media_ids` sans médias/playlists expirés, 404 appareil sur média expiré, invalidation des trames sur `media.*`/`playlist.*`. Fin : `test_resolver.py` (expiration en milieu de créneau ⇒ gagnant suivant, forçage tronqué), `test_player_schedule.py` (`until`, révision stable avant l'instant), `test_agent_cache.py` (manifeste sans média expiré), plan `power` cohérent.
- [x] **M5 backend balayeurs** (`builder`) : `content.expire`, `content.purge`, `storage.gc` (§3), verrou consultatif, audit, `DELETE` manuel via tombstone. Fin : pytest (idempotence à deux workers, purge reportée si forçage, éléments et programmations retirés + événements, échec stockage ⇒ ligne conservée puis réessai, chemins invalides rejetés par `storage.local`).
- [x] **M6 modules content-type** (`builder`) : variante `thumb` dans `content.image`, `content.video`, `content.pdf` ; commande `media_reprocess` (fait : `vds_server/media_reprocess.py`, `test_sweeper_content.py`). Fin : `test_content_modules.py` (taille, format, bombe d'image refusée), PDF piégé sans effet hors worker.
- [ ] **M7 player** (`builder`) : saut des éléments `until` dépassé (contrôle à chaque transition et à chaque tick, arrêt immédiat de l'élément courant), identité de cache (clé, sha256) dans le SW/IndexedDB, playlist vide ⇒ noir sans boucle serrée. Fin : vitest (horloge simulée, hors ligne, replay J+1 `stale`) + Playwright aux deux bases (remplacement ⇒ nouvelle image sans réappairage).
- [ ] **M8 agent** (`qa-tests`) : aucun code attendu. Fin : tests e2e agent (média remplacé ⇒ nouveau sha servi en LAN, média expiré retiré du cache).
- [ ] **M9 admin** (`builder`, skills `vds-ui-charte` + `vds-i18n`) : Médiathèque grille/tableau (bascule mémorisée, filtres type/tags/état/inutilisé, tri, pagination, sélection multiple + actions groupées), panneau détail (aperçu, métadonnées en mono, « Où est-il utilisé », « Remplacer le fichier » avec impact « N playlists, M écrans », expiration, tags), badges « Expire le… / Expiré / Inutilisé », éditeur de playlist (élément expiré barré), playlists : expiration, tags, usage. Fin : vitest + Playwright 2 bases × 2 thèmes, FR/EN, aucun libellé en dur.
- [ ] **M10 recette** (`qa-tests`) : démo écran réel (expiration à l'heure dite hors ligne, remplacement en direct, purge après rétention forcée à 0). Fin : `docs/demos/mediatheque.md`.
- [ ] **M11 `revue-securite`** : §10. Fin : aucun point bloquant.

## 10. Sécurité

- **Remplacement** = même chaîne que l'envoi (sniffing, liste blanche, pas de SVG, taille bornée avant lecture, sha256) ; aucun nom de fichier client dans la clé ; `original_filename` réduit à `Path(...).name[:255]`.
- **Clés** : générées uniquement par le serveur (`media_id` + nom de variante validé) ; une tombstone ne contient que des clés lues en base ; `storage.local._path` reste le garde-fou (regex + `is_relative_to`). Aucun endpoint n'accepte une clé ni un chemin.
- **Purge** : uniquement par le balayeur système (pas d'endpoint « purger maintenant ») ; transaction + verrou de ligne + FK `RESTRICT` en filet ; audit détaillé conservé. Rétention 0 autorisée mais en Avancé, organisation seulement.
- **Suppression de fichiers** : après commit uniquement (tombstone), jamais avant ; backend enregistré par ligne, pas de « backend courant » implicite.
- **Droits** : écritures sous cookie = contrôle de même origine existant ; `bulk` borné à 200 ; `usage` passe par `ContentScope` (lot J : pas de fuite inter-sites) ; un appareil n'obtient jamais un média expiré (404).
- **Remplacement comme vecteur** : un éditeur change ce qu'affichent tous les écrans sans toucher aux playlists ; c'est la fonction, tracée (`media.replaced`, ancien/nouveau sha256). Avec la validation avant diffusion (lot 4 « groupes + validation »), le remplacement devra passer par elle (point d'accroche §11).
- **Miniatures** : décodage dans les modules sous les bornes existantes (Pillow, ffmpeg rlimits, worker PDF isolé) ; servies avec `nosniff` + CSP `sandbox`.
- **Filet du balayeur** : `storage.gc` ne supprime jamais une clé encore référencée par `media_variant` ou `media.storage_key` (la tombstone est retirée).
- **Verrou de ligne** : tenu pendant tout le traitement du remplacement (`process_media` compris) et de `media_reprocess` ; un second remplacement du même média attend. Voulu : médias bornés en taille, traitement sous timeouts.
- **Expiration en masse** : un `bulk set_expiry` à une date déjà passée rend le média expiré ; la purge le retire alors des playlists. Voulu (même règle qu'une expiration naturelle), tracé en audit.
- **DoS** : tags ≤ 20 × 32 caractères, `limit` ≤ 200, `offset` ≤ 100000, autocomplétion ≤ 50, `q` échappé.

## 11. Points d'accroche (non implémentés)

- **Validation avant diffusion** : `media.revision` permettra une colonne future `published_revision` (révision en attente de validation) sans changer les clés.
- **Historique de versions** (backlog n°26) : allonger `replaced_grace_hours` et garder les lignes de variantes par révision.
- **Playlists dynamiques par tag** : `media.tags` + index GIN suffisent.
- **Droits site/groupe** (lot J) : `ContentScope` ; colonne future `owner_site_id` sur `media`/`playlist`.
- **Stockage objet (S3)** : nouveau module `storage`, tombstones déjà par backend.
- **Statistiques / preuve de diffusion** : `(media_id, revision)` identifie ce qui a été joué.
- **Date de publication** (`starts_at`) : non demandée ; même mécanique que `until`, additive.
- **Alertes** : utilisent les forçages (inchangé).

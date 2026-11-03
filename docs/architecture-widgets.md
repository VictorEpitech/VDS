# VDS — Architecture : widgets météo et RSS (chantier A)

Décision du 2026-11-03 (architecte, Victor absent : option recommandée la plus simple et sûre). **vds-core 1.12.0, additif.** Migration : une table noyau `content_feed`.

## 1. Décisions

1. **Deux modules content-type, format inchangé** : `content.weather` et `content.rss` dans `modules/<id>/` (manifeste, `config.schema.json`, `item.schema.json`, `assets/player.js`, `server/module.py`, i18n FR/EN). Aucun écran d'admin dédié : le formulaire d'élément est généré depuis `item.schema.json`.
2. **Le player ne contacte jamais une URL externe.** Le serveur récupère les données (client HTTP sortant anti-SSRF fourni par le noyau), les normalise en JSON borné, les stocke, et le player les lit sur **son propre serveur** (`/api/v1/player/feeds/{feed_id}`, sous `VDS_BASE_PATH`).
3. **Le noyau fournit une infrastructure générique de « flux de données »** (`feeds`) : cache, rafraîchissement, limites, dernier état connu. Aucune connaissance de la météo ni du RSS dans le noyau ; c'est l'équivalent du stockage média pour des données JSON. La logique (quelle URL, quel parsing) reste dans le module.
4. **Hors ligne** : le player garde le dernier état connu par flux dans IndexedDB, le précharge à chaque synchronisation de programmation, et l'affiche tant qu'il n'est pas plus vieux que `max_age_s` (propriété de l'élément). Au-delà, ou si aucune donnée n'a jamais été reçue, l'élément est sauté (`vds-error` `feed_unavailable` / `feed_stale`), rien n'est montré au public.
5. **Sources sans secret** : météo = **MET Norway Locationforecast 2.0** (`api.met.no`, sans clé, usage commercial autorisé avec attribution CC BY 4.0, User-Agent identifiant obligatoire). RSS = URL fournie par l'utilisateur (RSS 2.0 et Atom). Aucune clé d'API, aucun secret dans le dépôt ni dans la config.
6. **Texte seulement** : le RSS est réduit à titre, date, résumé en texte brut (HTML retiré côté serveur). Pas d'images de flux en V1 (point d'accroche : `image_media_id` futur via la médiathèque). Pas de géocodage en V1 : latitude/longitude saisies, plus un libellé libre.
7. Option écartée : tout faire dans le module (routes propres, tables propres). Le player ne peut pas s'authentifier sur une route de module, chaque module réimplémenterait cache, limites et anti-SSRF, et la surface sécurité serait dupliquée.

## 2. Contrat (vds-core 1.12.0, additif — note de compatibilité)

Modules existants inchangés ; `core: ">=1.1 <2.0"` reste valide. Nouveaux modules : `core: ">=1.12 <2.0"`.

### Python (`packages/sdk-py`)

```python
# vds_sdk/permissions.py : + "content.feeds"  (net.outbound existe déjà)

# vds_sdk/server/http.py
class SafeHttpError(ContractError): ...   # codes : net.blocked_address, net.scheme_refused,
                                          # net.too_large, net.timeout, net.redirect_refused, net.http_status
class SafeResponse(BaseModel):
    status: int; headers: dict[str, str]; body: bytes; final_url: str
class SafeHttp(Protocol):                 # ctx.core.http, permission net.outbound
    async def get(self, url: str, *, max_bytes: int, timeout_s: float = 10.0,
                  headers: Mapping[str, str] | None = None,
                  accept: tuple[str, ...] = ()) -> SafeResponse: ...

# vds_sdk/server/feeds.py
class FeedRequest(BaseModel):             # canonique, sérialisé trié ; feed_id = sha256(module_id + json)[:32]
    model_config = ConfigDict(frozen=True)
    params: dict[str, Any]                # ex. {"lat": 48.8566, "lon": 2.3522} ou {"url": "https://…"}
    refresh_s: int                        # borné par le noyau à [300, 86400]
class FeedsApi(Protocol):                 # ctx.core.feeds, permission content.feeds
    async def ensure(self, req: FeedRequest) -> str: ...   # crée/touche le flux, renvoie feed_id

# vds_sdk/server/content.py
class PlayerItemSpec(BaseModel):
    ...
    feeds: list[str] = Field(default_factory=list)          # nouveau : feed_id à précharger
class ContentType(Module):
    async def fetch_feed(self, req: FeedRequest, http: SafeHttp) -> dict[str, Any]:
        """Récupère et normalise ; JSON ≤ 64 Kio. Défaut : NotImplementedError."""
```

### TypeScript (`packages/sdk-ts/src/content.ts`, `CONTENT_API_VERSION` reste 1)

```ts
export interface FeedSnapshot { feedId: string; fetchedAt: string; data: unknown }
export interface ContentContext {
  ...
  /** vds-core 1.12.0 : dernier état connu du flux (réseau si possible, sinon IndexedDB) ; null si jamais reçu. */
  feed?(feedId: string): Promise<FeedSnapshot | null>;
}
```

### API player

`GET {base}/api/v1/player/feeds/{feed_id}` (auth écran existante) → `200 {feed_id, fetched_at, data}` avec `ETag` ; `304` ; `404 feeds.unknown` ; `204` si le flux existe mais n'a jamais réussi. Autorisé seulement si le `feed_id` figure dans la programmation résolue de cet écran (sinon `404`).

### Événements (`CORE_EVENTS_1_12`, payload Pydantic `v=1`)

- `feed.failed` : `{v, module_id, feed_id, error_code, failing_since}` — émis une fois au passage ok → échec (3 échecs consécutifs).
- `feed.recovered` : `{v, module_id, feed_id, fetched_at}`.

Aucun notifier n'y est abonné par défaut (hook seulement).

## 3. Noyau : service `feeds` (`vds_server.feeds`)

Table `content_feed` : `id text pk (feed_id)`, `module_id text`, `request jsonb`, `refresh_s int`, `data jsonb null`, `fetched_at timestamptz null`, `status text ('ok','failing','never')`, `error_code text null`, `fail_count int`, `next_at timestamptz`, `last_used_at timestamptz`, `created_at`.

- `ensure` est appelé par le module dans `to_player` ; il met à jour `last_used_at`. Au plus **200 flux par module** (`feeds.quota_exceeded`).
- Boucle de rafraîchissement (tâche de fond, tick 30 s) : flux dus, **4 en parallèle** au total, 1 requête à la fois par hôte ; échec → backoff exponentiel plafonné à `refresh_s`. Les données précédentes sont conservées en cas d'échec.
- Résultat de `fetch_feed` validé : JSON sérialisé ≤ **64 Kio**, sinon `feeds.too_large` (traité comme un échec).
- Purge : flux non utilisés depuis 7 jours supprimés.
- Pas de poussée WebSocket des données : le player interroge le flux lui-même (sondage à `refresh_s`, minimum 300 s, tant qu'un élément l'utilise). Aucun nouveau message WebSocket.

### `SafeHttp` (anti-SSRF, noyau)

- `https` seulement, port 443 seulement, pas d'identifiants dans l'URL.
- Résolution DNS par le noyau, **toutes** les adresses vérifiées : refus de loopback, privé RFC 1918, CGNAT 100.64/10, lien-local, multicast, réservé, ULA fc00::/7, IPv4-mappée ; refus de l'hôte de `VDS_PUBLIC_ORIGIN`. Connexion à l'IP vérifiée (épinglée) avec SNI et `Host` d'origine (anti-rebinding DNS).
- Redirections : 3 au plus, chacune revérifiée ; `https` → `http` refusé.
- Corps lu en flux, coupé à `max_bytes` (plafond dur 2 Mio) ; délai total ≤ 10 s ; pas de cookies ; pas de proxy d'environnement ; User-Agent `VDS/<version> (+<VDS_PUBLIC_ORIGIN>)`.
- Dépendance : `httpx` (serveur). Parsing XML : `defusedxml` (RSS).

## 4. Modules

### `content.weather` 0.1.0

- Manifeste : `kind content-type`, `targets [server, player, admin]`, `permissions ["net.outbound", "content.feeds"]`, `content.requires_network: false`, `element vds-content-weather`.
- `config.schema.json` (organisation) : `refresh_min` (int, défaut 60, min 30, `advanced`), `contact` (string, défaut "", `advanced`, ajouté au User-Agent comme l'exigent les conditions MET).
- `item.schema.json` : `label` (string, requis, `simple`), `latitude` / `longitude` (number, bornes, arrondies à 4 décimales côté serveur, `simple`), `days` (1–3, défaut 1, `simple`), `units` (`metric`|`imperial`, défaut `metric`, `advanced`), `max_age_s` (défaut 21600, `advanced`).
- `fetch_feed` : `GET https://api.met.no/weatherapi/locationforecast/2.0/compact?lat=..&lon=..`, `max_bytes` 512 Kio, respect de `Expires` (jamais plus tôt que `refresh_min`). Données normalisées : `{now: {temp_c, symbol}, days: [{date, min_c, max_c, symbol}], attribution: "MET Norway"}`.
- Player : pictogrammes embarqués dans `assets/` (jeu de symboles météo sous licence MIT, licence copiée), mention « Données MET Norway » discrète, adaptation paysage / portrait / bandeau, libellés de jours via `Intl` et la locale du contexte.

### `content.rss` 0.1.0

- Manifeste : même forme, `element vds-content-rss`, `permissions ["net.outbound", "content.feeds"]`.
- `config.schema.json` : `allowed_hosts` (liste, défaut vide = tout hôte public, `advanced`), `refresh_min` (défaut 15, min 5, `advanced`), `max_bytes` (défaut 1 Mio, max 2 Mio, `advanced`).
- `item.schema.json` : `url` (https, requis, `simple`), `max_items` (1–10, défaut 5, `simple`), `display` (`list`|`ticker`, défaut `list`, `simple`), `item_duration_s` (défaut 8, `advanced`), `show_date` (défaut true), `max_age_s` (défaut 86400, `advanced`).
- `to_player` : valide l'URL (même règles que `content.url` : https, `allowed_hosts`, origine propre refusée) puis `ensure`.
- `fetch_feed` : `defusedxml`, RSS 2.0 et Atom, 20 entrées gardées au plus, texte brut (balises retirées, entités décodées, titre ≤ 200 car., résumé ≤ 400 car.), dates en ISO 8601.
- Player : rendu en texte (`textContent`, jamais `innerHTML`), défilement ou rotation, durée = `max_items × item_duration_s` en mode liste.

## 5. Player

- Le runtime implémente `ctx.feed` : réseau avec délai 3 s → mise à jour IndexedDB (`feeds` store, clé `feed_id`) ; sinon valeur en cache.
- À chaque synchronisation de programmation : préchargement de tous les `feeds` des éléments (journée + lendemain). Purge des flux non référencés.
- Mode hébergé et autonome identiques : toutes les URL via la base du runtime (`VDS_BASE_PATH`).

## 6. Tâches

| # | Tâche | Agent | Critère de fin testable |
| --- | --- | --- | --- |
| W1 | SDK py 1.12.0 : `SafeHttp`, `FeedRequest`, `FeedsApi`, `PlayerItemSpec.feeds`, `ContentType.fetch_feed`, permission `content.feeds`, `CORE_EVENTS_1_12` + payloads, `CORE_API_VERSION` | grunt | `pytest packages/sdk-py` vert ; un manifeste déclarant `content.feeds` se charge, un manifeste avec permission inconnue est refusé |
| W2 | SDK ts : `FeedSnapshot`, `ContentContext.feed?` ; `make api-types` | grunt | `tsc` vert dans `packages/sdk-ts` et `apps/player` |
| W3 | `SafeHttp` noyau (httpx, DNS épinglé, redirections, plafonds) | builder | Tests : 127.0.0.1, 10.x, 169.254.169.254, [::1], fc00::, hôte résolvant vers privé, rebinding (2e résolution privée), redirection vers privé et vers http, corps > max, délai dépassé → tous refusés avec le bon code ; https public simulé → OK |
| W4 | Service `feeds` : migration `content_feed`, `ensure`, boucle, backoff, quota, plafond 64 Kio, purge, événements `feed.failed`/`feed.recovered` | builder | Tests : rafraîchissement dû, échec conservant les données, 3 échecs → un seul `feed.failed`, succès → `feed.recovered`, quota 201e → erreur, purge à J+7 |
| W5 | Route player `GET /api/v1/player/feeds/{id}` (ETag/304/204/404, contrôle d'appartenance à la programmation de l'écran) + `feeds` dans la programmation résolue | builder | Tests API avec `VDS_BASE_PATH=/` et `/espace/affichage` ; un écran ne lit pas un flux absent de sa programmation |
| W6 | Player : `ctx.feed`, store IndexedDB, préchargement à la synchro, sondage par élément, purge | builder | vitest : réseau OK → cache mis à jour ; réseau coupé → dernier état ; jamais reçu → null |
| W7 | Module `content.weather` (serveur + player + schémas + i18n + pictos + licence) | builder | Tests module : chargement manifeste, validation schémas, normalisation d'une réponse MET enregistrée (fixture), `max_age_s` dépassé → `feed_stale` ; Playwright : rendu paysage, portrait, bandeau |
| W8 | Module `content.rss` (serveur + player + schémas + i18n) | builder | Fixtures RSS 2.0, Atom, XML piégé (entité externe, billion laughs) refusé, HTML dans le résumé rendu en texte ; Playwright liste et défilement |
| W9 | Tests hors ligne de bout en bout | qa-tests | Playwright : synchro, coupure réseau, le widget affiche le dernier état ; au-delà de `max_age_s`, élément sauté sans message au public ; `stop()` sans fuite sur 200 cycles ; les deux `VDS_BASE_PATH` |
| W10 | Revue sécurité (SSRF, XML, XSS player, quotas) | revue-securite | Rapport sans bloquant ni important ouvert |
| W11 | Docs : README des deux modules (rôle, réglages, attribution MET, limites), `docs/architecture.md` › modules intégrés, ligne roadmap | grunt | Fichiers présents, liens valides |

Ordre : W1–W2 → W3 → W4–W5 → W6 → W7–W8 en parallèle → W9 → W10.

## 7. Risques

- Conditions MET Norway (User-Agent, fréquence, attribution) : non-respect → blocage par l'API. Mitigé par `refresh_min` ≥ 30 et `Expires`.
- Sans géocodage, saisir latitude/longitude est peu pratique (point d'accroche : sélecteur de lieu futur, hors V1).
- Flux RSS d'intranet (hôte privé) refusés par conception ; un relais par l'agent de site serait un futur module, non prévu en V1.
- Poussée temps réel des données (alertes) volontairement absente : sondage ≥ 300 s.

## 8. Ligne à ajouter dans `docs/roadmap.md` › Décisions

- 2026-11-03 — Widgets météo et RSS : modules `content.weather` (MET Norway, sans clé) et `content.rss` (RSS/Atom, texte seul) ; le serveur récupère via `SafeHttp` noyau (https, IP publiques épinglées, 3 redirections revérifiées, ≤ 2 Mio, ≤ 10 s) et stocke dans `content_feed` (≤ 64 Kio, rafraîchissement ≥ 300 s, 200 flux/module) ; le player lit `/api/v1/player/feeds/{id}` sur son serveur et garde le dernier état connu (IndexedDB, `max_age_s`) ; événements `feed.failed`/`feed.recovered` (hook) ; vds-core 1.12.0 additif (permission `content.feeds`, `PlayerItemSpec.feeds`, `ContentContext.feed?`). Voir `architecture-widgets.md`.

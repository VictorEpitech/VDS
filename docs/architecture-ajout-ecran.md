# VDS — Architecture, refonte lot D : assistant « Ajouter un écran »

Décision architecte du 2 novembre 2026. Source : `docs/refonte/ux.md` §2.3 (assistant) et §4 points 4-5.
**Sans bump vds-core** : une route admin additive du noyau et un refactor interne ; manifeste, SDK, catalogue d'événements, messages WebSocket, permissions et format de programmation inchangés. Conséquence côté client : `make api-types` uniquement.

## Principes

- Noyau uniquement, aucun module : écrans et appairage sont des entités du noyau.
- **Le code TV reste la preuve de présence physique.** Pas de liste des codes en attente côté admin (voir « Écarté »).
- Une seule logique de validation de code pour `POST /pairings/claim` (existant, inchangé) et `POST /screens/pair` (nouveau) : mêmes erreurs, mêmes statuts HTTP.
- Aucune hypothèse d'hébergeur : routes sous `<VDS_BASE_PATH>/api/v1/…` via le `Mount` existant ; écritures sous cookie couvertes par `SameOriginMiddleware` (global, rien à ajouter).

## (1) `POST /api/v1/screens/pair` — création + claim atomiques

Rôle : `admin` (comme `POST /screens` et `POST /pairings/claim`). Limiteur partagé avec `/pairings/claim` : 30 requêtes/min par utilisateur (`RateLimiter`, clé `user:<id>`), contre l'énumération de codes.

### Requête (`ScreenPair`, `extra="forbid"`)

```json
{"code": "K7Q-4MZ", "name": "Accueil RDC", "site_id": "…", "group_id": null, "orientation": null}
```

- `code` : 6-12 caractères, normalisé par `normalize_code` (tirets, casse, O/I/L tolérés).
- `name`, `site_id`, `group_id` : mêmes contraintes que `ScreenCreate` (groupe du même site, sinon `screen.group_other_site` 422).
- `orientation` : facultatif ; absent = déduit de `PairingRequest.device.screen_w/screen_h` (hauteur > largeur → `portrait`), à défaut `landscape`.

### Réponse `201` (`ScreenPaired`)

```json
{"screen": { /* ScreenOut, device = null */ }, "pairing": {"status": "claimed", "expires_at": "…"}, "replayed": false}
```

Le jeton d'appareil est émis plus tard, au prochain sondage du player (`GET /player/pairings/{id}`, flux existant) qui publie `screen.paired`. Le front sonde `GET /screens/{id}` toutes les 3 s jusqu'à `device.presence = "online"`.

### Transaction (ordre imposé)

1. Verrouiller la demande : `SELECT … FROM pairing_request WHERE code = :c FOR UPDATE` (toutes statuts confondus).
2. Valider **avant toute création** : inconnue → 404 ; expirée → marquer `expired`, **commit de ce seul marquage**, 410 (aucun écran n'existe encore, donc rien d'autre n'est commité) ; `claimed`/`consumed` → idempotence ou 409 (ci-dessous).
3. Créer l'écran (flush, audit `screen.created` avec `details.via = "screens.pair"`).
4. Réclamer : `status = claimed`, `screen_id`, `claimed_by`, audit `pairing.claimed`.
5. Un seul `commit`. Toute erreur entre 3 et 5 = rollback complet (pas d'écran orphelin).

Aucun événement de bus nouveau : `screen.paired` reste publié à la consommation par le player.

### Idempotence (sans table ni en-tête)

Si la demande est `claimed` ou `consumed`, que `claimed_by == actor.user_id` et que l'écran lié n'est pas archivé : `200` avec cet écran et `replayed: true` (double clic, réseau coupé après commit). Sinon `409 errors.pairing_code_claimed`. Le rejeu exige en plus que `site_id` et `name` de la requête correspondent à l'écran existant (sinon 409) ; il est audité `pairing.replayed`.

### Limiteur et audit des échecs (revue sécurité lot D)

- `FailureLimiter` (30 échecs / 60 s, clé `user:<actor_id>`), partagé `claim` + `screens/pair`. Seuls 404/409/410 comptent ; succès, rejeu et erreurs de forme libèrent la réservation. La limite est **par processus** (mémoire) : avec N workers, le plafond effectif est N x 30 ; acceptable (admins authentifiés, espace de codes borné par l'expiration).
- Échecs 404/409/410 audités `pairing.claim_failed` (`via`, `status`, `code` d'erreur ; jamais le code d'appairage) dans une session séparée, donc persistés malgré le rollback.
- Le 409 révèle à un admin qu'un code vient d'être réclamé par un autre admin : fuite acceptée (tous les admins voient le parc).

### Refactor interne (signatures)

```python
# vds_server/player/service.py
class PairingService:
    async def lock_code(self, code: str, actor: Principal) -> PairingRequest | ScreenReplay:
        """Verrouille et valide ; lève pairing_code_invalid/expired/claimed ; renvoie un rejeu."""
    def mark_claimed(self, actor: Principal, row: PairingRequest, screen_id: UUID) -> None:
        """Pose claimed + audit pairing.claimed ; ne commite pas."""
    async def claim(self, actor: Principal, code: str, screen_id: UUID) -> UUID:
        """Existant, contrat inchangé : lock_code + mark_claimed + commit."""

# vds_server/inventory/service.py
class InventoryService:
    async def add_screen(self, body: sc.ScreenCreate, **audit_details: str) -> Screen:
        """Valide site/groupe, ajoute, flush, audite ; ne commite pas."""
    async def create_screen(self, body: sc.ScreenCreate) -> Screen:
        """Existant, inchangé : add_screen + commit."""

# vds_server/player/enroll.py (nouveau, orchestration noyau)
async def pair_new_screen(
    session: AsyncSession, bus: EventBus | None, actor: Principal, body: ScreenPair
) -> tuple[Screen, PairingRequest, bool]:  # (écran, demande, rejoué)
```

La route vit dans `api/v1/inventory.py` (préfixe `/screens`), déclarée **avant** `/screens/{screen_id}`.

## (2) Erreurs typées — aligner sans casser

Les `code` existants sont conservés (front et i18n en dépendent, `/pairings/claim` inchangé). Le vocabulaire UX (`code_unknown`, `code_expired`, `already_paired`) est un **identifiant de motif côté front**, dérivé du `code` serveur :

| HTTP | `code` serveur | Motif UX | Endpoints |
| --- | --- | --- | --- |
| 404 | `errors.pairing_code_invalid` | `code_unknown` | claim, pair |
| 410 | `errors.pairing_code_expired` | `code_expired` | claim, pair |
| 409 | `errors.pairing_code_claimed` (**nouveau**) | `already_paired` | claim, pair |
| 404 | `screen.not_found` / `site.not_found` | — | pair |
| 422 | `screen.group_other_site`, validation | — | pair |
| 429 | (limiteur) | `too_many_attempts` | claim, pair |

- `already_paired` n'existait pas : aujourd'hui un code déjà réclamé tombe en `pairing_code_invalid` (filtre `status == "pending"`). Le 409 est un affinement : `/pairings/claim` renvoyait 404 dans ce cas, il renverra 409 — changement de statut sur un cas d'erreur, documenté, aucun client tiers.
- `errors.pairing_gone` (410) et `errors.pairing_code_unavailable` (503) sont **côté player** (sondage, génération) : hors assistant, inchangés.
- « Écran déjà ajouté sous le nom N — le remplacer ? » : le serveur ne peut pas reconnaître une TV déjà appairée (pas d'identité d'appareil stable, le code est neuf à chaque démarrage). Le remplacement d'appareil d'un écran existant reste `POST /pairings/claim` avec son `screen_id` (`_issue_device` révoque l'ancien). L'assistant propose ce choix uniquement si l'utilisateur part d'une fiche écran.
- Incohérence notée, non corrigée en D : `claim` lève `errors.screen_not_found` alors que l'inventaire lève `screen.not_found`.

## (3) Vérification finale — existant suffisant, aucun ajout serveur obligatoire

- **« Il joue »** : `GET /screens/{id}` expose déjà `device.presence`, `device.heartbeat.{revision, stale, missing_assets}`. Réussi si `presence = online`, `heartbeat` présent, `stale = false`, `missing_assets = 0`. Pas de programmation couvrant l'écran → état « en ligne, rien à diffuser » + action « Programmer » (pas un échec).
- **Test d'allumage** : affiché seulement si `power.driver` résolu pour l'écran (`GET /config/screen/{id}`) et un agent `online` sur le site (`GET /sites/{site}/agents`). Commande non destructive existante `power.probe` via `POST /sites/{site}/agents/{agent}/commands` `{"name": "power.probe", "screen_id": …}`, résultat par `GET …/commands` (statut, `code`, `result`). Jamais `power.set` dans l'assistant. Sinon étape masquée, non bloquante.
- Temps réel : sondage 3 s (ux §4.6) ; SSE/WebSocket admin reste un point d'accroche, non implémenté.
- Option additive non requise pour D : exposer `heartbeat.playing` assaini (`item_id`, `content_type`) dans `DeviceHeartbeatOut` si le player l'envoie déjà ; à décider au lot I (tableau de bord).

## (4) Android TV, provisioning, découverte — front seulement

- Étape 1 « Quel appareil ? » : TV navigateur / Pi / autre → saisie du code (`POST /screens/pair`). Android TV → deux voies : « J'ai un code à l'écran » (même chemin) ou « Installer depuis le réseau » : création par `POST /screens` puis assistant existant `provisioning/Wizard` + `StepList` (ticket lié à l'écran, flux `POST /player/pair` inchangé), `DetectPanel` pour choisir l'IP.
- Aucun changement serveur, ni du ticket, ni de la découverte.

## (5) Contrat

Pas de bump vds-core (même règle que `docs/architecture-onboarding.md`). Ajouts : route `POST /screens/pair`, schémas `ScreenPair`/`ScreenPaired`, code d'erreur `errors.pairing_code_claimed`. Pas de migration.

## (6) Tâches

| Agent | Tâche | Critère de fin |
| --- | --- | --- |
| backend | Refactor `lock_code`/`mark_claimed`/`add_screen` sans changer `claim`/`create_screen` ; `enroll.pair_new_screen` ; route `POST /screens/pair` ; 409 `pairing_code_claimed` ; limiteur partagé ; `make api-types` | tests existants verts ; `ruff`/`mypy` propres ; OpenAPI à jour |
| frontend-admin | Assistant 4 étapes (appareil, code 6 car. majuscules auto, nom/site/groupe avec héritage, vérification) ; mapping `code` → motif → clés i18n FR/EN ; branche Android vers `provisioning/Wizard` ; `/pairing` et `/screens/android` redirigés vers l'assistant (via base path) | parcours complet au clavier, 375 px, aucun libellé en dur, aucun message brut |
| qa-tests | pytest : atomicité (échec après création = aucun écran), expiré = demande `expired` et aucun écran, rejeu même admin = 200 `replayed`, autre admin = 409, éditeur 403, Origin refusée 403, 429 ; non-régression `/pairings/claim` ; vitest mapping erreurs ; Playwright parcours code → online aux deux `VDS_BASE_PATH` | tout vert sur `/` et `/espace/affichage` |

## Écarté

- **Liste des codes en attente par site** (ux §4.4) : une demande d'appairage est anonyme (endpoint public, pas de site) ; la lister permettrait de réclamer une TV sans y avoir accès physiquement. Point d'accroche : un futur module de découverte pourrait corréler une demande à un agent de site (même LAN) et préremplir le code ; non implémenté.
- Clé `Idempotency-Key` + table : surdimensionné, l'idempotence naturelle par code suffit.
- Renommer les `code` d'erreur existants : casse le front et `/pairings/claim` sans gain.

# Commandes player et captures d'écran (lot I, vds-core 1.11.0)

Statut : conçu le 2026-11-03 par l'architecte, à valider par Victor sur les points « Questions ». Sources : `docs/refonte/ux.md` (lot I, §2.4), `docs/refonte/xibo-ecarts.md` (rang 5), `docs/architecture-lot3.md` §1.3, `docs/architecture.md` (kiosque, heartbeat), `docs/architecture-actions-rapides.md` (lot E), `docs/architecture-droits.md` (lot J).

## 0. État de départ (constaté)

- `POST player/heartbeat` répond déjà `commands: []` (point d'accroche du lot 2) ; le player comprend encore le provisoire `{type: "reload"}`.
- `/ws/player` existe (auth `vds_dev_`, `schedule.changed`), mais **aucune trame `cmd` n'est poussée** (reste de L3-T5) et le player ignore `cmd`.
- La table `cmd` de lot 3 citait `reload`, `identify`, `screenshot`, `power.set`, `power.probe`. Les docs lot I / Xibo citent « recharger, redémarrer, capture (à la demande et périodique) ». **Aucun document ne fixe une liste de 5 commandes de lot I** : la liste ci-dessous est une proposition (Q1).
- Aucune route admin de commandes player, aucun stockage de capture (`scope_routes.py` n'en classe aucune).
- `power.set` / `power.probe` passent par l'agent (`POST sites/{id}/agents/{id}/commands`, admin) : **hors lot I**, inchangés.

## 1. Les 5 commandes de lot I

| `name` | `args` | Effet sur l'écran | TTL défaut (max) | Rôle minimum |
| --- | --- | --- | --- | --- |
| `reload` | `{}` | Recharge le shell du player (programmation et cache conservés) | 10 min (24 h) | éditeur dans le périmètre |
| `restart` | `{scope: "app" \| "device"}` | `app` : relance du processus player (Android : activité, Pi : Chromium, web : = `reload`) ; `device` : redémarrage de l'appareil si la capacité existe | 10 min (24 h) | admin |
| `identify` | `{seconds: 5..30}` (défaut 10) | Affiche le **nom de l'écran** seul, plein écran, puis reprend. Rien de technique | 2 min (10 min) | éditeur dans le périmètre |
| `screenshot` | `{}` | Capture de ce que le player affiche, envoyée au serveur. Invisible au public (pas de flash, pas d'indicateur) | 2 min (10 min) | éditeur dans le périmètre |
| `cache.resync` | `{}` | Revérifie le cache (taille + sha256) et retélécharge les manquants/corrompus. **Ne supprime jamais un média de la programmation en cours** (« vider le cache » brut écarté : écran noir hors ligne garanti) | 1 h (24 h) | admin |

Le player n'exécute que les commandes de cette liste **et** annoncées dans ses capacités ; sinon `rejected` / `unknown_command` ou `unsupported`. Point d'accroche conservé : `maintenance.unlock` (non implémenté ; le kiosque reste piloté par la config `player.kiosk*`).

## 2. Capacités annoncées par le player (contrat)

Heartbeat (HTTP et WS), champ optionnel ajouté :

```json
"capabilities": ["cmd.reload", "cmd.identify", "cmd.screenshot", "cmd.restart.app", "cmd.restart.device", "cmd.cache.resync"],
"boot_id": "uuid généré à chaque démarrage du shell"
```

Absent (vieux player) = `["cmd.reload"]` (seul le provisoire est compris). Stocké dans `last_heartbeat`, exposé dans `ScreenOut.capabilities: list[str]`. L'admin masque (ne grise pas) les actions non supportées, sauf la capture remplacée par l'aperçu théorique (§6.4).

Les capacités natives viennent d'un **adaptateur de plateforme** enregistré par le module `player-adapter` (kind existant au catalogue, cf. `docs/architecture.md`), jamais d'un `if android` dans le player noyau :

```ts
// packages/sdk-ts/src/adapter.ts (vds-core 1.11.0)
export type PlayerCapability =
  | "cmd.reload" | "cmd.identify" | "cmd.screenshot"
  | "cmd.restart.app" | "cmd.restart.device" | "cmd.cache.resync";

export interface PlayerAdapter {
  id: string;                                         // "player.android", "player.pi", "player.web"
  capabilities(): Promise<PlayerCapability[]>;        // réévalué au démarrage
  restart(scope: "app" | "device"): Promise<void>;    // ne résout pas si le redémarrage a lieu
  screenshot(opts: { maxEdge: number; quality: number }): Promise<Blob>; // image/jpeg, rejette si indisponible
}
export function registerPlayerAdapter(a: PlayerAdapter): void;
```

Mise en œuvre par plateforme (V1) :
- **Android** : pont `WebViewCompat.addWebMessageListener` déjà utilisé pour `vdsKiosk` (origine `http://127.0.0.1:47800`) ; messages `vdsCmd {name}` → natif. Capture = `PixelCopy` de la fenêtre (inclut la vidéo `SurfaceView`, ce que `View.draw` ne fait pas). `restart.app` = relance de l'activité en restant en lock task ; `restart.device` = `DevicePolicyManager.reboot()` **seulement si Device Owner**.
- **Pi (Chromium kiosque)** : `restart.app` par l'unité systemd (relance si le processus sort) ; capture **non supportée en V1** (le port de débogage distant de Chromium serait une surface d'attaque locale) ; `restart.device` non.
- **Web / PWA / Tizen** : `reload`, `identify`, `cache.resync` ; pas de capture (pas d'API sans geste utilisateur), pas de redémarrage.

## 3. Contrat de commande (serveur → player)

### 3.1 Modèle de données (migration `0019_commandes_player`, additive)

Table dédiée, pas `remote_command` : la file d'agent est append-only avec `seq` par agent et ack cumulatif ; le player n'a pas d'outbox ni de `seq`. Mélanger les deux casserait l'invariant de `docs/architecture.md` › File de commandes d'agent.

```python
class PlayerCommand(Base, Timestamps):
    __tablename__ = "player_command"
    id: Mapped[UUID] = uuid_pk()                         # = cmd_id
    screen_id: Mapped[UUID | None]                       # FK screen ON DELETE SET NULL (historique conservé)
    device_id: Mapped[UUID | None]                       # appareil appairé au moment de la demande
    name: Mapped[str]                                    # CHECK name IN (5 commandes)
    args: Mapped[dict]                                   # JSONB, validé par modèle Pydantic par commande
    status: Mapped[str]                                  # queued|sent|ok|failed|expired|rejected|cancelled
    code: Mapped[str | None]                             # ex. "cmd.unsupported", "screenshot.capture_failed"
    request_key: Mapped[str | None]                      # clé d'idempotence client
    batch_id: Mapped[UUID | None]                        # actions groupées (même clic)
    actor_kind: Mapped[str]                              # user|module|system
    actor_id: Mapped[str | None]
    expires_at: Mapped[datetime]
    sent_at: Mapped[datetime | None]
    done_at: Mapped[datetime | None]
    result: Mapped[dict | None]                          # JSONB, petit (ex. {"screenshot_id": …})
    # UNIQUE (actor_id, request_key) WHERE request_key IS NOT NULL
    # INDEX (device_id, status) WHERE status IN ('queued','sent')
```

Changement de device (réappairage, `DELETE screens/{id}/device`) : les commandes `queued|sent` du device précédent passent `expired` (`code="cmd.device_changed"`).

### 3.2 Livraison

1. Écriture de la ligne `queued`, puis `NOTIFY vds_command, 'screen:<screen_id>'` (canal existant, préfixe distinct de l'agent).
2. Le worker qui tient la connexion `/ws/player` pousse `cmd {cmd_id, name, args, expires_at}` et passe `sent`.
3. **Repli HTTP** (pas de WS, proxy hostile) : `POST player/heartbeat` renvoie dans `commands` les commandes `queued|sent` non expirées du device (au plus 10, plus anciennes d'abord), au format `{cmd_id, name, args, expires_at}`. Le `{type:"reload"}` provisoire disparaît (le player 1.11 ignore `type`, le serveur ne l'émet plus).
4. Reconnexion WS : le serveur repousse les `queued|sent` non expirées après `welcome`.

### 3.3 Résultat et accusé

- WS : `cmd.result {cmd_id, status: "ok"|"failed"|"rejected"|"expired", code?, data?}`.
- HTTP : nouveau champ optionnel du heartbeat `command_results: [same]` (≤ 20). Le serveur répond `results_acked: [cmd_id]` ; le player retire de sa file locale ce qui est acquitté.
- `reload` / `restart` : le player écrit `{cmd_id, state: "executing"}` en IndexedDB **avant** d'agir, puis au démarrage suivant envoie `ok` (et `boot_id` changé confirme `restart`). Sans redémarrage constaté en 2 min : `failed`, `code="cmd.restart_not_observed"`.
- Résultat déjà terminal en base : ignoré (idempotent), pas d'erreur.

### 3.4 Idempotence, expiration, hors ligne

- Player : journal `executed` en IndexedDB (cmd_id → statut, 200 entrées, 48 h). Une commande reçue deux fois (WS puis heartbeat, ou rejeu après coupure) n'est **jamais réexécutée** ; son résultat est renvoyé. Protège contre la boucle de redémarrage.
- Expiration : le player refuse toute commande dont `expires_at` < horloge corrigée de la dérive (`server_time`) → `expired`. Le serveur passe `expired` les `queued|sent` échues (balayage 1 min) et ne les livre plus.
- Hors ligne : la commande reste `queued` jusqu'à `expires_at` ; l'admin l'affiche « En attente : écran hors ligne » et peut l'annuler (`cancelled`, seulement depuis `queued`). Rien n'est exécuté en différé au-delà du TTL (une capture de la veille n'a pas de sens).
- Admin : `request_key` (UUID v4 par clic) → un double clic ou un rejeu réseau renvoie la même commande (200 au lieu de 201).
- Plafonds : 20 commandes `queued|sent` par écran (429 `cmd.too_many_pending`), `screenshot` dédoublonnée (une capture en attente par écran : la nouvelle demande renvoie la commande existante).

## 4. API admin (REST, `/api/v1`, préfixée par `VDS_BASE_PATH`)

```
POST   /screens/{screen_id}/commands                 scoped (rôle par commande, §5)
       body: {name, args?, ttl_s?, request_key, batch_id?}
       201|200 → PlayerCommandOut ; 404 hors périmètre ; 403 rôle ; 409 cmd.unsupported ; 429
GET    /screens/{screen_id}/commands?limit=20        scoped → list[PlayerCommandOut] (récentes d'abord)
GET    /player-commands?ids=<uuid,…≤200>             scoped → list[PlayerCommandOut] (sondage groupé, ids hors périmètre omis)
POST   /screens/{screen_id}/commands/{cmd_id}/cancel scoped (même rôle que la demande) ; 409 si pas queued
GET    /screens/{screen_id}/screenshots?limit=10     scoped → list[ScreenshotOut]
GET    /screenshots/{screenshot_id}/image            scoped → image/jpeg
```

```python
class PlayerCommandOut(BaseModel):
    id: UUID
    screen_id: UUID | None
    name: Literal["reload", "restart", "identify", "screenshot", "cache.resync"]
    args: dict[str, Any]
    status: Literal["queued", "sent", "ok", "failed", "expired", "rejected", "cancelled"]
    code: str | None
    requested_by: str | None        # nom affichable, jamais d'id technique
    created_at: datetime
    expires_at: datetime
    done_at: datetime | None
    screenshot_id: UUID | None

class ScreenshotOut(BaseModel):
    id: UUID
    screen_id: UUID
    trigger: Literal["on_demand", "periodic"]
    captured_at: datetime           # horloge player corrigée
    width: int
    height: int
    bytes: int
    image_url: str                  # chemin relatif sous VDS_BASE_PATH
```

`ScreenOut` gagne `capabilities` et `last_screenshot: ScreenshotOut | None` (pour la carte, sans requête de plus). Ces routes sont ajoutées à `SCOPE_ROUTES` (sinon `test_scope_routes` échoue). Écritures sous cookie : contrôle de même origine habituel. Types TS : `make api-types`.

Actions groupées : **même principe que le lot E** (fan-out client, 4 requêtes parallèles, 200 écrans max, `batch_id` commun pour l'audit, sondage groupé par `GET /player-commands?ids=`). Option écartée : `POST /screens/commands` en masse côté serveur (à rouvrir avec celle du lot E).

Côté player (authentification `vds_dev_`, `scope_neutral`) :

```
POST /player/screenshots/{cmd_id}      capture à la demande
POST /player/screenshots               capture périodique (sans cmd_id)
     body: image/jpeg brut, ≤ 2 Mo ; en-tête X-VDS-Captured-At (ISO)
     201 → {screenshot_id} ; 409 si cmd déjà terminée/étrangère au device ; 413 ; 415 ; 429
```

## 5. Droits (lot J)

- Contrôle de cible = celui du lot J : nœud de l'écran (`node_of_target("screen", id)` : groupe, sinon site) couvert par un nœud du périmètre (`covers`). Hors périmètre ⇒ **404 sans nom** (pas 403), comme le reste du lot J.
- `reload`, `identify`, `screenshot`, voir les commandes et les captures : admin ou éditeur dont le périmètre couvre l'écran. Raison : actions non destructives, utiles à l'éditeur qui vérifie sa publication.
- `restart`, `cache.resync` : **admin seul** (cohérent avec Allumer/Éteindre du lot E). 403 `cmd.role_required` ; l'UI masque.
- Éditeur sans périmètre : aucune action, liste vide.
- Acteurs module/système (J-D10) : hors périmètre, mais doivent déclarer la permission **`screens.command`** (nouvelle, catalogue 1.11) et passent par `rt.screens.send_command(...)` ; aucun module n'appelle le player directement.

```python
# vds_sdk.server (1.11.0)
class ScreenCommandRequest(BaseModel):
    screen_id: UUID
    name: Literal["reload", "identify", "screenshot"]   # restart / cache.resync interdits aux modules en V1
    args: dict[str, Any] = {}
    ttl_s: int = 600

class ScreensApi(Protocol):
    async def send_command(self, req: ScreenCommandRequest) -> UUID: ...   # exige screens.command
```

## 6. Captures d'écran

### 6.1 Production

- Format : JPEG, plus grand côté ≤ 1280 px, qualité 70, ≤ 2 Mo (refus 413 au-delà). Le player capture **son propre rendu** (jamais caméra, jamais autre application).
- Serveur : vérifie les octets magiques, **réencode** (Pillow) pour supprimer EXIF/métadonnées et borner la taille, calcule sha256, stocke dans le stockage objet existant sous le préfixe `screenshots/<screen_id>/<id>.jpg`, **séparé des médias** (jamais servi par `/media`).
- Table `screenshot (id, screen_id FK ON DELETE CASCADE, command_id NULL, trigger, captured_at, received_at, width, height, bytes, sha256, storage_key)`.

### 6.2 Rétention et vie privée

L'écran est public, mais `content.url` peut afficher une page tierce contenant des données personnelles ; la capture est donc traitée comme une donnée sensible :

- Config noyau (`core`, héritée, `x-vds-level: advanced`, niveau admin) :
  - `screenshots.retention_h` : défaut **24**, 1..168, scopes `organization`.
  - `screenshots.max_per_screen` : défaut **10**, 1..50, scopes `organization`.
  - `player.screenshot_interval_min` : défaut **0 = désactivé**, 0 ou 5..1440, scopes `group`, `screen` (jamais tout le parc d'un clic, même règle que `player.kiosk`). Servi dans la programmation résolue (champ optionnel `screenshot: {interval_min}`) ; absent = désactivé.
- Purge : tâche toutes les 15 min, supprime fichier **puis** ligne au-delà de la rétention ou du plafond par écran ; suppression d'écran = suppression des captures. L'audit conserve la demande, jamais l'image.
- Périodique : aucune capture hors ligne mise en file (pas d'historique rattrapé) ; serveur plafonne à 1 capture / min / device (429).
- Lecture : `GET /screenshots/{id}/image` avec session, contrôle de périmètre, `Cache-Control: private, no-store`, `X-Content-Type-Options: nosniff`, `Content-Disposition: inline`, `Content-Security-Policy: default-src 'none'`. Aucune URL publique ou signée longue durée.

### 6.3 Public

Aucune trace visible : pas d'indicateur de capture, pas de message en cas d'échec, `identify` n'affiche que le nom d'écran (pas d'id, d'IP, de version, de révision). Une commande en échec ne change rien à la lecture. `restart` conserve le verrouillage kiosque (lock task réappliqué au démarrage, logique existante).

### 6.4 Écran sans capacité de capture (Pi, web, Tizen)

La carte affiche un **aperçu théorique** clairement libellé (« Aperçu de la programmation, pas une capture réelle ») : vignette de l'élément en cours d'après `GET /fleet/playback` + vignette média existante. Aucun nouvel endpoint.

## 7. Bus et audit

Événements noyau (catalogue, payload Pydantic `@1`, émis par le service, jamais par le player directement) :

```python
class PlayerCommandRequestedV1(BaseModel):   # "player.command_requested@1"
    command_id: UUID; screen_id: UUID; name: str; actor_kind: str; batch_id: UUID | None

class PlayerCommandCompletedV1(BaseModel):   # "player.command_completed@1"
    command_id: UUID; screen_id: UUID | None; name: str
    status: Literal["ok", "failed", "expired", "rejected", "cancelled"]; code: str | None

class ScreenScreenshotCapturedV1(BaseModel): # "screen.screenshot_captured@1"
    screenshot_id: UUID; screen_id: UUID; trigger: Literal["on_demand", "periodic"]; captured_at: datetime
```

Points d'accroche : futur module alertes (`player.command_completed` en échec), statistiques. Aucun abonné en V1.

Audit (journal existant) : `screen.command.requested` (acteur, nom, args, `batch_id`), `screen.command.cancelled`, `screen.command.failed` (statut terminal `failed|expired|rejected`, code). Les `ok` ne sont pas audités (déjà dans `player_command`, évite le bruit des captures). La consultation d'une capture n'est pas auditée en V1 (Q4).

## 8. UI admin

- **Carte écran** (liste et tableau de bord) : dernière capture (ou aperçu théorique libellé) avec horodatage relatif, `alt` = « Capture de <écran>, il y a 3 min » ; menu Actions : Recharger, Identifier, Actualiser la capture, puis (admin) Redémarrer l'application, Redémarrer l'appareil (si capacité), Resynchroniser le cache (Avancé).
- **Fiche écran** : section « Capture » (image, Actualiser, historique des 10 dernières en vignettes) et section « Commandes récentes » (nom, demandeur, statut, cause traduite, Annuler si en attente).
- **Barre d'actions groupées** (lot E) : + Recharger, Identifier, Actualiser les captures, Redémarrer (admin). Redémarrer suit la validation différée 8 s avec « Annuler » (rien n'est envoyé avant). Écrans sans capacité exclus et comptés (« 2 écrans ignorés : non pris en charge »).
- **Tableau de bord (lot I)** : grille des dernières captures du périmètre, filtre site/groupe/« à traiter ». Ce n'est pas un mur d'écrans (module futur non touché).
- États d'une commande : En attente (écran hors ligne) · Envoyée · Réussie · Échec (cause) · Expirée · Annulée · Non prise en charge. Sondage 1 s pendant 20 s puis 5 s jusqu'à état terminal ou expiration ; annonces `aria-live="polite"`.
- Restart : confirmation explicite unitaire (« L'écran sera noir environ 30 s »). Charte : tokens `--vds-*`, aucune ombre, mono seulement pour les codes. Libellés i18n `screens.commands.*`, FR/EN avec pluriels. 375 px : feuille basse, cibles ≥ 44 px.

## 9. Compatibilité vds-core 1.11.0 (mineure, additive)

- Ajouts : capacités et `boot_id`, `command_results` / `results_acked` du heartbeat ; trame `cmd` effectivement émise sur `/ws/player` ; champ optionnel `screenshot` de la programmation ; interface `PlayerAdapter` (sdk-ts) ; `ScreensApi.send_command` (sdk-py) ; permission `screens.command` ; 3 événements `@1` ; migration `0019`.
- Vieux player (1.10) : pas de `capabilities` ⇒ seul `reload` proposé ; il reçoit `commands` au nouveau format qu'il ignore (aucun `type`) ⇒ la commande expire proprement côté serveur. Le serveur n'émet plus `{type:"reload"}`. Vieux serveur : le player 1.11 n'envoie que des champs optionnels, ignorés.
- Modules existants `core: ">=1.0 <2.0"` : chargés sans changement.

## 10. Tests

- pytest (aux deux `VDS_BASE_PATH`) : cycle queued→sent→ok par WS et par heartbeat ; double livraison sans double exécution (résultat identique) ; expiration côté serveur et côté player (horloge décalée) ; annulation ; `request_key` ; plafonds 429 ; changement de device ⇒ expired ; droits (éditeur hors périmètre 404 sans nom, éditeur + `restart` 403, sans périmètre vide, module sans `screens.command` refusé) ; `test_scope_routes` à jour ; upload : magic bytes, 413, 415, 409 (cmd étrangère), réencodage sans EXIF ; purge rétention et plafond (fichier supprimé) ; image servie `no-store` et refusée sans session.
- vitest player : journal `executed` (pas de reboot en boucle), `expired` avec dérive, `identify` n'affiche que le nom, aucune trace DOM d'une capture ou d'un échec, adaptateur absent ⇒ capacités minimales.
- Android (instrumenté ou démo) : `PixelCopy` avec vidéo, lock task conservé après `restart.app`, `restart.device` refusé sans Device Owner.
- Playwright admin : carte avec capture < 15 s (serveur simulé), aperçu théorique libellé, actions masquées selon rôle et capacité, file 8 s, FR/EN, 375 px.
- Démo `docs/demos/commandes.md` : écran réel Android + Pi, capture < 15 s en WS, < 70 s en repli HTTP.

## 11. Découpage

| Tâche | Contenu | Agent | Critère de fin |
| --- | --- | --- | --- |
| I0 | Contrat 1.11.0 : `vds_sdk.wire` (heartbeat, `cmd`, `cmd.result`), événements `@1`, permission, `ScreensApi`, `adapter.ts`, `CORE_API_VERSION`, note de compat | builder | `make lint` vert, JSON Schema et `wire.generated.ts` regénérés, modules existants chargés |
| I1 | Migration `0019` (`player_command`, `screenshot`) + modèles + downgrade | grunt | upgrade/downgrade/upgrade testés |
| I2 | Service de commandes : création (droits, idempotence, plafonds), livraison WS + NOTIFY + repli heartbeat, résultats, balayage d'expiration, annulation, audit, événements | builder | pytest §10 (cycle, droits, idempotence) verts aux deux bases |
| I3 | Captures serveur : upload, réencodage, stockage, lecture, purge, config `screenshots.*` et `player.screenshot_interval_min` | builder | pytest upload/purge/no-store verts |
| I4 | Player : exécution des 5 commandes, journal IndexedDB, capacités/`boot_id`, capture périodique, adaptateur web | builder | vitest §10 verts, aucune trace au public |
| I5 | Android `player.android` : pont `vdsCmd`, `PixelCopy`, restart app/device ; Pi : relance systemd | builder | démo Android : capture avec vidéo, kiosque conservé |
| I6 | Admin : carte, fiche, barre groupée, tableau de bord captures, aperçu théorique, i18n | builder + grunt (i18n, types) | Playwright §10 verts, FR/EN, 375 px |
| I7 | Tests transverses et démo `docs/demos/commandes.md` | qa-tests | toutes les cases de la démo cochées sur matériel réel |
| I8 | Revue sécurité (upload, rétention, droits, boucle de redémarrage, kiosque) | revue-securite | aucun point bloquant ouvert |

Ordre : I0 → I1 → (I2, I3, I4 en parallèle) → I5, I6 → I7 → I8.

## 12. Questions pour Victor

- Q1 Liste : les 5 commandes (`reload`, `restart`, `identify`, `screenshot`, `cache.resync`) conviennent-elles ? « Vider le cache » brut est écarté au profit de la resynchronisation.
- Q2 Éditeur : peut-il recharger / identifier / capturer dans son périmètre (proposé), ou tout réservé à l'admin ?
- Q3 Capture périodique : désactivée par défaut, rétention 24 h, 10 par écran : d'accord ?
- Q4 Consultation des captures auditée (RGPD, pages tierces affichées par `content.url`) ou non (proposé : non en V1) ?
- Q5 Pi sans capture en V1 (aperçu théorique) : acceptable, ou capture via l'agent de site plus tard ?

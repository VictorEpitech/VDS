# VDS — Architecture, Lot 3 : multi-sites et alimentation (vds-core 1.2.0)

Décision architecte du 23 octobre 2026, prise en l'absence de Victor avec des choix par défaut sûrs. Les points marqués **[à confirmer]** sont réversibles sans changement de contrat.
Ce document est la section « Lot 3 » de `docs/architecture.md` (fichier séparé faute d'édition possible du fichier principal au moment de la décision ; à fusionner tel quel).

Principe directeur : le serveur **planifie**, l'agent et le player **exécutent hors ligne** une planification déjà résolue (J + J+1), comme pour la diffusion (règle 5). La cascade et la supervision restent génériques ; tout ce qui parle un protocole constructeur ou un transport (SMTP) est un module.

## 1. Protocoles WebSocket

### 1.1 Commun aux deux canaux

- URL : `<VDS_BASE_PATH>/ws/agent` et `<VDS_BASE_PATH>/ws/player` (même `Mount` que l'API ; testés à `/` et `/espace/affichage`). Le client dérive l'URL WSS de son URL serveur configurée (`VDS_SERVER_URL` pour l'agent, URL saisie pour le player) en ajoutant `ws/agent` / `ws/player` au chemin, jamais à la racine de l'hôte.
- Sous-protocole obligatoire : `vds.agent.v1` / `vds.player.v1` ; absent ou inconnu ⇒ refus (`4426 version_unsupported`).
- Trame JSON unique (texte UTF-8, ≤ 256 Kio, sinon `4400`), modèles Pydantic dans `vds_sdk.wire` (exportés en JSON Schema, types TS générés dans `packages/sdk-ts/src/wire.generated.ts`) :

```python
class Frame(BaseModel):
    v: Literal[1] = 1
    type: str  # "hello", "welcome", "cmd", "cmd.result", "event", "ack", "heartbeat", ...
    id: UUID  # unique par trame
    seq: int | None = (
        None  # présent sur les trames fiables uniquement (cmd, event), croissant par émetteur et par session logique
    )
    ack: int | None = None  # acquittement cumulatif du dernier seq reçu du pair
    ts: datetime  # horloge de l'émetteur (sert à mesurer la dérive)
    data: dict[str, Any] = {}
```

- Codes de fermeture : `4400 bad_message` · `4401 unauthorized` (jeton inconnu) · `4403 forbidden` (cible hors périmètre) · `4408 hello_timeout` (5 s) · `4409 replaced` (nouvelle connexion du même agent/appareil) · `4410 revoked` (`agent_revoked` / `device_revoked`, le client efface ses secrets) · `4426 version_unsupported` · `4429 rate_limited`.
- Limites : 1 connexion active par agent / appareil, 50 trames/s, fenêtre de 500 trames fiables non acquittées.
- Multi-processus serveur : une commande est d'abord écrite dans `remote_command` puis signalée par `NOTIFY vds_command, '<target_kind>:<target_id>'` ; le processus qui tient la connexion la pousse. Aucun état WS partagé en mémoire entre workers.
- Le proxy (Traefik/Dokploy ou Caddy autonome) doit transmettre l'upgrade ; ping protocolaire toutes les 30 s pour rester sous les délais d'inactivité usuels.

### 1.2 `/ws/agent` (agent de site ↔ serveur)

**Jeton d'agent.** L'admin crée un agent pour un site (`POST /api/v1/sites/{site_id}/agents`) : le serveur génère `vds_agt_<32 octets base64url>` (256 bits), le renvoie **une seule fois**, stocke `sha256(jeton)` (`site_agent.token_hash`, unique ; pas de hachage lent : entropie pleine, comme `vds_dev_`). Rotation `POST …/agents/{id}/rotate` (ancien jeton invalide immédiatement, connexion fermée `4410`), révocation `POST …/revoke`. Audit + événements `agent.enrolled|rotated|revoked`. Un seul agent actif par site en V1 (index unique partiel), extensible plus tard.

**Authentification.** En-tête `Authorization: Bearer vds_agt_…` sur la requête d'upgrade (jamais en query string, jamais journalisé). Toute requête portant un en-tête `Origin` est refusée (`403` avant upgrade) : l'agent n'est pas un navigateur, cela ferme le détournement de WebSocket inter-sites. Côté agent : TLS vérifié obligatoire, `VDS_AGENT_CA_FILE` optionnel pour une AC privée, aucune option « insecure ». Le jeton est lu depuis `VDS_AGENT_TOKEN_FILE` (fichier 0600, secret Docker) ou `VDS_AGENT_TOKEN` ; jamais dans le dépôt ni dans la config serveur.

**Poignée de main.**

```text
agent → hello    {agent_version, core: "1.2.0", modules: [{id, version}], last_server_seq, host: {os, arch}, lan: {ipv4: [...]}}
serveur → welcome {agent_id, site_id, session_id, heartbeat_s: 30, server_time, resume_from: <dernier seq agent acquitté>,
                   modules_enabled: [{id, version, config_revision}], plan_revision, cache: {enabled, key_id}}
```

- Modules de l'agent : découverts dans l'image de l'agent (`modules/*/module.json` dont `targets` contient `site-agent`) ; l'agent n'active que l'intersection avec `modules_enabled` à version identique, refuse le reste (rapporté dans `heartbeat.modules`). Aucun téléchargement de code depuis le serveur en V1 (installation à chaud côté agent = point d'accroche).
- Le serveur rejoue ensuite les commandes `queued|sent` non expirées de seq > `last_server_seq` ; l'agent rejoue son outbox local (SQLite) à partir de `resume_from`.

**Fiabilité et reprise.** Trames fiables = `cmd` (serveur → agent), `event` et `cmd.result` (agent → serveur). Chaque côté persiste ses trames fiables jusqu'à `ack` (serveur : `remote_command` ; agent : `outbox` SQLite dans `VDS_AGENT_DATA_DIR`). Idempotence par `id` de trame / `cmd_id`. Reconnexion : backoff exponentiel 1 s → 60 s, gigue ±20 %, sans limite. Une commande dont `expires_at` est dépassé n'est jamais exécutée (`cmd.result{status:"expired"}`).

**Heartbeat.** Ping WebSocket 30 s (pas de pong en 2 intervalles ⇒ fermeture et reconnexion). Trame applicative `heartbeat` toutes les 60 s :

```json
{"uptime_s": 0, "clock_offset_ms": 0, "plan_revision": "…", "modules": [{"id": "power.adb", "status": "ok", "code": null}],
 "screens": [{"screen_id": "…", "power": {"observed": "on|off|standby|unknown", "driver": "power.adb", "at": "…"}}],
 "cache": {"enabled": true, "bytes": 0, "items": 0, "missing": 0}}
```

Le serveur met à jour `site_agent.last_seen_at` / `last_heartbeat` ; agent `offline` après 3 heartbeats manqués (180 s).

**Commandes serveur → agent** (`type: "cmd"`, `data = {cmd_id, name, screen_id?, args, expires_at, correlation_id}`) :

| `name` | `args` | Effet |
| --- | --- | --- |
| `power.set` | `{state: "on"\|"off", reason: "manual"\|"schedule"\|"exception"\|"alert", request_id}` | Cascade immédiate (forçage manuel, futur `alert.raised`) |
| `power.probe` | `{}` | Lecture d'état par le premier pilote capable de `power.state` |
| `power.detect` | `{address?, mac?}` | Assistant de détection (§ 2.4) |
| `plan.updated` | `{revision}` | L'agent recharge `GET <base>/api/v1/agent/plan` (HTTP, ETag) |
| `config.updated` | `{module_id, config_revision}` | Recharge de la config résolue des modules agent |
| `cache.sync` | `{revision}` | Recharge du manifeste de cache (§ 4) |

L'agent n'accepte aucune commande non listée (`cmd.result{status:"rejected", code:"unknown_command"}`). Résultat : `cmd.result {cmd_id, status: "ok"|"failed"|"expired"|"rejected", code, data}`.

**Événements agent → serveur** (`type: "event"`, `data = {name, version, payload, occurred_at}`). Le pont serveur republie sur le bus avec `origin = "agent:<site_id>"` et `source = <module_id ou "agent">`, après trois contrôles : nom déclaré par le module émetteur (même règle que `ScopedBus`), nom dans la liste blanche du pont (`power.*`, `agent.*`, `cache.*`, et `<id module>.*` du module), et tout `screen_id` du payload appartient au site de l'agent (`4403` sinon). Les données lourdes (journaux de détection, captures) passent par HTTP authentifié par le même jeton, jamais par le WS.

**HTTP réservé à l'agent** (même jeton, dépendance `rt.agent_auth`) : `GET <base>/api/v1/agent/plan` (planification d'alimentation résolue du site, § 3.3), `GET <base>/api/v1/agent/modules/{id}/config?screen_id=` (config résolue non secrète), `GET <base>/api/v1/agent/cache/manifest`, lecture des médias du manifeste (`<base>/media/<id>/<variant>` acceptant le jeton d'agent pour les médias programmés sur le site uniquement, via `rt.media_access.register_reader`).

**Fonctionnement hors ligne de l'agent.** Le plan J + J+1 et la config des pilotes sont en cache SQLite ; l'agent exécute seul les bascules on/off à l'heure du site, sans serveur. À la reconnexion, il renvoie les `power.changed` / `power.failed` en attente (outbox).

### 1.3 `/ws/player` (player ↔ serveur, levée du report du lot 2)

- **Authentification** : premier message `{"type":"auth","data":{"token":"vds_dev_…"}}` dans les 5 s (un navigateur ne peut pas poser `Authorization` sur un WebSocket ; la query string est journalisée par les proxys). Pas de cookie ⇒ pas de risque CSWSH ; l'`Origin` n'est donc pas imposé (le relais Android se présente en `http://127.0.0.1:47800`), seulement journalisé. Le relais Android L2-T9 doit relayer l'upgrade WS (ajout L3-T5).
- Le canal est **optionnel** : le heartbeat HTTP 60 s et le polling de `player/schedule` restent la voie nominale de repli (règle 5). Le WS n'apporte que la réactivité et les commandes.
- Serveur → player :

| `type` | `data` | Effet |
| --- | --- | --- |
| `welcome` | `{device_id, screen_id, heartbeat_s: 60, server_time, revision}` | Après auth |
| `schedule.changed` | `{revision}` | Le player refait `GET player/schedule` (ETag) ; jamais de programmation poussée par WS (une seule voie de données) |
| `cmd` | `{cmd_id, name, args, expires_at}` | Voir ci-dessous |

  Commandes player V1 : `reload` (recharge du shell), `identify {seconds≤30}` (affiche le nom d'écran, rien de technique), `screenshot` (capture puis `POST <base>/api/v1/player/screenshots/{cmd_id}`, ≤ 2 Mo, JPEG), `power.set {state, request_id}` (exécutée par les pilotes côté player : `power.cec`, `power.blank`), `power.probe`. Commande inconnue ⇒ `rejected`. Pas de commande de déverrouillage kiosque en V1 (point d'accroche : `maintenance.unlock`).
- Player → serveur : `heartbeat` (même payload que `POST player/heartbeat`, qui reste accepté), `cmd.result`, `event` limité à `power.changed|power.failed` du player.
- Révocation : `4410 device_revoked` ⇒ même effacement qu'au 401 HTTP du lot 2.

## 2. Contrat vds-core 1.2.0 (mineur, additif)

### 2.1 Compatibilité

`CORE_API_VERSION = "1.2.0"`. Les modules `core: ">=1.0 <2.0"` existants se chargent sans changement. Ajouts uniquement : interfaces `PowerDriver` (Python, cible `site-agent`), `PlayerPowerDriver` (TS, cible `player`), `Notifier` (Python, cible `server`), champ optionnel `Event.origin` (défaut `"server"`), événements et permissions ci-dessous, paquet `vds_sdk.wire`. Les kinds `power-driver` et `notifier` existaient déjà au catalogue 1.0 : un module 1.0/1.1 de ces kinds sans entrée exploitable est refusé au chargement avec `contract.kind_requires_1_2` (aucun n'existe). Validation du manifeste ajoutée : `power-driver` ⇒ `targets ⊆ {site-agent, player}` et `capabilities ⊆ {power.on, power.off, power.state, power.input}` ; `notifier` ⇒ `targets = [server]`.

### 2.2 Permissions ajoutées au catalogue

`power.control` (demander un `power.set`, ex. futur module d'alertes) · `power.calendars.write` (alimenter un calendrier d'exceptions) · `incidents.read`. `lan.access` (existant) est requis pour tout pilote réseau ; `net.outbound` pour `notify.mail` et `calendar.fr`.

### 2.3 Interface `PowerDriver` (`vds_sdk.agent.power`)

```python
PowerState = Literal["on", "off", "standby", "unknown"]
Capability = Literal["power.on", "power.off", "power.state", "power.input"]


class PowerTarget(BaseModel):
    screen_id: UUID
    site_timezone: str
    address: IPvAnyAddress | None  # IP LAN de l'écran (config du pilote ou heartbeat player)
    mac: str | None  # pour WoL
    config: dict[str, Any]  # config résolue du module pour cet écran (secrets en SecretStr)


class PowerResult(BaseModel):
    ok: bool
    state: PowerState  # état observé après l'ordre ("unknown" si non vérifiable)
    verified: bool  # True si l'état a été relu
    code: str | None = None  # ex. "power.adb.unreachable", "power.samsung.not_paired"


class DetectHint(BaseModel):
    address: IPvAnyAddress | None
    mac: str | None
    platform: str | None  # "android-tv", "tizen", "pi", "web" (heartbeat player)


class DetectResult(BaseModel):
    driver: str  # id du module
    confidence: float  # 0..1
    capabilities: frozenset[Capability]
    proposed_config: dict[str, Any]  # valeurs à enregistrer au niveau écran, jamais de secret
    requires_user_action: str | None  # clé i18n, ex. "power.samsung.accept_on_tv"
    code: str | None = None


class PowerDriver(Module):
    capabilities: ClassVar[frozenset[Capability]]

    async def set_power(self, target: PowerTarget, state: Literal["on", "off"]) -> PowerResult: ...
    async def get_state(self, target: PowerTarget) -> PowerState: ...  # "unknown" si non supporté
    async def detect(
        self, hint: DetectHint
    ) -> DetectResult | None: ...  # None = absent ; borne 10 s imposée par l'agent
```

Les secrets propres à un appareil (paire de clés ADB de l'agent, jeton Samsung obtenu à l'acceptation sur la TV, empreinte TLS de la TV épinglée au premier contact) restent dans l'état local de l'agent, via `ctx.state` (nouveau, `vds_sdk.agent.StateStore` : `get/set/delete` clé-valeur JSON par module, SQLite) ; ils ne remontent jamais au serveur.

Côté player (`packages/sdk-ts/src/power.ts`) :

```ts
export type PowerState = "on" | "off" | "standby" | "unknown";
export interface PlayerPowerDriver {
  id: string;                                   // "power.cec", "power.blank"
  available(): Promise<boolean>;                // ex. pont VdsNative.cec présent
  setPower(state: "on" | "off"): Promise<{ ok: boolean; state: PowerState; verified: boolean; code?: string }>;
  getState(): Promise<PowerState>;
}
export function registerPowerDriver(d: PlayerPowerDriver): void;
```

### 2.4 Cascade, vérification, détection (runtime agent et player, générique)

- Config noyau (`core`, héritée, scope jusqu'à `screen`) : `power.cascade` (liste ordonnée d'ids de pilotes, défaut `["power.adb","power.samsung-ws","power.wol","power.cec","power.blank"]`, `x-vds-level: advanced`), `power.retries` (défaut 2, advanced), `power.verify_delay_s` (défaut 8, advanced), `power.plan_id` (simple), `power.calendars` (simple, liste de clés de calendrier).
- Exécution : l'agent essaie dans l'ordre les pilotes **agent** actifs et configurés pour l'écran ; ordre puis relecture après `verify_delay_s` ; échec ⇒ retry puis pilote suivant. Le player applique **toujours** en parallèle ses pilotes locaux aux mêmes bornes (CEC `Image View On` + `Active Source` au rallumage, `Standby` à l'extinction, puis `power.blank` : noir plein écran, lecture suspendue). Ainsi l'écran noir garantit le résultat visible même si tout échoue, et le rallumage hors ligne reste assuré par le player (règle 5).
- Résultat : `power.changed@1` (au moins un pilote vérifié, ou `power.blank` seul ⇒ `verified=false`, `driver="power.blank"`), sinon `power.failed@1` après épuisement de la cascade.
- Détection : `power.detect` lance `detect()` de tous les pilotes agent en parallèle (10 s chacun) ; l'adresse vient du heartbeat player (`lan.ipv4`), la MAC de la table ARP de l'agent après un ping. Le serveur renvoie la liste triée par `confidence` ; l'admin propose la cascade et enregistre `proposed_config` en surcharges niveau écran après confirmation.

### 2.5 Interface `Notifier` (`vds_sdk.server.notifier`)

```python
class Recipient(BaseModel):
    email: EmailStr
    locale: Literal["fr", "en"] = "fr"


class Notification(BaseModel):
    id: UUID  # = id de l'incident + transition, clé d'idempotence
    kind: str  # "screen.offline", "agent.offline", "power.failed", "module.quarantined"
    transition: Literal["opened", "resolved"]
    severity: Literal["info", "warning", "critical"]
    subject: dict[
        str, str
    ]  # {"screen": "Accueil RDC", "site": "Campus Nord"} — noms, jamais d'id technique en clair
    occurred_at: datetime
    link: str  # chemin relatif à l'admin, ex. "screens/<id>" ; le module préfixe VDS_PUBLIC_ORIGIN + base


class DeliveryResult(BaseModel):
    ok: bool
    code: str | None = None  # "notify.mail.smtp_auth", "notify.mail.timeout"…
    retry_after_s: int | None = None


class Notifier(Module):
    async def send(self, n: Notification, recipients: list[Recipient]) -> DeliveryResult: ...
```

Un notifier s'abonne à `incident.opened` / `incident.resolved`, choisit ses destinataires (`ctx.core.users.list(role="admin")` sous `users.read` + destinataires de sa config) et appelle son propre `send`. Le noyau ne connaît aucun transport.

### 2.6 Événements ajoutés (`vds_sdk.events`, `CORE_EVENTS_1_2 = CORE_EVENTS_1_1 | …`)

| Événement | Payload v1 | Critique |
| --- | --- | --- |
| `agent.enrolled` / `agent.rotated` / `agent.revoked` | `agent_id, site_id` | non / non / oui |
| `agent.connected` / `agent.disconnected` | `agent_id, site_id, version` / `agent_id, site_id, reason` | non |
| `power.requested` | `request_id, screen_id, state, reason` | oui |
| `power.changed` | `request_id?, screen_id, state, driver, verified` | non |
| `power.failed` | `request_id?, screen_id, state, attempted: [driver], code` | oui |
| `power.plan_changed` | `scope_level, scope_id?` | non |
| `screen.status_changed` | `screen_id, status, previous` (`online\|offline\|scheduled_off\|unseen\|revoked`) | non |
| `incident.opened` / `incident.resolved` | `incident_id, kind, subject_type, subject_id, site_id?, severity` | oui |
| `cache.synced` | `site_id, items, bytes, missing` | non |

`alert.raised` reste réservé au module futur d'alertes prioritaires (il émettra `power.requested{reason:"alert"}` sous `power.control`).

### 2.7 Modules livrés au lot 3

| id | kind | targets | permissions | Config (niveau) |
| --- | --- | --- | --- | --- |
| `power.adb` | power-driver | site-agent | lan.access, screens.read | `host` (simple, scope screen), `port` 5555 (advanced) |
| `power.samsung-ws` | power-driver | site-agent | lan.access, screens.read | `host` (simple), `mac` (simple), `port` 8002 (advanced), `name` affiché sur la TV (advanced) |
| `power.wol` | power-driver | site-agent | lan.access | `mac` (simple, scope screen), `broadcast` (advanced, défaut 255.255.255.255), `port` 9 (advanced) |
| `power.cec` | power-driver | player | — | `active_source` true (advanced) |
| `power.blank` | power-driver | player | — | aucune ; toujours actif, non désactivable |
| `calendar.fr` | trigger | server | net.outbound, power.calendars.write | `school_zone` A/B/C/Corse (simple), `school_holidays_url` (advanced, source open data Éducation nationale) ; jours fériés calculés localement |
| `notify.mail` | notifier | server | net.outbound, users.read, incidents.read, db.own-tables | § 5 |

Tests sans matériel : chaque pilote prend un transport injectable (`AdbTransport`, `SamsungTransport`, émetteur UDP) ; fakes livrés dans `modules/<id>/tests/fakes.py` (serveur ADB TCP minimal `CNXN/AUTH/OPEN shell:`, serveur WebSocket TLS Samsung `ms.remote.control` avec flux de jeton, socket UDP de capture du paquet magique) ; `power.cec` testé avec un faux `VdsNative.cec` sous vitest ; `power.blank` sous Playwright.

## 3. Modèle de données (migration `0003_sites_power`, `down_revision = "0002_diffusion"`, additive)

Conventions de 0002 : `uuid gen_random_uuid()`, horodatages `timestamptz`, minutes depuis minuit, masque `weekdays` 1..127.

```sql
-- si absente de 0001 :
ALTER TABLE site ADD COLUMN timezone text NOT NULL DEFAULT 'Europe/Paris';

CREATE TABLE site_agent (id uuid PK, site_id uuid NOT NULL REFERENCES site(id) ON DELETE CASCADE,
  name text NOT NULL, token_hash text NOT NULL UNIQUE, token_prefix text NOT NULL,  -- 8 premiers car. pour l'admin
  version text, last_seen_at timestamptz, last_heartbeat jsonb, last_hello jsonb,
  enrolled_by uuid REFERENCES user_account(id) ON DELETE SET NULL, rotated_at timestamptz,
  revoked_at timestamptz, revoked_by uuid REFERENCES user_account(id) ON DELETE SET NULL, created_at, updated_at);
CREATE UNIQUE INDEX uq_site_agent_active_site ON site_agent (site_id) WHERE revoked_at IS NULL;

CREATE TABLE remote_command (id uuid PK,
  target_kind text NOT NULL CHECK (target_kind IN ('agent','device')), target_id uuid NOT NULL,
  seq bigint NOT NULL, name text NOT NULL, screen_id uuid REFERENCES screen(id) ON DELETE CASCADE,
  args jsonb NOT NULL DEFAULT '{}', status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued','sent','ok','failed','expired','rejected')),
  code text, result jsonb, correlation_id uuid, created_by uuid REFERENCES user_account(id) ON DELETE SET NULL,
  created_at, sent_at timestamptz, done_at timestamptz, expires_at timestamptz NOT NULL,
  UNIQUE (target_kind, target_id, seq));
CREATE INDEX ix_remote_command_pending ON remote_command (target_kind, target_id, seq) WHERE status IN ('queued','sent');

CREATE TABLE power_plan (id uuid PK, name text NOT NULL, description text, created_by, updated_by, created_at, updated_at);
CREATE TABLE power_plan_rule (id uuid PK, plan_id uuid NOT NULL REFERENCES power_plan(id) ON DELETE CASCADE,
  weekdays smallint NOT NULL CHECK (weekdays BETWEEN 1 AND 127),
  start_minute smallint NOT NULL, end_minute smallint NOT NULL,          -- plage ALLUMÉE ; hors plage = éteint
  CHECK (start_minute >= 0 AND start_minute < end_minute AND end_minute <= 1440), created_at, updated_at);

CREATE TABLE power_exception (id uuid PK,
  scope_level text NOT NULL CHECK (scope_level IN ('organization','site','group','screen')), scope_id uuid,
  starts_on date NOT NULL, ends_on date NOT NULL CHECK (starts_on <= ends_on),
  start_minute smallint, end_minute smallint,                             -- NULL = journée entière
  state text NOT NULL CHECK (state IN ('on','off')), label text NOT NULL,
  created_by, updated_by, created_at, updated_at);
CREATE INDEX ix_power_exception_scope ON power_exception (scope_level, scope_id, starts_on, ends_on);

CREATE TABLE power_calendar (id uuid PK, key text NOT NULL UNIQUE,      -- "fr.public", "fr.school.zone-a"
  module_id text NOT NULL, name jsonb NOT NULL, refreshed_at timestamptz, created_at, updated_at);
CREATE TABLE power_calendar_day (calendar_id uuid NOT NULL REFERENCES power_calendar(id) ON DELETE CASCADE,
  day date NOT NULL, label text NOT NULL, PRIMARY KEY (calendar_id, day));

CREATE TABLE power_state (screen_id uuid PRIMARY KEY REFERENCES screen(id) ON DELETE CASCADE,
  desired text NOT NULL CHECK (desired IN ('on','off')), desired_reason text NOT NULL,
  desired_since timestamptz NOT NULL, observed text NOT NULL DEFAULT 'unknown'
    CHECK (observed IN ('on','off','standby','unknown')),
  observed_at timestamptz, driver text, verified boolean NOT NULL DEFAULT false, last_code text, updated_at);

CREATE TABLE power_action (id uuid PK, screen_id uuid NOT NULL REFERENCES screen(id) ON DELETE CASCADE,
  request_id uuid, state text NOT NULL, reason text NOT NULL, origin text NOT NULL,   -- "agent:<site>", "player"
  driver text, ok boolean NOT NULL, verified boolean NOT NULL, code text, at timestamptz NOT NULL);
CREATE INDEX ix_power_action_screen_at ON power_action (screen_id, at DESC);           -- purge > 30 j

CREATE TABLE incident (id uuid PK, kind text NOT NULL, severity text NOT NULL
    CHECK (severity IN ('info','warning','critical')),
  subject_type text NOT NULL CHECK (subject_type IN ('screen','agent','module','site')), subject_id text NOT NULL,
  site_id uuid REFERENCES site(id) ON DELETE CASCADE, details jsonb NOT NULL DEFAULT '{}',
  opened_at timestamptz NOT NULL, resolved_at timestamptz);
CREATE UNIQUE INDEX uq_incident_open ON incident (kind, subject_type, subject_id) WHERE resolved_at IS NULL;
```

Module `notify.mail` (branche Alembic `notify.mail`) : `m_notify_mail_delivery (id, notification_id uuid, incident_id uuid REFERENCES incident(id) ON DELETE CASCADE, recipient text, status text CHECK (status IN ('sent','failed','pending')), code text, attempts int, sent_at)`, unique `(notification_id, recipient)`.

### 3.1 Résolution de l'alimentation (noyau, résolveur pur `power/resolver.py`)

Pour un écran et un jour local du site : plan = `power.plan_id` résolu par héritage (aucun plan ⇒ toujours allumé, comportement lot 2 inchangé) ; puis jours des calendriers `power.calendars` ⇒ éteint toute la journée ; puis `power_exception` du niveau le plus spécifique (écran > groupe > site > organisation) ; à niveau égal, `off` l'emporte. Sortie : créneaux absolus J + J+1 `{from, to, state}`.

- Player : `GET player/schedule?format=1` gagne un champ additif `power: [{from, to, state}]` (clients lot 2 l'ignorent) ; hors créneau `on` = `power.set off` local + noir, quel que soit le contenu programmé.
- Agent : `GET agent/plan` = `{revision, screens: [{screen_id, timezone, power: [...], cascade: [...], drivers: {<id>: config}}]}` (ETag).
- `power_state.desired` mis à jour par une tâche du noyau à chaque borne ; `power.requested` émis à chaque borne et à chaque forçage manuel.

## 4. Cache média de site

- Rôle : un seul téléchargement WAN par média et par site. L'agent lit `GET agent/cache/manifest` (médias et variantes programmés J + J+1 sur les écrans du site, avec `sha256` et taille), télécharge avec le jeton d'agent, vérifie le sha256, stocke dans `VDS_AGENT_DATA_DIR/media/<sha256>` (adressage par contenu, quota `agent.cache.max_gb` défaut 20, éviction LRU hors manifeste), puis émet `cache.synced`.
- Service LAN **désactivé par défaut** [à confirmer] : `agent.cache.lan_listen` (`0.0.0.0:47900` si activé, config de site, advanced). C'est le seul port en écoute de l'agent, LAN uniquement, jamais exposé vers Internet ni vers le serveur : la règle 7 (aucun port entrant pour la liaison serveur) est préservée, l'activation est un choix explicite de l'admin.
- Autorisation sans dépendance au serveur : URL signées `http://<ip-agent>:47900/m/<sha256>?exp=<epoch>&kid=<key_id>&sig=<hmac>`. Clé HMAC par agent dérivée côté serveur (`HKDF(VDS_SECRET_KEY, "vds-cache" ‖ agent_id ‖ rotation)`), transmise dans `welcome.cache`, renouvelée à la rotation du jeton. Le serveur ajoute à `player/schedule` un champ additif `media[].mirrors: [url]` valable jusqu'à la fin de J+1. Lecture seule, `GET`/`HEAD`/Range, aucun listing, aucune autre route.
- Player : essaie le miroir (délai 3 s), sinon l'URL serveur ; le **sha256 déjà vérifié par le player** garantit l'intégrité, donc HTTP en LAN est acceptable. Contrainte : un player servi en HTTPS (Tizen, PWA) ne peut pas lire un miroir `http://` (contenu mixte) et l'ignore ; Android (relais `127.0.0.1`) et Pi en profitent.

## 5. Supervision et notifier mail

- Noyau, service `supervision` (tâche périodique 30 s, seuils en config `core`, advanced) : statut d'écran `screen.status_changed` = `scheduled_off` si `power_state.desired = off` (aucune alerte), `offline` si 3 heartbeats manqués alors que `desired = on` depuis plus de `core.supervision.grace_s` (défaut 600 s, couvre le démarrage de la TV). Incidents ouverts/résolus (un seul ouvert par sujet) : `screen.offline` (warning), `agent.offline` > 300 s (critical), `power.failed` (warning, résolu au prochain `power.changed` vérifié), `module.quarantined` (critical). Le noyau n'envoie rien : il publie `incident.opened|resolved` (critiques, outbox).
- `notify.mail` (aiosmtplib) : config **uniquement par l'environnement pour la connexion** : `smtp_host` (`x-vds-env: VDS_SMTP_HOST`), `smtp_port` (`VDS_SMTP_PORT`, défaut 587), `smtp_security` (`VDS_SMTP_SECURITY`, `starttls|tls`, défaut `starttls`, pas de mode clair), `smtp_username` (`VDS_SMTP_USERNAME`), `smtp_password` (`x-vds-secret`, `VDS_SMTP_PASSWORD`), `from` (`VDS_MAIL_FROM`) — tous `advanced`, scope `organization`. Modifiables dans l'admin : `notify_admins` (simple, défaut true), `extra_recipients` (simple), `kinds` (advanced, défaut tous), `send_resolved` (advanced, défaut true). Certificat SMTP toujours vérifié. Sans `VDS_SMTP_HOST` : module `degraded` (`notify.mail.not_configured`), rien n'est perdu (incidents en base).
- Mail : sujet et corps traduits (i18n du module, FR/EN selon la locale du destinataire), noms d'écran/site, heure locale du site, lien `VDS_PUBLIC_ORIGIN + VDS_BASE_PATH + /admin/<link>` ; aucun jeton, IP ni trace technique. Envoi idempotent par `(notification_id, recipient)`, 3 tentatives (1, 5, 15 min). Tests : `aiosmtpd` en fixture, aucun secret en dépôt (`.env.example` liste les variables vides).
- Mode hébergé : mêmes variables, fournies par Dokploy ; aucun `if studio`.

## 6. Découpage du lot 3

| Tâche | Agent | Dépend de | Parallèle avec | Critère de fin |
| --- | --- | --- | --- | --- |
| L3-T0 Backlog sécurité mineur (§ 7) | builder + revue-securite | — | T1, T3 | Items 6, 7, 8, 10, 11, 12 fermés avec test chacun, roadmap cochée |
| L3-T1 Contrat vds-core 1.2.0 : `vds_sdk.agent.power` (+ `StateStore`), `vds_sdk.server.notifier`, événements, permissions, validation manifeste par kind, `Event.origin`, `sdk-ts/power.ts` | builder | — | T0 | Tests de contrat ; modules 1.0/1.1 chargent inchangés ; `CORE_API_VERSION = "1.2.0"` |
| L3-T2 Migration `0003_sites_power` + modèles SQLAlchemy + clés config `core` `power.*` / `core.supervision.*` | grunt (builder relit) | T1 | T3 | `alembic upgrade heads` puis downgrade sur PG17 jetable ; modèles mypy strict |
| L3-T3 `vds_sdk.wire` : trames agent v1 / player v1, JSON Schema, `make api-types` étendu à `wire.generated.ts` | grunt | T1 | T2 | Schémas exportés, aller-retour Pydantic ↔ TS testé |
| L3-T4 Serveur `/ws/agent` : jeton (création, rotation, révocation), hello/welcome, `remote_command` + NOTIFY, reprise, pont d'événements, HTTP `agent/*` | builder | T2, T3 | T6, T7 | Tests aux deux `VDS_BASE_PATH` : auth, `Origin` refusé, 4403 hors site, reprise après coupure, commande expirée |
| L3-T5 Serveur + player `/ws/player` : auth premier message, `schedule.changed`, commandes, captures ; relais Android WS | builder | T3, T4 (`remote_command`) | T6, T7 | Playwright : commande `reload`/`identify` ; repli polling si WS coupé ; révocation 4410 |
| L3-T6 Planning d'alimentation : résolveur pur, API plans/exceptions, `power` dans `player/schedule`, `agent/plan`, module `calendar.fr` | builder | T2 | T4, T5, T7 | Tests du résolveur (DST, 24:00, exception vs calendrier, héritage) ; fériés FR calculés |
| L3-T7 Agent de site : config, connexion/backoff, SQLite outbox/état/plan, chargeur de modules, cascade + vérification, exécution hors ligne | builder | T1, T3 | T4, T6 | Tests contre un faux serveur WS : bascule à l'heure serveur coupé, outbox rejouée, image Docker agent en CI |
| L3-T8a Pilotes agent `power.adb`, `power.samsung-ws`, `power.wol` + `detect` + fakes | builder | T1 (T7 pour l'intégration) | T8b | Chaque pilote vert contre son fake ; détection triée |
| L3-T8b Pilotes player `power.cec` (pont `VdsNative.cec` Android) et `power.blank` | builder | T1, T5 | T8a | vitest (faux pont) + Playwright (noir hors plage, rallumage hors ligne) |
| L3-T9 Cache média de site : manifeste, téléchargement, quota, service LAN signé (désactivé par défaut), `mirrors` | builder | T4, T7 | T10 | Test : un seul téléchargement pour N écrans ; signature expirée/forgée refusée ; repli serveur |
| L3-T10 Supervision + incidents + `notify.mail` | builder | T2, T4, T6 | T9 | `scheduled_off` sans incident ; offline ⇒ un mail ouverture + un résolution (aiosmtpd) ; aucun secret journalisé |
| L3-T11 Admin : sites/agents (jeton affiché une fois), plans/exceptions/calendriers, assistant de détection, cascade par écran, incidents, config notifier | builder (admin) | T4, T6, T8a, T10 ; T0 (CSP) | — | vitest + Playwright 2 bases × 2 thèmes, i18n FR/EN complète |
| L3-T12 E2E réel (serveur + agent + fakes + player) aux deux bases, revue sécurité du lot, démo TV réelle | qa-tests puis revue-securite | tout | — | `make e2e` vert ; revue sans bloquant ; `docs/demos/lot-3.md` |

Chemin critique : T1 → T3 → T4 → T9/T10 → T11 → T12. Lancer ensemble au départ : T0, T1 ; puis T2 + T3 ; puis T4, T6, T7, T8a.

## 7. Backlog sécurité mineur : ordre de traitement (L3-T0, avant T4)

1. Les items qui touchent l'authentification, les jetons et les en-têtes de réponse (réutilisés tels quels par `/ws/agent`, `/ws/player` et `agent/*`) : à fermer avant T4/T5 pour ne pas dupliquer un défaut dans les nouveaux canaux.
2. **10 — CSP de l'admin** : avant T11, qui ajoute des écrans admin ; CSP stricte `default-src 'self'`, `connect-src 'self'` (le WS admin n'existe pas), nonce ou hachages, `frame-ancestors 'none'`, testée aux deux `VDS_BASE_PATH` et aux deux thèmes.
3. Les items restants (traitement des médias, player) en parallèle de T6/T7, chacun avec son test de non-régression.
Le détail des items 6, 7, 8, 11, 12 n'est pas consigné dans `docs/` : l'agent de L3-T0 les reprend du rapport de revue sécurité du lot 2 et les classe dans cet ordre.

## 8. Points d'accroche préservés

`alert.raised` → `power.requested{reason:"alert"}` (permission `power.control`) · `maintenance.unlock` (commande player réservée) · installation de module à chaud sur l'agent (`modules_enabled` + version) · plusieurs agents par site (index unique partiel à lever) · pilotes futurs cloud / IR / prise connectée = nouveaux `power-driver` sans changement de contrat · `remote_command` sert aussi aux futures commandes de mur d'écrans.

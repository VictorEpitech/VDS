# Refonte lot 2 — Découverte LAN des TV (`discovery.lan`)

Statut : **décidé par l'architecte** le 30 octobre 2026, dans le cadre des arbitrages de Victor (recherche au clic seulement, ordre enrôlement → `discovery.lan` → navigation). Rien n'est implémenté.
Contrat : **vds-core 1.5.0** (mineur, additif). Source : `docs/refonte/automatisation.md` D4, précisée et resserrée ici. D5 (adresse par MAC, `screen.address_changed`) et D6 (assistant de mise en service complet) restent **hors lot**. À fusionner dans `docs/architecture.md` › Agent de site.

## 1. Décision

Option retenue : **le runtime de l'agent possède le réseau, le module possède l'interprétation.**

- Commande fermée `discovery.scan`, envoyée par le serveur à l'agent du site, routée par le runtime vers le module actif qui déclare la capacité `discovery.lan`.
- Le module intégré `discovery.lan` (agent seulement) ne touche **jamais** un socket : il reçoit du runtime une façade `DiscoveryNet` dont chaque primitive applique elle-même les bornes (plages, ports, débits, tailles). Le module compose ces primitives et transforme les réponses en « appareil présumé » : marque, modèle, plateforme, pilotes suggérés.
- Le serveur n'envoie **aucune cible** : ni IP, ni CIDR, ni URL. L'agent calcule lui-même son périmètre à partir de ses interfaces. Résultat : le serveur (donc un admin, ou un compte compromis en mode hébergé) ne peut pas pointer l'agent vers une adresse choisie. C'est la parade anti-SSRF principale.
- Lancement au clic d'un admin uniquement : aucun minuteur, aucun scan au démarrage ni à la reconnexion de l'agent.

Ce que ça coûte : une façade réseau à maintenir dans le runtime (~300 lignes, testée une fois), un nouveau `kind` de module et une nouvelle permission. En échange : le module est remplaçable (un tiers peut écrire `discovery.lan` mieux renseigné) sans pouvoir élargir ce qu'il touche.

Option écartée : **le module ouvre ses propres sockets, le runtime filtre a posteriori** (comme `power.*`). Plus simple (pas de façade), mais les bornes deviennent une convention que le module peut ignorer ; inacceptable pour un code qui balaie un réseau partenaire. Déjà écartées dans D4 : découverte dans le runtime (métier dans le socle), depuis le serveur (impossible en hébergé, règle 7), scan automatique (IDS, réseaux partenaires).

## 2. Ce que la découverte fait et ne fait pas

Détection passive au sens de la contrainte : on écoute ou on frappe à la porte, on n'entre jamais.

| Source | Action exacte | Ce qu'on en tire |
| --- | --- | --- |
| Voisinage | lecture de `/proc/net/arp` (et `ip neigh` NDP en lecture) ; aucun paquet émis | MAC ↔ IP, fabricant par OUI |
| mDNS | requête multicast « QU » (réponse unicast) depuis un port éphémère, **pas** de bind sur 5353 (cohabite avec avahi) ; services fixes `_googlecast._tcp`, `_androidtvremote2._tcp`, `_adb-tls-connect._tcp`, `_airplay._tcp` | nom, modèle (`md=` TXT), plateforme |
| SSDP | `M-SEARCH` multicast, `ST` fixes : `urn:dial-multiscreen-org:service:dial:1`, `urn:samsung.com:device:RemoteControlReceiver:1`, `urn:schemas-upnp-org:device:MediaRenderer:1` ; puis **un** `GET` de la description XML (règles §6) | fabricant, `modelName`, `friendlyName` |
| Ports | `connect()` TCP puis fermeture immédiate, **zéro octet envoyé**, sur une liste fermée : 5555 (ADB), 8001, 8002 (Samsung WS), 8008, 8009 (Cast), 3000, 3001 (webOS), 9197 (DIAL Samsung) | protocoles exposés ; remplit aussi la table ARP (MAC sans `NET_RAW`) |

Interdit, et vérifiable (§9 D2) : protocole ADB (`CNXN`), handshake WebSocket Samsung, `eureka_info` Cast, toute requête HTTP autre que la description SSDP, tout identifiant ou jeton, tout envoi WoL. Un `connect()` sur 5555 sans `CNXN` ne déclenche pas d'invite RSA.

Filtrage vie privée : le module ne remonte que les hôtes portant **au moins un signal TV** (port de la liste ouvert, service mDNS de la liste, réponse SSDP de la liste). Téléphones, PC et imprimantes vus seulement dans l'ARP ne sortent pas de l'agent. Une TV éteinte (aucun signal) n'est pas détectable : le repli reste « Ajouter une adresse ».

Capacités Linux : aucune. `cap_drop: [ALL]` du service `agent` (revue E8) reste valable ; pas de `NET_RAW` (pas d'ARP actif ni d'ICMP).

## 3. Périmètre réseau (calculé par l'agent)

```
Pour chaque interface UP, non loopback, non docker/veth/bridge (préfixes de nom exclus : docker, br-, veth, virbr, cni, flannel, tailscale, wg, tun, tap) :
  pour chaque adresse IPv4 de l'interface :
    garder si dans 10/8, 172.16/12, 192.168/16 (RFC1918 uniquement ; pas CGNAT 100.64/10, pas link-local, pas public)
    réseau = l'adresse/préfixe de l'interface ; si préfixe < /22 : réduit au /22 qui contient l'IP de l'agent (truncated = true)
Retirer : adresse réseau, broadcast, IP(s) de l'agent.
Au plus 2 réseaux retenus (les deux premiers par ordre d'interface) ; au-delà, ignorés et signalés.
```

IPv6 : NDP en lecture seule et réponses mDNS acceptées si l'hôte a aussi une IPv4 privée ; aucun balayage IPv6. Toute adresse renvoyée (mDNS, SSDP `LOCATION`, ARP) est revalidée contre ce périmètre ; hors périmètre ⇒ ignorée.

## 4. Contrat vds-core 1.5.0 (additif)

`CORE_API_VERSION = "1.5.0"`. Modules 1.0 à 1.4 chargent inchangés. Note de compatibilité dans `vds_sdk` comme pour 1.3 et 1.4.

### 4.1 Manifeste

- Nouveau `kind: "discovery"`, cible `site-agent` uniquement, `core >= 1.5`.
- Capacité réservée `discovery.<scope>` ; seule valeur admise en 1.5 : `discovery.lan`. Un module qui la déclare doit implémenter `Discoverer` (vérifié au chargement, `check_discoverer`) et déclarer la permission `network.discover`.
- Permission nouvelle **`network.discover`** (catalogue fermé) : « sonder le réseau local du site pour y repérer des écrans ». Admise pour `kind: discovery` seulement ; affichée à l'activation.
- Annotation JSON Schema nouvelle **`x-vds-device-address`** : `"ip"` | `"mac"` sur une propriété de configuration de module `power-driver`. Sert à (a) préremplir l'adresse sans que l'admin ni le noyau connaissent les clés d'un pilote, (b) marquer « déjà ajouté ». À poser dans `power.adb` (`host` → `ip`), `power.samsung-ws` (`host` → `ip`, `mac` → `mac` si présent), `power.wol` (`mac` → `mac`). Point d'accroche réutilisé plus tard par D5.

Le module intégré : `modules/discovery.lan/module.json`, `kind: discovery`, `capabilities: ["discovery.lan"]`, `permissions: ["network.discover"]`, config (`x-vds-level: site`) :

```json
{
  "type": "object",
  "properties": {
    "probe_ports": {
      "type": "boolean", "default": true, "x-vds-level": "site",
      "title": "discovery.lan.config.probe_ports"
    }
  },
  "additionalProperties": false
}
```

`probe_ports: false` = voisinage + mDNS + SSDP seulement (réseaux partenaires sensibles). Répond à la question 3 de `automatisation.md` : sondes de ports **activées par défaut**, désactivables au site.

### 4.2 SDK : `vds_sdk.agent.discovery`

```python
PlatformHint = Literal["android", "google_tv", "tizen", "webos", "cast", "unknown"]


class DiscoveryScope(BaseModel):  # construit par le runtime (§3), jamais par le serveur ni le module
    model_config = ConfigDict(frozen=True)
    networks: tuple[IPv4Network, ...]  # 1 ou 2, RFC1918, préfixe >= /22
    truncated: bool
    probe_ports: bool
    deadline: datetime  # UTC, début + 30 s


class DiscoveredDevice(BaseModel):  # extra="forbid", chaînes bornées à 128 car., sans contrôle
    ip: IPv4Address
    mac: str | None = Field(None, pattern=r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
    vendor: str | None = None  # OUI ou SSDP manufacturer
    model: str | None = None  # mDNS md=, SSDP modelName
    name: str | None = None  # friendlyName / instance mDNS (proposé comme nom d'écran)
    platform: PlatformHint = "unknown"
    protocols: frozenset[Literal["adb", "samsung_ws", "cast", "dial", "webos", "airplay"]]
    suggested_drivers: tuple[str, ...] = ()  # ids de modules power-driver, ordre de cascade ; <= 4
    confidence: Literal["high", "medium", "low"] = "low"


class DiscoveryNet(Protocol):  # fourni par le runtime ; chaque appel revalide le périmètre
    def neighbors(self) -> Mapping[IPv4Address, str]: ...  # IP -> MAC, lecture seule
    async def mdns_browse(self, services: Sequence[str], window_s: float) -> AsyncIterator[MdnsRecord]: ...
    async def ssdp_search(self, targets: Sequence[str], window_s: float) -> AsyncIterator[SsdpReply]: ...
    async def ssdp_description(self, reply: SsdpReply) -> SsdpDescription | None: ...
    async def tcp_open(self, ip: IPv4Address, ports: Sequence[int]) -> frozenset[int]: ...
    async def sweep(self, ports: Sequence[int]) -> AsyncIterator[tuple[IPv4Address, frozenset[int]]]: ...


class Discoverer(Protocol):
    scopes: ClassVar[frozenset[str]]  # {"lan"}

    async def discover(
        self,
        scope: DiscoveryScope,
        net: DiscoveryNet,
        report: Callable[[DiscoveredDevice], Awaitable[None]],
    ) -> None: ...
```

`MdnsRecord`, `SsdpReply`, `SsdpDescription` : modèles figés (instance, hôte, IP, port, TXT ≤ 1 Kio ; IP, `ST`, `SERVER`, `LOCATION` ; `manufacturer`, `modelName`, `friendlyName`, `deviceType`). Les ports admis par `tcp_open`/`sweep` sont la liste fermée du §2 (`DISCOVERY_PORTS` dans le SDK) ; tout autre port lève `DiscoveryScopeError`.

Le runtime, pas le module : fusionne les rapports par MAC puis IP, déduplique, rejette tout `DiscoveredDevice` hors périmètre, filtre `suggested_drivers` sur les modules `power-driver` réellement actifs sur l'agent, plafonne à **128 appareils**, coupe à l'échéance.

### 4.3 Wire (`vds_sdk.wire`)

Commande fermée, même mécanique que `screen.provision` (trame `cmd`, progression fiable, `cmd.result`) :

```python
class DiscoveryScanArgs(BaseModel):  # extra="forbid" ; aucune cible réseau
    scan_id: UUID
    scope: Literal["lan"] = "lan"
    probe_ports: bool  # valeur résolue côté serveur depuis la config du site


class DiscoveryBatch(BaseModel):  # progression, au plus 16 appareils par trame, au plus 1 trame / 500 ms
    scan_id: UUID
    seq: int
    devices: list[DiscoveredDevice]


class DiscoveryResult(BaseModel):  # dans cmd.result.data
    scan_id: UUID
    networks: list[IPv4Network]
    truncated: bool
    device_count: int
    capped: bool  # plafond de 128 atteint
    duration_ms: int
```

Échecs (`cmd.result.status = failed`, `code`) : `discovery.no_provider` (aucun module actif), `discovery.no_private_network`, `discovery.busy` (un scan déjà en cours sur l'agent), `discovery.timeout`, `discovery.internal`. Agent < 1.5 ⇒ `unknown_command`.

Délais : budget agent **30 s** (voisinage 0 s, mDNS 4 s et SSDP 4 s en parallèle, balayage de ports 20 s au plus, description SSDP 2 s chacune) ; `expires_at` de la commande = envoi + 60 s ; le serveur passe le scan en `failed/discovery.timeout` à 60 s sans `cmd.result` (expiration paresseuse à la lecture). Débit de balayage : 64 connexions simultanées, 400 ms par `connect()`, 1 024 hôtes × 8 ports au plus.

### 4.4 Événements (payload Pydantic `@1`, bus simple, aucun critique)

| Nom | Payload |
| --- | --- |
| `discovery.started@1` | `scan_id, site_id, agent_id, requested_by, probe_ports` |
| `discovery.completed@1` | `scan_id, site_id, device_count, capped, truncated, duration_ms` |
| `discovery.failed@1` | `scan_id, site_id, code` |

Pas d'événement par appareil : la liste vit dans la base, les consommateurs futurs la lisent par l'API. Ajoutés à `CORE_EVENTS_1_5`. Aucune adresse, MAC ni nom d'appareil dans un événement.

`discovery.batch` (agent → serveur, payload DiscoveryBatch) est un message de transport fiable, hors catalogue vds-core : il porte des adresses et des MAC et n'est jamais publié sur le bus. Le pont le remet au service discovery du noyau ; les modules ne voient que `discovery.started/completed/failed`. Le contrat ne change pas (1.5.0).

## 5. Serveur

### 5.1 Migration `0009_discovery_scan` (branche `core`, réversible)

```
discovery_scan
  id             uuid pk
  site_id        uuid not null fk site(id) on delete cascade
  agent_id       uuid null fk site_agent(id) on delete set null
  requested_by   uuid null fk app_user(id) on delete set null
  status         text not null check (status in ('pending','running','completed','failed'))
  probe_ports    boolean not null
  error_code     text null
  networks       cidr[] not null default '{}'
  truncated      boolean not null default false
  capped         boolean not null default false
  created_at     timestamptz not null default now()
  finished_at    timestamptz null
index partiel unique (site_id) where status in ('pending','running')   -- un scan à la fois par site

discovery_device
  id             uuid pk
  scan_id        uuid not null fk discovery_scan(id) on delete cascade
  ip             inet not null
  mac            macaddr null
  vendor, model, name   text null (<= 128)
  platform       text not null
  protocols      text[] not null
  suggested_drivers text[] not null
  confidence     text not null
  unique (scan_id, ip)
```

Purge : scans de plus de 24 h (tâche de purge existante). Seul le dernier scan d'un site sert à l'admin.

### 5.2 API (toutes sous `<base>/api/v1`, donc valables pour `VDS_BASE_PATH=/` et `/espace/affichage`)

| Méthode et chemin | Accès | Réponse |
| --- | --- | --- |
| `GET /sites/{site_id}/discovery/capabilities` | admin | `{available: bool, reason: "agent_missing"\|"agent_offline"\|"agent_outdated"\|"module_disabled"\|null, probe_ports: bool}` |
| `POST /sites/{site_id}/discovery/scans` `{}` | admin, cookie, même origine | 202 `{scan_id, status:"pending"}` ; 409 `discovery.unavailable` (+ `reason`), 409 `discovery.busy` ; 429 `discovery.cooldown` + `Retry-After` |
| `GET /sites/{site_id}/discovery/scans/latest` | admin | 200 `ScanOut` ; 404 si aucun scan de moins de 24 h |
| `GET /sites/{site_id}/discovery/scans/{scan_id}` | admin | 200 `ScanOut` (l'admin interroge toutes les 1 s pendant `running`, pas de WS admin en V1) |

```
ScanOut = {id, status, error_code, created_at, finished_at, networks[], truncated, capped,
           devices: [{id, ip, mac, vendor, model, name, platform, protocols[], suggested_drivers[],
                      confidence, known_screen_id: uuid|null}]}
```

`known_screen_id` : calculé à la lecture en comparant `ip`/`mac` aux valeurs résolues des propriétés annotées `x-vds-device-address` des écrans du site. Le noyau ne connaît aucune clé de pilote.

Aucun endpoint « adopter » : préremplir puis créer passe par l'API existante (création d'écran, `PUT` de config niveau écran), voir §7. Aucun endpoint d'annulation (budget 30 s).

Le corps du `POST` est vide : `probe_ports` est la valeur résolue de `discovery.lan.probe_ports` du site. Pas de CIDR, pas de port, pas de cible acceptés (`extra="forbid"` ⇒ 422).

### 5.3 Réception des résultats

Les trames `DiscoveryBatch` sont acceptées seulement de l'agent **courant** du site du scan, pour un `scan_id` en `pending|running` (sinon ignorées et journalisées). Le serveur revalide chaque appareil : IP dans un des `networks` déclarés par le `cmd.result`, ces réseaux RFC1918 et préfixe ≥ /22 ; plafond 128 lignes par scan appliqué aussi côté serveur. Premier lot ⇒ `running`. `cmd.result` ⇒ `completed|failed` + événement.

## 6. Sécurité

| Risque | Parade |
| --- | --- |
| SSRF : utiliser l'agent pour joindre une adresse choisie | aucune cible dans l'API ni la commande ; périmètre calculé par l'agent ; façade `DiscoveryNet` seule voie réseau du module ; ports en liste fermée ; adresses de toute réponse revalidées |
| SSDP `LOCATION` piégé (rebond vers un autre hôte, interne ou public) | `GET` seulement si l'hôte de `LOCATION` est l'IP littérale qui a répondu, dans le périmètre, port ≥ 1024 ou 80 ; pas de nom DNS, pas de redirection, pas de cookie, `Content-Length` ≤ 64 Kio et lecture coupée à 64 Kio, 2 s, analyse XML par `defusedxml` (pas d'entités, pas de DTD) ; au plus 1 description par IP et 32 par scan |
| Données hostiles dans les noms et modèles (XSS, contrôle, taille) | chaînes ≤ 128 caractères, caractères de contrôle retirés à l'agent et au serveur ; l'admin les rend en texte, jamais en HTML |
| Connexion intrusive ou authentification | primitives sans envoi de données sur TCP ; pas de client ADB, Samsung WS ni HTTP générique dans la façade ; test d'architecture qui interdit l'import de `socket`, `asyncio.open_connection`, `httpx` dans `modules/discovery.lan` |
| Balayage perçu comme intrusif, IDS | au clic d'un admin seulement ; 1 scan à la fois par site ; 60 s minimum entre deux scans d'un site ; 20 scans / heure / site ; ports désactivables au site ; RFC1918 de l'agent, /22 max, 2 réseaux max |
| Abus par un admin (mode hébergé : admin d'un client) | rôle `admin` seul, même origine ; limité au site dont l'agent est concerné ; audit |
| Vie privée (MAC d'appareils personnels) | seuls les hôtes avec signal TV sortent de l'agent ; conservation 24 h ; aucun appareil dans les événements ni l'audit |
| Amplification du risque ADB ouvert (P10) | la liste rend visibles les TV ADB du LAN à l'admin : bandeau rappelant le VLAN des écrans quand `adb` apparaît |
| Réseau du site trop large (10.0.0.0/8 à plat) | réduit au /22 de l'agent, `truncated` affiché (« seule une partie du réseau a été parcourue ») |

Audit : `site.discovery.started` (acteur, site, scan_id, probe_ports) ; `site.discovery.finished` (scan_id, status, device_count, networks). Aucun appareil, aucune MAC.

## 7. Admin

- Bouton « Rechercher les écrans » dans le panneau Site › Écrans et dans l'assistant d'ajout d'écran. Affiché seulement si `capabilities.available` ; si `reason = agent_outdated` ou `module_disabled`, message actionnable ; si `agent_missing` (cas du mode hébergé sans agent, et de tout site sans agent), **masqué**, la saisie d'adresse reste. Aucune lecture du mode.
- Liste en direct : nom, marque, modèle, IP (police mono), MAC (mono), protocoles, badge « Déjà ajouté » (lien vers l'écran), pilotes suggérés. Cases à cocher ; bouton « Ajouter une adresse » en repli.
- Préremplissage à l'ajout : nom d'écran = `name` sinon `vendor model` ; `core.power.cascade` = `suggested_drivers` ; pour chaque pilote suggéré, la propriété annotée `x-vds-device-address: "ip"` reçoit `ip`, celle annotée `"mac"` reçoit `mac` si connue ; écrit au niveau écran par l'API de config existante (valeur + provenance). Android ⇒ propose l'assistant `screen.provision` avec l'adresse préremplie (`?address=`).
- Toutes les URL passent par la base d'API existante (`VDS_BASE_PATH`) ; libellés `discovery.*` FR/EN.

## 8. Agent (runtime)

`vds_agent.discovery` : routage `discovery.scan` vers l'unique module `discovery.lan` actif ; calcul du périmètre (§3) ; implémentation de `DiscoveryNet` ; verrou « un scan à la fois » ; fusion, plafond, échéance ; envoi des lots ; `cmd.result`. Le module reçoit `report()` et rien d'autre (pas de `StateStore` requis).

## 9. Tâches

Chaque tâche serveur, agent et admin est testée aux deux `VDS_BASE_PATH` (`/` et `/espace/affichage`).

| Id | Agent | Contenu | Critère de fin vérifiable |
| --- | --- | --- | --- |
| D1 | `builder` | Contrat 1.5.0 : `CORE_API_VERSION`, `kind: discovery`, capacité `discovery.<scope>` + `check_discoverer`, permission `network.discover`, annotation `x-vds-device-address`, `vds_sdk.agent.discovery` (§4.2), `DiscoveryScanArgs`/`DiscoveryBatch`/`DiscoveryResult` dans `vds_sdk.wire`, `DISCOVERY_PORTS`, payloads `CORE_EVENTS_1_5`, note de compatibilité | `test_compat_15.py` vert : modules 1.0–1.4 chargent inchangés ; `discovery.lan` sans `network.discover` refusé ; `network.discover` sur un `power-driver` refusé ; `DiscoveryScanArgs` avec un champ `cidrs` ⇒ erreur ; MAC mal formée refusée ; agent 1.4 simulé ⇒ `unknown_command` |
| D2 | `builder` | Agent runtime : périmètre §3, façade `DiscoveryNet` (voisinage, mDNS QU, SSDP + description bornée, `tcp_open`, `sweep`), verrou, fusion, plafond 128, échéance 30 s, lots ≤ 16 | pytest `apps/site-agent` : interfaces simulées (`/16` ⇒ /22 + `truncated`, docker0 et 100.64/10 exclus, IP publique seule ⇒ `no_private_network`) ; port 22 ⇒ `DiscoveryScopeError` ; `LOCATION` vers autre IP, nom DNS, redirection, 65 Kio, DTD ⇒ ignorés ; capture : zéro octet envoyé après `connect()` ; 2ᵉ scan concurrent ⇒ `busy` |
| D3 | `builder` | Module `modules/discovery.lan` 0.1.0 : manifeste, config `probe_ports`, empreintes (mDNS, SSDP, ports, OUI filtré TV), `suggested_drivers`, filtre « signal TV », i18n FR/EN | tests sur captures rejouées (Android TV TCL, Google TV, Samsung Tizen, LG webOS, Chromecast, imprimante, téléphone) : marques/modèles/pilotes attendus, imprimante et téléphone absents ; test d'architecture : aucun import réseau dans le module |
| D4 | `builder` | Serveur : migration `0009`, service de scan, endpoints §5.2, réception des lots §5.3, expiration 60 s, cooldown et quotas, `known_screen_id` par annotation, événements, audit, purge 24 h, annotations posées dans `power.adb`, `power.samsung-ws`, `power.wol` | pytest aux deux bases : éditeur 403 ; `Origin` étranger 403 ; corps avec `cidrs` 422 ; site sans agent ⇒ `available:false, agent_missing` ; agent 1.4 ⇒ `agent_outdated` ; 2ᵉ POST ⇒ 409 `busy` puis 429 à moins de 60 s ; lot d'un autre agent ignoré ; IP hors `networks` rejetée ; 129ᵉ appareil rejeté ; sans résultat à 60 s ⇒ `failed/discovery.timeout` ; `known_screen_id` renseigné pour un écran de même MAC ; `downgrade` puis `upgrade` OK |
| D5 | `builder` (skills `vds-ui-charte`, `vds-i18n`) | Admin : bouton conditionnel, liste en direct (sondage 1 s), « Déjà ajouté », cases, préremplissage par annotation et cascade, renvoi vers l'assistant Android, bandeau VLAN si `adb`, `truncated`/`capped` expliqués | vitest + Playwright (serveur simulé) 2 bases × 2 thèmes, FR/EN : bouton absent quand `agent_missing` ; aucun libellé en dur ; nom piégé `<img onerror>` rendu en texte ; ajout ⇒ `PUT` config avec les clés tirées du schéma (aucune clé de pilote codée en dur dans l'admin) |
| D6 | `qa-tests` | E2E réel (`make e2e-agent`) : réseau Docker de test avec faux appareils (répondeur mDNS Cast, répondeur SSDP Samsung, port 5555 ouvert, hôte « imprimante ») | aux deux bases : clic ⇒ liste en moins de 35 s ; imprimante absente ; ajout ⇒ écran créé avec `power.adb.host` et cascade, provenance « écran » ; aucun paquet ADB `CNXN` vu par le faux appareil |
| D7 | `revue-securite` | Façade réseau, SSDP `LOCATION`, quotas, vie privée, permissions, audit | aucun point bloquant |

Hors lot : D5 de la refonte (adresse par MAC, `screen.address_changed`), assistant « Mettre en service un site » (D6 de la refonte), découverte IPv6 active, autres scopes `discovery.<x>`.

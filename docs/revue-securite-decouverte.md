# Revue de sécurité : découverte LAN (D7)

Périmètre : commits 535ab9e, cc16730, 4813075, 12757b5, fa4e557, d218cd6 ; spec `docs/architecture-découverte.md`.
Méthode : lecture seule du code. Aucune connexion réseau, aucun test lancé. `uv pip audit` et `pnpm audit` n'ont pas été lancés (aucune nouvelle dépendance dans le lot, `httpx` était déjà présent).

**Verdict : non bloquant pour le lot.** Aucun constat bloquant. Un constat majeur (M1) à corriger dans un lot proche. Six constats mineurs.

## Points vérifiés conformes

- **Pas de cible venant du serveur** : `DiscoveryScanArgs` contient seulement `scan_id`, `scope="lan"` et `probe_ports`, avec `extra="forbid"` (`packages/sdk-py/src/vds_sdk/wire/messages.py`). Le corps du POST, `ScanCreate`, est vide et en `extra="forbid"` (`apps/server/src/vds_server/discovery/schemas.py:19-22`). L'agent valide les arguments avec `validate_for("agent")` avant de les distribuer (`apps/site-agent/src/vds_agent/runtime.py:441`).
- **Périmètre calculé par l'agent** (`apps/site-agent/src/vds_agent/discovery_net.py:119-152`) : RFC1918 seulement, loopback et link-local exclus, interfaces docker/veth/tailscale/wg… exclues, réseaux plus larges que /22 ramenés à /22, deux réseaux au plus. Les adresses de l'agent lui-même, l'adresse réseau et l'adresse de diffusion sont exclues (`:100-103`). Les sources UDP hors périmètre sont ignorées (`:439-444`).
- **Ports** : liste fermée `DISCOVERY_PORTS`, sans le port 22 (`packages/sdk-py/src/vds_sdk/agent/discovery.py:20`). Elle est vérifiée par `tcp_open` et `sweep` (`discovery_net.py:498,506`). Le sondage fait un `connect()` puis ferme, sans écrire un seul octet (`:328-337`).
- **Description SSDP** : seul le schéma `http` est accepté, l'hôte doit être une IP littérale (un nom DNS est refusé) égale à l'IP source, sans identifiants, sur le port 80 ou un port ≥1024 (22 est donc exclu). Les redirections ne sont pas suivies, `trust_env=False` (pas de proxy), 64 Kio au plus, 2 s, 32 descriptions au plus par scan et une par IP (`discovery_net.py:446-491`, `:355`).
- **Bornes** : budget de 30 s (`discovery.py:26,175`), 128 appareils (`discovery.py:86`, revérifié côté serveur `service.py:481`), un scan à la fois sur l'agent (`discovery.py:144`), un scan actif par site imposé par un index unique partiel (`migrations/versions/0010_discovery_scan.py:32`, `IntegrityError` → 409), pause de 60 s et quota de 20 scans par heure (`service.py:296-305`), TTL de 60 s (`service.py:43,130-142`).
- **Champs d'appareil** : caractères de contrôle retirés, 128 caractères au plus (`discovery_net.py:57-62`, `Text128`). TXT mDNS limité à 64 clés et 1 Kio. Paquets UDP tronqués à 9000 octets, file de 512 entrées. Pointeurs DNS limités à 8 sauts. L'admin affiche ces champs en texte React, sans `dangerouslySetInnerHTML` ni lien construit à partir d'une IP (`apps/admin/src/features/discovery/DiscoveryPanel.tsx:105-123`).
- **IP et MAC hors du bus** : `discovery.batch` est intercepté par le pont avant `_source` et n'est jamais publié (`apps/server/src/vds_server/agents/bridge.py` `republish`, d218cd6). `discovery.started/completed/failed` ne portent que des identifiants et des compteurs (`service.py:115-127,349-361`). L'audit ne contient aucun appareil, seulement les CIDR (`service.py:101-114`). Les journaux du serveur et de l'agent ne contiennent ni IP ni MAC (`service.py:456,466`, `discovery.py:73`).
- **Lots** : un lot n'est accepté que de l'agent courant du site du scan, pour un scan en `pending|running` (`service.py:455-467`). Chaque IP est revalidée RFC1918 à la réception, puis contre les `networks` du résultat à la clôture (`service.py:478,540-554`). Les codes d'erreur de l'agent sont assainis (`_clean_code`).
- **Rôles, IDOR, même origine** : les 4 routes exigent `require("admin")` (`api/v1/discovery.py:18`). Les admins ont une portée globale, avec une seule organisation dans les deux modes. `GET /scans/{scan_id}` filtre sur `id` **et** `site_id` (`service.py:399-403`), donc pas d'IDOR entre sites. Le POST passe par `SameOriginMiddleware` (non exempté, pas de Bearer, `middleware/same_origin.py`).
- **Endpoint générique** : `discovery.scan` figure dans `INTERNAL_AGENT_COMMANDS` (`agents/service.py:158,190`) et un test le couvre (`tests/test_discovery.py:510`).
- **Mode hébergé** : le bouton est masqué si `agent_missing`, sans lecture du mode. L'admin ne choisit aucune cible, donc un compte hébergé compromis ne peut pas viser une adresse.

## Majeur

### M1. SSRF GET dans le LAN par réponse SSDP usurpée (chemin, requête et port contrôlés par l'attaquant)

**Statut : corrigé (agent:). `ssdp_description` exige une IP déjà vue par ARP, mDNS ou port ouvert ; ports de LOCATION en liste fermée (80, 1400, 7676, 8008, 8060, 9197, 52235) ; LOCATION avec chaîne de requête ou chemin > 256 caractères refusée. Tests : IP non corroborée, port hors liste avec requête, ARP. Résiduel : un usurpateur qui se fait passer pour un hôte réellement présent (routeur) reste possible, mais seulement vers un chemin sans requête et un port UPnP connu.**
`apps/site-agent/src/vds_agent/discovery_net.py:240-258` (`parse_ssdp`) et `:461-475` (`ssdp_description`).

**Scénario.** Un hôte du LAN (réseau invité, appareil compromis) écoute les M-SEARCH. Il répond en UDP avec une **IP source usurpée**, par exemple `192.168.1.1` (le routeur), ou une IP du **second réseau** de l'agent quand celui-ci a deux interfaces. Avec `rp_filter` en mode lâche, réglage par défaut de systemd, le paquet est accepté. La réponse porte `LOCATION: http://192.168.1.1:8080/cgi-bin/reboot.cgi?action=…`. Le contrôle `host == reply.ip` est alors satisfait. L'agent émet un GET sans authentification vers un chemin et une requête choisis par l'attaquant, sur tout port 80 ou ≥1024, contre n'importe quelle IP du périmètre. Les deux usages : déclencher des actions GET à effet de bord sur des équipements embarqués, et rebondir d'un VLAN à l'autre via un agent à deux interfaces. Ce que l'attaquant peut lire en retour reste faible (4 champs XML de 128 caractères), mais l'action est menée avec la position réseau de l'agent.

**Correctif.**
- N'accepter `ssdp_description` que si l'IP est corroborée par une autre source du même scan : entrée ARP (`neighbors()`), port ouvert, ou réponse mDNS.
- Restreindre le port de `LOCATION` à une liste fermée de ports UPnP connus (80, 1400, 7676, 8008, 8060, 9197, 52235…) au lieu de « ≥1024 ».
- Refuser une `LOCATION` qui contient une chaîne de requête et limiter la longueur du chemin.
- Ajouter un test : réponse usurpée avec query vers un port hors liste ⇒ aucun GET.

## Mineurs

### m1. Contournement du filtre DTD/ENTITY par un encodage non ASCII

**Statut : corrigé. `_parse_xml` refuse tout document non décodable en UTF-8 strict (UTF-16/32, BOM) ou contenant un NUL, avant le filtre DOCTYPE/ENTITY, puis analyse la chaîne décodée. Aucune dépendance ajoutée. Tests UTF-16/32.**
`discovery_net.py:263-271`. La recherche de `<!doctype` et `<!entity` se fait sur des octets bruts. Un document UTF-16 (avec BOM) ou UTF-32 passe le filtre, puis `ET.fromstring` l'analyse avec sa DTD interne. Les entités externes ne sont pas résolues par ElementTree, et l'expat embarqué (≥2.4) limite l'amplification de type « billion laughs ». Avec un document de 64 Kio au plus, l'impact se limite à un coût CPU et à une expansion bornée.
**Correctif** : utiliser `defusedxml.ElementTree.fromstring(..., forbid_dtd=True)`, ou refuser tout document qui ne commence pas, après espaces, par `<?xml` / `<` en ASCII ou UTF-8 (rejeter BOM UTF-16/32), et ajouter un test UTF-16.

### m2. `probe_ports=false` (réglage du site) appliqué seulement par convention du module

**Statut : corrigé. `LanDiscoveryNet.probe_ports` (renseigné par le runtime depuis les arguments du scan) ; `sweep`/`tcp_open` lèvent `DiscoveryScopeError("discovery.ports_disabled")` quand il vaut false. Test dédié.**
`modules/discovery.lan/agent/module.py:59`. La façade `LanDiscoveryNet.sweep` / `tcp_open` (`discovery_net.py:497-531`) ne connaît pas `probe_ports` (`discovery.py:163-169`). Un autre module `discovery` ou une régression balaierait les ports alors que l'admin les a désactivés pour un réseau partenaire, au risque d'alertes IDS. C'est contraire au principe de la spec §2 : les bornes sont dans la façade, pas dans la convention.
**Correctif** : passer `probe_ports` à `LanDiscoveryNet`, faire lever `DiscoveryScopeError("discovery.ports_disabled")` à `sweep`/`tcp_open` quand il vaut false, et ajouter un test.

### m3. La façade n'est pas une frontière d'isolation (module chargé dans le processus de l'agent)

**Statut : risque accepté (V1). Modules d'agent chargés depuis le disque local, jamais installés à chaud depuis le serveur. À faire avant d'ouvrir `kind: discovery` à des modules tiers : proxy exposant les 6 méthodes seulement, signature obligatoire. Limite à documenter dans `docs/architecture-découverte.md` §6.**
`apps/site-agent/src/vds_agent/discovery.py:175`, `discovery_net.py:355`. Le module reçoit l'objet `net` dans le même processus Python. Il peut donc utiliser `net._http` (client httpx sans bornes d'URL) ou `import socket`. Les bornes ne protègent que contre un module honnête mais bogué. Les modules d'agent viennent du disque local, pas d'une installation à chaud depuis le serveur, ce qui limite le risque.
**Correctif** : documenter cette limite dans `docs/architecture-découverte.md`, rendre `_http` inaccessible (fermeture ou objet proxy exposant seulement les 6 méthodes), et exiger une signature pour tout module `kind: discovery` qui n'est pas intégré.

### m4. Usurpation d'identité d'une TV et confusion visuelle Unicode

**Statut : corrigé côté agent. `clean()` retire les catégories Unicode Cc et Cf (bidi, largeur nulle, BOM). Test. Reporté côté admin (affichage de la confiance et de la MAC avant préremplissage, pas de présélection `low`) : à voir avec frontend.**
`discovery_net.py:54-62`. `clean()` ne retire que les contrôles ASCII. Les caractères bidi (U+202A-202E, U+2066-2069) et de largeur nulle passent. N'importe quel hôte du LAN peut s'annoncer en mDNS ou SSDP comme « Samsung QM55 – Accueil ». L'admin le sélectionne, le formulaire préremplit l'IP dans la config du pilote, puis le pilote (ADB, Samsung WS) se connecte à l'hôte de l'attaquant. Le jeton d'appairage Samsung ou la clé ADB peuvent alors être présentés à un faux appareil.
**Correctif** :
- Retirer les catégories Unicode `Cf` et `Cc` dans `clean()`.
- Afficher la `confidence` et la MAC de façon visible avant le préremplissage.
- Ne pas présélectionner un appareil de confiance `low`.

### m5. Traces d'exception pouvant contenir des données d'appareil

**Statut : corrigé. `log.error("discovery.module_failed", extra={"error": type(exc).__name__})`, sans trace. Test : aucune IP ni MAC dans les journaux.**
`apps/site-agent/src/vds_agent/discovery.py:179-181`. `log.exception("discovery.module_failed")` journalise la trace complète d'une exception du module. Une `ValidationError` pydantic inclut `input_value`, qui peut contenir une IP, une MAC ou un nom. Cela contredit la règle « pas d'IP/MAC dans les logs ».
**Correctif** : journaliser seulement `type(exc).__name__`, ou une trace sans arguments.

### m6. Écritures déclenchées par des GET non soumis au contrôle de même origine

**Statut : reporté (serveur non touché, hors périmètre de ce correctif). À signaler à backend : sortir `expire_stale` des GET (tâche périodique ou POST seulement) pour garder les lectures sans écriture. Effet limité à une expiration anticipée de quelques secondes.**
`apps/server/src/vds_server/discovery/service.py:366-372`. `GET /scans/latest` et `GET /scans/{id}` exécutent `expire_stale` : mise à jour de `remote_command` et du scan, audit, publication d'événements. Un site tiers peut déclencher ces écritures en GET cross-site, avec le cookie selon son attribut `SameSite`. L'effet se limite à une expiration anticipée de quelques secondes de ce qui aurait expiré de toute façon. Pas d'élévation possible, mais cela entorse la règle 13.
**Correctif** : faire l'expiration dans une tâche périodique, ou en écriture seulement (POST), et garder les GET en lecture seule.

## Notes (sans action requise)

- `apps/server/src/vds_server/discovery/service.py:208` : une config illisible renvoie `probe_ports=True` (le choix le plus intrusif). Préférer `False` en cas d'erreur.
- La recherche du doublon dans `discovery.py:81-84` est linéaire, bornée à 128 appareils : acceptable.

# Refonte : installation et mise en service pilotées depuis l'admin

Statut : **proposition de l'architecte, à arbitrer par Victor** (30 octobre 2026). Rien n'est implémenté.
Après arbitrage, reporter dans `docs/architecture.md` et ajouter une ligne dans `docs/roadmap.md` › Décisions.

Objectif : lancer un agent de site et mettre une TV en service depuis l'admin, avec le moins de clics et de saisies.
Contraintes : noyau minimal, agent sans port entrant (règle 7), aucune hypothèse d'hébergeur (règle 11), mode hébergé = préréglage, modules futurs = points d'accroche seulement.

Existant réutilisé tel quel : création d'agent (`site_agent`, jeton `vds_agt_` remis une fois), `<base>/ws/agent`, commandes `power.detect|probe|set`, assistant « Ajouter une TV Android » (`screen.provision`, ticket d'appairage), plans d'alimentation, `identify`.
Aujourd'hui, le point de friction : copier un jeton long dans `docker/agent.env`, construire l'image, lancer le compose, puis saisir l'IP de chaque TV à la main.

## Constat de départ

Le serveur ne peut pas atteindre le LAN du site (hébergé : jamais ; autonome : pas toujours). « Lancer un agent depuis le web » veut donc dire : **le web fabrique tout ce qu'il faut, l'humain colle une seule ligne sur une machine du LAN, et l'admin voit l'agent arriver tout seul**. Tout ce qui suit l'enrôlement passe par le WebSocket sortant déjà en place.

## Décisions proposées

### D1. Enrôlement par code à usage unique, jamais par jeton d'agent affiché

L'admin clique « Installer l'agent » sur un site. Le serveur émet un **code d'enrôlement** `vds_enr_<128 bits base32>` : usage unique, 60 min, stocké en sha256, lié au site, révocable. L'agent le présente une fois et reçoit en retour son jeton `vds_agt_`, qu'il chiffre dans son état. Le jeton d'agent n'apparaît plus jamais à l'écran ni dans l'historique du shell.

```
POST <base>/api/v1/agent/enroll            (sans cookie, sans Bearer ; limité par IP)
  req : {"code": "vds_enr_…", "hostname": str, "agent_version": "1.4.0", "platform": "linux/arm64"}
  200 : {"agent_id": uuid, "site_id": uuid, "token": "vds_agt_…", "ws_path": "ws/agent"}
  401 enroll.invalid | 410 enroll.expired | 409 enroll.consumed | 429
```

Si le site a déjà un agent, la consommation **remplace** son jeton : l'ancienne session est fermée en 4411 `token_rotated` (existant). C'est aussi le parcours « Réinstaller / déplacer l'agent ».

Rejetées : afficher `vds_agt_` dans la commande (jeton long-vivant dans l'historique shell et les captures d'écran) ; code court 6 caractères comme l'appairage des écrans (exposé à Internet en mode hébergé, attaquable) ; appairage inversé « l'agent affiche un code, l'admin le saisit » (l'agent n'a pas d'écran, le code serait dans les logs : une étape de plus).

### D2. Lancement : une ligne `docker run`, par scénario

L'admin affiche une commande prête à copier (bouton « Copier »), avec l'URL publique et le code déjà remplis. URL = `VDS_PUBLIC_ORIGIN` + `VDS_BASE_PATH` (mode-agnostique) ; image = `VDS_AGENT_IMAGE` (défaut : GHCR, version alignée sur le serveur).

```
docker run -d --name vds-agent --restart unless-stopped --network host \
  -v vds-agent:/data/agent -e VDS_SERVER_URL=<url> -e VDS_ENROLL_CODE=<code> <image>:<version>
```

| Scénario | Méthode recommandée | Clics / saisies |
| --- | --- | --- |
| Hébergé (Studio) ou serveur distant | une ligne `docker run` sur un Pi / mini-PC Linux du site | 1 copier-coller |
| Autonome, serveur sur le LAN du site | agent **co-localisé** posé par l'installeur autonome (D3) | 0 |
| Autonome, autres sites | comme l'hébergé | 1 copier-coller |
| Pi dédié (lot 5) | image Pi + fichier `vds-agent.env` déposé sur la partition de démarrage, téléchargé depuis l'admin | 1 fichier |

Variantes proposées dans le même panneau, repliées : « Télécharger `compose.yaml` + `agent.env` » (même code, pour qui préfère Compose) ; et un `install.sh` servi sous `<base>/agent/install.sh` qui vérifie seulement Docker, l'architecture et la joignabilité, puis exécute la même ligne (pas de `sudo` implicite, pas de `curl | sh` par défaut : on affiche la ligne `docker run`, le script est une option).
L'UI attend l'agent en direct : « En attente de l'agent… » → « Agent en ligne (hostname, version) » à `agent.enrolled` puis `agent.connected`, sans recharger la page.

Rejetées : installeur Windows/MSI (Docker Desktop ne garantit pas le broadcast Wake-on-LAN en `host`, coût de maintenance pour un développeur seul ; Windows = non supporté en V1) ; paquet `.deb`/systemd natif (deuxième chaîne de livraison, à rouvrir si un client refuse Docker) ; le serveur qui démarre lui-même l'agent via `docker.sock` ou SSH (le serveur deviendrait root sur l'hôte ou sur le LAN, interdit par la règle 7 dans l'esprit) ; l'app Android d'une TV comme agent (la TV dort, elle ne peut pas se réveiller elle-même).

### D3. Agent co-localisé en mode autonome : posé par l'installeur, pas par le serveur

Quand le serveur autonome tourne sur le LAN des écrans, l'installation autonome (lot 5, « installation en un clic ») propose « Ce serveur pilote aussi les écrans de ce réseau ». Si oui, elle crée le premier site et un code d'enrôlement par une CLI du serveur, puis démarre le profil Compose `agent` :

```
docker compose exec server python -m vds_server.cli agent enroll-code --site "<nom>" --ttl 10m  # imprime le code
docker compose --profile agent up -d   # service agent (network_mode host), VDS_ENROLL_CODE injecté
```

La CLI est du noyau (les agents et les sites en font partie) et n'a rien de spécifique au mode : le même appel sert aux scripts d'un intégrateur.
Rejetées : secret d'enrôlement permanent partagé serveur/agent dans `.env` (crédit long-vivant qui enrôle n'importe quel agent) ; détection « même hôte » par IP (fausse derrière le NAT Docker) ; agent embarqué dans le processus serveur (le serveur hébergé ne doit rien savoir du LAN, et casserait le « même code dans les deux modes »).

### D4. Découverte des TV : commande fermée générique, logique dans un module agent

Nouvelle commande d'agent `discovery.scan`, routée par le runtime vers les modules qui déclarent la capacité `discover.<proto>` (même mécanique que `provision.<platform>`). Module intégré **`discovery.lan`** (agent) : voisinage ARP/NDP, écoute mDNS (`_googlecast._tcp`, `_androidtvremote2._tcp`, `_adb-tls-connect._tcp`) et SSDP/DIAL (Samsung `RemoteControlReceiver`), puis sondes TCP courtes sur les seuls hôtes vus (5555 ADB, 8001/8002 Samsung WS, 8008 Cast). Le **modèle** vient de mDNS/SSDP/Cast (`eureka_info`), le **fabricant** de l'OUI de la MAC.
Le choix du pilote ne change pas : pour chaque hôte coché, le serveur enchaîne le `power.detect` existant, qui propose la cascade (ADB si 5555, Samsung WS si 8002, WoL avec la MAC découverte en dernier recours, CEC/écran noir via le player).

```python
# vds_sdk.agent.discovery (vds-core 1.4.0)
class DiscoveryScope(BaseModel):          # bornes imposées par le runtime, pas par le module
    cidrs: list[IPv4Network]               # défaut : sous-réseaux privés des interfaces de l'agent, /22 max
    active_probe: bool                     # config héritée `discovery.active_probe`, x-vds-level site
    timeout_s: int = 20
class DiscoveredDevice(BaseModel):
    ip: IPvAnyAddress; mac: str | None; vendor: str | None; name: str | None; model: str | None
    protocols: list[str]                   # "adb", "samsung_ws", "cast", "dial"…
    hints: dict[str, str] = {}             # ex. {"platform": "android"} ; jamais de secret
class Discoverer(Protocol):
    async def discover(self, scope: DiscoveryScope, report: Callable[[DiscoveredDevice], Awaitable[None]]) -> None: ...
```

Le runtime fusionne par MAC puis IP, borne le débit, refuse toute adresse publique (même contrôle que `screen.provision`) et marque les hôtes déjà connus du site (écran existant de même MAC/IP).
Rejetées : découverte dans le runtime de l'agent (métier dans le socle, non remplaçable) ; découverte depuis le serveur (impossible en hébergé) ; scan automatique au démarrage de l'agent (bruit sur des réseaux partenaires, IDS) : le scan est lancé par un admin, audité.

### D5. Adresse d'écran par MAC, IP rafraîchie par l'agent

Quand la MAC est connue (découverte), l'écran la garde dans sa config d'alimentation ; avant chaque commande, l'agent résout l'IP courante par sa table de voisinage et remonte le changement (`screen.address_changed`). On supprime la consigne « réservez l'IP dans le routeur » pour les cas simples. Rejetée : exiger un bail DHCP fixe (étape manuelle hors VDS, source n°1 de pannes à venir).

### D6. Assistant « Mettre en service un site » : 5 étapes, presque sans saisie

1. **Site** : nom, fuseau (préremplis par l'organisation). 1 clic.
2. **Agent** : D2/D3, attente en direct. Option « Pas d'agent » (player seul : CEC / écran noir), qui saute l'étape 3.
3. **Découverte** : « Rechercher les écrans » → liste en direct (nom, modèle, fabricant, protocoles, « déjà ajouté ») ; cases à cocher ; « Ajouter une adresse » en repli manuel.
4. **Mise en service** des écrans cochés, en lot (2 en parallèle, verrou par IP existant) : création de l'écran (nom = nom découvert), `power.detect` → cascade enregistrée, puis selon `hints.platform` : Android → `screen.provision` existant (une seule invite RSA par TV, rappelée à l'écran) ; Samsung/Tizen, Pi, autres → code d'appairage affiché avec la consigne de la plateforme. `identify` en fin d'étape pour vérifier visuellement chaque TV.
5. **Diffusion et alimentation** : choisir une playlist existante ou « Mire d'accueil » (contenu de démonstration fourni par un module content, ciblé au site) ; plan d'alimentation proposé (lun-ven 8 h-19 h, modifiable) rattaché au site par la config héritée. Écrans à 0 clic supplémentaire ensuite.

Tout passe par l'API existante plus D1/D4 ; l'assistant est une composition côté admin, pas un objet noyau (pas de table « onboarding »). Reprise : chaque étape relit l'état réel (site, agent en ligne, écrans existants), on peut quitter et revenir.
Rejetées : écran déclaré automatiquement dès qu'il est découvert (le scan voit aussi des TV privées, consoles, imprimantes : l'admin coche) ; contenu par défaut codé dans le noyau (c'est un module content).

### D7. Mises à jour de l'agent : signalées, pas exécutées par le serveur

L'agent remonte sa version au `hello` (existant). L'admin affiche « Mise à jour disponible » avec la ligne à copier (`docker pull` + recréation), et propose `--label` compatible avec un outil de mise à jour d'hôte que l'exploitant choisit (Watchtower, etc.). Point d'accroche futur : commande `agent.update` vers un module « updater » qui aurait les droits nécessaires. Rejeté V1 : auto-mise à jour par `docker.sock` monté dans l'agent (root sur l'hôte du site).

## Impacts sur le contrat et le noyau : vds-core 1.4.0 (mineur, additif)

- **Noyau serveur** : table `agent_enrollment` (id, site_id, code_sha256, expires_at, consumed_at, consumed_ip, created_by) en migration `0008_agent_enrollment` ; `POST /api/v1/sites/{id}/agent/enrollments` (admin, même origine, renvoie le code une fois + lignes de commande générées), `DELETE …/enrollments/{eid}`, `POST /api/v1/agent/enroll` ; CLI `vds_server.cli agent enroll-code` ; `POST /api/v1/sites/{id}/discovery` (admin) qui envoie `discovery.scan` ; adresse MAC optionnelle sur l'écran (dans la config d'alimentation, pas une colonne noyau si possible).
- **Wire** (`vds_sdk.wire`) : commande `discovery.scan {scan_id, scope}` ; résultats en événements fiables. Agent < 1.4 → `unknown_command` : l'admin masque « Rechercher » et garde la saisie d'IP.
- **SDK** : `vds_sdk.agent.discovery` (ci-dessus), capacité `discover.<proto>` vérifiée au chargement, permission **`network.discover`** (catalogue fermé, kind agent uniquement).
- **Événements** (payload Pydantic `@1`) : `agent.enrollment_created`, `agent.enrolled` (critique, outbox), `discovery.started`, `discovery.device_found`, `discovery.completed`, `discovery.failed`, `screen.address_changed`.
- **Agent** : lecture de `VDS_ENROLL_CODE` si aucun jeton en état, échange puis oubli du code ; `VDS_AGENT_TOKEN` reste accepté (rétrocompatible). Image publiée en CI (multi-arch amd64/arm64), plus de `--build` sur le site.
- **Audit** : `agent.enrollment.created|revoked|consumed`, `site.discovery.started` (admin, cibles CIDR).
- Modules 1.0 à 1.3 chargent inchangés ; note de compatibilité dans `vds_sdk` comme pour 1.3.

## Sécurité des jetons et risques

| Risque | Parade |
| --- | --- |
| Code d'enrôlement fuité (capture, historique shell) | 60 min, usage unique, lié à un site, révocable ; consommé il ne vaut plus rien ; l'admin voit `consumed_ip` et le hostname, peut révoquer l'agent |
| Force brute sur `/agent/enroll` (exposé en hébergé) | 128 bits, limiteur par IP (/64 en IPv6, existant), réponses uniformes, aucune énumération de sites |
| Enrôlement par un tiers avant l'exploitant | l'UI affiche hostname + IP de l'agent arrivé ; bouton « Ce n'est pas mon agent » = révocation + nouveau code |
| Scan perçu comme intrusif sur un réseau partenaire | lancé à la main, audité, RFC1918 des interfaces de l'agent seulement, `/22` max, sondes actives désactivables au site |
| `curl | sh` | jamais présenté par défaut ; script servi par le serveur, même origine, TLS, lisible avant exécution |
| Image GHCR privée inaccessible depuis le site | question ouverte : image agent publique (aucun secret dedans) ou registre authentifié documenté |
| ADB réseau laissé ouvert (décision P10) amplifié par la découverte | la découverte rend visibles les TV ADB du LAN à tout admin : rappeler le VLAN des écrans dans l'assistant |
| Dérive « wizard dans le noyau » | l'assistant reste dans l'admin ; le noyau n'ajoute que enrôlement, commande générique et événements |

## Questions pour Victor

1. Image agent publique sur GHCR (recommandé) ou privée avec identifiants à fournir au site ?
2. Windows hors périmètre V1 pour l'agent : d'accord ?
3. Sondes actives (TCP) activées par défaut au niveau site, ou passif seul (mDNS/SSDP/ARP) par défaut ?
4. Ordre : D1-D2 (enrôlement) d'abord, seul, avant D4-D6 ? Recommandé : oui, c'est le gain immédiat et sans risque de contrat lourd.

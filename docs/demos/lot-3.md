# Démo lot 3 — Multi-sites, agent de site et alimentation

Durée : ~90 min. Aucun secret dans ce document : jetons, mots de passe SMTP et codes saisis par vous, jamais copiés ici.
Matériel : un serveur VDS (`https://vds.client.fr` en autonome, ou `https://allia-innovation.fr/espace/affichage` en hébergé), une TV Samsung Tizen 2023, une TV Android/Google TV (TCL 2023), un hôte d'agent sur le LAN du site (Pi ou mini-PC Linux, Docker), une boîte mail destinataire, un accès au routeur du site.

## Non couvert par l'automatique (à constater ici)

- Les pilotes réels face aux vraies TV : ADB réseau, API WebSocket Samsung (port 8002), Wake-on-LAN, HDMI-CEC. L'automatique n'utilise que des fakes TCP/WSS/UDP.
- Le comportement de veille réelle des TV (réseau coupé en veille profonde, délai de réveil, état lu).
- Le réseau réel du site : NAT, pare-feu sortant, proxy, DHCP, diffusion UDP.
- L'envoi réel de mail (SMTP du client, SPF/DKIM, boîte de réception).
- Automatisé : `make lint`, `make test`, `make e2e` (dont `make e2e-agent` : vrai serveur + vrai agent + fakes + faux SMTP, aux deux `VDS_BASE_PATH`), `make api-types-check`.

## 0. Préparer

- [ ] Compte admin sur le serveur. SMTP configuré par l'environnement (`VDS_SMTP_HOST`, `VDS_SMTP_PORT`, `VDS_SMTP_SECURITY`, `VDS_SMTP_USERNAME`, `VDS_SMTP_PASSWORD`, `VDS_MAIL_FROM`), certificat SMTP valide.
- [ ] TV : IP réservée (bail DHCP fixe), Ethernet de préférence. Samsung : « Allumer avec mobile » activé. Android : débogage réseau activé (`docs/drivers/power.adb.md`).
- [ ] Relever pour chaque TV : IP, MAC.
- [ ] Module `notify.mail` actif (voir écart à trancher dans `docs/roadmap.md` › Lot 3) : état `degraded` tant que `VDS_SMTP_HOST` est absent.

## 1. Site et agent

- [ ] Admin : créer le site (fuseau Europe/Paris), puis un agent. Le jeton `vds_agt_…` n'est affiché qu'une fois ; recharger la page ne le montre plus.
- [ ] Un second agent actif sur le même site est refusé (`agents.already_active`).
- [ ] Sur l'hôte : lancer l'agent (`docker/site-agent.Dockerfile`), `VDS_SERVER_URL` **avec préfixe** en hébergé, `VDS_AGENT_TOKEN_FILE`, `VDS_AGENT_CA_FILE` si AC privée.
- [ ] Contrôle côté LAN : aucun port en écoute sur l'hôte (`ss -ltnp` ne montre rien pour l'agent, sauf l'option cache LAN si activée), seul le trafic sortant 443.
- [ ] Admin : l'agent passe « en ligne » en moins de 2 min, version et modules visibles, aucune info technique affichée sur les écrans publics.
- [ ] `http://` refusé par l'agent (message `agent.server_url_must_be_https`).

## 2. Appairage et configuration des TV

- [ ] Appairer un écran sur chaque TV (lot 2), rattaché au site.
- [ ] Configurer par écran : Samsung (`host`, `mac`), Android (`host`), WoL (`mac`, diffusion du sous-réseau). Cascade du site vérifiée dans l'admin, avec la provenance de chaque valeur (site, groupe, écran) ; une surcharge d'écran puis son retrait redonnent la valeur héritée.
- [ ] Assistant de détection : lancer « Détecter » sur chaque TV. Samsung : popup d'autorisation à accepter sur la TV. ADB : « Toujours autoriser » à cocher. Le résultat propose le bon pilote et l'ordre de cascade.

## 3. Pilotes, un par un (commande manuelle depuis l'admin)

TV Samsung (`docs/drivers/power.samsung-ws.md`) :
- [ ] Éteindre : la TV s'éteint, l'état lu passe `off`/`standby` dans les 10 s.
- [ ] Rallumer : réveil par WoL puis API ; l'image revient, l'état lu `on`. Noter le délai réel.
- [ ] Jeton refusé ou popup ignorée : l'échec est lisible (code), la cascade passe au pilote suivant.

TV Android (`docs/drivers/power.adb.md`) :
- [ ] `power.probe` : `on`. Éteindre (`KEYCODE_SLEEP`) : état `standby`. Rallumer (`KEYCODE_WAKEUP`) : `on`.
- [ ] TV en veille profonde (réseau coupé) : le réveil ADB échoue, WoL prend le relais (`docs/drivers/power.wol.md`), résultat non vérifié si la TV ne répond pas à la relecture.

WoL :
- [ ] Capture `tcpdump -i <iface> udp port 9` sur l'hôte : paquet magique émis (6 × `ff` puis 16 × MAC) vers la bonne diffusion.
- [ ] La TV éteinte depuis plus de 10 min se rallume (si WoL activé côté TV).

CEC (Pi + HDMI) :
- [ ] `cec-client -l` voit l'adaptateur ; extinction et allumage de la TV par CEC. Noter les marques où CEC est ignoré.

Écran noir (`power.blank`) :
- [ ] Sur une TV qui refuse tous les autres pilotes : le player affiche un écran noir, `power.changed` avec `verified=false`, le rétroéclairage reste allumé (limite attendue).

Cascade :
- [ ] Rendre le premier pilote inopérant (mauvaise IP) : bascule vers le suivant, `attempted` liste les pilotes essayés. Tous inopérants : incident `power.failed` ouvert (voir § 6).

## 4. Planning d'alimentation et exceptions

- [ ] Créer un plan (lun-ven 08:00-18:00), l'attacher au site. L'agent reçoit `plan.updated` et récupère le plan (révision visible dans l'état de l'agent).
- [ ] Les TV s'éteignent à 18:00 et se rallument à 08:00 à l'heure du site, sans action manuelle. Relever l'écart réel à l'heure.
- [ ] Exception d'un jour (fermeture exceptionnelle) sur un écran : écran éteint toute la journée ; exception d'un autre niveau (site) et priorité `off` à niveau égal.
- [ ] Calendrier de jours fériés FR : écran éteint un jour férié.
- [ ] Changement d'heure (dernier dimanche de mars/octobre) : si la démo tombe à proximité, vérifier le créneau 08:00 à l'heure locale. Sinon, régler la date de l'hôte de test et relancer l'agent.
- [ ] Forçage manuel « Allumer maintenant » hors créneau : exécuté, puis retour au planning à la borne suivante.

## 5. Coupure réseau

- [ ] Couper l'accès Internet de l'hôte d'agent (pare-feu sortant ou câble WAN) pendant le créneau en cours, **avant** une borne de planning (ex. 5 min avant 18:00).
- [ ] À la borne, sans serveur : les TV s'éteignent (planning autonome de l'agent, plan J + J+1 en cache).
- [ ] Les players continuent de jouer le contenu en cache (lot 2), aucun message technique visible.
- [ ] Redémarrer l'agent pendant la coupure : il reprend avec le plan en cache et ses clés (état chiffré), les bascules suivantes ont lieu.
- [ ] Rétablir le réseau : l'agent se reconnecte (backoff ≤ 60 s), rejoue les événements en attente, l'admin affiche l'état réel de chaque écran.
- [ ] Couper plus de 5 min : incident `agent.offline` (critique) et mail ; rétablissement : incident résolu.
- [ ] Révoquer l'agent depuis l'admin : connexion fermée (4410), secrets de l'agent effacés ; recréer un jeton (rotation) et reconnecter.
- [ ] Redémarrer le serveur seul (agent actif) : reconnexion automatique de l'agent.

## 6. Supervision et mail

- [ ] Écran en `scheduled_off` (hors créneau) : aucune alerte, aucun mail, même sans heartbeat de player.
- [ ] Débrancher une TV pendant un créneau « allumé » : après la grâce de démarrage (600 s) et 3 heartbeats manqués, incident `screen.offline` (warning) et un seul mail.
- [ ] Rebrancher : incident résolu, un mail de résolution.
- [ ] Tous les pilotes en échec : incident `power.failed`, un mail ; un changement vérifié résout l'incident ; un `power.changed` non vérifié (WoL seul) le laisse ouvert.
- [ ] Le mail arrive en boîte de réception (pas en indésirables), en FR/EN selon la langue de l'utilisateur, sans secret ni IP interne, avec lien vers l'incident (préfixe `/espace/affichage` en hébergé).
- [ ] SMTP indisponible : réessais visibles, aucun incident perdu en base.
- [ ] Acquitter un incident dans l'admin : non résolu mais plus signalé comme nouveau.

## 7. Aux deux modes

- [ ] Reprendre §1, §3 et §6 sous `VDS_BASE_PATH=/` puis `/espace/affichage` : URL de l'agent, liens des mails et appels API respectent le préfixe.
- [ ] En hébergé, rôle éditeur : lecture du parc et des incidents, aucune écriture (plans, agents, commandes d'alimentation refusés).

## Résultat à noter

- Délai réel d'allumage/extinction par TV et pilote retenu.
- Écarts avec `docs/drivers/*.md` (réglages TV, firmware) à reporter dans ces fichiers.
- Points à ouvrir dans `docs/roadmap.md` › Lot 3 (écarts constatés).

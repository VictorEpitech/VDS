# Démo — Recherche des TV du réseau (découverte LAN)

Durée : ~30 min. Aucun secret ici. Matériel : serveur VDS (autonome `https://vds.client.fr` ou hébergé `https://allia-innovation.fr/espace/affichage`), un agent de site sur le LAN de la TV, une vraie TCL Android/Google TV allumée (ou en veille réseau), un téléphone ou la fenêtre du navigateur à 375 px, accès au routeur (VLAN).

## Non couvert par l'automatique (à constater ici)

- Les vraies réponses mDNS / SSDP / ARP d'une TCL, les ports réellement ouverts (5555, 8008, 8009, 6466), la veille réseau réelle.
- Le périmètre réel de l'agent (interfaces, masque, VLAN, Docker, VPN) et le comportement derrière un pare-feu.
- Le rendu (375 px, thèmes clair / sombre / Allia).
- Automatisé : `make e2e-agent` (`test_discovery_lan_scan_to_prefill`, vrai serveur + vrai agent + réseau factice, `/` et `/espace/affichage`), tests serveur `test_discovery.py`, module `modules/discovery.lan/tests`, agent `apps/site-agent/tests/test_agent_discovery.py`, admin `discovery.test.tsx`.

## 0. Préparer

- [ ] Module `discovery.lan` activé sur le serveur (Modules, facultatif : désactivé, la recherche est indisponible, raison `module_disabled`).
- [ ] Agent du site « en ligne », version >= 0.1.0 avec vds-core 1.5.
- [ ] Une TCL branchée en Ethernet ou Wi-Fi sur le même sous-réseau que l'agent, ADB réseau activé (Options développeur).
- [ ] Aucun écran existant pour cette TCL (pour voir le préremplissage), puis un autre pour la reconnaissance.

## 1. Lancer la recherche

- [ ] Site > bouton « Rechercher les TV du réseau » visible (agent en ligne). Sur un site sans agent : bouton absent, aucun message technique.
- [ ] Clic : l'état passe à « en cours », la liste se remplit en direct (un lot toutes les 500 ms), fin en <= 30 s.
- [ ] Un deuxième clic pendant le scan est refusé (« déjà en cours »). Juste après la fin : message d'attente avec délai (60 s). Après 20 scans dans l'heure : message de quota.

## 2. Vérifier ce qui est trouvé

- [ ] La TCL apparaît avec l'IP attendue (comparer au routeur), la MAC, le nom annoncé (ex. « TCL Salon ») ou « marque modèle ».
- [ ] Pilote suggéré : `power.adb` puis `power.wol` (sans ADB ouvert : `power.wol` seul). Confiance affichée, un appareil de confiance faible n'est jamais présélectionné.
- [ ] Ni imprimante, ni téléphone, ni PC, ni aucune adresse publique ou hors du réseau de l'agent dans la liste.
- [ ] Réglage du site « Sonder les ports » désactivé : relancer (après 60 s), la TCL reste visible via mDNS mais sans protocole ADB, pilote suggéré réduit à `power.wol`.

## 3. Créer l'écran

- [ ] Sélectionner la TCL : formulaire de création prérempli (nom, IP dans le pilote ADB, MAC dans le pilote WoL, ordre de bascule = pilotes suggérés). Rien d'autre n'est écrit.
- [ ] Créer l'écran puis « Éteindre / Allumer » depuis l'admin : la vraie TCL réagit via le pilote suggéré.
- [ ] Relancer la recherche : la TCL est marquée comme écran déjà configuré (lien vers l'écran), non proposée à la création.

## 4. Affichage

- [ ] 375 px : liste lisible sans défilement horizontal, boutons >= 44 px, texte IP / MAC en police mono seulement.
- [ ] Thèmes clair, sombre (`prefers-color-scheme`), Allia (hébergé) : contrastes, pas d'ombre portée, états en cours / vide / erreur.
- [ ] FR et EN : aucune clé brute affichée ; un nom d'appareil hostile (`<img onerror>`) est affiché en texte.

## 5. Réseau et VLAN

- [ ] TV dans un autre VLAN que l'agent : non trouvée (périmètre = sous-réseaux de l'agent, /22 maximum), aucun message d'erreur trompeur.
- [ ] Agent sur un hôte avec Docker, VPN ou plusieurs interfaces : seuls les réseaux privés réels sont balayés (2 maximum), jamais les ponts Docker ni le VPN.
- [ ] Agent hors ligne : bouton absent ou raison « agent hors ligne » ; coupure de l'agent pendant le scan : échec « délai dépassé », pas de scan bloqué (nouveau scan possible).
- [ ] Les deux `VDS_BASE_PATH` (`/` et `/espace/affichage`) : mêmes parcours.

## 6. Confidentialité (admin technique)

- [ ] Journal d'audit : `site.discovery.started` / `site.discovery.finished` présents, sans IP ni MAC.
- [ ] Les résultats disparaissent au bout de 24 h.

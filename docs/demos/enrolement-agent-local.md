# Démo : enrôlement de l'agent local (sans ligne de commande)

Cible : serveur de la TCL (Docker, `VDS_BASE_PATH=/`), TV Android. Spec : `docs/architecture-enrolement.md`. Seule commande unique : le démarrage du compose.

## 0. Vérification du compose (par l'équipe, sans toucher aux conteneurs en cours)
Déjà exécutée le 2026-10-30 avec un projet à part et un autre port :
```
cd docker
export POSTGRES_PASSWORD=x VDS_PORT=18123 VDS_ADMIN_EMAIL=a@x.test VDS_ADMIN_PASSWORD=<mot de passe> VDS_SECRET_KEY=<40 caractères>
VDS_BASE_PATH=/                  docker compose -p vdse7 config -q    # ok
VDS_BASE_PATH=/espace/affichage  docker compose -p vdse7 config -q    # ok
docker compose -p vdse7 up -d --build                                  # build local de server + agent
docker compose -p vdse7 down -v                                        # nettoyage
```
Résultat aux deux bases : l'agent démarre en `dormant`, visible par `GET /agent/local` ; après activation il passe `active` en moins de 15 s.

## 1. Démarrage
- [ ] `docker compose up -d --build` (dossier `docker/`) : 3 services `db`, `server`, `agent` en `Up`, `server` en `healthy`.
- [ ] Aucun fichier `agent.env`, aucun jeton à saisir.

## 2. Activer l'agent local depuis l'admin
- [ ] Connexion admin, Sites, choisir un site, panneau Agent.
- [ ] La carte « Ce serveur » est visible et indique qu'un agent est détecté (nom de la machine, version). Elle est absente en mode hébergé.
- [ ] Cliquer « Activer » : l'agent apparaît « en ligne » en moins de 15 s, sans commande ni rechargement.
- [ ] Le jeton `vds_agt_...` n'apparaît nulle part dans la page (inspecteur du DOM, onglet Réseau, liste des inscriptions).

## 3. Voir l'agent arriver (autre machine)
- [ ] « Une autre machine » : une commande `docker run` à copier et un `agent.env` à télécharger s'affichent, le code `vds_enr_...` est montré une seule fois.
- [ ] Sans `VDS_PUBLIC_ORIGIN`, l'admin explique la variable à définir (erreur actionnable).
- [ ] Lancer la commande sur l'autre machine : l'écran passe à « Agent arrivé » avec nom de machine et IP.
- [ ] Rejouer la même commande : refus (code déjà utilisé).
- [ ] « Ce n'est pas mon agent » puis confirmer : l'agent est révoqué, un nouveau code est proposé ; l'agent local revient en sommeil.

## 4. Déplacer l'agent local
- [ ] Sur un autre site, « Activer » : l'ancien site n'a plus d'agent actif, le nouveau est « en ligne ».

## 5. Installer Android (sans ligne de commande)
- [ ] Site avec agent en ligne : assistant d'installation Android (`docs/demos/provision-android-tv.md`), saisir l'IP de la TV, accepter l'invite de débogage sur la TV.
- [ ] Le player s'installe et s'appaire ; l'écran apparaît dans le site.

## 6. Contrôles à l'œil
- [ ] FR puis EN : aucun libellé manquant dans le panneau.
- [ ] Thèmes V-Tech et Allia : pas d'ombre, police mono réservée au code et aux identifiants techniques.
- [ ] Redémarrer `agent` (`docker compose restart agent`) : il revient « en ligne » seul (jeton chiffré rangé).

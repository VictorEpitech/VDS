# Recette : commandes player et captures (lot I, matériel réel)

Durée : ~60 min. Spec : `docs/architecture-commandes.md` (§3 livraison, §6 captures, §10 tests).
Automatisé (vert) : `make test` (`test_player_commands.py`, `test_screenshots.py`, `test_player_static.py` aux deux `VDS_BASE_PATH`), vitest player (`commands.test.ts`) et admin, `make e2e` (`commands.spec.ts`, FR/EN, 375 px), `make i18n-check`. Ici : tout ce qui dépend du vrai appareil, de l'écran et du temps réel.

## 0. Préparation

- [ ] Android (TCL ou box) : APK `player.android` à jour, appairée, en ligne, kiosque verrouillé. Noter si l'appareil est Device Owner : ____.
- [ ] Pi : image `packaging/pi` flashée, appairée, en ligne, Chromium kiosque.
- [ ] Un écran Tizen ou navigateur web (sans capture) pour l'aperçu théorique, si disponible.
- [ ] Compte admin et compte éditeur restreint à un seul groupe (le Pi ou l'Android hors de ce groupe).
- [ ] Une vidéo en lecture sur l'Android (pour la capture).
- [ ] Refaire les §1 à 8 avec `VDS_BASE_PATH=/`, puis un passage rapide §1, §2, §3 avec `VDS_BASE_PATH=/espace/affichage`.

## 1. Android : reload, identify

- [ ] Carte de l'écran : boutons Recharger, Identifier, Capture (cibles 44 px). Redémarrer et Resynchroniser le cache seulement pour l'admin (cache dans Avancé).
- [ ] Recharger : la lecture reprend après rechargement, état « Exécutée » sur la carte en moins de 10 s (relevé : ____ s). Rien d'anormal à l'écran, pas de message public.
- [ ] Identifier (10 s) : la TV affiche uniquement le nom de l'écran, sans id, IP, version ni révision ; disparaît seule à la fin.
- [ ] Double clic rapide sur Recharger : un seul rechargement (même commande, clé d'idempotence).

## 2. Android : capture avec vidéo

- [ ] Pendant la lecture d'une vidéo, « Capture » : l'image apparaît sur la carte en moins de 15 s via WS (relevé : ____ s). L'image montre bien la vidéo (pas un cadre noir : PixelCopy).
- [ ] Légende datée ; l'image s'ouvre au clic ; hors session (onglet privé, URL copiée) elle est refusée.
- [ ] Rien sur la TV pendant ni après la capture (aucun indicateur).
- [ ] Repli HTTP (bloquer le WebSocket du player, par exemple règle de pare-feu sur le routeur ou proxy) : la capture arrive en moins de 70 s (relevé : ____ s).
- [ ] Capture périodique : régler `player.screenshot_interval_min` = 5 sur l'écran uniquement ; une capture toutes les ~5 min, jamais plus d'une par minute. Remettre à 0 ensuite.

## 3. Android : restart app, restart device

- [ ] Redémarrer (admin) : confirmation explicite, délai de 8 s annulable (annuler n'envoie rien).
- [ ] Portée « application » : l'app se relance, la lecture reprend, le **verrouillage kiosque est conservé** (bouton Accueil / Retour sans effet, aucune sortie possible), commande « Exécutée » (boot_id changé). Pas de boucle de redémarrage (une seule relance).
- [ ] Portée « appareil » **avec** Device Owner : l'appareil redémarre, revient en kiosque verrouillé et rejoue seul, commande « Exécutée ».
- [ ] Portée « appareil » **sans** Device Owner : refus clair côté admin (« non pris en charge » / échec avec cause traduite), rien ne redémarre, la lecture continue.
- [ ] Redélivrer la même commande (couper puis rétablir le réseau juste après l'envoi) : jamais réexécutée.

## 4. Pi : assistant et CSP loopback

- [ ] L'assistant local du Pi (`packaging/pi/helper`) répond sur `http://127.0.0.1:47801` uniquement (aucun port à l'écoute sur l'interface LAN : vérifier avec `ss -ltn` sur le Pi lui-même).
- [ ] Redémarrer (portée application) : Chromium relancé par systemd, kiosque revenu, lecture reprise.
- [ ] Console Chromium du player (via accès local, pas de réseau) : aucune violation CSP pour l'appel à `127.0.0.1:47801` ; aucun autre appel bloqué.
- [ ] Pas de bouton Capture sur la carte du Pi : aperçu théorique (§7).

## 5. Hors ligne

- [ ] Couper le réseau du player, envoyer Recharger : la carte indique « En attente : écran hors ligne » ; Annuler fonctionne (état « Annulée », seulement tant que non envoyée).
- [ ] Envoyer à nouveau puis rétablir le réseau avant le TTL : exécutée à la reconnexion, une seule fois.
- [ ] Envoyer, laisser dépasser le TTL hors ligne, rétablir : état « Expirée », **rien exécuté**.
- [ ] Une capture demandée hors ligne puis périmée n'est pas rattrapée.
- [ ] Pendant toute la coupure, la lecture et les rallumages programmés continuent (règle 5).

## 6. Éditeur restreint

- [ ] Éditeur : voit Recharger, Identifier, Capture pour les écrans de son groupe ; ne voit ni Redémarrer ni Resynchroniser.
- [ ] Écran hors de son périmètre : introuvable (404 sans nom d'écran dans l'URL ni l'interface) ; ses captures et commandes ne fuitent ni dans le tableau de bord des captures ni dans le sondage.
- [ ] Action groupée (barre de sélection) : l'écran sans appareil est ignoré et compté.

## 7. Aperçu théorique et vie privée

- [ ] Écran sans capacité de capture (Pi, web, Tizen) : vignette de l'élément en cours, libellée « Aperçu de la programmation, pas une capture réelle » ; aucun bouton de capture.
- [ ] Rétention : régler `screenshots.retention_h` = 1 et `max_per_screen` = 2 (organisation) ; après la purge (15 min max), les captures hors plafond ou vieilles disparaissent ; supprimer l'écran supprime ses captures. Remettre 24 / 10.
- [ ] Public : à aucun moment un échec, une capture ou une commande n'est visible sur les TV.
- [ ] FR puis EN : libellés de la fiche, de la file et du tableau de bord ; carte lisible à 375 px.

# Recette : mises en page, calques et forçage de zone (écran réel)

Durée : ~60 min. Spec : `docs/architecture-mises-en-page.md`. Contrat vds-core 1.9.0.
Automatisé (vert) : `test_layout_compose.py`, `test_resolver_layouts.py`, `test_layouts_api.py` (aux deux `VDS_BASE_PATH`), `test_layout_migration.py`, `test_compat_19.py`, `apps/player/test/layout.test.ts`, `apps/player/e2e/layout.spec.ts`, `apps/admin/e2e/layouts.spec.ts`.
Non automatisable : rendu réel sur TV, décodeur vidéo unique, fluidité, lisibilité à distance, hors ligne sur appareil.

## 0. Préparation

- [ ] Un écran paysage réel (TV Samsung si possible : 1 décodeur) et un écran en portrait (ou rotation) appairés, en ligne.
- [ ] Médias : 2 vidéos, 3 images, un logo PNG transparent, une image de fond. Playlists : P-vidéo, P-images, P-bandeau (images de texte).
- [ ] Un compte éditeur et un compte admin. Noter l'heure serveur / TV (écart : ____ s).
- [ ] Faire §1 à 8 avec `VDS_BASE_PATH=/`, puis §1, §3, §5, §8 avec `VDS_BASE_PATH=/espace/affichage` (toutes les URL, assets et WebSocket sous la base).

## 1. Calques (3 zones dont un logo en incrustation)

- [ ] Contenus > Mises en page > Créer depuis le modèle « Bandeau bas », orientation paysage ; ajouter une zone « Logo » en coin, z supérieur.
- [ ] Éditeur : flèches déplacent (pas 1 %), Maj+flèches redimensionnent, glisser à la souris avec grille ; sortie d'écran impossible. Enregistrer.
- [ ] Zone principale = P-images, bandeau = P-bandeau, logo = playlist d'un seul PNG ; fond = image. Programmer sur l'écran.
- [ ] TV : géométrie conforme à l'aperçu, logo au-dessus des autres zones, fond visible sous les zones vides, zones indépendantes (cycles non synchronisés).
- [ ] Aucune bordure, nom de zone ni texte technique à l'écran (public).

## 2. Orientation

- [ ] Programmer une mise en page portrait sur l'écran paysage : avertissement d'orientation à l'assistant, non bloquant. Valider : la mise en page s'affiche proportionnellement (pas de plantage).
- [ ] Programmer la même sur l'écran portrait : plus d'avertissement ; rendu cohérent avec l'aperçu.

## 3. Vidéo unique (max_exclusive_elements = 1)

- [ ] Deux zones avec P-vidéo : l'éditeur affiche l'avertissement de limite.
- [ ] TV : une seule vidéo à la fois ; l'autre zone saute l'élément (pas d'écran noir ni blocage), reprend quand le décodeur est libre (~5 s).
- [ ] Config `player.max_exclusive_elements = 2` sur cet écran (provenance visible) : les deux jouent, ou la TV décroche : noter le comportement : ______.

## 4. Forçage de zone

- [ ] Forcer > « Certaines zones » : seules les zones candidates sont proposées (nom + clé mono) ; couverture annoncée (« n écrans, m sans la zone »). Zéro zone refusé.
- [ ] Forcer le bandeau avec P-images, 5 min : seul le bandeau change, au début de la playlist forcée ; les autres zones ne sont ni redémarrées ni coupées (vérifier le logo et la vidéo en cours).
- [ ] Pastille « Zone forcée : bandeau » : liste des forçages, fiche écran, aperçu (contour admin seulement, rien sur la TV).
- [ ] Arrêt anticipé : retour du bandeau à sa playlist en < 10 s. Sinon, fin automatique à l'heure : la zone revient seule, au début de sa playlist.
- [ ] Forçage d'écran en parallèle : il prime sur le forçage de zone (la mise en page est remplacée).
- [ ] Audit : `override.create` avec `zone_ids`, puis expire / stop.

## 5. Fenêtre active de zone (active_from / active_until)

- [ ] Zone « Promo » avec fenêtre démarrant dans ~3 min et finissant ~3 min plus tard.
- [ ] TV : la zone apparaît à l'heure et disparaît à l'heure (écart : ____ s / ____ s), sans recharger les autres zones.
- [ ] Fenêtre déjà passée : zone absente dès le départ, fond visible à la place.

## 6. Zone absente

- [ ] Forçage de la zone « promo » sur 2 écrans dont un dont la mise en page n'a pas cette zone : avertissement à la création (« 1 écran sans la zone »).
- [ ] L'écran sans la zone ne change pas (aucun calque inventé) ; l'autre applique le forçage. Mention « non appliqué » dans l'indicateur admin.
- [ ] Forçage de zone sur un écran éteint (`scheduled_off`) : il ne l'allume pas, aucune alerte.

## 7. Hors ligne

- [ ] Mise en page en lecture, couper le réseau de la TV (ou le serveur) 10 min : lecture continue, zones indépendantes.
- [ ] Pendant la coupure : une fenêtre de zone démarre et s'arrête à l'heure prévue ; un forçage de zone déjà reçu (daté) démarre et finit à l'heure locale ; les autres zones ne sont pas touchées.
- [ ] Couper alors qu'un changement de mise en page est programmé : bascule nette à l'heure.
- [ ] Redémarrer la TV serveur coupé : le dernier document est rejoué (fond, zones, médias en cache).
- [ ] Rétablir le réseau : reprise silencieuse ; modifier la mise en page (une seule zone) : seule cette zone se recrée, les autres gardent curseur et lecture.

## 8. Compatibilité et nettoyage

- [ ] Écran avec un ancien player 1.8 (APK non mis à jour) et programmation playlist : lecture inchangée. Avec une mise en page : dégradation gracieuse (zone `main`) : noter : ______.
- [ ] Supprimer la mise en page programmée : refus `layout.in_use` avec liste d'usage. Supprimer une playlist utilisée : la zone devient vide, la mise en page est mise à jour.
- [ ] Interface en FR puis EN : aucun libellé manquant dans liste, éditeur, formulaire de forçage ; 375 px : canevas en lecture seule, champs numériques éditables.

# Recette : forçage avec fin automatique (TCL réelle)

Durée : ~45 min. Spec : `docs/architecture-forcage.md`.
Automatisé (vert) : `make test` (`test_override_resolver.py`, `test_overrides_api.py` dont le cycle complet création, flotte, plan player/agent, prolongation, arrêt, expiration par le balayeur, aux deux `VDS_BASE_PATH`), vitest admin et player, `make e2e` (`overrides.spec.ts`), `make i18n-check`. Ici : tout ce qui dépend de la vraie TCL, de l'écran et du temps réel.

## 0. Préparation

- [ ] TCL appairée, en ligne, joue la playlist A programmée (écran visible depuis la salle). Une deuxième playlist B, reconnaissable, existe.
- [ ] Agent de site en ligne, pilote d'alimentation actif (`power.adb`) ; au moins un autre écran (si possible un deuxième appareil) dans le même groupe.
- [ ] Un plan d'alimentation avec une plage `scheduled_off` qui commence dans ~20 min sur la TCL (voir §3).
- [ ] Deux navigateurs : compte admin et compte éditeur. Heure du serveur et de la TV alignées (noter l'écart : ____ s).
- [ ] Refaire les §1 à 8 une première fois avec `VDS_BASE_PATH=/`, puis le §9 (et un passage rapide §1, §4) avec `VDS_BASE_PATH=/espace/affichage`.

## 1. Forcer 15 minutes, fin automatique

- [ ] Carte de la TCL : bouton « Forcer » (cible 44 px minimum). Le formulaire n'a que 3 champs visibles : Quoi, Où (prérempli), Combien de temps. Le volet Avancé est replié.
- [ ] Puces de durée : 15 min, 1 h, 4 h, fin de journée ; la durée par défaut affichée vient de la config héritée (`core` › `override.default_duration_minutes`).
- [ ] Choisir B, 15 min : l'aperçu annonce « 1 écran » avant validation. Valider.
- [ ] La TV passe à B en moins de 10 s (relevé : ____ s). Rien n'indique « forcé » à l'écran (public).
- [ ] Carte écran : état « Forcé jusqu'à HH:MM », actions Arrêter / Prolonger.
- [ ] Accueil : bloc « Forçages en cours » liste le forçage avec les minutes restantes.
- [ ] À l'heure de fin : la TV revient seule à A, sans action ni rechargement (écart à l'heure de fin : ____ s). La carte repasse à l'état normal en moins d'une minute ; le forçage disparaît du bloc de l'Accueil.
- [ ] Audit : `override.create` puis `override.expire` (acteur système).

## 2. Arrêt anticipé

- [ ] Forcer B 1 h, puis « Arrêter » : confirmation (annonce les écrans hors ligne s'il y en a). Après validation, la TV revient à A en moins de 10 s.
- [ ] Rejouer l'arrêt (second onglet) : pas d'erreur bloquante (idempotent).

## 3. Rallumage d'un écran en « hors service prévu »

- [ ] Attendre que la TCL soit éteinte par sa plage `scheduled_off` (pastille « Hors service prévu », aucune alerte ni incident).
- [ ] Forcer B 15 min (option Avancé « respecter le hors service » laissée par défaut : non) : la TV se rallume, joue B. Aucune alerte d'alimentation.
- [ ] À la fin du forçage : la TV se rééteint seule (la plage `scheduled_off` est toujours en cours) ; toujours aucune alerte.
- [ ] Même scénario avec « respecter le hors service » = oui : le forçage ne rallume pas la TV (elle reste éteinte). Aperçu : « 0 écran rallumé ».
- [ ] Admin uniquement : Avancé › « forcer l'extinction » éteint la TV pour la durée, puis rallume si la programmation le demande.

## 4. Écran hors ligne

- [ ] Forcer B 30 min, puis couper le réseau de la TCL (ou arrêter le serveur). Elle continue B sans interruption.
- [ ] Arrêter le forçage côté admin pendant la coupure : la confirmation annonce « 1 écran hors ligne l'affichera jusqu'à HH:MM ». La TV garde B.
- [ ] À l'heure de fin, réseau toujours coupé : la TV repasse seule à A (autonomie du player). Rétablir le réseau : la carte reflète l'état réel, sans retour en arrière visible.
- [ ] Rallumage programmé sans serveur : forçage actif sur une plage `scheduled_off`, serveur coupé, la TV se rallume/rééteint à l'heure prévue.
- [ ] Redémarrer la TV (courant coupé) pendant un forçage, serveur coupé : elle reprend le forçage jusqu'à sa fin (cache).

## 5. Prolongation

- [ ] Forcer B 15 min, puis « Prolonger » (+1 h) avant la fin : « Forcé jusqu'à » avance d'1 h, la TV ne revient pas à A à l'heure initiale.
- [ ] Plafond : prolonger plusieurs fois au-delà de `override.max_duration_hours` : message d'erreur clair, forçage inchangé.
- [ ] Prolonger un forçage déjà terminé : message « déjà terminé », aucune réactivation.

## 6. Éditeur vs admin

- [ ] Éditeur : peut forcer un écran, un groupe, un site, arrêter, prolonger.
- [ ] Éditeur : n'a pas « forcer l'extinction » dans Avancé ; la cible « organisation » n'est pas proposée ; l'appel direct à l'API est refusé (403).
- [ ] Lecteur (viewer) : aucun bouton « Forcer », « Arrêter », « Prolonger ».
- [ ] Sélection multiple : sélectionner un groupe entier propose « cibler le groupe » ; sinon un forçage par écran, compte rendu « N forçages créés jusqu'à HH:MM ».
- [ ] Le dernier forçage l'emporte : forcer B puis C sur le même écran, C s'affiche ; arrêter C fait revenir B (si encore actif), pas A.

## 7. Responsive et accessibilité

- [ ] 375 px : formulaire et cartes sans défilement horizontal, cibles ≥ 44 px, volet Avancé utilisable.
- [ ] Clavier seul : ouvrir le formulaire, choisir, valider, arrêter ; focus visible, retour du focus logique à la fermeture.
- [ ] Lecteur d'écran : le décompte de fin est annoncé à la minute (pas à la seconde).
- [ ] FR puis EN : aucun libellé manquant ni clé brute (formulaire, aperçu, confirmations, erreurs).

## 8. Thèmes

- [ ] `theme.vtech` clair puis sombre, `theme.allia` : lisibilité des puces, de l'état « Forcé » et du bloc de l'Accueil ; pas d'ombre portée, police mono réservée aux données techniques.

## 9. VDS_BASE_PATH

- [ ] `VDS_BASE_PATH=/espace/affichage` : toutes les actions (création, aperçu, arrêt, prolongation, rafraîchissement 30 s) fonctionnent sans 404 ; la TV suit.
- [ ] Mode hébergé (connexion Studio) : écriture refusée depuis une autre origine ; lecture et actions OK depuis l'admin.

## 10. Notes

Écart d'heure TV/serveur, durées relevées, anomalies : ______

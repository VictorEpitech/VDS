# Recette : mise à jour du player Android depuis l'admin (vds-core 1.6.0)

Durée : ~1 h. Spec : `docs/architecture-apk-update.md`. Prérequis serveur et fourniture d'APK : `docs/demos/provision-android-tv.md` §0.
Automatisé (vert) : `make test` (dont `apps/server/tests/test_apk_update.py` : aller-retour serveur, agent, `power.adb` avec faux ADB), `make lint`, `make e2e`, `make i18n-check`. Ici : tout ce qui dépend de la vraie TCL et de l'écran.

## 0. Préparation

- [ ] TCL déjà provisionnée avec l'assistant (Device Owner ou kiosque doux, appairée, `power.adb.host` renseigné), joue une playlist.
- [ ] Noter AVANT : version installée affichée sur la carte écran, mode kiosque (Device Owner / doux), état d'appairage (écran « en ligne »).
- [ ] Fournir une APK de `version_code` supérieur, même signataire (`VDS_APK_SIGNER_SHA256`) : image CI ou `VDS_APK_DIR` avec `apk.json` (sans redémarrer le serveur, relecture ≤ 60 s).
- [ ] Agent de site en ligne, module `power.adb` actif (0.3.0).
- [ ] Compte admin, compte éditeur, ouverts dans deux navigateurs.

## 1. Mise à jour nominale (TCL réelle)

- [ ] Carte écran : version installée en police mono + pastille « Mise à jour disponible ».
- [ ] Action « Mettre à jour le player » : confirmation explicite (« N écrans vont redémarrer leur affichage pendant ~1 min »), pas de délai d'annulation 8 s.
- [ ] Coupure d'affichage observée : 30 à 90 s, durée relevée : ____ s. L'écran revient seul, sans manipulation.
- [ ] Résultat par écran : succès, version APRÈS = nouvelle version, pastille disparue, action devient « déjà à jour » (409 si rejouée).
- [ ] Device Owner / kiosque conservé (aucune touche Accueil/Assistant accessible si Device Owner), appairage conservé (pas de nouveau code), playlist reprise.
- [ ] Aucune désinstallation (données et rôle Device Owner intacts) ; événement et audit `screen.apk_update.*` présents.

## 2. Échecs

- [ ] ADB non autorisé : révoquer les autorisations de débogage USB sur la TV, relancer. Échec rapide, message « ADB non autorisé : rejouez l'assistant d'installation ». Version inchangée, écran toujours en lecture.
- [ ] ADB injoignable (débogage réseau coupé) : « TV injoignable en ADB réseau ».
- [ ] Signataire différent (APK signée autrement, fournie hors épinglage ou en `VDS_APK_DIR` sans épinglage) : refus par la TV, message « version signée autrement déjà installée (aucune désinstallation automatique) », ancienne version toujours en place, Device Owner intact.
- [ ] Rétrogradation (APK plus ancienne) : « rétrogradation refusée ».
- [ ] Agent hors ligne : écran non éligible, raison « agent du site hors ligne » ; action sans effet.
- [ ] Aucune APK disponible : bloc d'aide (CI ou `VDS_APK_DIR`) et « aucune application disponible sur le serveur » (503 si forcé).
- [ ] Mise à jour déjà en cours : seconde demande refusée, « mise à jour déjà en cours ».
- [ ] « Réessayer les échecs » relance uniquement les écrans en échec.

## 3. Rôles et sélection multiple

- [ ] Éditeur : aucun bouton « Mettre à jour le player » (carte, barre de sélection, filtre) ; appel API direct refusé (403).
- [ ] Admin : filtre « à mettre à jour », « tout sélectionner » sur un site ; écrans non éligibles exclus et comptés avec leur raison.
- [ ] Au moins 3 écrans : 2 installations simultanées maximum par site, un seul téléchargement de l'APK (cache partagé).

## 4. Affichage et i18n

- [ ] 375 px de large : carte, pastille, confirmation et résultats lisibles, sans défilement horizontal, cibles tactiles ≥ 44 px.
- [ ] Thèmes `theme.vtech` et `theme.allia` : contrastes, pas d'ombre portée, police mono limitée à la version.
- [ ] FR puis EN : aucun libellé manquant ni clé brute dans les raisons d'éligibilité et les codes d'échec (`update.android.*`, `update.timeout`, `update.no_provider`, `update.module_error`).
- [ ] Pluriels corrects pour 1 et plusieurs écrans.

## 5. Les deux `VDS_BASE_PATH`

Rejouer §1 (au moins la mise à jour nominale) et le cas ADB non autorisé avec :
- [ ] `VDS_BASE_PATH=/`
- [ ] `VDS_BASE_PATH=/espace/affichage` (admin, API, téléchargement d'APK par l'agent, WebSocket agent : tout sous le préfixe).

## Notes de recette

Relever : durée de coupure, version avant/après, écarts. Risque connu : à faire hors heures d'ouverture (coupure 30 à 90 s).

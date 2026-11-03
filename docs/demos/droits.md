# Démo droits — rôles et périmètres des éditeurs

Durée : ~40 min. Spécification : `docs/architecture-droits.md`. Aucun secret ici.
Matériel : un serveur VDS (autonome `/` puis hébergé `/espace/affichage`), deux sites (A avec groupes A1 et A2, B avec B1), un écran par site, un compte admin, trois comptes éditeurs (`ed_A` périmètre site A, `ed_A1` périmètre groupe A1, `ed_none` sans périmètre), un téléphone ou une fenêtre à 375 px.

## Non couvert par l'automatique (à constater ici)

- Playwright admin tourne contre des serveurs simulés ; la non-fuite serveur est couverte par pytest (`apps/server/tests/test_perimeter.py`, `test_perimeter_leak.py`). La jonction vraie UI / vrai serveur ne l'est que par cette démo.
- Le mode hébergé réel (cookie du Studio, `role_source=provider` venant d'un vrai Studio) : seul le comportement de VDS est testé, pas le Studio.
- Clavier réel (lecteur d'écran) sur l'arbre de périmètre.

## 0. Préparer

- [ ] Admin : créer les contenus (playlists, médias) partagés, propres à A, A1, A2 et B, avec des noms reconnaissables (`PL-Partagé`, `PL-A`, `PL-A1`, `PL-A2`, `PL-B`), une règle de planning et un forçage sur B.
- [ ] Créer les comptes `ed_A`, `ed_A1`, `ed_none` (invitation) ; leur attribuer le rôle Éditeur.

## 1. Admin : Réglages › Utilisateurs

- [ ] Le menu Réglages > Utilisateurs n'existe que pour l'admin.
- [ ] Liste : rôle, actif, dernière activité, résumé de périmètre (« 1 site », « 1 groupe », « Aucun périmètre »). À 375 px : cartes, pas de défilement horizontal.
- [ ] Filtres par recherche, rôle et site.
- [ ] Fiche `ed_A` : cocher le site A, Enregistrer, message « Périmètre enregistré. » ; cocher un site coche ses groupes ; clavier : flèches haut/bas + espace.
- [ ] Éditer la même fiche dans deux onglets : la seconde sauvegarde affiche le conflit, « Recharger » reprend la version actuelle.
- [ ] Fiche `ed_none` : message « Aucun périmètre : un administrateur doit attribuer… ».
- [ ] Un admin ne peut ni changer son propre rôle ni se désactiver ; le dernier admin est protégé (message explicite).
- [ ] L'admin voit tout (B compris) partout.

## 2. Éditeur restreint (`ed_A`, puis `ed_A1`)

- [ ] Menu : aucune entrée Réglages réservée admin.
- [ ] Médiathèque, Playlists, Mises en page, Programmation, Écrans, Supervision, tableau de bord : AUCUN nom ni compteur du site B (listes, cartes, filtres, sélecteurs, recherche, tags).
- [ ] Saisir à la main l'URL d'un objet de B : page « introuvable » (pas « accès refusé »), sans nom.
- [ ] `ed_A` voit PL-Partagé et PL-A, PL-A1, PL-A2, avec badge propriétaire ; les actions d'écriture sont masquées (non grisées) sur PL-Partagé.
- [ ] `ed_A1` voit PL-A (ancêtre, lecture seule) et PL-A1, pas PL-A2 ; règles d'ancêtres affichées « héritée de … » en lecture seule.
- [ ] Création de playlist par `ed_A1` : propriétaire = A1 sans choix ; `ed_A` (A, A1, A2) : sélecteur « Propriétaire » sans option « Partagé ».
- [ ] Média partagé utilisé aussi par B : son « Utilisation » affiche un compteur « n autres éléments hors de votre périmètre », sans nom.
- [ ] Forçage : la cible est choisie dans l'arbre (seulement A) ; l'aperçu ne cite pas de forçage de B.
- [ ] Supervision / incidents : seuls les écrans de A.
- [ ] `ed_none` : pages vides avec le message d'état vide, aucun contenu, même partagé.
- [ ] Mobile 375 px : mêmes constats.

## 3. Rôle éditeur « organisation »

- [ ] Donner « Toute l'organisation » à un éditeur : il voit tout, peut créer du contenu Partagé (option présente), modifier et supprimer du partagé.
- [ ] `ed_A` ne peut pas passer un contenu de A en « Partagé » ni supprimer un partagé (action absente).

## 4. Mode hébergé (`VDS_BASE_PATH=/espace/affichage`, `auth.studio-allia`)

- [ ] Connexion unique depuis le Studio : un membre arrive éditeur sans périmètre (« Aucun périmètre »).
- [ ] Un président ou une direction arrive admin ; son rôle n'est pas modifiable.
- [ ] Fiche de ce membre : rôle désactivé avec « Rôle géré par le service de connexion » (`role_source = provider`), mais le périmètre et l'activation restent modifiables.
- [ ] Un admin VDS attribue un périmètre au membre : au rechargement, il voit uniquement son périmètre.
- [ ] Toutes les URL (API, assets, WebSocket) sont sous `/espace/affichage` ; refaire les sections 1 et 2 rapidement.
- [ ] Rien n'est écrit dans le Studio ; seule la vérification de session sort du serveur.

## 5. Critères de sortie

- [ ] Aucun nom de B observé à aucun moment côté `ed_A` / `ed_A1` / `ed_none`.
- [ ] Les deux modes (`/` et `/espace/affichage`) passent.
- [ ] FR puis EN : aucun libellé manquant (clés affichées) sur les écrans Utilisateurs, badge propriétaire et états vides.

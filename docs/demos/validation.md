# Démo validation de publication — éditeur propose, admin valide

Durée : ~40 min. Spécification : `docs/architecture-validation.md`. Aucun secret ici.
Matériel : un serveur VDS (autonome `/` puis hébergé `/espace/affichage`), un site avec un écran réel enrôlé qui joue une programmation, un compte admin, un compte éditeur `ed` dont le périmètre couvre l'écran, deux navigateurs (ou fenêtres privées), un téléphone ou une fenêtre à 375 px.

## Prérequis

- [ ] Rebuild Docker pour les migrations 0018 à 0021 (dont la table `publication`) :
  `VDS_APK_HOST_DIR=/home/victor/vds-apk docker compose -f docker/compose.yaml -f docker/compose.apk.yaml --env-file .env up -d --build server agent`
- [ ] Reprise de l'existant : après démarrage, les playlists, mises en page et programmations déjà présentes sont « Publié » et l'écran joue comme avant.

## Non couvert par l'automatique (à constater ici)

- Ce que l'écran réel affiche réellement après approbation (poussée et changement de contenu).
- Le mode hébergé réel (rôles venant d'un vrai Studio).
- Lisibilité des badges (icône + libellé) sur écran réel et à 375 px.

## 1. Cycle éditeur puis admin

- [ ] Admin : réglage `diffusion.review_required` à Oui (défaut). Créer et publier une playlist `PL-V1` programmée sur l'écran réel (l'admin enregistre, c'est publié automatiquement).
- [ ] Éditeur : modifier `PL-V1` (ajouter un élément). Badge « Modifications non publiées » (icône + libellé) ; l'écran réel continue de jouer l'ancienne version.
- [ ] Éditeur : « Soumettre pour validation » avec un commentaire. Badge « En revue ».
- [ ] Éditeur modifie à nouveau l'objet en revue : il repasse en brouillon, resoumission nécessaire.
- [ ] Admin : Diffusion > « Publications en attente » liste l'objet, avec l'aperçu de la copie de travail.
- [ ] Admin : « Refuser » sans motif est impossible ; avec motif, l'objet repasse en brouillon et l'éditeur voit le motif de refus.
- [ ] Éditeur corrige, resoumet. Admin : « Valider ». L'écran réel joue la nouvelle version (poussée sans rechargement manuel).
- [ ] Éditeur : « Retirer » une soumission en revue ramène en brouillon et la fait disparaître de la file admin.
- [ ] Conflit : l'éditeur modifie l'objet pendant que l'admin a la fiche ouverte, puis l'admin valide : message « Le contenu a changé depuis votre ouverture. Recharger » ; rien de publié.
- [ ] Le même cycle sur une mise en page et sur une programmation.

## 2. Dépendances

- [ ] Éditeur crée une playlist jamais publiée et une programmation qui l'utilise, soumet la programmation. Admin : valider signale les dépendances non publiées et propose « Valider aussi les dépendances » ; sans cocher, la validation est refusée.
- [ ] Avec la case cochée : tout est publié et l'écran joue.
- [ ] Admin enregistre une programmation qui dépend d'une playlist jamais publiée : l'enregistrement réussit, l'objet reste brouillon et la raison est affichée.

## 3. Forçage refusé si non publié

- [ ] Admin crée un forçage visant une playlist jamais publiée : refusé avec un message clair (`publication.not_published`).
- [ ] Forçage sur une playlist publiée : accepté et joue la version publiée, pas les modifications non publiées.
- [ ] L'éditeur peut créer un forçage dans son périmètre (non soumis à validation) sur du publié uniquement.

## 4. Modifications non publiées et garde-fous

- [ ] Admin enregistre `PL-V1` alors qu'elle contient des modifications de l'éditeur : tout est publié (comportement documenté dans l'aide), l'objet passe « Publié ».
- [ ] Éditeur tente de supprimer un objet qui a une version publiée : action absente ou refusée ; un objet jamais publié reste supprimable.
- [ ] Éditeur tente de remplacer le fichier d'un média utilisé par un objet publié : refusé (réservé à l'admin).
- [ ] Un média utilisé seulement par une version publiée ne peut pas être supprimé ni purgé.
- [ ] Éditeur : les boutons « Valider » et « Refuser » n'existent pas ; un appel direct à l'API est refusé par le serveur.

## 5. Réglage `diffusion.review_required`

- [ ] Réglages (niveau Simple, organisation seulement) : la case existe et n'apparaît à aucun autre niveau.
- [ ] Passer à Non : un éditeur qui enregistre publie directement ; plus de soumission nécessaire, badge « Publié ».
- [ ] Repasser à Oui : le comportement par brouillon revient pour les éditeurs.
- [ ] Un objet en revue au moment du passage à Non : son état reste cohérent (publié à la prochaine écriture admin, ou approuvé par l'admin).

## 6. Audit et rôles

- [ ] Journal d'audit : une ligne par soumission, retrait, validation, refus (acteur, objet, révision, commentaire). Pas de ligne supplémentaire pour les publications automatiques.
- [ ] Éditeur hors périmètre : l'objet n'apparaît pas, aucun nom ne fuit, pas de soumission possible.

## 7. Aux deux `VDS_BASE_PATH`

- [ ] Refaire les sections 1 et 3 avec `VDS_BASE_PATH=/` puis `/espace/affichage` : API, assets et WebSocket sous la base.
- [ ] Mode hébergé : un membre éditeur du Studio soumet, un admin valide ; mêmes résultats.
- [ ] Mobile 375 px : badges, file « Publications en attente » et boutons utilisables sans défilement horizontal.

## 8. Critères de sortie

- [ ] L'écran réel ne joue jamais du non publié.
- [ ] Les badges sont lisibles sans la couleur (icône + libellé).
- [ ] FR puis EN : aucun libellé manquant sur badges, boutons, erreurs et page des publications en attente.

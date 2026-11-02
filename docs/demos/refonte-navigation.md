# Démo — Refonte navigation (A–D)

Durée : ~15 min. Aucun secret ici. Serveur autonome (`/`) puis hébergé (`/espace/affichage`), un compte admin, un éditeur.

## Non couvert par l'automatique

- Rendu réel : barre d'onglets du bas à 375 px, zones tactiles, thèmes clair / sombre / Allia, lisibilité.
- Automatisé : `ia.test.tsx`, `app.test.tsx` (nav 5 entrées, redirections, base `/` et `/espace/affichage`), `onboarding.test.tsx`, `apps/server/tests/test_onboarding.py` (`/onboarding`, `/fleet/summary`, `screens?issues`), `i18n.test.ts`.

## 1. Navigation

- [ ] Cinq entrées : Accueil, Écrans, Contenus, Diffusion, Plus. Pas de « Supervision ».
- [ ] « Plus » reste active sur Sites, Alimentation, Notifications.
- [ ] Fil d'Ariane sur Ajouter un écran (retour vers Écrans).

## 2. Mobile 375 px

- [ ] Navigation en onglets en bas, 5 entrées visibles, cibles >= 44 px.
- [ ] Aucun défilement horizontal sur Accueil, Écrans, Contenus, Plus.

## 3. Anciennes URL (signets)

- [ ] `/supervision` -> Écrans filtré « À traiter ».
- [ ] `/pairing`, `/media`, `/playlists`, `/playlists/<id>`, `/schedules` -> nouvelles pages.
- [ ] Idem sous `/espace/affichage` : la base est conservée.

## 4. Premiers pas

- [ ] Instance vide : carte Premiers pas sur l'Accueil, étape suivante mise en avant.
- [ ] Ignorer une étape facultative ; la progression avance. « Compte » non ignorable.
- [ ] Créer site, écran, média, playlist, programmation : étapes cochées d'elles-mêmes.
- [ ] Parcours terminé : message, carte masquée ; Plus > Aide la rouvre.
- [ ] Éditeur : carte en lecture seule, aucune action d'écriture.
- [ ] Compteurs de l'Accueil cohérents avec Écrans ; lien « à traiter » mène au filtre.

## 5. Thèmes et langues

- [ ] V-Tech clair, V-Tech sombre, Allia : contrastes lisibles, pas d'ombres, mono réservé aux données techniques.
- [ ] Bascule FR/EN : aucun libellé en dur ni clé brute (Accueil, Premiers pas, Plus, Aide).

## 6. Assistant « Ajouter un écran » (lot D)

- [ ] Écrans, Accueil et Premiers pas mènent tous à `/screens/add` ; plus de bouton « Créer sans code ».
- [ ] Quatre étapes visibles (Appareil, Code, Nom et site, Vérification), « Retour » à chaque étape, annonce « Étape n sur 4 ».
- [ ] Parcours entier au clavier ; focus sur le titre de chaque étape ; aucun défilement horizontal à 375 px.
- [ ] Code : majuscules automatiques, tirets et espaces tolérés, état « Code complet » (icône + texte).
- [ ] Nom/site/groupe : réglages repris affichés avec leur badge de provenance ; orientation seulement dans « Avancé ».
- [ ] Code inconnu, expiré, déjà utilisé, trop d'essais : cause claire + une action ; le code saisi est conservé.
- [ ] Vérification : « En attente de la TV » puis « L'écran fonctionne » (en ligne, battement frais, aucun média manquant), sondage 3 s.
- [ ] Test d'allumage visible seulement avec un pilote réglé et un agent en ligne (jamais d'allumage/extinction réels).
- [ ] Android TV : « Installer depuis le réseau » mène à l'assistant d'installation ; grisé avec explication sans agent.
- [ ] Fiche écran > « Ajouter un appareil » (`?screen=`) : remplace l'appareil, deux étapes (code, vérification).
- [ ] `/screens/android` et `/pairing` redirigent vers l'assistant, requête et base conservées (`/` et `/espace/affichage`).
- [ ] V-Tech clair/sombre et Allia, FR et EN.

### Sur écran réel (non automatisable)

- [ ] 375 px (téléphone réel, pas l'émulation) : étapes lisibles, cibles >= 44 px, clavier virtuel ne masque ni le champ Code ni le bouton « Continuer ».
- [ ] 375 px : fil des quatre étapes sans troncature, messages d'erreur du code sans défilement horizontal.
- [ ] Thèmes V-Tech clair, V-Tech sombre, Allia : badge de provenance, état « Code complet », alertes d'erreur et focus visibles, pas d'ombres.
- [ ] FR puis EN en direct : aucune clé brute ni libellé en dur, y compris les erreurs (inconnu, expiré, déjà utilisé, trop d'essais) et la vérification.
- [ ] Vraie TV : ouvrir le player sur la TV, lire le code à distance (taille et contraste suffisants à 3 m), le saisir tel qu'affiché (tirets, O/I/L) puis constater « En attente de la TV » -> « L'écran fonctionne » en moins de ~10 s.
- [ ] Vraie TV : laisser le code expirer (15 min) -> « Code expiré », saisir le nouveau code, le code saisi précédemment est bien remplacé.
- [ ] Vraie TV : double clic sur « Ajouter l'écran » ou rechargement après succès : un seul écran créé (rejeu).
- [ ] Vraie TV Android : « Installer depuis le réseau » avec un agent en ligne (invite RSA acceptée sur la TV).
- [ ] Deux `VDS_BASE_PATH` : refaire le parcours code -> en ligne en `/` puis en `/espace/affichage` ; `/pairing` et `/screens/android` des signets mènent à l'assistant.
- [ ] Automatisé : `apps/server/tests/test_screen_pair.py`, `apps/admin/src/features/screens/add/add.test.tsx`, `apps/admin/src/features/diffusion.test.tsx`, e2e `diffusion.spec.ts` et `provision.spec.ts` (passent par les redirections).

## Lot E : actions rapides et sélection multiple

Spécification : `docs/architecture-actions-rapides.md`. Prérequis : un site avec un agent en ligne, au moins deux écrans (un en ligne, un autre), un pilote d'alimentation réglé sur l'un d'eux.

### Automatisé

- [ ] `apps/server/tests/test_fleet_playback.py` : programmation active, `next_change_at`, hors plage, bornes, week-end, changement d'heure Europe/Paris, dates de validité (aux deux `VDS_BASE_PATH`).
- [ ] `apps/admin/src/features/actions/actions.test.tsx` : file, annulation, concurrence, sondage mutualisé, réessai.
- [ ] e2e `apps/admin/e2e/actions.spec.ts` (4 combinaisons préfixe x thème) : Allumer un écran en ligne, action en lot puis annulation (aucune commande envoyée après 8 s), suppression en lot confirmée, aucune requête hors origine serveur.

### Sur écran réel (non automatisable)

- [ ] Écran en ligne : le bouton Allumer de sa carte est actif (agent en ligne) ; clic, puis la TV s'allume réellement et la carte annonce « est allumé ».
- [ ] Écran éteint (hors service prévu) : Allumer le rallume ; agent arrêté : boutons grisés avec la raison liée.
- [ ] Carte : état, dernière activité, alimentation et programmation active (nom, playlist, « jusqu'à ») cohérents avec la réalité, y compris après un changement de créneau.
- [ ] Sélection de 2 écrans puis Éteindre : décompte « Envoi dans N s » visible et annoncé, « Annuler l'action » dans les 8 s, puis constater qu'aucune TV ne s'éteint ; recommencer sans annuler : les TV s'éteignent, résultat par écran affiché.
- [ ] Annulation à 8 s pile et navigation vers une autre page pendant l'attente : le bandeau suit, l'action part (ou est annulée) une seule fois.
- [ ] Fermer l'onglet pendant l'attente : avertissement du navigateur, puis rien n'est envoyé.
- [ ] Un écran dont l'agent est hors ligne dans la sélection : « N écrans ignorés » affiché, les autres sont traités ; « Réessayer les échecs » ne relance que les échecs.
- [ ] Suppression en lot : confirmation explicite avec focus sur le bouton, aucun délai d'annulation, écrans retirés de la liste.
- [ ] 375 px (téléphone réel) : barre d'actions en feuille basse sans masquer le dernier écran, cases à cocher et boutons >= 44 px, aucun défilement horizontal.
- [ ] Thèmes V-Tech clair, V-Tech sombre, Allia : barre et bandeau d'actions tonaux sans ombre, focus visible, contrastes lisibles (alerte, succès, échec).
- [ ] Clavier seul : cocher, « Tout sélectionner », atteindre la barre, choisir une action, annuler avec Entrée ; lecteur d'écran : nombre sélectionné, compte à rebours et résultat annoncés (`aria-live`).
- [ ] FR puis EN en direct : aucune clé brute, pluriels corrects (1 écran / 2 écrans), causes d'échec traduites.
- [ ] Comme administrateur seulement : un éditeur ne voit ni cases à cocher, ni Allumer/Éteindre.
- [ ] Deux `VDS_BASE_PATH` : refaire Allumer unitaire, lot + annulation et suppression en `/` puis en `/espace/affichage` ; onglet Réseau : toutes les requêtes vont vers l'origine du serveur, aucune vers un appareil du LAN.

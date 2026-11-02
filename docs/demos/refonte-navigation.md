# Démo — Refonte navigation (A–C)

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

# Démo assistants et aide — import média, connexion agent, aide, palette, glossaire

Durée : ~35 min. Spécification : `docs/architecture-ux-fg.md` (lots F et G). Aucun secret ici.
Matériel : un serveur VDS (autonome `/` puis hébergé `/espace/affichage`), un compte admin et un compte éditeur, un site sans agent et une machine du site pour lancer l'agent, 3 fichiers de test (une image paysage, une image portrait 1080x1920, un fichier non accepté), un fichier trop lourd, un téléphone ou une fenêtre à 375 px.

## Prérequis

- [ ] Rebuild Docker (migrations 0018 à 0021 appliquées) :
  `VDS_APK_HOST_DIR=/home/victor/vds-apk docker compose -f docker/compose.yaml -f docker/compose.apk.yaml --env-file .env up -d --build server agent`

## Non couvert par l'automatique (à constater ici)

- Un vrai agent de site qui se connecte (Playwright utilise un agent simulé).
- Le clavier réel et le lecteur d'écran sur l'assistant, le panneau d'aide et la palette.
- La qualité rédactionnelle des textes d'aide et du glossaire (relecture de Victor), en FR et EN.
- Ton selon le thème : vouvoiement sous `theme.allia`, neutre sous `theme.vtech`.

## 1. Assistant d'import média

- [ ] Contenus > « Ajouter un contenu » ouvre l'assistant (≤ 4 étapes, fil d'étapes, Retour, résumé).
- [ ] Glisser-déposer de 3 fichiers (dont 1 refusé) : 2 envois terminés, 1 en erreur avec message clair traduit et actions « Retirer » / « Réessayer ». Envois 2 en parallèle, progression par fichier.
- [ ] Fichier non accepté : message de type refusé. Fichier trop lourd : message avec la limite et la taille.
- [ ] Image portrait avec cible paysage : avertissement de ratio, jamais bloquant. Image basse résolution : avertissement.
- [ ] Fin d'assistant « Ajouter à une playlist » : playlist existante, éléments dans l'ordre d'envoi ; ou nouvelle playlist avec nom par défaut.
- [ ] Éditeur avec la validation active : la playlist modifiée passe en brouillon (comportement normal).
- [ ] Clavier seul de bout en bout ; à 375 px sans défilement horizontal.
- [ ] Import par URL absent (reporté, normal).

## 2. Assistant « Connecter un agent de site »

- [ ] Réglages > Sites > « Connecter un agent » (et étapes Premiers pas 2 et 4) ouvre l'assistant.
- [ ] La commande copiable contient l'origine publique et la base : sous `/espace/affichage`, l'URL contient ce préfixe ; bouton Copier fonctionne.
- [ ] Lancer la commande sur la machine du site : l'assistant affiche « Agent connecté il y a N s » (sondage 3 s) puis passe à la détection (`DetectPanel`).
- [ ] Détection : appareils trouvés, « Ajouter » en un clic.
- [ ] Ne pas lancer la commande pendant 2 min : message actionnable (port sortant 443).
- [ ] Aucun port entrant ouvert pour l'agent (connexion sortante seulement).

## 3. Aide contextuelle

- [ ] Bouton « ? » à côté des titres de page : ouvre un panneau latéral (`aria-expanded`), Échap ferme et rend le focus au bouton.
- [ ] Sujets : écrans, contenus, playlists, mises en page, programmation, forçage, alimentation, agents, publication, héritage ; 3 phrases chacun, sans lien externe.
- [ ] Page Aide (menu Plus) : liste des sujets, glossaire, « Rouvrir Premiers pas » réaffiche le parcours.
- [ ] Ton : thème Allia vouvoie ; thème V-Tech neutre.

## 4. Glossaire

- [ ] Termes (forçage, groupe, héritage...) soulignés : infobulle au survol, au focus clavier et au toucher (mobile).
- [ ] Lecteur d'écran : l'infobulle est annoncée avec le terme.

## 5. Palette Ctrl/Cmd+K

- [ ] Ctrl+K (Cmd+K sur Mac) ouvre la palette ; Échap ferme ; Entrée exécute.
- [ ] « ecran » trouve « Écran … » (insensible aux accents) ; recherche sur écrans, sites, groupes, médias, playlists, mises en page, programmations ; actions de navigation et de création.
- [ ] Compte éditeur : aucune action réservée à l'admin n'est proposée ; compte restreint : aucun nom hors périmètre.
- [ ] Mobile : icône de recherche à la place du raccourci.

## 6. Aux deux `VDS_BASE_PATH`

- [ ] Refaire les sections 1, 2, 3 et 5 avec `/` puis `/espace/affichage` : route Aide, commande agent et assets sous la base.

## 7. Critères de sortie

- [ ] FR puis EN : aucun libellé manquant (clés `help.*` affichées) ; textes +30 % sans débordement à 375 px.
- [ ] Charte V-Tech : pas d'ombres portées, police mono uniquement pour la commande copiable.
- [ ] Accessibilité clavier complète sur assistants, panneau, palette et infobulles.

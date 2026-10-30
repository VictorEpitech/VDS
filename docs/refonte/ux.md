# Refonte UX de l'admin — audit et proposition

Statut : proposition (aucun code). Base : `docs/design.md`, charte V-Tech, `apps/admin/src/app/routes.tsx`, `routes/`, `features/`.

## 1. Points de friction actuels

**Navigation**
- 9 entrées à plat dans l'en-tête (Accueil, Écrans, Sites, Médiathèque, Playlists, Planning, Alimentation, Supervision, Notifications) : à 375 px la barre déborde, et rien ne hiérarchise « je fais » / « je surveille » / « je règle ».
- Routes « techniques » sans entrée de menu claire : `/pairing`, `/screens/android` (atteintes par boutons de `/screens`). Pas de fil d'Ariane ni de retour contextuel.
- Absents du menu mais prévus par `design.md` : mises en page (layouts), utilisateurs et groupes, modules et réglages.

**Accueil**
- `Home` n'affiche que l'état du serveur et la légende des états : aucune action, aucun chiffre du parc, aucun guidage. Le tableau de bord « critique » de `design.md` n'existe pas.

**Vocabulaire**
- Mélange tâche/technique : « Pairing / appairage », « Alimentation » (power), « Supervision », « Notifications », « Provisionner Android ». Le chemin principal doit parler comme `design.md` : « Allumer à 8h30 », pas « pilote ».
- Plusieurs mots pour une même intention (associer un écran : « pair », « appairer », « lier », « code »).
- Playlist, mise en page, planning, forçage : l'enchaînement n'est pas expliqué ; l'utilisateur ne sait pas dans quel ordre créer.

**Ajouter un écran** (aujourd'hui : 3 chemins séparés)
- `/screens` : « Créer » (formulaire) puis « Appairer » (`/pairing?screen=`) puis, pour Android, `/screens/android`. L'utilisateur doit savoir à l'avance quel chemin prendre ; il crée un écran « vide » avant d'avoir le code TV.
- Le code TV, le site et le groupe sont saisis dans deux écrans différents ; pas de test d'alimentation ni de vérification « il joue bien » en fin de parcours (prévus par `design.md` parcours 1).

**Premier démarrage**
- `Bootstrap` crée le compte, puis `Home` vide. Aucun site, agent, écran, contenu proposé : l'ordre site → agent → écran → contenu → planning est à deviner.

**Supervision**
- Page séparée de la liste des écrans : l'état (badge) est dans `Supervision`, les actions dans `Screens`/`Power`. Il faut changer de page pour constater puis agir. Incidents, détection et commandes sont empilés sans priorité (le plus grave n'est pas en haut).

**Planning**
- Liste de règles (`Schedules`) sans vue calendrier ni visualisation des alternances sur une même cible ; forçage non distingué visuellement ; priorité numérique brute (`priority 3`) = jargon.

## 2. Proposition

### 2.1 Architecture d'information
Principe : 5 entrées, orientées intention, + « Plus » (réglages). Sur 375 px : barre d'onglets en bas (4 entrées + « Plus »), pas de menu qui déborde.

| Entrée | Contient (anciennes routes) | Vocabulaire FR / EN |
| --- | --- | --- |
| Accueil | tableau de bord + « Premiers pas » | Accueil / Home |
| Écrans | parc (sites, groupes, écrans), fiche écran (état, programmation active, alimentation, héritage), ajout d'écran, alertes du parc | Écrans / Screens |
| Contenus | médiathèque, playlists, mises en page (+ éditeur) | Contenus / Content |
| Diffusion | planning (calendrier, forçages), publications en attente | Diffusion / Playback |
| Plus | sites et agents, alimentation (plages, exceptions), notifications, utilisateurs, modules et réglages, langue, aide | Réglages / Settings |

Règles :
- **Supervision disparaît comme page** : état et incidents vivent dans Écrans (filtre « à traiter ») et sur l'Accueil. `/supervision` redirige vers `/screens?filter=issues`.
- **Alimentation devient un onglet de la fiche écran/site** (« Allumage ») + bouton rapide allumer/éteindre sur la carte ; la page globale reste dans Réglages.
- **Sites** : un site est un filtre/regroupement de Écrans (sélecteur de site en tête de page) ; la gestion (agents, groupes) est dans Réglages › Sites.
- Libellés : « Appairage » devient « Ajouter un écran » ; « Provisionner Android » devient « Installer sur Android TV » ; « Forçage » reste le terme canonique (vocabulaire projet) avec sous-texte « remplace temporairement le planning ».
- Anciennes URL conservées en redirections (signets, mode hébergé : toutes via `VDS_BASE_PATH`).
- Mode hébergé : en-tête léger (logo du thème, « Retour au Studio »), même IA, aucune branche `studio`.

### 2.2 Accueil guidé « Premiers pas »
Carte en haut de l'Accueil tant que non terminée, repliable, puis remplacée par le tableau de bord. Chaque étape : icône + libellé + état (à faire / en cours / fait) + bouton unique. **S'auto-coche** à partir de l'état réel (jamais de case manuelle).

1. Créer le compte et changer le mot de passe (coché après bootstrap).
2. Créer un site (coché si ≥ 1 site). Sous-étape facultative : connecter un agent (utile seulement pour l'allumage réseau).
3. Ajouter un premier écran (coché si ≥ 1 écran appairé).
4. Importer un contenu (≥ 1 média).
5. Créer une playlist et la prévisualiser.
6. Programmer la diffusion (≥ 1 planning ciblant l'écran).
7. Régler l'allumage/extinction (facultatif, ignorable).

Comportements : barre de progression textuelle (« 3 sur 6 »), « Ignorer cette étape » persistant, étape suivante mise en avant (couleur Primary + icône + libellé), le tout au clavier. Fin : message de réussite puis carte masquée (rouvrable dans Aide). Dès l'étape 3, l'Accueil affiche aussi le tableau de bord (parc, à traiter, en attente de validation, prochains changements).

### 2.3 Assistants
Format commun : panneau plein écran sur mobile, pas à pas (≤ 4 étapes), fil d'étapes visible, « Retour » toujours possible, brouillon conservé, résumé final. États : en attente (information), en cours (indicateur + libellé), réussi, échec. Toute erreur = **cause en langage clair + une action** (jamais de message brut). Couleur + icône + libellé.

- **Ajouter un écran** (point d'entrée unique, remplace création + appairage + Android) : 1) Quel appareil ? (TV avec navigateur / Android TV / Raspberry Pi / autre) ; 2) saisir le code vu sur la TV (champ code grand, validation au fil de la frappe, 6 caractères, majuscules auto) ; 3) nom, site, groupe (héritage affiché avec badge) ; 4) vérification : l'écran affiche son contenu, test d'allumage si pilote disponible. Erreurs : « Code inconnu ou expiré — la TV en affiche un nouveau toutes les X min, saisissez le nouveau » ; « Écran déjà ajouté sous le nom N — le remplacer ? ».
- **Installer sur Android TV** : étapes auto-détectées (réseau, ADB, Device Owner, installation, premier démarrage) avec cases qui se cochent d'elles-mêmes ; en échec, l'étape fautive porte l'action : « Activer le débogage réseau sur la TV » (mini-tutoriel illustré) ou « Réessayer ». Réutilise `provisioning/Wizard`/`StepList`.
- **Connecter un agent de site** : commande copiable (bloc mono, bouton « Copier »), attente du premier battement (« Agent connecté il y a 3 s »), détection automatique des appareils du réseau (existe : `DetectPanel`) avec « Ajouter » en un clic. Erreur : « Aucune connexion reçue depuis 2 min — vérifiez que le site a accès à Internet (port sortant 443) ».
- **Ajouter un contenu** : glisser-déposer multi-fichiers, ou coller une URL ; barre de progression par fichier, contrôle auto (format, poids, ratio vs orientation de la cible) avec message actionnable (« Vidéo trop lourde : 480 Mo, limite 200 Mo — compresser ? »). Fin : « Ajouter à une playlist » (existante ou nouvelle, un clic).
- **Programmer** : 1) quoi (playlist ou mise en page) ; 2) où (sélecteur de cible avec compteur « 12 écrans touchés ») ; 3) quand (modèles : « Tous les jours 8h–18h », « En semaine », « Une date ») ; 4) aperçu des alternances sur la même cible, conflits signalés (« Remplace “Accueil” le mardi »). Priorité numérique cachée dans « Avancé ».

### 2.4 Actions rapides et en masse
- Liste Écrans : sélection multiple (cases + « tout sélectionner » filtré) → barre d'actions flottante (tonale, sans ombre) : Allumer, Éteindre, Redémarrer, Changer de groupe, Appliquer un planning, Forcer un contenu (durée), Supprimer (avec confirmation).
- Carte écran : dernière capture, état, programmation active, boutons Allumer/Éteindre et « Forcer… ».
- Bouton global « Forcer un message » sur Accueil (cible + durée, fin automatique).
- Ctrl/Cmd+K : palette de recherche et d'actions (clavier ; sur mobile, icône de recherche).
- Duplication (playlist, planning, écran) et glisser-déposer pour réordonner (avec alternative clavier).
- Tableau de bord « à traiter » : un clic sur « 3 écrans hors ligne » ouvre la liste filtrée ; action « Réessayer l'allumage » en ligne.
- Annulation (« Annuler » 8 s) après action en masse plutôt que confirmation modale systématique ; confirmation conservée pour suppression et révocation.

### 2.5 Aide contextuelle
- Icône « ? » à côté des titres : panneau latéral court (3 phrases + lien doc), contenu i18n FR/EN.
- Textes d'aide sous chaque champ complexe (déjà dans le schéma des modules : `description` du JSON Schema).
- États vides utiles : chaque liste vide explique et propose l'action (« Aucun écran. Ajouter votre premier écran »).
- Glossaire en infobulle sur les termes (forçage, groupe, héritage), accessible au clavier et au toucher.
- Bouton « Aide » dans Plus : rouvre Premiers pas et les tutoriels.
- Ton : neutre en V-Tech ; vouvoiement, phrases courtes en Allia ; via clés i18n, jamais en dur.

### 2.6 Contraintes transverses
- Charte : tokens `--vds-*`, pas d'ombres, rayons tokens, mono réservée aux codes/IP/versions. Icônes via le composant `Icon` du thème actif.
- 375 px : barre d'onglets basse, tableaux en cartes, actions en masse en feuille basse, cibles tactiles ≥ 44 px.
- Accessibilité AA : focus visible, annonces `aria-live` pour la progression des assistants, ordre de tabulation logique, aucun état par la couleur seule.
- i18n : +30 % de longueur, clés par fonctionnalité, pluriels i18next.

## 3. Lots livrables

| Lot | Contenu | Taille |
| --- | --- | --- |
| A. Quick wins | Renommages de vocabulaire FR/EN ; états vides avec action ; redirections d'URL ; fil d'Ariane ; Accueil : cartes de compteurs (écrans par état, incidents) depuis les API existantes | S |
| B. Navigation | Nouvelle IA 5 entrées + onglets bas mobile ; menu « Plus » ; Supervision fusionnée dans Écrans (filtre) | M |
| C. Premiers pas | Carte checklist auto-cochée (compteurs existants : sites, écrans, médias, plannings), étapes ignorables | M |
| D. Assistant « Ajouter un écran » unifié | Fusion création + appairage + Android ; vérification finale ; erreurs actionnables | M |
| E. Actions rapides | Carte écran enrichie, allumer/éteindre en ligne, sélection multiple et barre d'actions, annulation | M |
| F. Assistants contenu et agent | Import multi-fichiers avec contrôles, ajout direct à playlist, assistant agent avec détection | M |
| G. Aide contextuelle | Panneau « ? », glossaire, palette Ctrl+K | M |
| H. Planning visuel | Vue calendrier, modèles de créneaux, aperçu des alternances/conflits, forçage visible | L |
| I. Tableau de bord complet | Captures, publications à valider, prochains changements, temps réel | L |
| J. Utilisateurs, modules et réglages | Écrans de gestion via formulaire généré (JSON Schema) | L |

Ordre : A, B, C en premier (valeur immédiate, peu de dépendances) ; D avant E ; H et I après stabilisation des API (section 4).

## 4. Besoins serveur / API (à passer à l'architecte)

1. **Endpoint « premiers pas »** : `GET /onboarding` renvoyant l'état de chaque étape (comptes calculés côté serveur) + persistance des étapes ignorées par utilisateur/organisation. Sans cela, le front agrège plusieurs listes (acceptable en lot C, fragile).
2. **Résumé de parc** : `GET /fleet/summary` (comptes par état, incidents ouverts, écrans à traiter) avec filtre site, pour Accueil et filtre « à traiter ».
3. **Actions en masse** : `POST /screens/actions` (ids ou cible, action, paramètres) avec résultat par écran (réussi/échec + cause) ; idempotence et annulation (fenêtre d'annulation, jeton).
4. **Appairage simplifié** : création + claim en une transaction (`POST /screens/pair` avec code, nom, site, groupe) ; erreurs typées (`code_unknown`, `code_expired`, `already_paired`) ; détection par le serveur de l'écran en attente (liste des codes affichés par site) pour éviter la saisie.
5. **Vérification de fin d'assistant** : état « premier contenu joué » et résultat de test d'alimentation consultable (événement/endpoint), poussé en temps réel.
6. **Temps réel** (SSE ou WebSocket de l'admin) pour progression des assistants (agent connecté, Android, import), états d'écran. Sinon sondage 3 s (suffisant en V1).
7. **Médias** : validation serveur (format, poids, ratio) avec messages typés ; import URL ; ajout direct à playlist.
8. **Planning** : endpoint de prévisualisation des alternances et de détection de conflits sur une cible/plage ; modèles de créneaux ; forçage avec fin automatique et durée.
9. **Recherche/palette** : index de recherche transverse (écrans, contenus, plannings) ; sinon recherche côté client sur listes déjà chargées.
10. **Préférences utilisateur** (langue, thème, filtres sauvegardés, aide masquée) persistées côté serveur ; à arbitrer avec le mode hébergé (session Studio, aucune écriture dans la base du Studio).
11. **Rôles** : visibilité des entrées de menu et actions selon admin/éditeur (la publication soumise à validation impose un état « en validation » exposé par l'API).

Hors périmètre V1 (points d'accroche seulement) : alertes, statistiques, diffusion conditionnelle, SSO générique.

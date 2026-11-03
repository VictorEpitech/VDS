# Recette : planning (calendrier, priorités, simulation, forçage) sur écran réel

Durée : ~40 min. Spec : `docs/architecture-planning.md`. Fait par Victor.
Automatisé (vert) : `make test` (résolveur, `test_planning_api.py` dont forçage actif/échu, aux deux `VDS_BASE_PATH`), vitest admin (`features/planning`), `make e2e` (`planning.spec.ts`, `planning-p2.spec.ts`, `planning-p4.spec.ts` : FR/EN, 4 combinaisons préfixe x thème, AA, 375 px, sombre), `make i18n-check`. Ici : ce que la timeline affiche contre ce que joue réellement l'écran, et le temps réel.

## 0. Préparation

- [ ] Une TCL appairée, en ligne, dans un groupe G d'un site Moulins (fuseau Europe/Paris). Un deuxième écran dans G si possible.
- [ ] Trois playlists reconnaissables à l'écran : A (Fond), B (Normale), C (Prioritaire), plus D pour le forçage.
- [ ] Programmations : A sur le site toute la journée (priorité Fond) ; B sur l'écran 08:00-18:00 (Normale) ; C sur le groupe 12:00-14:00 (Prioritaire) ; une programmation avec date de fin demain.
- [ ] Heure de la TV et du serveur alignées. Passer tout le document avec `VDS_BASE_PATH=/`, puis §1, §3, §5 avec `/espace/affichage`, en FR puis en EN (§1 au moins).

## 1. Timeline contre l'écran réel

- [ ] Planning de l'écran, vue Jour, curseur « maintenant » : le créneau sous le curseur correspond à ce que joue la TV (relevé : ____).
- [ ] Pour 4 instants (avant 08:00, 09:00, 12:30, après 18:00) : ouvrir le créneau (inspecteur), comparer avec la TV. Attendu : A, B, C, A.
- [ ] L'inspecteur donne la raison (« Priorité plus haute », « Seule programmation applicable ») et liste les perdants.
- [ ] Sur la piste Effectif, chaque changement de contenu tombe à la minute du changement réel sur la TV (écart : ____ s).
- [ ] Vues Semaine et Mois : mêmes résultats que la vue Jour pour un jour donné ; badges de conflits et d'expirations cohérents.
- [ ] Vue groupe : une ligne par écran, identique à la vue de chaque écran.

## 2. Priorités et masquage

- [ ] Le créneau B, recouvert par C entre 12:00 et 14:00, est hachuré avec « Masqué par C » sur sa piste de source ; la TV joue bien C.
- [ ] Le panneau de conflits annonce « B est masqué en partie par C ». Une programmation qui ne passe jamais est signalée.
- [ ] Changer C en Normale : B et C à égalité, la timeline et la TV se mettent d'accord sur le même gagnant (noter lequel : ____). Remettre Prioritaire.
- [ ] Les trois niveaux (Fond / Normale / Prioritaire) ont une icône et un libellé, pas seulement une couleur. Valeur personnalisée : aucun niveau coché, la bande reste affichée.
- [ ] Une programmation de l'écran bat celle du site même si la priorité du site est plus haute (ordre : cible la plus précise d'abord) ; la TV confirme.

## 3. Simulation (aucun effet)

- [ ] Bouton Créer : l'assistant se pré-remplit en cliquant un créneau vide (heures du site, jour de la semaine).
- [ ] Choisir une playlist et des horaires : l'aperçu annonce « Ajoute X sur Écran » puis « Remplace X par Y » en changeant les jours. Rien n'est écrit tant qu'on n'enregistre pas.
- [ ] Fermer sans enregistrer : la TV et la timeline sont inchangées (relevé : ____). Aucune ligne dans l'audit.
- [ ] Enregistrer : la TV passe au nouveau contenu au moment annoncé par l'aperçu (écart : ____ s).
- [ ] Editer et supprimer une programmation : l'aperçu annonce le retour au contenu précédent ; vérifier sur la TV après validation.

## 4. Forçage

- [ ] Forcer D sur la TV pour 15 min depuis la carte écran. Planning de l'écran : le créneau forcé apparaît (état Forcé, icône), inspecteur : source « Forçage », avec Prolonger / Arrêter.
- [ ] La TV joue D sous 10 s ; rien d'affiché au public indique un forçage.
- [ ] Le forçage masque la programmation pendant sa durée, y compris un créneau Prioritaire (C) si l'on est dans 12:00-14:00.
- [ ] Prochains changements (tableau de bord) annonce la fin du forçage.
- [ ] Arrêt anticipé : la TV revient au contenu programmé sous 10 s ; la timeline repasse à l'état normal.

## 5. Expiration visible

- [ ] Programmation dont la date de fin est demain : repère d'expiration sous la base de la timeline ; le lien ouvre la playlist concernée (URL sous la base).
- [ ] Vue Mois : badge d'expiration sur le jour concerné. Conflit « contenu expirant » avec l'heure et l'impact (trou ou repli).
- [ ] Contenu média avec fin de validité dans 5 min : la TV retire l'élément à l'heure indiquée par le repère (écart : ____ s) et la timeline annonce le trou.
- [ ] Prochains changements liste l'expiration.

## 6. Fuseau et heure d'été/hiver

- [ ] Navigateur dans un autre fuseau que Moulins (ex. régler l'ordinateur sur Londres) : la mention du fuseau du site apparaît dans la barre d'outils ; les heures affichées sont celles de Moulins et correspondent à la TV. Navigateur en Europe/Paris : aucune mention.
- [ ] Groupe avec deux sites de fuseaux différents : mention « fuseaux mixtes », fuseau de chaque ligne.
- [ ] Autour d'un changement d'heure (dernier dimanche de mars/octobre) : jour de 23 h ou 25 h, aucun créneau décalé ni doublé dans la timeline ; la TV bascule à l'heure locale attendue (à rejouer à la date, ou avec l'horloge de la TV avancée si le serveur le permet). Non automatisable sur l'API (fenêtre limitée à 42 jours), couvert par le test du résolveur.

## 7. Hors ligne et accessibilité

- [ ] Couper le serveur : la TV continue de jouer le plan reçu (journée et lendemain) y compris le passage A, B, C ; rebrancher, pas de rechargement visible.
- [ ] Clavier seul : flèches entre créneaux, Entrée = inspecteur, `N` = maintenant, PageUp/PageDown = jour. Fenêtre à 375 px : Liste par défaut, aucun débordement.
- [ ] Bascule FR/EN : aucune clé brute (`planning.*`) affichée, dates et heures localisées.
- [ ] Rôle éditeur : peut créer et simuler ; ne voit pas les actions réservées admin (voir forçage).

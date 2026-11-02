# Écarts fonctionnels VDS / Xibo CMS — admin « simple par défaut, puissant à la demande »

Statut : proposition de l'architecte (2026-11-02), **arbitrée par Victor le 2026-11-02 (§5)**, aucun code. Sources : `docs/roadmap.md`, `docs/refonte/ux.md`, `docs/design.md`, `docs/architecture-actions-rapides.md`. Référence Xibo : CMS v4 (fonctions d'admin, de mémoire, pas de copie).
États : **fait** (livré et testé) · **partiel** (prévu ou amorcé) · **absent**. Priorité = impact quotidien sur un parc de TV de campus (P1 tous les jours, P2 chaque semaine, P3 rare). Effort S (< 2 j), M (≤ 1 sem.), L (> 1 sem.).
`À vérifier` = statut non confirmable sans lecture des modèles (`apps/server/src/vds_server/db/models.py`), à faire par `scout`.

## 1. Inventaire et état

| # | Fonction Xibo | État VDS | Prio | Effort | Où / commentaire |
| --- | --- | --- | --- | --- | --- |
| 1 | Layouts : canevas, régions, ordre Z, fond, modèles | absent | P1 | L | Lot 4 « éditeur de grille + calques » ; modèles 4 gabarits (`design.md`) |
| 2 | Widgets en région (une playlist par région) | absent | P1 | M | Dépend de 1 ; la zone porte une playlist existante |
| 3 | Médiathèque : import, types, progression, sha256 | fait | — | — | Lot 2 (`storage.local`, image/vidéo/PDF/URL) |
| 4 | Médiathèque : dossiers | écarté | — | — | D1 : pas de dossiers ; vues grille + tableau, tags légers |
| 5 | Médiathèque : tags, recherche | à vérifier | P2 | S | Tags d'écran dans le périmètre V1 ; tags de média non spécifiés |
| 6 | Médiathèque : expiration / date de retrait, remplacement en place | absent | P2 | M | D6 : expiration sur médias ET playlists + purge base et stockage ; « Remplacer le fichier » sans casser les playlists |
| 7 | Médiathèque : usage (où est utilisé ce média) | absent | P2 | S | Lecture seule ; bloque la suppression aveugle |
| 8 | Playlists : ordre, durées, formulaire généré | fait | — | — | Lot 2 (L2-T8) |
| 9 | Playlists : transitions, sous-playlists, playlists dynamiques par tag | absent | P3 | M | Dynamique par tag = point d'accroche, pas V1 |
| 10 | Campagnes (séquence de layouts) | écarté V1 | — | — | D4 : aucune campagne en V1 |
| 11 | Planning : cible, créneaux, priorité numérique | fait | — | — | Lot 2 ; priorité brute = jargon (`ux.md` §1) |
| 12 | Planning : récurrence (quotidienne, hebdo, mensuelle), dates début/fin | partiel | P1 | M | Créneaux HH:MM livrés ; jours/dates/exceptions à confirmer ; lot H |
| 13 | Planning : dayparting (créneaux nommés réutilisables) | absent | P2 | S | « Matin », « Pause déjeuner » = modèles de créneaux (lot H) |
| 14 | Planning : vue calendrier, conflits, alternances | absent | P1 | L | Lot H ; endpoint de prévisualisation (`ux.md` §4.8) |
| 15 | Forçage / événement « interrupt », durée, fin auto | absent | P1 | M | Lot 4 « alternance + override » ; `source: "override"` réservé dans `/fleet/playback` |
| 16 | Groupes d'écrans, sites, héritage de config | fait | — | — | Lots 1–3 ; Xibo n'a pas l'héritage avec provenance (avantage VDS) |
| 17 | Groupes dynamiques par tag | partiel | P2 | S | Sélecteur de cible site/groupe/**tag** prévu (`design.md`) |
| 18 | Commandes écran : allumer/éteindre, WoL, CEC | fait | — | — | Lot 3 + lot E (masse, annulation 8 s) ; plus riche que Xibo |
| 19 | Commandes écran : redémarrer, recharger, capture d'écran | absent | P1 | M | Lot I (route de commandes player + stockage des captures) |
| 20 | Profils d'écran (réglages par type d'appareil) | partiel | P3 | — | Config héritée + clés `core` couvrent le besoin |
| 21 | Mise à jour du player à distance | fait | — | — | `apk.update` (vds-core 1.6.0) |
| 22 | Supervision, état, incidents, notifications mail | fait | — | — | Lot 3 + navigation A–C ; centre de notifications dans l'admin absent (P3) |
| 23 | Utilisateurs, rôles | partiel | P2 | M | admin/éditeur livrés ; écrans de gestion = lot J |
| 24 | Permissions par objet (partage view/edit/delete) | absent | P3 | L | Proposé : périmètre par site/groupe, pas d'ACL par objet (Q2) |
| 25 | Brouillon / publication / validation | absent | P2 | M | Lot 4 « groupes + validation » ; statuts dans `design.md` |
| 26 | Versions / historique d'un layout | absent | P3 | M | Audit existe (lot 1) ; restauration = plus tard |
| 27 | Aperçu au ratio réel | absent | P1 | M | Le player web en iframe sur la programmation résolue ; principe 3 de `design.md` |
| 28 | Import / export (layouts, playlists) | absent | P3 | M | Utile entre instances clients ; hors V1 |
| 29 | Widget horloge, texte, bandeau défilant | absent | P1 | S | Modules `content.*` / calques (lot 4) |
| 30 | Widget météo, RSS / ticker | absent | P2 | M | Lot 4 ; récupération côté serveur, cache, aucune clé dans le player |
| 31 | Widget page web | fait | — | — | `content.url` 0.2.0 (WebView native Android) |
| 32 | Datasets (tableaux de données saisies) | absent | P3 | L | Futur module `content.dataset` |
| 33 | Polices, modèles de layout partagés | absent | P3 | S | Thème + gabarits du lot 4 |
| 34 | Journal d'audit consultable | partiel | P3 | S | Audit écrit côté serveur ; pas d'écran |

### Modules futurs interdits en V1 (points d'accroche seulement)

| Fonction Xibo | Module futur VDS | Point d'accroche à préserver |
| --- | --- | --- |
| Alertes d'urgence automatiques (CAP, priorité absolue) | `alerts.*` | Le forçage manuel (#15) est V1 ; l'alerte publie un forçage via le bus, sans code noyau |
| SSO SAML / LDAP / Azure | `auth.*` | Interface `auth-provider` existante ; `auth.studio-allia` seul en V1 |
| Géo-planification, critères, déclencheurs | diffusion conditionnelle | Champ de règle extensible côté programmation, non implémenté |
| Layouts interactifs, actions tactiles | tactile | Rien dans le player V1 ; kiosque reste sans interaction |
| Groupes de synchronisation, murs d'écrans | murs d'écrans | Une mise en page = un écran ; pas de clé de synchro |
| Proof of play, rapports, statistiques | statistiques | Événements de lecture non émis en V1 ; nom réservé côté bus à concevoir |

## 2. Principe « simple par défaut, puissant à la demande »

1. **Un seul chemin court par intention** : 3 champs maximum visibles (quoi, où, quand). Tout le reste dans le volet « Avancé » unique (`design.md`), replié, mémorisé par utilisateur.
2. **Valeurs par défaut sûres** : planning « tous les jours, toute la journée » ; forçage avec fin obligatoire (1 h proposée) ; expiration vide ; priorité normale ; publication soumise à validation si le groupe l'exige.
3. **Modèles avant formulaires** : gabarits de mise en page, modèles de créneaux, durées de forçage prédéfinies. Le formulaire libre est la variante « Avancé ».
4. **Le niveau vient du schéma, pas de l'écran** : `x-vds-level: simple|advanced` sur chaque champ de module ; les fonctions noyau suivent la même règle (priorité, récurrence fine, transitions = `advanced`).
5. **Pas de concept sans besoin quotidien** : campagnes, dossiers, ACL par objet restent absents tant qu'un tag ou un groupe suffit.
6. **La puissance se voit dans le résultat, pas dans le formulaire** : compteur d'écrans touchés, aperçu, conflits annoncés en clair.

### Conséquences sur les lots F–J de `ux.md`

- **F (contenus)** : ajouter tags de média, expiration (date de retrait), « remplacer le fichier », « où est-il utilisé ». Pas de dossiers.
- **G (aide)** : glossaire étendu (forçage, créneau, mise en page, expiration) ; la palette Ctrl+K indexe aussi les tags.
- **H (planning)** : récurrence par modèles (tous les jours / semaine / dates / jours fériés exclus), créneaux nommés (dayparting), priorité en 3 niveaux visibles + numérique en Avancé, **forçage avec fin auto** et état `overridden`. Le forçage sort de H et passe devant (lot 1 ci-dessous).
- **I (tableau de bord)** : commandes player (recharger, redémarrer, capture) et stockage des captures, dans le même lot que la carte écran.
- **J (utilisateurs, modules)** : rôles par site/groupe, file de validation ; pas d'ACL par objet. Écran d'audit en lecture seule.
- **Nouveau, hors F–J** : éditeur de mise en page (lot 4 roadmap) avec aperçu, et widgets horloge/texte/météo/RSS en modules `content.*`.

## 3. Ordre proposé (top 6)

1. **Forçage avec fin automatique** (M) — `source: "override"` dans `/fleet/playback`, état `overridden`, bouton « Forcer » sur carte, en masse et sur l'Accueil. Fin : un forçage d'1 h sur un groupe s'affiche sur l'écran réel en < 30 s, se termine seul hors ligne, est visible dans la carte et l'audit ; pytest aux deux `VDS_BASE_PATH`.
2. **Mises en page + calques simples** (L) — 4 gabarits, une playlist par zone, calques horloge/texte défilant/logo, paysage/portrait, aperçu au ratio réel. Fin : mise en page « principal + bandeau » créée en < 2 min sans le volet Avancé, jouée hors ligne sur Android et Pi ; navigation clavier complète.
3. **Planning visuel (lot H)** (L) — calendrier semaine, modèles de récurrence, créneaux nommés, conflits en clair, priorité cachée. Fin : un éditeur programme « en semaine 8h–12h » sans saisir de priorité ; le conflit avec un planning existant est annoncé avant enregistrement ; prévisualisation = même résolveur que le player.
4. **Médiathèque organisée (lot F étendu)** (M) — tags, recherche, expiration, remplacer, usage. Fin : un média expiré disparaît de toutes les playlists à l'heure dite, y compris hors ligne (expiration dans la programmation résolue) ; remplacement sans re-saisie de playlist.
5. **Commandes player et captures (lot I)** (M) — recharger, redémarrer, capture à la demande et périodique, dernière capture sur la carte. Fin : capture visible dans la carte < 15 s après demande ; aucune capture stockée au-delà de la rétention configurée ; commandes tracées.
6. **Widgets météo et RSS + validation de publication** (M) — modules `content.weather`, `content.rss` (récupération serveur, cache) ; statuts brouillon / en validation / publié. Fin : un éditeur soumet, un admin valide depuis mobile, l'écran joue ; le flux RSS en panne garde le dernier contenu sans message technique au public.

Contrat : 1, 4 et 5 modifient la programmation résolue ou les trames player → bump vds-core mineur additif + note de compatibilité, à concevoir lot par lot par l'architecte. 2 et 6 passent par des modules `content.*` sauf le modèle de mise en page (noyau, déjà prévu).

## 4. Questions de décision pour Victor

- Q1 Médiathèque : tags + filtres enregistrés seulement (recommandé), ou dossiers façon Xibo ?
- Q2 Permissions : périmètre par site/groupe (recommandé), ou partage par objet ?
- Q3 Forçage avant mises en page (recommandé : oui, impact quotidien immédiat) ?
- Q4 Campagnes : inutiles en V1 (une playlist pourra contenir une mise en page plus tard) ?
- Q5 Priorité : 3 niveaux visibles (normal / important / forçage) + numérique en Avancé ?
- Q6 Expiration portée par le média (héritée par les éléments) ou par l'élément de playlist ?

## 5. Décisions de Victor (2026-11-02)

- **D1 (Q1) Médiathèque** : deux modes d'affichage, **grille** (aperçus) et **tableau**. **Pas de dossiers.** Tags légers possibles (filtre, recherche), sans hiérarchie.
- **D2 (Q2) Droits** : périmètre **par site ou par groupe**, jamais objet par objet (pas d'ACL par objet). Mise en œuvre au lot J.
- **D3 (Q3) Ordre** : le **forçage passe avant les mises en page** (lot 1 = forçage avec fin automatique, conception : `docs/architecture-forcage.md`).
- **D4 (Q4) Campagnes** : **aucune en V1**.
- **D5 (Q5) Priorité** : 3 niveaux visibles **normal, important, forçage** ; la priorité numérique n'apparaît qu'en Avancé (mapping : normal = 0, important = 100 ; forçage = entité distincte qui gagne toujours sur la programmation).
- **D6 (Q6) Expiration** : portée **à la fois par les médias et par les playlists**, appliquée dans la programmation résolue (donc hors ligne), avec **nettoyage** : purge en base et suppression des fichiers du stockage serveur des objets expirés (rétention configurable, audit conservé). Conception détaillée au lot médiathèque (rang 4) ; contrat : bump vds-core mineur additif à ce moment-là.

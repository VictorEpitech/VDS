# VDS — Design

## Principes UX

1. **L'essentiel d'abord** : champs `simple` visibles, volet « Avancé » générique pour les champs `advanced`.
2. **Héritage visible** : badge de provenance (« hérité du site Moulins ») + action « revenir à l'héritage ».
3. **Aperçu avant publication** au ratio réel de la cible (paysage / portrait).
4. **Pas de jargon dans le chemin principal** : « Allumer à 8h30 », pas « Wake-on-LAN ».

Utilisateurs : admin, éditeur (publication soumise à validation selon le groupe). FR/EN, libellés jusqu'à +30 %. Admin pensée desktop, actions rapides sur mobile (état du parc, allumer/éteindre, valider).

## Écrans admin V1

| Écran | Rôle | Priorité |
| --- | --- | --- |
| Tableau de bord | État du parc, publications en attente, incidents | critique |
| Parc d'écrans | Sites → groupes → écrans, statut, capture, actions | critique |
| Fiche écran | Statut, programmation active, alimentation, réglages hérités | critique |
| Médiathèque | Import images, vidéos, PDF, URL, widgets | critique |
| Mises en page | Liste paysage/portrait, accès éditeur | V1 |
| Éditeur de grille | Zones, calques, aperçu | critique |
| Playlists | Éléments, durées, transitions | V1 |
| Planning | Schedules et overrides sur calendrier | critique |
| Utilisateurs et groupes | Rôles, groupes, validation | V1 |
| Modules et réglages | Modules, permissions, formulaires générés, langues | V1 |

## Parcours prioritaires

1. **Ajouter un écran** : code affiché sur la TV → saisie + site/groupe → détection et test d'alimentation → héritage du groupe.
2. **Publier un contenu** : glisser dans la médiathèque → ajouter à une playlist → aperçu → publier ou envoyer en validation.
3. **Programmer** : layout + playlists → cible → créneau → voir les alternances sur la même cible.
4. **Forcer** : depuis le planning, layout + cible + créneau ; alternance suspendue clairement visible.
5. **Alimentation** : plages d'un site/groupe → exceptions → on/off immédiat depuis le parc (mobile inclus).

## Éditeur de grille

- Format au départ (16:9 / 9:16), modèles (plein écran, 2 colonnes, principal + latérale, principal + bandeau).
- Zones redimensionnables sur grille magnétique, une playlist par zone, marges de sécurité.
- Calques : bandeau défilant, logo, horloge, météo ; position/taille/opacité ; ordre modifiable ; calque commun à un site.
- Aperçu en direct accéléré ; bascule paysage/portrait.
- Hors périmètre : dessin, texte riche, retouche.

## États d'un écran

| État (code) | Sens | Traitement |
| --- | --- | --- |
| `online` | Allumé, joue | neutre / succès |
| `scheduled_off` | Éteint par le planning | neutre atténué (Secondary), jamais alerte |
| `offline` | Injoignable alors qu'il devrait être allumé | erreur |
| `power_failed` | Joignable, ordre d'alimentation échoué | avertissement |
| `overridden` | Override actif | accent (Tertiary) |
| `pairing` | Code affiché, non validé | information |

Toujours couleur + icône + libellé.

## Composants spécifiques

- Badge de provenance + « revenir à l'héritage »
- Volet « Avancé » repliable, unique
- Formulaire généré depuis JSON Schema : texte, nombre, liste, interrupteur, plage horaire, sélecteur de cible, groupe de champs
- Sélecteur de cible (site/groupe/tag/écran) avec compteur d'écrans touchés
- Carte d'écran : dernière capture, nom, état, programmation active, actions rapides
- Statut de publication : brouillon, en validation, publié, refusé

## Écrans système du player

| Écran | Quand | Contenu |
| --- | --- | --- |
| Appairage | 1er démarrage / reset | Logo, code en grand, adresse admin |
| Chargement | Démarrage, mise à jour | Logo, indicateur discret |
| Hors ligne avec cache | Serveur injoignable | Rien : le cache joue |
| Hors ligne sans contenu | Injoignable + cache vide | Écran neutre, aucun message technique |
| Écran noir | Plage off sans pilote | Noir |
| Maintenance | Déverrouillage depuis l'admin | Code, diagnostic |

Thème sombre recommandé pour ces écrans (fond `#0F172A`).

## Accessibilité

WCAG 2.2 AA (états compris), navigation clavier complète y compris l'éditeur de grille, jamais la couleur seule. Tailles de texte minimales lisibles à distance dans les gabarits de layout.

## Charte et thèmes

VDS a deux thèmes, chargés comme modules (`kind: theme`). Les composants n'utilisent que les variables `--vds-*` : changer de thème ne touche aucun composant.

| | `theme.vtech` (autonome) | `theme.allia` (hébergé Studio) |
| --- | --- | --- |
| Source | `charte-graphique-v-tech.md` | `integration-studio-allia.md` › Charte Allia |
| Primary | `#2A4B5A` / `#8BD3E6` en sombre | `#0e49be` (vif) |
| Titres / texte | Inter / Roboto | Bricolage Grotesque / Manrope |
| Mono | JetBrains Mono | JetBrains Mono |
| Icônes | Material Symbols Outlined | lucide (comme le Studio) |
| Ton | neutre | vouvoiement, phrases courtes, pas d'emoji |
| Mode sombre | oui | à définir |

En mode hébergé, l'en-tête de VDS reste léger : logo Allia officiel, lien « Retour au Studio » vers `/espace`, nom de l'utilisateur, déconnexion via le Studio. L'admin doit rester utilisable à 375 px (règle du Studio).

Manques : couleurs d'état (proposition à valider dans les deux thèmes), logo V-Tech, mode sombre Allia. Voir le skill `vds-ui-charte`.

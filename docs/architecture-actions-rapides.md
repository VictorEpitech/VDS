# Actions rapides et sélection multiple (lot E refonte UX)

Statut : décidé le 2026-11-02. Source : `docs/refonte/ux.md` §2.4 et lot E.

## Constat

- **Régression** : depuis la fusion de Supervision dans Écrans (lot B), les boutons Allumer/Éteindre ne sont rendus que par `ScreensStatus`, affiché seulement sous le filtre « à traiter » (`offline`, `power_failed`). Un écran **en ligne** n'a plus de bouton d'allumage dans la liste principale (`routes/Screens.tsx`).
- L'API existante couvre déjà : commandes d'alimentation unitaires (`POST /api/v1/sites/{site_id}/agents/{agent_id}/commands`, `power.set` avec `request_id` idempotent), état d'alimentation (`GET /api/v1/power/screens/{id}`), changement de groupe (`PATCH /api/v1/screens/{id}`), suppression (`DELETE /api/v1/screens/{id}`).
- N'existent pas : forçage (override) avec durée, commandes player admin (`reload`, `screenshot`), stockage de captures, programmation active par écran.

## Décisions

1. **Pas d'endpoint d'actions en masse en lot E.** La sélection multiple est un *fan-out* côté admin sur les endpoints unitaires existants. Aucun changement du contrat de module, du bus ni des trames agent/player : **pas de bump vds-core**.
   - Concurrence plafonnée à 4 requêtes ; sélection plafonnée à 200 écrans.
   - Sondage des commandes **mutualisé par agent** (un `GET .../commands` par agent toutes les 700 ms, pour toutes les commandes en attente de cet agent), pas un sondage par écran.
   - Résultat par écran (réussi / échec + cause traduite via `reasonOf`) ; « Réessayer les échecs » relance uniquement les échecs, avec un **nouveau** `request_id`.
   - Option écartée pour V1 : `POST /api/v1/screens/actions` (résultat par écran, jeton d'annulation serveur). Coût : service, file, audit agrégé, tests, types générés. À rouvrir si le parc dépasse ~50 écrans par sélection ou si le sondage devient un problème mesuré.
2. **Annulation = validation différée côté client, pas de jeton serveur.** Une action en masse réversible (Allumer, Éteindre, Changer de groupe) est mise en file 8 s avec « Annuler » ; rien n'est envoyé au serveur avant l'échéance. Raison : une extinction physique ne s'annule pas (rallumer n'est pas annuler, et peut échouer en CEC).
   - La file vit dans un fournisseur au niveau de l'application (survit à la navigation SPA) ; `beforeunload` avertit si une action est en attente ; fermer l'onglet abandonne l'action (le bandeau le dit).
   - Suppression : confirmation explicite conservée, pas d'annulation. Action unitaire sur la carte (Allumer/Éteindre d'un seul écran) : envoi immédiat, pas de délai.
3. **Périmètre de la barre d'actions en lot E** : Allumer, Éteindre, Changer de groupe, Supprimer. Retirés (masqués, pas grisés) :
   - « Forcer un contenu » et « Forcer… » sur la carte → lot H (le forçage avec fin automatique est un modèle de données de la programmation, §4.8 de `ux.md`).
   - « Appliquer un planning » → lot H (une programmation a une seule cible ; l'appliquer à N écrans = changer de groupe, ou cibler le groupe).
   - « Redémarrer » et « dernière capture » → lot I (exigent une route admin de commandes player et le stockage des captures ; à concevoir ensemble).
4. **Carte écran enrichie** (une seule carte, utilisée pour tous les filtres) : nom, site · groupe, état d'affichage unifié (`displayState` : présence + alimentation + incidents), dernière activité, état d'alimentation, **programmation active**, Allumer/Éteindre, kiosque, menu des autres actions. Le filtre « à traiter » ne fait que filtrer la même carte ; `ScreensStatus.ScreenCard` disparaît.
5. **Seul ajout serveur : lecture de la programmation active, en lot**, réutilisant le résolveur qui produit la programmation du player (aucune logique de résolution dupliquée dans le front) :

```
GET /api/v1/fleet/playback?site_id=<uuid?>        droits : ceux de GET /api/v1/screens
200 → list[ScreenPlaybackOut]

class ScreenPlaybackOut(BaseModel):
    screen_id: UUID
    source: Literal["schedule", "none"]   # "override" ajouté en lot H, additif
    schedule_id: UUID | None
    schedule_name: str | None
    playlist_id: UUID | None
    playlist_name: str | None
    until: datetime | None                # fin du créneau courant (UTC)
    next_change_at: datetime | None
```
   Additif à l'API REST admin (pas au contrat de module) : regénérer `packages/sdk-ts` (`make api-types`). Placé sous `/fleet` pour éviter le conflit avec `/screens/{screen_id}`.
6. **Droits** : Allumer/Éteindre restent réservés au rôle `admin` (règle serveur actuelle des routes agents) ; le front masque ces actions pour les autres rôles au lieu de laisser venir un 403. Changer de groupe et Supprimer : mêmes droits que les routes unitaires.
7. **Disponibilité de l'allumage** : boutons actifs si l'agent du site est `online`, quel que soit l'état de présence de l'écran (c'est précisément le cas « écran en ligne » à rétablir, et le cas « écran éteint » à rallumer). Sinon désactivés avec la raison liée (`aria-describedby`). En masse, les écrans sans agent en ligne sont exclus et comptés (« 2 écrans ignorés : agent hors ligne »).

## Garde-fous

- L'admin ne parle qu'au serveur (via `useApi`, préfixé par `VDS_BASE_PATH`) ; les commandes d'alimentation passent par l'agent de site. **Aucune connexion de l'admin vers un appareil du LAN.**
- Aucune branche de mode : rien ne dépend du Studio ; tests e2e sous `VDS_BASE_PATH=/` et `/espace/affichage`.
- Barre d'actions tonale sans ombre, feuille basse à 375 px, cibles ≥ 44 px, `aria-live` pour la file et les résultats, libellés i18n FR/EN avec pluriels.
- Aucun module futur introduit ; `source: "override"` n'est qu'un point d'accroche du schéma.

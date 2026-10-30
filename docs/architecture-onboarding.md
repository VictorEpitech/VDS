# VDS — Architecture, refonte navigation : « Premiers pas » et résumé de parc

Décision architecte du 30 octobre 2026. Source : `docs/refonte/ux.md` §2.2 et §4 points 1-2.
**Sans bump vds-core** : API admin additive du noyau ; manifeste, SDK, catalogue d'événements et permissions inchangés. Seule conséquence côté contrat client : `make api-types` (OpenAPI → `packages/sdk-ts`).

## Principes

- Noyau uniquement, aucun nouveau module : les deux endpoints ne font que **compter** des entités que le noyau connaît déjà (sites, écrans, appareils, médias, playlists, programmations, alimentation, incidents, agents).
- **État calculé, jamais déclaré** : une étape est « faite » d'après l'état réel ; la seule donnée persistée est le choix de l'utilisateur d'ignorer une étape ou de masquer la carte.
- Réponses en **identifiants stables** (`step`, `status`, `reason`), jamais en libellés : le front traduit (`onboarding.step.<id>.*`, `fleet.reason.<id>`), FR/EN.
- Aucune hypothèse d'hébergeur : routes sous `<VDS_BASE_PATH>/api/v1/…` via le `Mount` existant ; aucune étape ne dépend du fournisseur d'auth (voir étape `account`).
- `GET` en lecture seule (aucune écriture, aucune création paresseuse de ligne). Écritures `PUT` sous cookie : contrôle de même origine du pipeline existant, auditées.
- Rôles : `GET` admin et éditeur ; `PUT` admin seul (l'état est partagé par l'organisation).

## (a) Premiers pas — `GET /api/v1/onboarding`

### Étapes (ordre fixe, ids stables)

| # | `step` | Facultative | `done` si | `in_progress` si |
| --- | --- | --- | --- | --- |
| 1 | `account` | non | au moins un utilisateur `admin` actif (toujours vrai une fois authentifié) | — |
| 2 | `site` | non | au moins un site non archivé | — |
| 3 | `screen` | non | au moins un écran non archivé avec un appareil actif (appairé) | un écran existe sans appareil actif, ou une demande d'appairage est en attente |
| 4 | `media` | non | au moins un média | — |
| 5 | `playlist` | non | au moins une playlist avec au moins un élément | une playlist existe mais est vide |
| 6 | `schedule` | non | au moins une programmation dont la cible couvre au moins un écran non archivé | une programmation existe mais ne couvre aucun écran |
| 7 | `power` | oui | au moins une plage ou une exception d'alimentation définie (tous niveaux) | — |

- `account` : le changement de mot de passe est une affaire du module `auth.local` (forcé à la première connexion, dans son propre parcours), pas du noyau. L'étape reste pour la cohérence de la liste et se coche d'elle-même : aucun `if studio`, aucune capacité nouvelle demandée à `AuthProvider`.
- « Prévisualiser » (étape 5) n'est pas mesurable côté serveur et n'entre pas dans le calcul.
- Sous-étape `site_agent` de l'étape 2 : indicateur informatif `details.agent_connected`, jamais bloquant.
- Priorité des statuts : `done` l'emporte sur `skipped` (une étape ignorée puis réalisée s'affiche faite) ; `skipped` l'emporte sur `in_progress` et `todo`.
- `account` n'est pas ignorable.

### Réponse `200` (`OnboardingOut`)

```json
{
  "steps": [
    {"step": "account",  "status": "done",        "optional": false, "skippable": false, "count": 1, "details": {}},
    {"step": "site",     "status": "done",        "optional": false, "skippable": true,  "count": 2, "details": {"agent_connected": false}},
    {"step": "screen",   "status": "in_progress", "optional": false, "skippable": true,  "count": 0, "details": {"pending_pairing": 1}},
    {"step": "media",    "status": "todo",        "optional": false, "skippable": true,  "count": 0, "details": {}},
    {"step": "playlist", "status": "skipped",     "optional": false, "skippable": true,  "count": 0, "details": {}},
    {"step": "schedule", "status": "todo",        "optional": false, "skippable": true,  "count": 0, "details": {}},
    {"step": "power",    "status": "todo",        "optional": true,  "skippable": true,  "count": 0, "details": {}}
  ],
  "progress": {"completed": 3, "total": 6},
  "next_step": "screen",
  "complete": false,
  "card": {"hidden": false, "hidden_at": null}
}
```

- `status` : `todo | in_progress | done | skipped`.
- `progress` : `total` = étapes non facultatives ; `completed` = parmi elles, `done` + `skipped` (« 3 sur 6 » de l'UX).
- `next_step` : première étape non facultative ni `done` ni `skipped`, sinon première facultative dans ce cas, sinon `null`.
- `complete` : toutes les étapes non facultatives `done` ou `skipped`.
- `count` : nombre d'entités comptées pour l'étape (affichage « 2 sites »), sans liste.
- `details` : objet ouvert, clés documentées ci-dessus seulement ; ajout de clé = additif.
- Masquage automatique : le front affiche la réussite quand `complete` passe à vrai, puis appelle `PUT …/card`. Le serveur ne masque jamais de lui-même.

### Écritures (admin, même origine, auditées)

| Requête | Corps | Effet | Audit |
| --- | --- | --- | --- |
| `PUT /api/v1/onboarding/steps/{step}` | `{"skipped": true \| false}` | ignore / rouvre l'étape (idempotent) | `onboarding.step_skipped` / `onboarding.step_reopened` |
| `PUT /api/v1/onboarding/card` | `{"hidden": true \| false}` | masque / rouvre la carte (Aide › Premiers pas) | `onboarding.card_hidden` / `onboarding.card_shown` |

Réponse : `200` avec `OnboardingOut` complet (le front remplace son cache TanStack Query sans second `GET`). Pas d'événement de bus : aucun module n'en a besoin, le catalogue d'événements reste inchangé.

### Erreurs (format d'erreur existant `{"code", "detail"}`)

| HTTP | `code` | Cas |
| --- | --- | --- |
| 401 | (pipeline d'auth) | non authentifié |
| 403 | (pipeline de rôles / même origine) | éditeur sur `PUT`, ou origine refusée |
| 404 | `onboarding.step_unknown` | `{step}` hors liste |
| 409 | `onboarding.step_not_skippable` | `PUT …/steps/account` avec `skipped: true` |
| 422 | (validation Pydantic) | corps invalide |

### Persistance — migration additive du noyau `onboarding_state`

Prochaine révision de la branche noyau (après la tête actuelle), sans modification de table existante.

| Colonne | Type | Contrainte |
| --- | --- | --- |
| `organization_id` | `uuid` | PK, FK `organization.id` `ON DELETE CASCADE` |
| `skipped_steps` | `text[]` | `NOT NULL DEFAULT '{}'` |
| `card_hidden_at` | `timestamptz` | nullable |
| `updated_by` | `uuid` | FK `user_account.id` `ON DELETE SET NULL` |
| `created_at`, `updated_at` | `timestamptz` | comme les autres tables |

- Une ligne par organisation (singleton aujourd'hui) ; absence de ligne = valeurs par défaut. Les `PUT` font un upsert.
- `skipped_steps` accepte uniquement les ids connus à l'écriture ; un id devenu inconnu est ignoré à la lecture (pas d'erreur).
- Écarté : stocker dans `config_override` (état d'interface mêlé à la configuration héritée, apparaîtrait dans l'écran de réglages) ; stockage par utilisateur (l'UX vise l'installation, pas la personne) ; `localStorage` (par navigateur, fragile).

### Point d'accroche (non implémenté)

Des modules pourraient plus tard contribuer des étapes (ex. `discovery.lan` : « détecter les appareils »). Réservé : ids d'étape de module de la forme `<module_id>:<step>`, déclarés dans le manifeste ; cela, et seulement cela, serait une évolution de contrat (bump mineur vds-core). Les ids noyau restent sans préfixe.

## (b) Résumé de parc — `GET /api/v1/fleet/summary`

Paramètre : `site_id` (UUID, facultatif). Absent = toute l'organisation.

### Prédicat « à traiter » (unique, partagé)

Un écran non archivé est **à traiter** si : statut `offline` ou `revoked`, **ou** au moins un incident ouvert dont le sujet est cet écran (`screen.offline`, `power.failed`). `scheduled_off` et `unseen` ne sont jamais à traiter (`unseen` relève des Premiers pas).

Le statut est recalculé à la requête par la fonction pure `supervision.status.screen_status` (appareil, `last_seen_at`, `power_state.desired`, seuils `supervision.*` résolus), **pas** par le cache mémoire du `Supervisor` (cohérent en multi-processus, et identique à la logique des incidents).

Le même prédicat sert le filtre de liste : ajout additif `GET /api/v1/screens?issues=true` (et `status=<statut>` répétable), pour que `/screens?filter=issues` (cible de la redirection `/supervision`) et les compteurs de l'Accueil ne divergent jamais. Implémentation : une seule fonction SQL/Python côté serveur, appelée par les deux routes.

### Réponse `200` (`FleetSummaryOut`)

```json
{
  "site_id": null,
  "generated_at": "2026-10-30T09:12:00Z",
  "screens": {
    "total": 14,
    "by_status": {"online": 9, "offline": 2, "scheduled_off": 1, "unseen": 1, "revoked": 1}
  },
  "to_handle": {
    "screens": 4,
    "by_reason": {"offline": 2, "revoked": 1, "power_failed": 1}
  },
  "incidents": {
    "open": 5,
    "unacknowledged": 3,
    "by_severity": {"critical": 1, "warning": 4},
    "by_kind": {"screen.offline": 2, "power.failed": 1, "agent.offline": 1, "module.quarantined": 1}
  },
  "agents": {"total": 3, "online": 2, "offline": 1}
}
```

- `by_status` : toutes les clés de `ScreenStatus` toujours présentes (0 compris) ; un nouveau statut = clé ajoutée (additif).
- `to_handle.by_reason` : un écran peut compter dans plusieurs raisons ; `to_handle.screens` compte les écrans distincts. Clés possibles : `offline`, `revoked`, `power_failed`.
- `incidents` : incidents ouverts (`resolved_at IS NULL`). Avec `site_id`, seuls ceux du site ; les incidents sans site (`module.quarantined`) ne sont comptés que sans filtre.
- `agents` : agents de site non révoqués du périmètre ; `offline` selon `supervision.agent_offline_after_s`.
- Réservé pour le lot I (non renvoyé maintenant, ajout additif) : `pending_validation`, `next_changes`.
- Aucune liste d'écrans : le détail passe par `GET /api/v1/screens?issues=true&site_id=…`.

### Erreurs

| HTTP | `code` | Cas |
| --- | --- | --- |
| 401 / 403 | (pipeline) | non authentifié / rôle absent |
| 404 | `site.not_found` | `site_id` inconnu ou archivé |
| 422 | (validation) | `site_id` non UUID |

Pas de migration : uniquement des lectures (agrégats `COUNT … GROUP BY` + `screen_status` en Python sur les écrans du périmètre, quelques centaines au plus en V1).

## Tests attendus (agent `qa-tests`)

- Les deux endpoints aux deux `VDS_BASE_PATH` (`/` et `/espace/affichage`).
- Onboarding : chaque étape `todo → in_progress → done` sur données réelles ; `skipped` puis réalisation = `done` ; `account` non ignorable (409) ; éditeur 403 sur `PUT` ; `PUT` sans Origin valide refusé ; `GET` sans ligne `onboarding_state` = défauts et n'en crée pas.
- Fleet : cohérence stricte entre `to_handle.screens` et la longueur de `GET /screens?issues=true` (même périmètre, même instant) ; filtre site ; incident sans site exclu du résumé filtré ; `site.not_found`.

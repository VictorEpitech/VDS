# content.weather

Type de contenu (kind `content-type`, vds-core 1.12). Affiche la météo d'un lieu : température actuelle et prévisions sur 1 à 3 jours.

## Rôle

Le serveur interroge **MET Norway Locationforecast 2.0** (`api.met.no`, aucune clé d'API, aucun secret) via le client sortant anti-SSRF du noyau, normalise la réponse (`{now, days, attribution}`, JSON borné) et la stocke dans `content_feed`. Le player lit ces données sur **son propre serveur** (`/api/v1/player/feeds/{id}`) : il ne contacte jamais MET. Hors ligne, il affiche le dernier état connu tant qu'il a moins de `max_age_s` ; au-delà, l'élément est sauté (`feed_stale`) et rien n'est montré au public. Voir `docs/architecture-widgets.md`.

## Réglages

Organisation (niveau Avancé) :

- `refresh_min` (défaut 60, minimum 30) : intervalle entre deux récupérations. Le serveur respecte aussi l'en-tête `Expires` de MET (jamais plus tôt).
- `contact` (défaut vide) : adresse e-mail ou site ajouté au `User-Agent` envoyé à MET, qui l'exige pour joindre l'exploitant. Le `User-Agent` de base est `VDS/<version> (+<VDS_PUBLIC_ORIGIN>)`.

Élément : `label` (nom affiché), `latitude`, `longitude` (arrondies à 4 décimales), `days` (1 à 3), `units` (`metric` ou `imperial`, Avancé), `max_age_s` (défaut 21600, Avancé). Deux éléments au même lieu partagent le même flux.

## Attribution

Données © MET Norway, licence CC BY 4.0 (https://api.met.no/doc/License). Le player affiche la mention « Données MET Norway » de façon discrète ; la donnée normalisée porte `attribution: "MET Norway"`.

## Limites

- Pas de géocodage : latitude et longitude sont saisies à la main (point d'accroche futur : sélecteur de lieu).
- Les jours sont découpés en UTC (écart d'au plus quelques heures aux latitudes européennes).
- Le player (pictogrammes, mise en page paysage, portrait, bandeau) est une tâche distincte : `assets/player.js` est pour l'instant un élément qui échoue proprement (`player_pending`), donc l'élément est sauté.

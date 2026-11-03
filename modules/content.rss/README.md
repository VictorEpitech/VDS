# content.rss

Type de contenu (kind `content-type`, vds-core 1.12). Affiche les articles d'un flux RSS 2.0 ou Atom, en liste ou en bandeau défilant.

## Rôle

Le serveur récupère le flux via le client sortant anti-SSRF du noyau, l'analyse avec `defusedxml` (DTD, entités et références externes refusées : XXE et « billion laughs » rejetés), puis le réduit à **du texte brut** : `{title, items: [{title, summary, published}]}`, 20 entrées au plus, titre ≤ 200 caractères, résumé ≤ 400, balises retirées, entités décodées, dates en ISO 8601. Ni lien, ni image, ni HTML ne quittent le serveur. Le player lit ces données sur son propre serveur et les rend avec `textContent`. Hors ligne, dernier état connu jusqu'à `max_age_s`, puis élément sauté. Voir `docs/architecture-widgets.md`.

## Réglages

Organisation (niveau Avancé) :

- `allowed_hosts` (défaut vide = tout hôte public en https) : liste blanche de domaines, sous-domaines compris. Relue à chaque récupération.
- `refresh_min` (défaut 15, minimum 5) : intervalle entre deux récupérations.
- `max_bytes` (défaut 1 Mio, maximum 2 Mio) : taille maximale du flux.

Élément : `url` (https, requis), `max_items` (1 à 10, défaut 5), `display` (`list` ou `ticker`), `item_duration_s` (défaut 8), `show_date` (défaut oui), `max_age_s` (défaut 86400). En mode liste la durée de l'élément est `max_items × item_duration_s`. Comme `content.url`, l'adresse du VDS lui-même est refusée.

## Limites

- Les flux d'un réseau privé (intranet) sont refusés par conception ; un relais par l'agent de site serait un futur module.
- Texte seul : pas d'images de flux en V1 (point d'accroche : image via la médiathèque).
- Player : `display: list` montre les entrées (titre, date locale, résumé coupé) par pages d'autant d'entrées que la zone en tient, une page toutes les `item_duration_s` secondes ; `ticker` fait défiler les titres en continu puis rend la main (`vds-ended`). Tout est inséré avec `textContent`. Les entrées sans titre ni résumé sont ignorées.

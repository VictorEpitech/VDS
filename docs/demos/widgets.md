# Démo widgets — météo (MET Norway) et RSS

Durée : ~40 min. Spécification : `docs/architecture-widgets.md`, README de `modules/content.weather` et `modules/content.rss`. Aucun secret ici (aucune clé d'API).
Matériel : un serveur VDS (autonome `/` puis hébergé `/espace/affichage`) avec accès sortant https, un compte admin, un écran réel (Android TV ou Pi) enrôlé, une playlist de test, un moyen de couper le réseau de l'écran (câble, Wi-Fi, ou pare-feu) puis du serveur, un téléphone ou une fenêtre à 375 px, un flux RSS public en https et un flux Atom.

## Prérequis

- [ ] Rebuild Docker pour les migrations 0018 à 0021 (dont `content_feed` et `publication`) :
  `VDS_APK_HOST_DIR=/home/victor/vds-apk docker compose -f docker/compose.yaml -f docker/compose.apk.yaml --env-file .env up -d --build server agent`
- [ ] Les logs serveur montrent les migrations appliquées sans erreur ; `content.weather` et `content.rss` apparaissent dans Réglages > Modules (actifs, `core >=1.12`).

## Non couvert par l'automatique (à constater ici)

- Appel réel à `api.met.no` et à un vrai flux RSS depuis le serveur (les tests utilisent des fixtures).
- Rendu sur écran réel (lisibilité à distance, pictogrammes, défilement fluide) et sur le matériel de packaging.
- Coupure réseau réelle de l'écran et du serveur.

## 1. Météo : configuration

- [ ] Réglages > Modules > Météo (niveau Avancé) : `refresh_min` vaut 60 par défaut, minimum 30 (saisir 10 est refusé).
- [ ] `contact` vide : ajouter un élément météo à la playlist ; il reste inactif avec le message `contact_required` (traduit, pas la clé brute).
- [ ] Renseigner `contact` (adresse e-mail ou site), Enregistrer : le module devient actif.
- [ ] Ajouter un élément Météo : `label` (ex. « Paris »), latitude 48.8566, longitude 2.3522 ; le formulaire est généré, `days` 1 à 3, `units` et `max_age_s` (défaut 21600) sous Avancé.
- [ ] Latitude hors bornes (ex. 95) : refusée avec message clair. Saisir 48.85661234 : enregistré arrondi à 4 décimales.
- [ ] Deux éléments au même lieu : un seul flux côté serveur (pas de double requête dans les logs).
- [ ] Aucune clé d'API demandée nulle part.

## 2. Météo : rendu sur écran réel

- [ ] Programmer la playlist sur l'écran réel : température actuelle et prévisions (1, 2 puis 3 jours) s'affichent, jours dans la langue de l'écran.
- [ ] Mention discrète « Données MET Norway » visible mais non gênante (attribution CC BY 4.0).
- [ ] Trois mises en page selon la zone : paysage plein écran, portrait, bandeau (zone très large, ratio >= 2,6) dans une mise en page à zones.
- [ ] `units` imperial : températures en °F.
- [ ] Le player n'émet aucune requête vers `api.met.no` (console réseau ou DevTools distant : seul le serveur VDS est contacté, URL sous la base).
- [ ] Les logs serveur montrent un `User-Agent` `VDS/<version> (+<origine>)` avec le contact ; pas de nouvelle requête avant `refresh_min` ni avant `Expires`.

## 3. RSS : configuration et rendu

- [ ] Ajouter un élément RSS avec une URL https valide : `max_items` (1 à 10), `display` liste ou bandeau, `item_duration_s`, `show_date`.
- [ ] URL `http://` : refusée. URL de l'origine du VDS lui-même : refusée. URL d'un hôte privé (ex. 192.168.x.x, `localhost`) : refusée ou flux jamais récupéré (anti-SSRF), sans fuite d'information technique au public.
- [ ] Réglages > Modules > RSS : `allowed_hosts` renseigné avec un autre domaine : le flux d'un hôte non listé est refusé ; vide = tout hôte public.
- [ ] Mode liste : titres, date locale, résumé coupé ; une page toutes les `item_duration_s` s ; durée de l'élément = `max_items x item_duration_s`.
- [ ] Mode bandeau : défilement continu des titres, puis passage à l'élément suivant.
- [ ] Flux Atom : mêmes rendus. Texte seul, aucune image, aucun lien, aucun HTML visible (balises absentes, entités décodées).
- [ ] Flux dont les titres contiennent `<script>` ou du HTML : affichés comme texte inerte.

## 4. Hors ligne et saut au-delà de `max_age_s`

Prérequis : un élément météo avec `max_age_s` court (ex. 600) et un élément RSS avec `max_age_s` court, dans la même playlist, déjà affichés une fois.

- [ ] Coupure du serveur VDS pendant la lecture : la lecture continue, météo et RSS affichent le dernier état connu sans message d'erreur visible.
- [ ] Coupure d'Internet côté serveur seulement (le player joint toujours le serveur) : le dernier état connu reste servi ; dans Supervision, le flux passe en échec après 3 tentatives (événement `feed.failed`, une seule fois), aucune alerte envoyée au public.
- [ ] Rétablissement : le flux repart, `feed.recovered`, les données se rafraîchissent.
- [ ] Redémarrage de l'écran serveur coupé (hors ligne) : au rallumage, le dernier état connu (IndexedDB) s'affiche tant que son âge est inférieur à `max_age_s`.
- [ ] Attendre plus que `max_age_s` serveur coupé : l'élément est sauté (la playlist enchaîne), l'écran n'affiche ni erreur ni message technique ; la cause (`feed_stale`) est remontée au serveur au retour du réseau.
- [ ] Écran jamais synchronisé sur un flux (cache vide) et serveur coupé : élément sauté (`feed_unavailable`).
- [ ] Un écran ne peut pas lire un flux absent de sa programmation (`/api/v1/player/feeds/<id>` d'un autre flux : 404).

## 5. Aux deux `VDS_BASE_PATH`

- [ ] Refaire les sections 1 à 4 (au moins rendu, hors ligne, saut) avec `VDS_BASE_PATH=/` puis `/espace/affichage` : l'URL des flux player est sous la base ; aucun 404 d'asset ni d'API.
- [ ] Mode hébergé : même comportement, thème Allia, charte respectée.

## 6. Critères de sortie

- [ ] Aucun accès du player à une URL externe.
- [ ] Aucune clé, aucun secret saisi ou stocké.
- [ ] Mention MET toujours présente à l'écran.
- [ ] FR puis EN : aucun libellé manquant (clés affichées) dans les formulaires Météo et RSS ni dans les messages d'erreur.
- [ ] Mobile 375 px : formulaires sans défilement horizontal.

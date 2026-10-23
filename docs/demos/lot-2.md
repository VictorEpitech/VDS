# Démo lot 2 — Premier écran qui joue (TCL Google TV)

Durée : ~60 min. Aucun secret dans ce document : mots de passe et jetons saisis par vous, jamais copiés ici.
Contexte : un serveur VDS joignable depuis la TV (`https://vds.client.fr` en autonome, ou `https://allia-innovation.fr/espace/affichage` en hébergé), une TCL Google TV, un poste avec `adb`.

## Non couvert par l'automatique (à constater ici)

- WebView Android + Service Worker sur `127.0.0.1:47800`, Device Owner, démarrage au boot, relance en arrière-plan.
- Décodeur vidéo matériel réel, rendu des couleurs et du texte à 3 m, lecture PDF/URL sur WebView, télécommande.
- Tout ce qui est automatisé (`make test`, `make e2e`, `make lint`, `make api-types-check`) tourne sur Chromium de bureau, pas sur la TCL.

## 0. Préparer les médias de test

- [ ] 1 image JPEG/PNG, 1 vidéo H.264 (court, ~20 s) et 1 vidéo lourde (≥ 300 Mo), 1 PDF de 3 pages, 1 URL HTTPS autorisant l'affichage en iframe.
- [ ] Un compte admin et un compte éditeur sur le serveur.

## 1. Installation de l'APK par ADB

```
adb connect <ip-tv>:5555
adb install -r app-release.apk        # ou app-debug.apk
```
- [ ] Débogage réseau activé (Options pour les développeurs), empreinte acceptée sur la TV.
- [ ] `adb install` répond `Success`. L'icône VDS Player apparaît.

## 2. Device Owner (TV neuve, aucun compte Google)

```
adb shell dpm set-device-owner app.vds.player/.AdminReceiver
```
- [ ] Succès. Sinon : compte présent, réinitialiser la TV. Repli à noter : kiosque doux (`set-home-activity`).
- [ ] Après lancement : plus de barre système, Accueil/Retour/appui long sans effet.

## 3. URL serveur

- [ ] Premier lancement : écran « Adresse du serveur » ; saisir l'URL **avec préfixe** en hébergé. Vérification `GET <url>/health` OK.
- [ ] URL erronée : message d'erreur sobre, pas de sortie de l'app.
- [ ] Injection alternative : `adb shell am start -n app.vds.player/.ProvisionActivity --es server_url <url>` (permission `DUMP`, shell uniquement). Vérifier qu'un `--es server_url` envoyé à `MainActivity` est sans effet.
- [ ] URL `http://` hors boucle locale : refusée (APK standard). APK `-Pvds.lanHttp=true` : acceptée, `player.insecure_upstream` visible côté admin.
- [ ] Iframe `content.url` d'une autre origine : `window.VdsNative` indéfini (aucun `ping`/`reload`/`info`).
- [ ] Faire l'essai complet une fois avec `VDS_BASE_PATH=/` puis une fois avec `/espace/affichage`.

## 4. Appairage

- [ ] La TV affiche un code `ABC-234` et rien d'autre de technique (ni URL, ni version).
- [ ] Admin : créer un écran, « Appairer », saisir le code, choisir l'écran. La TV bascule seule (≤ 3 s) sur l'écran d'attente de programmation.
- [ ] Code expiré (15 min) ou déjà utilisé : refusé côté admin avec message clair.
- [ ] Redémarrer la TV avant le claim : le même code revient.

## 5. Médiathèque

- [ ] Envoyer l'image, la vidéo, la vidéo lourde, le PDF : progression visible, statut `processing` puis `ready`, miniature/vignette.
- [ ] Envoyer deux fois le même fichier : proposition de dédoublonnage.
- [ ] Fichier au-dessus de `core.media_max_upload_mb` : refus clair. Derrière le proxy Dokploy : limite du proxy alignée.
- [ ] Supprimer un média utilisé par une playlist : 409 explicite.

## 6. Playlist et programmation

- [ ] Créer une playlist : image 8 s, vidéo (durée = fin naturelle), PDF (durée par page), URL, puis l'image à nouveau. Réordonner au clavier.
- [ ] Programmation : cible = l'écran, créneau 08:00-19:00, priorité 0. La TV lit la playlist en boucle ≤ 60 s après enregistrement (heartbeat).
- [ ] Modifier la playlist : la révision change, la TV recharge sans écran noir prolongé.
- [ ] Hors créneau : écran noir (`off`), pas d'information affichée.
- [ ] Programmation plus spécifique (écran) prioritaire sur site.

## 7. Risques à observer

- [ ] **Deux `<video>` sur un seul décodeur** : mettre deux vidéos de suite, puis une seule en boucle, puis vidéo -> image -> vidéo. Observer : image figée, écran noir prolongé, saut, erreur `playback_error`/`prepare_failed` dans le résumé heartbeat. Le player libère la vidéo courante avant de préparer la suivante (court noir attendu entre deux vidéos) ; mesurer ce noir.
- [ ] **Vidéo lourde** : lecture, saut en avant (Range), mémoire stable pendant 30 min. Vérifier aussi le temps de téléchargement initial (sha256 en JS).
- [ ] **HTTP / HTTPS** : refaire l'étape 6 avec un serveur en `http://IP-locale`. Attendu : le relais en boucle donne un contexte sécurisé, SW présent, hors ligne OK. Si le SW est absent (`navigator.serviceWorker.controller` nul via `chrome://inspect`), la lecture hors ligne est impossible : bloquant.
- [ ] HTTPS avec certificat de réseau interne non reconnu : comportement de la WebView (échec de chargement attendu, aucun contournement).
- [ ] **PDF et URL** : le PDF s'affiche page par page ; une URL qui interdit l'iframe est sautée, pas d'écran d'erreur public. Rappel : lecture `props.pages` du PDF encore à finaliser (roadmap, case ouverte).
- [ ] Horloge TV sans NTP : décalage tolérable, créneaux corrects.

## 8. Coupure réseau

- [ ] Couper le réseau (câble/Wi-Fi) pendant la lecture : la lecture continue sans interruption, sans bandeau public.
- [ ] Redémarrer l'app (ou la TV) réseau coupé : la playlist reprend depuis le cache, y compris la vidéo (Range).
- [ ] Coupure **du serveur** seul (arrêt du conteneur) : idem. Admin : l'écran passe `offline` après 3 heartbeats manqués.
- [ ] Créneau `off` puis rallumage programmé traversés réseau coupé : le noir puis la lecture reprennent à l'heure.
- [ ] Remettre le réseau : le heartbeat reprend, les modifications faites entre-temps sont récupérées.

## 9. Coupure de courant et reprise

- [ ] Débrancher la TV en pleine lecture, rebrancher (serveur joignable) : l'app revient seule (boot), sans interaction, et rejoue.
- [ ] Idem **serveur coupé** au rallumage : l'app rejoue depuis le cache, sans code d'appairage.
- [ ] Coupure pendant le téléchargement d'un média : au retour, reprise ou nouveau téléchargement, aucun média corrompu joué (sha256 vérifié).
- [ ] Horloge fausse au démarrage sans réseau : dérive précédente réutilisée.

## 10. Révocation

- [ ] Admin : révoquer l'appareil de l'écran. La TV (≤ 60 s) efface jeton, programmation et cache, et revient à un **nouveau** code d'appairage.
- [ ] Ré-appairer : l'ancien jeton reste invalide.
- [ ] Appairer un second appareil sur le même écran : le premier est révoqué (`replaced`).

## 11. Kiosque

- [ ] Aucune voie de sortie à la télécommande (Accueil, Retour, menu, appui long, boutons applis).
- [ ] Aucun texte technique visible du public à aucun moment (URL, version, erreurs, code hors appairage).

## Résultat

Noter pour chaque case : OK / KO / observation. Toute case KO du paragraphe 7 est à reporter dans `docs/roadmap.md` › Risques.

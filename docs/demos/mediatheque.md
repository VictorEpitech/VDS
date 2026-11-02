# Démo médiathèque — expiration, remplacement, purge, miniatures

Durée : ~45 min. Spécification : `docs/architecture-mediatheque.md`. Aucun secret ici. Ne rien scanner du LAN : l'écran est appairé à la main.
Matériel : un serveur VDS (autonome `/` ou hébergé `/espace/affichage`), un écran réel appairé (TV Samsung ou TCL), un téléphone ou une fenêtre à 375 px, un compte éditeur et un compte admin d'organisation.

## Non couvert par l'automatique (à constater ici)

- L'admin n'est testé par Playwright que contre des serveurs simulés (`apps/admin/vite.mock-*.ts`), pas contre le vrai backend. Le contrat serveur est couvert par pytest ; la jonction vraie UI / vrai serveur ne l'est que par cette démo.
- Le rendu réel sur TV (décodage vidéo, fondu, noir à l'expiration), la précision de l'horloge de la TV, le comportement réseau coupé réellement (câble débranché).
- Automatisé : `make test`, `make lint`, `make e2e` (admin x4 combinaisons préfixe/thème, player, `e2e:real`).

## 0. Préparer

- [ ] Deux images (A, B), une vidéo courte, un PDF de 3 pages, dans une playlist « Démo » diffusée en continu sur l'écran.
- [ ] Écran appairé, lecture visible, admin et écran sur le même serveur.

## 1. Miniatures

- [ ] Médiathèque, vue grille : image, vidéo et PDF ont chacun une miniature nette (pas d'icône générique, pas de lien cassé).
- [ ] Vue tableau (bascule, mémorisée après rechargement) : mêmes miniatures, triables, filtre par tag fonctionnel.
- [ ] Ouvrir le détail d'un PDF : les pages s'aperçoivent ; un PDF volontairement corrompu est refusé avec un message clair, sans média créé.

## 2. Expiration à l'heure dite, hors ligne

- [ ] Poser sur l'image A une expiration à H+3 min (détail du média, Date/Heure, Enregistrer ; « Expire le » s'affiche, fuseau Europe/Paris).
- [ ] Couper le réseau de l'écran (câble ou Wi-Fi) avant l'échéance. L'écran continue de jouer.
- [ ] À l'heure dite, même sans réseau, A cesse d'être joué (y compris si elle est affichée à cet instant : arrêt au tick suivant) ; B et la suite continuent.
- [ ] Rétablir le réseau : l'admin montre A « Expiré », l'éditeur de playlist la barre (« Expiré, n'est plus diffusé »), « Où est-il utilisé » le signale.
- [ ] Expiration au milieu d'un créneau : programmer la playlist sur un créneau en cours, expirer la playlist à H+3 min ; au bout, l'écran passe à ce qui suit (autre programmation, sinon noir/hors service), sans redémarrage.
- [ ] Playlist dont tous les éléments sont expirés : écran noir, aucune information technique côté public ; l'admin signale « Playlist sans élément actif ».
- [ ] Prolonger A pendant la rétention (nouvelle date) : diffusion reprise, « Expiré » disparaît.
- [ ] Ajouter A (expirée) à une autre playlist : refusé. Créer un forçage sur une playlist expirée : refusé.

## 3. Remplacement en direct, sans réappairage

- [ ] Image A de nouveau diffusée. Détail > « Remplacer le fichier » : le message d'impact indique le nombre de playlists et d'écrans.
- [ ] Choisir un autre fichier (même type), « Remplacer maintenant » : l'écran affiche la nouvelle image sous quelques secondes (WebSocket) ou au plus tard au prochain heartbeat, sans réappairage ni rechargement manuel.
- [ ] Pendant le téléchargement, l'ancienne image reste affichée (pas d'écran vide).
- [ ] Remplacer par un fichier d'un autre type (image par vidéo) : refus explicite. Par un fichier corrompu ou un PDF piégé : refus, l'écran garde l'ancien contenu.
- [ ] Si un agent de site est présent : son cache récupère le nouveau fichier ; l'écran en LAN joue la nouvelle version.
- [ ] Journal d'audit : entrée « média remplacé » avec ancien et nouveau sha256.

## 4. Purge après rétention forcée à 0

Réservé à une instance de démo : irréversible.
- [ ] Admin d'organisation > Configuration avancée : `core.content.expired_retention_days` = 0 (la provenance indique « organisation »).
- [ ] Expirer un média non critique ; attendre le passage du balayeur (jusqu'à 1 h, ou redémarrer le serveur si la boucle le permet).
- [ ] Le média disparaît de la médiathèque, retiré des playlists ; l'audit conserve « média purgé » avec titre, sha256 et éléments retirés.
- [ ] Les fichiers disparaissent du stockage après le délai du collecteur (5 min, plus 48 h de grâce si issus d'un remplacement). L'écran ne tente plus de le charger.
- [ ] Remettre la rétention à la valeur d'origine (retour à l'héritage).

## 5. Responsive 375 px et thèmes

- [ ] À 375 px : médiathèque (grille, tableau, détail), téléversement, remplacement et éditeur d'expiration utilisables, sans défilement horizontal ni zone illisible.
- [ ] Thème V-Tech puis Allia (mode hébergé) : contrastes lisibles, pas d'ombres portées, police mono seulement pour sha256 et clés.
- [ ] Bascule FR/EN : aucun libellé manquant ni clé brute sur ces écrans.
- [ ] Aux deux préfixes (`/` et `/espace/affichage`) : miniatures, téléversement et remplacement fonctionnent (aucune URL absolue cassée).

# Recette : relancer l'application VDS sur une TV (vds-core 1.14.0)

Durée : ~45 min. À faire sous `VDS_BASE_PATH=/` puis `/espace/affichage`. Aucun secret ici.
Matériel : TV de test Android/Google TV appairée (kiosque Device Owner, puis kiosque doux), agent de site actif avec `power.adb` >= 0.4.0 (capacité `app.restart.android`, ADB réseau autorisé), compte admin et compte éditeur, un écran Pi ou web.
Référence : `docs/architecture-relance-app.md`.
Automatisé (déjà vert) : pytest serveur `test_restart_app` (2 bases), agent `test_agent_app_restart`, `modules/power.adb`, `test_compat_114`, vitest admin `commands` et `media`. Ici : ce qui dépend de la vraie TV.

## 1. Chemin player (app saine)

- [ ] Admin, fiche de l'écran : menu Actions contient « Relancer l'application ». Confirmer : l'historique indique « Exécutée par le player » et passe à ok.
- [ ] La TV relance l'app, lecture reprise, kiosque conservé (aucun launcher ni info technique visible du public).

## 2. Chemin agent (app figée ou fermée)

- [ ] `adb shell am force-stop app.vds.player` à la main (ou app figée) : attendre que le player passe hors ligne.
- [ ] « Relancer l'application » reste proposé. Confirmer : historique « Exécutée par l'agent de site », statut ok, l'app revient en < 30 s.
- [ ] Kiosque conservé après relance, en Device Owner ET en kiosque doux. Rien d'affiché au public.
- [ ] Relancer deux fois de suite : la seconde est refusée (anti-rafale 30 s), message traduit.
- [ ] Pendant une mise à jour APK : refus `busy` traduit.

## 3. Indisponibilité et erreurs

- [ ] Arrêter l'agent de site, player hors ligne : bouton absent ou erreur « Agent du site hors ligne » (409), jamais de relance tardive à son retour (TTL 2 min).
- [ ] Écran Pi / web / PWA : chemin player seul ; Tizen : bouton absent (hors périmètre, Q1).
- [ ] ADB non autorisé / app non installée / TV injoignable : message FR/EN précis, sans sortie shell brute.
- [ ] Compte éditeur : aucun bouton ; l'API répond 403 (`cmd.role_required`).
- [ ] Sélection multiple : file de 8 s annulable (annuler n'envoie rien).
- [ ] Interface en EN : libellés et codes d'erreur `app.restart.*` traduits.

## 4. API (admin, cookie)

- [ ] `POST <base>/api/v1/screens/{id}/commands` avec `{"name":"restart","args":{"scope":"app"}}` : 202 ; même `request_key` : même commande (idempotence). Origine croisée : 403.
- [ ] `via: "agent"` avec `scope: "device"` : 422.

## 5. Vignettes médiathèque (ce63ca6), aux deux bases

Pour `VDS_BASE_PATH=/` puis `/espace/affichage` :
- [ ] Médiathèque : vignettes d'images affichées, URL sans double slash initial (onglet Réseau : `/api/...` et non `//api/...` sous `/`).
- [ ] Vidéo : pictogramme vidéo ; document (PDF) : pictogramme document ; aucune image cassée.
- [ ] Sous `/espace/affichage` : les URL de vignettes commencent par `/espace/affichage/api/`.
- [ ] Sélecteur de médias dans un éditeur de contenu : mêmes vignettes.

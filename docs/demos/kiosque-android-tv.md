# Recette kiosque Android TV (TCL Google TV, compte Google)

Durée : ~2 h (+ 4 h sans télécommande, à lancer en fin de journée). Aucun secret ici : jetons et mots de passe saisis par vous.
Matériel : TCL 2023 avec compte Google, poste avec `adb`, APK release signée, serveur VDS (`/` puis `/espace/affichage`), compte admin, écran appairé.
Référence : `docs/architecture.md` › « Android TV : compte Google et kiosque géré », `packaging/android/README.md`, `docs/revue-securite-kiosque.md`.
Automatisé (déjà vert) : `make lint`, `make test`, `make e2e`, `make api-types-check`, `./gradlew :relay:test`. Ici : tout ce qui dépend de la vraie TCL.

## 1. Device Owner avec compte Google (ordre dpm)

- [ ] `adb connect <ip>:5555`, empreinte acceptée.
- [ ] Retirer le compte Google puis les profils supplémentaires. Noter si la TCL impose une réinitialisation d'usine.
- [ ] `adb shell dumpsys account` : aucun compte. `adb shell pm list users` : un seul utilisateur.
- [ ] `adb install -r app-release.apk` puis `adb shell dpm set-device-owner app.vds.player/.AdminReceiver` : accepté (sinon noter le message exact).
- [ ] Réajouter le compte Google. Play Store s'ouvre, le kiosque tient. Plus de sélecteur de profil au démarrage.
- [ ] `adb shell settings put secure attentive_timeout -1` ; veille automatique TCL désactivée.
- [ ] URL serveur par `ProvisionActivity`, appairage de l'écran.
- [ ] Admin : l'écran affiche « Verrouillé » et « verrouillage natif », aucune alerte kiosque doux.

## 2. Verrouillé (Device Owner)

- [ ] Accueil, Retour, appui long, Assistant/micro, Google, raccourcis YouTube/Netflix, Entrée/source, réglages TCL : sans effet visible, lecture continue.
- [ ] Notifications et lancement d'autres apps bloqués.
- [ ] Redémarrer la TV : l'app revient seule, verrouillée. Tuer l'app (`adb shell am force-stop app.vds.player`) : relance verrouillée.
- [ ] Aucune URL, code ni info technique visible du public à aucun moment.
- [ ] `adb shell dumpsys activity activities | grep -i lock` : lock task actif.
- [ ] Débogage ADB réseau et changement de date refusés dans les Réglages (restrictions Device Owner). `adb connect` impossible après reverrouillage (procédure technicien : maintenance d'abord).

## 3. Kiosque doux (sans Device Owner)

Sur une TV de test, ou après `dpm remove-active-admin app.vds.player/.AdminReceiver` :
- [ ] `adb shell cmd package set-home-activity app.vds.player/.MainActivity`
- [ ] `adb shell appops set app.vds.player SYSTEM_ALERT_WINDOW allow`
- [ ] `adb shell settings put secure attentive_timeout -1`
- [ ] Boot : l'app démarre seule. Retour neutralisé, plein écran, écran maintenu.
- [ ] Accueil, Assistant, source : une autre app ou les réglages apparaissent, puis l'app revient seule. Chronométrer le délai (le public voit IP/réseau pendant ce temps : limite connue).
- [ ] Admin : alerte « Kiosque doux » visible (état demandé `locked`, appliqué `soft`).
- [ ] Après une OTA ou mise à jour du launcher : le rôle HOME est-il repris ? (à revérifier à chaque mise à jour système).

## 4. Maintenance bornée (5 à 240 min)

- [ ] Admin, fiche écran : « Mode maintenance », durée 5 min + motif. Durées hors 5–240 refusées. Seul un admin peut lancer (un éditeur : bouton absent ou refus serveur).
- [ ] La TV passe en maintenance en quelques secondes : Accueil, Retour et Réglages fonctionnent, la lecture continue, aucun bandeau ni compte à rebours côté public.
- [ ] Badge admin : « Maintenance jusqu'à HH:MM », mode appliqué rapporté par l'appareil.
- [ ] Débogage réseau activable pendant la maintenance ; coupé au reverrouillage.
- [ ] Fin anticipée (« Terminer la maintenance ») : reverrouillage immédiat.
- [ ] Journal d'audit (base) : `maintenance_started` (durée, motif), `maintenance_ended` (`user`, ou `system` à l'échéance).

## 5. Reverrouillage après échéance, en ligne et hors ligne

- [ ] En ligne : à l'échéance, la TV se reverrouille (au plus un heartbeat de retard), l'admin repasse « Verrouillé ».
- [ ] Hors ligne : lancer 5 min, puis couper le réseau de la TV. À l'échéance, reverrouillage seul (minuterie native), sans serveur.
- [ ] Hors ligne + redémarrage de la TV pendant la maintenance : après boot, verrouillé (ancre monotone perdue) ; si l'horloge est en retard de plus de 5 min sur le serveur, aucune maintenance acceptée.
- [ ] Reculer la date de la TV pendant une maintenance (réseau coupé) : l'échéance ne s'allonge pas.
- [ ] Ouvrir les Réglages TV pendant la maintenance et y rester à l'échéance : au retour sur l'app, le lock task est de nouveau actif (nouvelle tentative toutes les 5 s, correctif B1). Noter si l'écran reste libre.
- [ ] `unlocked` (réglage groupe/écran) : survit au redémarrage hors ligne ; se reverrouille si le player est mort (bail 15 min).

## 6. Retour automatique après Home/Retour

- [ ] Maintenance terminée ou kiosque doux : Accueil puis laisser l'app : retour au premier plan, délai noté.
- [ ] Idem après ouverture de l'Assistant, de la source, des réglages TCL.
- [ ] Après 4 h sans télécommande : l'écran ne s'éteint pas, l'app est toujours au premier plan, lecture OK.

## 7. Réglages TCL à consigner

- [ ] Veille automatique sans signal, économiseur d'écran, « Éteindre l'écran après 4 h », démarrage rapide/HDMI-CEC, mise à jour auto des apps (Play Store, WebView), OTA nocturne. Valeur retenue pour chacun.
- [ ] Mise à jour de la WebView par le Play Store pendant la lecture : le player redémarre seul (`onRenderProcessGone`), pas de plantage.
- [ ] Après OTA : débogage réseau toujours actif ? HOME conservé ? Device Owner conservé ?

## 8. Vérifications du badge admin

- [ ] Liste et fiche écran : « Verrouillé » / « Déverrouillé » / « Maintenance jusqu'à HH:MM », FR et EN, thèmes clair et sombre.
- [ ] Filtre kiosque de la liste cohérent avec les badges.
- [ ] « Mode appliqué : verrouillage natif » en Device Owner ; « verrouillage doux » + alerte sans ; « pas encore rapporté » avant le premier heartbeat.
- [ ] Le badge reflète l'état natif confirmé : forcer un échec (TV en Réglages) montre `soft` ou non confirmé, jamais un faux `lock_task`.
- [ ] Ces libellés sont des déclarations de l'appareil, pas une preuve (revue M5).
- [ ] Aux deux `VDS_BASE_PATH`.

## Résultat à noter (puis reporter dans `docs/architecture.md`)

- Version Android/firmware TCL, ordre dpm exact, besoin de réinitialisation, comportement du Play Store sous Device Owner.
- Comportement de l'Assistant et des menus TCL en lock task, délais de retour automatique.
- Écarts à ouvrir dans `docs/roadmap.md` (K6).

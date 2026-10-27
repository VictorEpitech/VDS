# Revue sécurité : kiosque géré (Android TV, maintenance, verrouillage piloté)

Périmètre : commits `1660866`..`5eaa6d4` (1660866, 08079d6, 6988f59, 324c136, 5eaa6d4), décision « Android TV : compte Google et kiosque géré » (`docs/architecture.md` §606-632). Revue en lecture seule, le 24 octobre 2026. Les dépendances n'ont pas été réaudités : une seule ligne Gradle a été ajoutée côté relais.

**Verdict initial : bloquant pour le lot** (B1). **Statut au 24 octobre 2026, après correctifs : plus aucun point bloquant** (lint, pytest, vitest, Playwright verts). Le texte sous le tableau est la revue d'origine, conservée pour traçabilité.

| Constat | Statut | Correctif ou reste |
| --- | --- | --- |
| B1 reverrouillage jamais retenté | Corrigé | `KioskDispatcher` retente toutes les 5 s sans annuler le relock ; `MainActivity.onResume` rappelle `enforce()` ; `enforce` capte `IllegalArgument/IllegalState` et n'annonce `lock_task` qu'après `lockTaskModeState` ; le player renvoie tant que non confirmé. À confirmer sur la TCL (K6). |
| I1 recul d'horloge hors ligne | Corrigé | échéance absolue `elapsedRealtime` jamais allongée ; maintenance refusée si l'horloge retarde de plus de 5 min sur `server_time` ; `DISALLOW_CONFIG_DATE_TIME` + date auto. Reste : après redémarrage de la TV l'ancre est perdue (borne serveur+240 min). |
| I2 JS tierce peut déverrouiller | Corrigé | `claimKiosk` (une fois, avant tout content-type, retiré de `window.VdsNative`) + jeton aléatoire ; `unlocked` = bail natif de 15 min. Reporté : isolation des content-types tiers en iframe sandbox. |
| I3 heartbeat = mode demandé | Corrigé | le natif répond le mode appliqué (`kiosk.applied`) ; `ok:false` = non confirmé, `soft` (`kiosk.mismatch` côté player). |
| I4 ADB et options développeur | Corrigé en partie, risque assumé | `SAFE_BOOT`, `FACTORY_RESET`, `CONFIG_DATE_TIME` (levée en maintenance). `DISALLOW_DEBUGGING_FEATURES` n'est plus posée : ADB LAN ouvert accepté (décision du 27 octobre 2026, voir `docs/revue-securite-provision-android.md` I2/I3). Sans Device Owner : non applicable. |
| M1 audit | Corrigé | motif optionnel, valeur résolue auditée, déplacement d'écran audité. |
| M2 motif non normalisé | Corrigé | `clean_reason`. Reporté : neutralisation `=+-@` à l'export CSV (aucun export aujourd'hui). |
| M3 `enforce` renvoie `LOCK_TASK` à tort | Corrigé | voir B1. |
| M4 ancienneté de `unlocked` | Reporté (backlog) | filtre « déverrouillé depuis N jours ». |
| M5 heartbeat déclaratif | Reporté | libellé « rapporté par l'appareil » à vérifier en K6 (recette §8). |
| M6 mode web : clavier actif en maintenance | Reporté (accepté) | pas une sortie. |

## Ce qui tient

- **Rôles** : `POST/DELETE /api/v1/screens/{id}/kiosk/maintenance` passe par `require("admin")` (`apps/server/src/vds_server/api/v1/kiosk.py:29`). Le `PUT/DELETE /api/v1/config` générique passe par `write_guard=require("admin")` (`main.py:100`). Un éditeur peut lire mais ne peut ni déverrouiller ni lancer une maintenance. Aucune élévation éditeur→admin n'a été trouvée côté serveur.
- **Portées** : `player.kiosk` est limité aux portées groupe et écran, contrôlé par `key_scopes` dans `set_override`. `load_requests` ignore de toute façon les niveaux organisation et site (`player/kiosk.py:80`).
- **Échéance en lecture seule** : `readOnly` est refusé en `PUT` et en `DELETE` (`config/service.py:144`, `:223`). Seul `upsert_override` écrit la clé, et seulement depuis `KioskService`. Aucun autre chemin d'écriture de `ConfigOverride` n'existe : pas de duplication ni d'import. Il n'y a donc pas de contournement par l'API de config.
- **Durée bornée côté serveur** : de 5 à 240 min, validée deux fois (Pydantic et service). `until` est calculé avec l'horloge serveur, jamais fourni par le client. Une maintenance échue est ignorée par `served()` dès l'échéance, puis retirée par un delete conditionnel. Ce delete est sûr avec plusieurs workers : un seul audit `system/expired`.
- **ETag et cache** : `kiosk` entre dans `revision_of` (`player/schedule.py:161`). La réponse porte `Cache-Control: private, no-cache` et le heartbeat compare la révision. Un écran en ligne reçoit donc `locked` au plus un heartbeat après l'échéance. Un 304 garde l'ancien `server_time`, ce qui ne peut que raccourcir le plafond côté player.
- **Même origine** : les nouveaux endpoints sont sous `<base>/api/`, donc couverts par `SameOriginMiddleware`, sans exemption Bearer (`middleware/same_origin.py`).
- **Pont natif** : `addWebMessageListener` et `addDocumentStartJavaScript` sont restreints à l'origine du relais. `BridgeRouter` exige `isMainFrame` et `trusted()` (URL courante sous `<base>/player/`). `content.url` refuse la même origine, et la CSP du player `frame-src https:` s'applique aussi aux redirections. Une page externe qui redirige vers `http://127.0.0.1:47800` est donc bloquée. Le relais ne retire pas l'en-tête CSP. La sandbox de l'iframe n'a pas `allow-top-navigation`.
- **Message forgé** : la regex `kiosk:([A-Za-z0-9_-]{1,32}):(locked|unlocked|maintenance):(\d{1,12})` est stricte, et `coerceIn(0, 240 min)` borne la durée. Un mode inconnu ou une durée nulle donnent `LOCKED`.
- **Retrait de la commande maintenance** : `showMaintenance`, le type d'écran `maintenance`, les libellés i18n et la branche de `onCommands` sont supprimés. Le serveur n'a jamais émis ce type. Aucun bandeau ni compte à rebours n'est affiché au public. Le test e2e `player.spec.ts:336` vérifie que la commande est ignorée.
- **Motif** : 200 caractères au plus, stocké en JSON dans `audit_log`. Il n'est ni journalisé (`logging`) ni affiché par l'admin (pas de vue d'audit), et React échappe de toute façon le texte. Aucune injection possible aujourd'hui.

## Bloquant

### B1. Un reverrouillage qui échoue n'est jamais retenté : l'écran reste hors lock task jusqu'au redémarrage

- **Où** :
  - `packaging/android/relay/src/main/kotlin/app/vds/relay/KioskDispatcher.kt:50-73` : `timer.cancel()` en tête de `apply`, et aucune nouvelle tentative quand le passage à `LOCKED` échoue.
  - `packaging/android/app/src/main/kotlin/app/vds/player/AndroidKiosk.kt:40-63` : seul `SecurityException` est intercepté.
  - `apps/player/src/kiosk.ts:95-103` : `req()` résout avec le JSON d'échec au lieu de rejeter, `ok` n'est pas lu et `sent` reste positionné.
  - `MainActivity.kt` : `enforce()` n'est appelé que dans `bootstrap()`, jamais au retour au premier plan.
- **Scénario** : un technicien en maintenance ouvre les Réglages TV, donc `MainActivity` est arrêtée. À l'échéance :
  1. Le minuteur natif appelle `relock()`, et `startLockTask()` depuis l'arrière-plan lève. Sur AOSP, c'est `IllegalArgumentException: Invalid task, not in foreground`, à confirmer sur la TCL. Une nouvelle tentative est programmée à 5 s.
  2. Presque en même temps, le tick JS envoie `kiosk:…:locked:0`. `apply()` commence par `timer.cancel()`, ce qui tue la nouvelle tentative, puis échoue à son tour et répond `ok:false`.
  3. Le JS ne renvoie rien (`sent === "locked:0"`).
  4. `AndroidKiosk.locked` vaut déjà `true` (affecté avant `enforce()`), donc `onRelocked` n'est pas appelé.
  5. Au retour sur l'app (HOME persistant), rien ne rappelle `enforce()`.

  Le même cas se produit, sans même de minuteur, quand un admin repasse en `locked` un écran `unlocked` dont l'app n'est pas au premier plan. Résultat : l'écran public n'est plus en lock task. Les touches Assistant, Source et réglages TCL redeviennent actives jusqu'au prochain redémarrage. L'admin voit `applied=locked` : le heartbeat remonte le mode JS, pas le mode natif (voir I3). Seul l'avertissement « kiosque doux » apparaît.
- **Correctif** :
  - Dans `KioskDispatcher`, quand le verrouillage échoue, reprogrammer `relock()` toutes les `RETRY_MS`, et ne jamais annuler un relock en attente sur une demande `LOCKED`.
  - Dans `MainActivity.onResume` ou `onWindowFocusChanged(true)`, appeler `kiosk.enforce()` si `kiosk.locked`.
  - Dans `AndroidKiosk.enforce`, intercepter `IllegalArgumentException` et `IllegalStateException`. Ne renvoyer `LOCK_TASK` que si `ActivityManager.lockTaskModeState == LOCK_TASK_MODE_LOCKED`.
  - Côté JS, sur `ok:false`, remettre `sent = ""` pour réessayer au tick suivant.
  - Ajouter le cas « lock échoue puis réussit au premier plan » à `KioskDispatcherTest`.

## Important

### I1. Maintenance prolongeable indéfiniment hors ligne en reculant l'horloge de la TV

- **Où** : `apps/player/src/kiosk.ts:51-63` et `KioskDispatcher.kt:52`. La doc affirme « insensible à la dérive de l'horloge TV » (`architecture.md:629`), ce qui est faux hors ligne.
- **Scénario** : pendant une maintenance légitime (lock task levé, Réglages accessibles), une personne coupe le réseau et recule la date de la TV d'un an. `now()` vaut l'horloge locale plus la dérive persistée, donc il recule aussi. `until > now` reste vrai et `remaining` devient énorme, puis est borné à 240 min par le natif. Ce plafond natif est réarmé à chaque nouvelle demande, c'est-à-dire à chaque rechargement de page ou redémarrage. Entre deux, le JS reste en `maintenance` avec `webLocked=false`. Résultat : le kiosque est relâché sans limite tant que l'écran reste hors ligne.
- **Correctif** :
  - Dans `apply()`, si `now < Date.parse(server_time) - 5 min`, considérer l'horloge comme manipulée et forcer `locked`.
  - Côté natif, garder une échéance absolue en `elapsedRealtime` par valeur de `until`, et ne jamais l'allonger : passer `until` dans le message et conserver `min(échéance existante, nouvelle)`.
  - Avec Device Owner, poser `DISALLOW_CONFIG_DATE_TIME` et `setAutoTimeRequired(true)`.
  - Corriger la phrase de la doc.

### I2. Toute JS de la frame principale peut déverrouiller sans limite, et le natif n'authentifie pas la demande

- **Où** : `VdsBridge.kt:67-95`, `BridgeRouter.kt:18-23` et `apps/player/src/content-loader.ts:29` (scripts `content_types` chargés par `import()` dans la frame principale).
- **Scénario** : un module content-type, installé par un admin ou compromis dans sa chaîne de publication, appelle `window.VdsBridgeMsg.postMessage("kiosk:x:unlocked:0")`. `unlocked` n'a aucune borne native, et `isMainFrame` comme `trusted()` sont vrais. Les permissions du manifeste ne protègent pas cette capacité. On n'a pas trouvé de vecteur éditeur : les médias sont servis avec `CSP: sandbox`, ni SVG ni HTML ne sont acceptés, et `content.url` est cross-origin.
- **Correctif** :
  - Au démarrage, le player capture `VdsNative.kiosk` dans une fermeture, puis supprime `window.VdsNative.kiosk` avant de charger un content-type.
  - Côté natif, n'accepter `kiosk:` que si la demande cite la `revision` du document servi, vérifiée par le relais qui voit passer `/player/schedule`. Ou, au minimum, n'accepter qu'un seul émetteur, avec un nonce remis une seule fois au premier `info()`.
  - À terme, isoler les content-types tiers (iframe sandbox), comme prévu par l'architecture.

### I3. Le heartbeat remonte le mode demandé par le JS, pas le mode appliqué par le natif

- **Où** : `apps/player/src/kiosk.ts:82-84` et `inventory/devices.py:78-92`.
- **Scénario** : c'est la conséquence de B1 et de la désynchronisation après un relock natif. L'admin voit `applied=locked` (ou `maintenance`) alors que l'appareil est dans l'autre état.
- **Correctif** : la réponse native renvoie le mode réellement appliqué (`dispatcher.mode` et l'état lock task), et `report()` l'utilise. Afficher « état non confirmé » si `ok:false`.

### I4. ADB et options développeur activables pendant une maintenance, et actifs après reverrouillage

- **Où** : `AndroidKiosk.kt` (aucune `addUserRestriction`).
- **Scénario** : pendant une maintenance, une personne active le débogage réseau dans les Réglages. Après le reverrouillage, n'importe qui sur le LAN peut lancer `adb connect`, puis `am task lock stop`, réécrire l'URL via `ProvisionActivity` (`DUMP`, accordée au shell), ou désinstaller des apps.
- **Note (27 octobre 2026)** : la partie `DISALLOW_DEBUGGING_FEATURES` est abandonnée par décision (ADB LAN ouvert accepté pour l'alimentation par ADB). Correctif d'origine : avec Device Owner, poser `DISALLOW_DEBUGGING_FEATURES`, `DISALLOW_SAFE_BOOT`, `DISALLOW_FACTORY_RESET` et `DISALLOW_CONFIG_DATE_TIME`. Les lever seulement pendant `maintenance` si le besoin est réel, sinon les garder en permanence. Documenter la procédure ADB du technicien.

## Mineur

- **M1. Audit incomplet** (`config/service.py:190-210`) :
  - `screen.kiosk.unlocked` et `screen.kiosk.locked` n'ont pas de motif.
  - `delete_override` audite toujours `locked`, même si le groupe reste `unlocked` (valeur effective `unlocked`).
  - Déplacer un écran dans un groupe `unlocked` le déverrouille sans audit `screen.kiosk.*`.
  - `maintenance_ended/expired` est daté à la détection et non à l'échéance.

  Correctif : accepter un `reason` optionnel sur le PUT et le DELETE de `player.kiosk`. Auditer la valeur résolue après l'écriture. Auditer le changement de mode effectif lors d'un déplacement d'écran. Ajouter `until` aux `details` de `maintenance_ended`.
- **M2. Motif non normalisé** (`api/v1/kiosk.py:19`) : retours à la ligne, caractères de contrôle et caractères bidi (U+202E) passent. C'est sans effet aujourd'hui, mais ce sera un risque d'usurpation visuelle dans une future vue d'audit ou dans un export CSV ou de journaux. Correctif : `strip()`, refuser les catégories Unicode `Cc`, `Cf` et `Zl`/`Zp`, et neutraliser `=+-@` en tête de cellule à l'export.
- **M3. `enforce()` renvoie `LOCK_TASK` même quand le verrouillage a levé `SecurityException`** (`AndroidKiosk.kt:60-63`) : l'admin croit à un lock task actif. Correctif : renvoyer `SOFT` dans ce cas (voir aussi B1).
- **M4. `unlocked` n'a aucune durée et survit au redémarrage hors ligne** : c'est voulu par la décision, mais rien ne rappelle à l'admin un écran déverrouillé depuis longtemps. Correctif : afficher l'ancienneté du déverrouillage et filtrer « déverrouillé depuis plus de N jours » dans l'admin (backlog).
- **M5. Heartbeat déclaratif** : un détenteur du jeton d'appareil peut annoncer `locked/lock_task`. C'est inhérent au modèle, mais l'admin ne doit pas présenter l'état comme une preuve. Un libellé « rapporté par l'appareil » suffit.
- **M6. Mode web** (Pi, Tizen, PWA) : en `unlocked` ou `maintenance`, `webLocked=false` réactive clavier, menu contextuel et retour arrière dans la page. Ce n'est pas une sortie, puisque Chromium `--kiosk` et Tizen restent figés, mais à noter : un clavier branché sur un Pi public peut interagir avec la page pendant une maintenance.

# VDS — Architecture : `content.url` en WebView native (Android)

Décision du 2026-11-02. Aucun changement du contrat de module ni du noyau (vds-core inchangé). Seul le module `content.url` passe en 0.2.0, et le pont `VdsNative` du shell Android gagne une capacité optionnelle.

## Problème

`content.url` affiche la page dans une `<iframe sandbox>`. Les sites qui envoient `X-Frame-Options` ou `frame-ancestors`, comme https://www.epitech.eu/, sont refusés (`net::ERR_BLOCKED_BY_RESPONSE`). L'événement `load` se déclenche quand même, et la page d'erreur Chromium apparaît alors **au public**, ce qui enfreint la règle 6. Xibo s'en sort parce qu'il charge la page en navigation de premier niveau dans une WebView native.

## Décision

Sur Android, quand le shell expose `VdsNative.web`, `content.url` utilise **toujours** une WebView native superposée, positionnée sur le rectangle de l'élément. Il n'y a pas de stratégie « iframe d'abord, puis repli » : depuis le JS, on ne détecte pas de façon fiable qu'une iframe cross-origin a été bloquée. Partout ailleurs (PWA, Chromium sur Pi, Tizen, ou APK trop ancien sans `web`), on garde l'iframe actuelle.

Le choix se fait par détection de capacité, sans test de plateforme. Le player reste servi par le serveur et l'APK évolue à son propre rythme : un ancien APK retombe automatiquement sur l'iframe.

Options écartées :
- Proxy serveur qui retire XFO/CSP : surface SSRF, cookies et licences des sites, pages cassées.
- Navigation de premier niveau de la WebView du player : le player perd son état et son autonomie.
- Drapeaux Chromium : inaccessibles en production.

## Pont (frame principale d'origine locale uniquement, comme `cec`/`kiosk`)

Le script `VdsBridge.script` ajoute `web` à `window.VdsNative` seulement si le shell le prend en charge. Les messages passent par `VdsBridgeMsg` et sont routés par `BridgeRouter`, qui exige `isMainFrame && trusted()`.

```ts
// packages/sdk-ts : type déclaratif (pas d'évolution du contrat content-type)
interface VdsNativeWeb {
  /** Crée ou recharge la vue `id` cachée, résout quand la page principale est chargée sans erreur. */
  prepare(id: string, url: string, rect: Rect): Promise<string>; // JSON {ok:boolean, code?:string}
  show(id: string): void;
  rect(id: string, rect: Rect): void;     // ResizeObserver + bail
  hide(id: string): void;                  // about:blank, retire la vue
}
interface Rect { x: number; y: number; w: number; h: number } // px CSS ; le natif multiplie par density
```

Messages, avec le même format que `cec`/`kiosk` :
- `web:<reqId>:prepare:<id>:<json {url,rect}>` → réponse `{"ok":true}` ou `{"ok":false,"code":"url_native_error|url_timeout|url_refused"}`.
- `web:0:show:<id>`, `web:0:rect:<id>:<json>`, `web:0:hide:<id>` : sans réponse.

`<id>` vaut `[A-Za-z0-9_-]{1,32}`. Le natif accepte au plus 2 vues à la fois (la courante plus la préparation de la suivante).

## WebView native (packaging/android, `WebOverlay.kt`)

- Instance séparée, ajoutée dans un `FrameLayout` racine au-dessus de la WebView du player. `MainActivity` passe donc de `setContentView(webView)` à `setContentView(FrameLayout(webView, overlays…))`.
- **Aucun pont** : ni `addJavascriptInterface`, ni `addWebMessageListener`, ni `addDocumentStartJavaScript` sur cette vue. Le stockage local du player (origine `127.0.0.1`) lui est inaccessible, puisque c'est une autre origine. Les cookies tiers sont désactivés pour cette vue.
- Réglages : JS et DOM storage actifs ; `allowFileAccess`/`allowContentAccess` = false ; `mixedContentMode = NEVER_ALLOW` ; `setSupportMultipleWindows(false)` ; `mediaPlaybackRequiresUserGesture = true` ; `onPermissionRequest` → `deny()` ; géolocalisation refusée ; téléchargements ignorés ; pas de menu contextuel.
- `shouldOverrideUrlLoading` : seules les navigations `https:` de la frame principale sont acceptées, hors de l'origine du relais (redirections comprises). Tout le reste (`http:`, `intent:`, `file:`, `content:`, `127.0.0.1`, etc.) est refusé. Si la page initiale atterrit sur un refus, le natif répond `url_refused`.
- **Kiosque** : la vue est non focusable, et `dispatchTouchEvent` renvoie `false`. Les touches arrivent donc à la WebView du player (le geste admin de déverrouillage continue de fonctionner) et la télécommande ne peut pas naviguer dans la page distante.
- **Aucune info technique au public** : la vue reste `INVISIBLE` jusqu'à `onPageFinished` sans `onReceivedError` ni `onReceivedHttpError` sur la frame principale. En cas d'erreur, elle n'est jamais montrée : le natif répond `ok:false` et le player saute l'élément.
- Délai natif de 9 s, aligné sur `LOAD_TIMEOUT_MS`.
- **Pas de vue orpheline** : toutes les vues sont détruites au `onPageStarted` de la WebView du player (rechargement watchdog), au `onRenderProcessGone`, au passage en `onStop`, et si aucun `rect` ni `show` n'arrive pendant 30 s (bail ; le module renouvelle `rect` toutes les 10 s).
- Ordre Z : la vue native couvre tout ce qui est dessiné dessous dans sa zone. Le player appelle `hide` sur toutes les vues avant d'afficher son interface de déverrouillage admin. Le natif cache aussi les vues quand le kiosque passe en `unlocked`/`maintenance`.

## Module `content.url` 0.2.0 (`assets/player.js`)

- `prepare()` garde les contrôles actuels (`url_same_origin`, `url_invalid`, `offline`). Ensuite, si `window.VdsNative?.web` existe, il passe par le natif. Le rectangle vient de `getBoundingClientRect()` et un `ResizeObserver` le tient à jour. Sinon, il garde le chemin iframe actuel.
- `start()` → `show(id)` ; `stop()` → `hide(id)`. `reload_interval_s` → `prepare` sur la même vue.
- Les codes d'erreur remontent dans `vds-error` / `errors[]`, et rien n'est jamais affiché au public.
- `allowed_hosts` reste validé côté serveur sur les props de l'élément, sans changement. Le natif ajoute son propre filtre `https:` et hors relais.
- Manifeste : version 0.2.0. La description FR/EN passe de « cadre isolé » à « cadre isolé, ou vue native sur Android ».

## Hors Android (limite connue, suivi)

L'iframe reste en place. Un site qui refuse l'iframe y montre encore la page d'erreur Chromium. Point d'accroche prévu, non implémenté ici : la partie serveur du module sonde les en-têtes à l'enregistrement de l'élément (https, `allowed_hosts`, IP publique uniquement, 5 s, sans suivre les redirections vers le privé). Elle pose alors `frame_refused: true` dans les props résolues, et le chemin iframe rejette avec `url_frame_refused`. L'admin affiche « Ce site refuse l'affichage en cadre : il ne s'affichera que sur les écrans Android ». Cela reste de la logique de module, hors noyau.

## Tests

- relay (JVM) : `BridgeRouter` refuse `web:*` si `!isMainFrame` ou `!trusted()`, refuse un `id` invalide et au-delà de 2 vues ; filtre d'URL (https seul, origine du relais refusée, `intent:` refusé).
- vitest : `content.url` avec un faux `VdsNative.web` (chemin natif, rect, renouvellement du bail, `ok:false` → rejet) ; sans `web` → iframe, comportement inchangé.
- Sur la TV TCL : https://www.epitech.eu/ s'affiche, en plein écran et dans une zone partielle. Une page de test qui teste `window.VdsNative`/`VdsBridgeMsg` et renvoie le résultat à un serveur de test → indéfinis. Touches et D-pad sans effet sur la page, geste admin fonctionnel. URL en erreur ou redirection vers `http:` → jamais visible, élément sauté. Rechargement watchdog → aucune vue orpheline. Hors ligne → sauté. Vérifier avec `VDS_BASE_PATH=/` et `/espace/affichage`.
- PWA et Chromium : comportement identique à aujourd'hui.

## Risques

- Ordre Z : un bandeau ou une zone superposée au-dessus d'une zone `content.url` sera masqué sur Android.
- Transitions : la vue native ne suit pas les fondus CSS, donc coupe franche.
- Mémoire sur TV d'entrée de gamme : 2 à 3 WebViews actives.
- Renderer partagé : une page lourde qui plante emporte aussi le player. C'est déjà vrai avec l'iframe, et le watchdog s'en charge.
- Sites avec bannière cookies : affichée telle quelle.

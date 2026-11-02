# content.url

Type de contenu (kind `content-type`, vds-core 1.1).

Page web https dans `<iframe sandbox="allow-scripts allow-same-origin">` (jamais `allow-top-navigation`). `requires_network: true` : le player saute l'élément hors ligne ; `vds-error` `offline` si le réseau tombe pendant l'affichage. `allowed_hosts` (liste blanche, relue au redémarrage du module) restreint les domaines. Limite : les sites qui interdisent l'affichage en cadre (X-Frame-Options, CSP) ne s'affichent pas.


## Vue native (0.2.0)

Si le shell expose `window.VdsNative.web` (Android, voir `docs/architecture-content-url-natif.md`), la page est chargée dans une WebView native superposée au rectangle de l'élément (`prepare` / `show` / `rect` / `hide`). Le rectangle est suivi par un `ResizeObserver` et renouvelé toutes les 10 s (bail natif de 30 s). Un échec (`url_native_error`, `url_timeout`, `url_refused`) fait sauter l'élément et rien n'est affiché au public. Sans `web` (PWA, Pi, Tizen, ancien APK) : iframe, comportement inchangé. Limite : coupe franche à la place des fondus, et la vue masque tout ce qui est dessiné au-dessus.

# content.url

Type de contenu (kind `content-type`, vds-core 1.1).

Page web https dans `<iframe sandbox="allow-scripts allow-same-origin">` (jamais `allow-top-navigation`). `requires_network: true` : le player saute l'élément hors ligne ; `vds-error` `offline` si le réseau tombe pendant l'affichage. `allowed_hosts` (liste blanche, relue au redémarrage du module) restreint les domaines. Limite : les sites qui interdisent l'affichage en cadre (X-Frame-Options, CSP) ne s'affichent pas.

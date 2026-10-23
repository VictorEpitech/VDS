---
name: vds-content-type
description: Créer ou modifier un type de contenu VDS (module kind content-type - image, vidéo, PDF, URL, météo, RSS, et futurs widgets) - rendu côté player en web component, config, préchargement, comportement hors ligne.
---

# Type de contenu VDS

Suis d'abord le skill `vds-module`. Ce skill ajoute les règles du rendu dans le player.

## Interface côté player (packages/sdk-ts)

```ts
export interface VdsContentElement extends HTMLElement {
  configure(config: unknown, ctx: ContentContext): void; // ctx : zone (w,h,orientation), langue, horloge, accès cache
  preload(): Promise<void>;     // doit être appelé avant play ; récupère et met en cache
  play(): Promise<void>;        // démarre le rendu ; résout quand prêt à l'affichage
  stop(): void;                 // libère TOUT (timers, vidéo, mémoire)
  readonly durationMs: number | null; // null = durée fixée par la playlist
}
```

Enregistrement : `customElements.define("vds-<id>", Classe)`.

## Règles

- **Hors ligne d'abord** : `preload` met en cache tout ce qui est nécessaire ; `play` ne doit dépendre d'aucun réseau. Les widgets de données (météo, RSS) gardent la dernière valeur connue et l'affichent si le réseau tombe.
- **Ressources limitées** (TV 2023, WebView) : une vidéo décodée à la fois, images redimensionnées à la taille de la zone, `stop()` libère vraiment la mémoire.
- **S'adapte à la zone** : paysage, portrait, petite zone de bandeau ; tailles de texte lisibles à distance.
- **Aucun message d'erreur technique au public** : en cas d'échec, rendu neutre + événement `content.failed` remonté au serveur.
- **Modules tiers** : rendus dans une iframe sandbox, communication par `postMessage` avec le même contrat.
- Les données externes (météo, RSS) sont récupérées par le serveur ou l'agent et poussées au player, pas appelées directement depuis la TV quand c'est évitable.

## Tests

Vitest pour la logique, Playwright pour : préchargement, lecture, coupure réseau pendant la lecture, `stop()` sans fuite (mesure mémoire sur 200 cycles).

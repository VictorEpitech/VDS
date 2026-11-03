// vds-content-weather : contrat packages/sdk-ts/src/content.ts (CONTENT_API_VERSION 1).
// Élément en attente d'implémentation (tâche W-player du chantier docs/architecture-widgets.md) :
// il échoue proprement (`vds-error`), l'élément est donc sauté et rien n'est montré au public.
export class VdsContentWeather extends HTMLElement {
  configure() {}

  async prepare() {
    throw new Error("player_pending");
  }

  start() {}

  stop() {}
}

if (!customElements.get("vds-content-weather")) {
  customElements.define("vds-content-weather", VdsContentWeather);
}

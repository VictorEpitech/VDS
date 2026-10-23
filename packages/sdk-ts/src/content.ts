export const CONTENT_API_VERSION = 1;

import type { Locale } from "./runtime-config";

export interface ContentContext {
  /** Locale courante. */
  locale: Locale;
  /** Orientation du conteneur. */
  orientation: "landscape" | "portrait";
  /** Dimensions de la zone d'affichage. */
  zone: { width: number; height: number };
  /** Transforme une URL relative à l'URL serveur en URL servie par le Service Worker. */
  resolveUrl(relative: string): string;
}

export interface VdsErrorDetail {
  /** Code d'erreur. */
  code: string;
}

export interface VdsContentElement extends HTMLElement {
  /** Configure l'élément avec les propriétés fournies et le contexte. */
  configure(props: unknown, ctx: ContentContext): void;
  /** Charge et décode le contenu, en cas d'échec l'élément est ignoré. */
  prepare(): Promise<void>;
  /** Rend l'élément visible et actif. */
  start(): void;
  /** Libère les ressources utilisées par l'élément. */
  stop(): void;
  /** Optionnel : true si l'élément monopolise une ressource matérielle (décodeur vidéo) ; le player ne précharge pas un élément du même type pendant qu'il joue. */
  readonly exclusive?: boolean;
}

export const CONTENT_EVENTS = {
  /** Événement déclenché à la fin de la lecture. */
  ended: "vds-ended",
  /** Événement déclenché en cas d'erreur. */
  error: "vds-error"
} as const;

export type VdsContentEventMap = {
  "vds-ended": Event;
  "vds-error": CustomEvent<VdsErrorDetail>;
};

/** Tag d'élément de contenu, composé de `vds-content-` suivi d'une chaîne. */
export type ContentTypeTag = `vds-content-${string}`;

/** Config d'exécution servie par `<base>/config.js` (voir docs/architecture.md > Chemin de base). */

export type Locale = "fr" | "en";

export interface RuntimeConfig {
  /** "" pour la racine, sinon "/espace/affichage" (sans "/" final). */
  basePath: string;
  apiBase: string;
  wsBase: string;
  defaultLocale: Locale;
  version: string;
  theme: {
    id: string;
    cssUrl: string;
    icons: string | null;
    logo: string | null;
  };
  auth: {
    provider: string;
    loginUrl: string | null;
    logoutUrl: string | null;
    /** Liens fournis par le module d'auth ; libellé = clé i18n `nav.links.<id>`. */
    links: { id: string; href: string }[];
  };
}

declare global {
  interface Window {
    __VDS__?: RuntimeConfig;
  }
}

export class RuntimeConfigError extends Error {
  constructor() {
    super("errors.runtime_config_missing");
    this.name = "RuntimeConfigError";
  }
}

export function readRuntimeConfig(win: Pick<Window, "__VDS__"> = window): RuntimeConfig {
  const cfg = win.__VDS__;
  if (!cfg || typeof cfg.basePath !== "string" || !cfg.theme?.cssUrl) {
    throw new RuntimeConfigError();
  }
  return cfg;
}

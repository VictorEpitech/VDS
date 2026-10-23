import { createContext, useContext, type ReactNode } from "react";
import type { RuntimeConfig } from "@vds/sdk-ts";
import { joinBase } from "./url";

const Ctx = createContext<RuntimeConfig | null>(null);

export function RuntimeConfigProvider({ config, children }: { config: RuntimeConfig; children: ReactNode }) {
  return <Ctx.Provider value={config}>{children}</Ctx.Provider>;
}

export function useRuntimeConfig(): RuntimeConfig {
  const cfg = useContext(Ctx);
  if (!cfg) throw new Error("RuntimeConfigProvider manquant");
  return cfg;
}

/** URL absolue-chemin préfixée par la base (pour fetch, ressources). */
export function useAppUrl(): (path: string) => string {
  const { basePath } = useRuntimeConfig();
  return (path) => joinBase(basePath, path);
}

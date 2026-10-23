import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { readRuntimeConfig, type RuntimeConfig } from "@vds/sdk-ts";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { I18nextProvider } from "react-i18next";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { initI18n } from "../lib/i18n";
import { RuntimeConfigProvider } from "../lib/runtime-config";
import { loadStylesheet } from "../lib/theme";
import { routes } from "./routes";

export async function bootstrap(container: HTMLElement): Promise<void> {
  let config: RuntimeConfig;
  try {
    config = readRuntimeConfig();
  } catch {
    const i18n = await initI18n("fr");
    const p = document.createElement("p");
    p.setAttribute("role", "alert");
    p.textContent = i18n.t("errors.runtime_config_missing");
    container.appendChild(p);
    return;
  }
  const [i18n] = await Promise.all([initI18n(config.defaultLocale), loadStylesheet(config.theme.cssUrl)]);
  const queryClient = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } } });
  // basename = VDS_BASE_PATH lu dans la config d'exécution ("" pour la racine).
  const router = createBrowserRouter(routes, { basename: config.basePath || "/" });
  createRoot(container).render(
    <StrictMode>
      <RuntimeConfigProvider config={config}>
        <I18nextProvider i18n={i18n}>
          <QueryClientProvider client={queryClient}>
            <RouterProvider router={router} />
          </QueryClientProvider>
        </I18nextProvider>
      </RuntimeConfigProvider>
    </StrictMode>,
  );
}

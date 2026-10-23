import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readRuntimeConfig, RuntimeConfigError, type RuntimeConfig } from "@vds/sdk-ts";
import { I18nextProvider } from "react-i18next";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { initI18n } from "../lib/i18n";
import { RuntimeConfigProvider } from "../lib/runtime-config";
import { routes } from "./routes";

function config(over: Partial<RuntimeConfig> = {}): RuntimeConfig {
  return {
    basePath: "/espace/affichage",
    apiBase: "/espace/affichage/api/v1",
    wsBase: "/espace/affichage/ws",
    defaultLocale: "fr",
    version: "1.2.3",
    theme: { id: "theme.allia", cssUrl: "/x.css", icons: null, logo: null },
    auth: {
      provider: "auth.studio-allia",
      loginUrl: null,
      logoutUrl: "/espace/deconnexion",
      links: [{ id: "back_to_studio", href: "/espace" }],
    },
    ...over,
  };
}

async function setup(cfg: RuntimeConfig, path = "/") {
  const i18n = await initI18n(cfg.defaultLocale, null);
  const router = createMemoryRouter(routes, { initialEntries: [path], basename: cfg.basePath || "/" });
  render(
    <RuntimeConfigProvider config={cfg}>
      <I18nextProvider i18n={i18n}>
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </I18nextProvider>
    </RuntimeConfigProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response('{"status":"ok"}', { status: 200 }))),
  );
});

describe("admin", () => {
  it("préfixe les liens avec la base et appelle /health sous la base", async () => {
    await setup(config(), "/espace/affichage/");
    expect(await screen.findByRole("heading", { level: 1, name: "Tableau de bord" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Écrans" })).toHaveAttribute("href", "/espace/affichage/screens");
    expect(vi.mocked(fetch).mock.calls[0]?.[0]).toBe("/espace/affichage/health");
    expect(await screen.findByText("Serveur joignable")).toBeInTheDocument();
  });

  it("liens de la config (aucune branche par mode) et déconnexion", async () => {
    await setup(config(), "/espace/affichage/");
    expect(screen.getByRole("link", { name: "Retour au Studio" })).toHaveAttribute("href", "/espace");
    expect(screen.getByRole("link", { name: "Se déconnecter" })).toBeInTheDocument();
  });

  it("sans lien ni déconnexion en mode autonome", async () => {
    await setup(config({ basePath: "", auth: { provider: "auth.local", loginUrl: null, logoutUrl: null, links: [] } }));
    expect(screen.queryByRole("link", { name: "Retour au Studio" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Se déconnecter" })).toBeNull();
  });

  it("serveur injoignable : état hors ligne avec libellé", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("", { status: 503 }));
    await setup(config({ basePath: "" }));
    expect(await screen.findByText("Serveur injoignable")).toBeInTheDocument();
  });

  it("bascule FR/EN", async () => {
    await setup(config(), "/espace/affichage/");
    await userEvent.click(screen.getByRole("button", { name: "English" }));
    await waitFor(() => expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Dashboard"));
    expect(document.documentElement.lang).toBe("en");
  });

  it("route inconnue : page introuvable", async () => {
    await setup(config(), "/espace/affichage/nope");
    expect(await screen.findByRole("heading", { level: 1, name: "Page introuvable" })).toBeInTheDocument();
  });
});

describe("readRuntimeConfig", () => {
  it("échoue sans config", () => {
    expect(() => readRuntimeConfig({})).toThrow(RuntimeConfigError);
  });
  it("lit window.__VDS__", () => {
    expect(readRuntimeConfig({ __VDS__: config() }).basePath).toBe("/espace/affichage");
  });
});

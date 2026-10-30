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

const ME = { id: "u1", display_name: "Ada", email: "ada@vds.test", role: "admin", provider: "auth.local", locale: null };

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        new Response(JSON.stringify(
            url.endsWith("/me")
              ? ME
              : url.endsWith("/onboarding")
                ? { steps: [], progress: { completed: 0, total: 6 }, next_step: null, complete: false, card: { hidden: true, hidden_at: "x" } }
                : url.endsWith("/fleet/summary")
                  ? { screens: { total: 0, by_status: {} }, to_handle: { screens: 0, by_reason: {} } }
                  : url.includes("/api/v1/")
                    ? []
                    : { status: "ok" },
          ), { status: 200 }),
      ),
    ),
  );
});

describe("admin", () => {
  it("préfixe les liens avec la base et appelle /health sous la base", async () => {
    await setup(config(), "/espace/affichage/");
    expect(await screen.findByRole("heading", { level: 1, name: "Tableau de bord" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Écrans" })).toHaveAttribute("href", "/espace/affichage/screens");
    await screen.findByText("Serveur joignable");
    const urls = vi.mocked(fetch).mock.calls.map((c) => c[0]);
    expect(urls).toContain("/espace/affichage/health");
    expect(urls).toContain("/espace/affichage/api/v1/me");
    expect(await screen.findByText("Serveur joignable")).toBeInTheDocument();
  });

  it("liens de la config (aucune branche par mode) et déconnexion", async () => {
    await setup(config(), "/espace/affichage/");
    expect(await screen.findByRole("link", { name: "Retour au Studio" })).toHaveAttribute("href", "/espace");
    expect(screen.getByRole("link", { name: "Se déconnecter" })).toBeInTheDocument();
  });

  it("mode autonome : pas de lien d'enveloppe, déconnexion par le module (bouton)", async () => {
    await setup(
      config({ basePath: "", apiBase: "/api/v1", auth: { provider: "auth.local", loginUrl: null, logoutUrl: null, links: [] } }),
    );
    expect(await screen.findByRole("button", { name: "Se déconnecter" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Retour au Studio" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Se déconnecter" })).toBeNull();
  });

  it("serveur injoignable : état hors ligne avec libellé", async () => {
    vi.mocked(fetch).mockImplementation((url) =>
      Promise.resolve(
        String(url).endsWith("/me") ? new Response(JSON.stringify(ME)) : new Response("", { status: 503 }),
      ),
    );
    await setup(config({ basePath: "" }));
    expect(await screen.findByText("Serveur injoignable")).toBeInTheDocument();
  });

  it("bascule FR/EN", async () => {
    await setup(config(), "/espace/affichage/");
    await screen.findByRole("heading", { level: 1, name: "Tableau de bord" });
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

describe.each(["", "/espace/affichage"])("redirections des anciennes URL, base « %s »", (b) => {
  const cases: [string, string, string][] = [
    ["/supervision", "/screens", "?filter=issues"],
    ["/pairing?screen=abc", "/screens/add", "?screen=abc"],
    ["/media", "/content/media", ""],
    ["/playlists", "/content/playlists", ""],
    ["/playlists/p1", "/content/playlists/p1", ""],
    ["/schedules", "/playback", ""],
    ["/content", "/content/media", ""],
  ];
  it.each(cases)("%s -> %s", async (from, path, search) => {
    const cfg = config({ basePath: b, apiBase: `${b}/api/v1` });
    const i18n = await initI18n("fr", null);
    const router = createMemoryRouter(routes, { initialEntries: [`${b}${from}`], basename: b || "/" });
    render(
      <RuntimeConfigProvider config={cfg}>
        <I18nextProvider i18n={i18n}>
          <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
            <RouterProvider router={router} />
          </QueryClientProvider>
        </I18nextProvider>
      </RuntimeConfigProvider>,
    );
    await waitFor(() => expect(router.state.location.pathname).toBe(`${b}${path}`));
    expect(router.state.location.search).toBe(search);
  });

  it("navigation à 5 entrées, Supervision absente", async () => {
    const cfg = config({ basePath: b, apiBase: `${b}/api/v1` });
    const i18n = await initI18n("fr", null);
    const router = createMemoryRouter(routes, { initialEntries: [`${b}/`], basename: b || "/" });
    render(
      <RuntimeConfigProvider config={cfg}>
        <I18nextProvider i18n={i18n}>
          <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
            <RouterProvider router={router} />
          </QueryClientProvider>
        </I18nextProvider>
      </RuntimeConfigProvider>,
    );
    const nav = await screen.findByRole("navigation", { name: "Navigation principale" });
    const links = Array.from(nav.querySelectorAll("a")).map((a) => a.textContent);
    expect(links).toEqual(["Accueil", "Écrans", "Contenus", "Diffusion", "Plus"]);
  });
});

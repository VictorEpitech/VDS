import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { RuntimeConfig } from "@vds/sdk-ts";
import { I18nextProvider } from "react-i18next";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { routes } from "../app/routes";
import { initI18n } from "../lib/i18n";
import { RuntimeConfigProvider } from "../lib/runtime-config";

const ME = { id: "u1", display_name: "Ada", email: "ada@vds.test", role: "admin", provider: "auth.local", locale: null };

function config(base: string, over: Partial<RuntimeConfig["auth"]> = {}): RuntimeConfig {
  return {
    basePath: base,
    apiBase: `${base}/api/v1`,
    wsBase: `${base}/ws`,
    defaultLocale: "fr",
    version: "1.0.0",
    theme: { id: "theme.vtech", cssUrl: "/x.css", icons: null, logo: null },
    auth: { provider: "auth.local", loginUrl: null, logoutUrl: null, links: [], ...over },
  };
}

type Handler = (body: unknown, url: string) => Response | Promise<Response>;
let handlers: Record<string, Handler> = {};
let calls: { key: string; body: unknown }[] = [];

const json = (status: number, body?: unknown, headers?: Record<string, string>) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers });
const err = (status: number, code: string, headers?: Record<string, string>) => json(status, { code, params: {} }, headers);

async function setup(cfg: RuntimeConfig, path: string, lang: "fr" | "en" = "fr") {
  const i18n = await initI18n(lang, null);
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
  return router;
}

beforeEach(() => {
  handlers = {};
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = new URL(url, "http://x").pathname;
      const key = `${init?.method ?? "GET"} ${path}`;
      const body = init?.body ? (JSON.parse(String(init.body)) as unknown) : undefined;
      calls.push({ key, body });
      const h = handlers[key];
      return h ? h(body, url) : err(404, "errors.not_found");
    }),
  );
});

afterEach(() => vi.unstubAllGlobals());

const fill = async (label: string, value: string) => userEvent.type(await screen.findByLabelText(label), value);

for (const base of ["", "/espace/affichage"]) {
  describe(`auth.local, VDS_BASE_PATH=${base || "/"}`, () => {
    const cfg = config(base);
    const api = (p: string) => `${base}/api/v1${p}`;
    const root = `${base}/`;
    const here = (r: { state: { location: { pathname: string } } }) => r.state.location.pathname.slice(base.length) || "/";

    it("non connecté : redirige vers la connexion avec return_to sous le préfixe", async () => {
      handlers[`GET ${api("/me")}`] = () => err(401, "auth.unauthenticated");
      handlers[`GET ${api("/m/auth.local/status")}`] = () => json(200, { initialized: true });
      const router = await setup(cfg, `${root}screens`);
      expect(await screen.findByRole("heading", { level: 1, name: "Connexion" })).toBeInTheDocument();
      expect(here(router)).toBe("/login");
      expect(new URLSearchParams(router.state.location.search).get("return_to")).toBe(`${base}/screens`);
    });

    it("connexion : envoie return_to valide, navigue vers redirect_to et affiche l'utilisateur", async () => {
      let logged = false;
      handlers[`GET ${api("/me")}`] = () => (logged ? json(200, ME) : err(401, "auth.unauthenticated"));
      handlers[`GET ${api("/m/auth.local/status")}`] = () => json(200, { initialized: true });
      handlers[`POST ${api("/m/auth.local/login")}`] = () => {
        logged = true;
        return json(200, { redirect_to: `${base}/screens` });
      };
      const router = await setup(cfg, `${root}screens`);
      await fill("Adresse e-mail", " ada@vds.test ");
      await fill("Mot de passe", "secret-secret");
      await userEvent.click(screen.getByRole("button", { name: "Se connecter" }));
      expect(await screen.findByRole("heading", { level: 1, name: "Écrans" })).toBeInTheDocument();
      expect(screen.getByText("Ada")).toBeInTheDocument();
      expect(here(router)).toBe("/screens");
      expect(calls.find((c) => c.key.startsWith("POST"))?.body).toEqual({
        email: "ada@vds.test",
        password: "secret-secret",
        return_to: `${base}/screens`,
      });
    });

    it("return_to hors préfixe ou externe : ignoré, redirect_to externe refusé", async () => {
      let logged = false;
      handlers[`GET ${api("/me")}`] = () => (logged ? json(200, ME) : err(401, "auth.unauthenticated"));
      handlers[`GET ${api("/m/auth.local/status")}`] = () => json(200, { initialized: true });
      handlers[`POST ${api("/m/auth.local/login")}`] = () => {
        logged = true;
        return json(200, { redirect_to: "https://evil.test/" });
      };
      const router = await setup(cfg, `${root}login?return_to=${encodeURIComponent("https://evil.test/")}`);
      await fill("Adresse e-mail", "ada@vds.test");
      await fill("Mot de passe", "x");
      await userEvent.click(screen.getByRole("button", { name: "Se connecter" }));
      await waitFor(() => expect(calls.some((c) => c.key.startsWith("POST"))).toBe(true));
      expect(calls.find((c) => c.key.startsWith("POST"))?.body).toMatchObject({ return_to: null });
      await waitFor(() => expect(here(router)).toBe("/"));
    });

    it("erreurs traduites : identifiants, trop de tentatives, champs vides", async () => {
      handlers[`GET ${api("/me")}`] = () => err(401, "auth.unauthenticated");
      handlers[`GET ${api("/m/auth.local/status")}`] = () => json(200, { initialized: true });
      let n = 0;
      handlers[`POST ${api("/m/auth.local/login")}`] = () =>
        ++n === 1 ? err(401, "auth_local.invalid_credentials") : err(429, "errors.auth.too_many_attempts", { "Retry-After": "60" });
      await setup(cfg, `${root}login`);
      await userEvent.click(await screen.findByRole("button", { name: "Se connecter" }));
      expect((await screen.findAllByText("Ce champ est obligatoire.")).length).toBe(2);
      expect(calls.some((c) => c.key.startsWith("POST"))).toBe(false);
      await fill("Adresse e-mail", "ada@vds.test");
      await fill("Mot de passe", "bad");
      await userEvent.click(screen.getByRole("button", { name: "Se connecter" }));
      expect(await screen.findByText("Identifiants incorrects.")).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: "Se connecter" }));
      expect(await screen.findByText(/Trop de tentatives/)).toBeInTheDocument();
    });

    it("403 changement de mot de passe exigé : écran dédié puis retour à l'accueil", async () => {
      let changed = false;
      handlers[`GET ${api("/me")}`] = () =>
        changed ? json(200, ME) : err(403, "auth_local.password_change_required");
      handlers[`POST ${api("/m/auth.local/password")}`] = () => {
        changed = true;
        return json(204);
      };
      const router = await setup(cfg, root);
      expect(await screen.findByRole("heading", { level: 1, name: "Changer le mot de passe" })).toBeInTheDocument();
      await fill("Mot de passe actuel", "old-old");
      await fill("Nouveau mot de passe", "new-new-new-new");
      await userEvent.click(screen.getByRole("button", { name: "Changer le mot de passe" }));
      expect(await screen.findByRole("heading", { level: 1, name: "Tableau de bord" })).toBeInTheDocument();
      expect(here(router)).toBe("/");
      expect(calls.find((c) => c.key.startsWith("POST"))?.body).toEqual({ current: "old-old", new: "new-new-new-new" });
    });

    it("amorçage : compte déjà créé => redirection vers la connexion", async () => {
      handlers[`GET ${api("/m/auth.local/status")}`] = () => json(200, { initialized: true });
      handlers[`GET ${api("/me")}`] = () => err(401, "auth.unauthenticated");
      const router = await setup(cfg, `${root}bootstrap`);
      expect(await screen.findByRole("heading", { level: 1, name: "Connexion" })).toBeInTheDocument();
      expect(here(router)).toBe("/login");
      expect(screen.getByText(/premier administrateur existe déjà/)).toBeInTheDocument();
    });

    it("amorçage : 409 à l'envoi => connexion ; succès => tableau de bord", async () => {
      handlers[`GET ${api("/m/auth.local/status")}`] = () => json(200, { initialized: false });
      handlers[`GET ${api("/me")}`] = () => err(401, "auth.unauthenticated");
      handlers[`POST ${api("/m/auth.local/bootstrap")}`] = () => err(409, "auth_local.already_initialized");
      const router = await setup(cfg, `${root}bootstrap`);
      await fill("Adresse e-mail", "root@vds.test");
      await fill("Nom affiché", "Root");
      await fill("Mot de passe", "long-enough-secret");
      await fill("Jeton d'amorçage", "tok");
      expect(screen.getByLabelText("Jeton d'amorçage")).toHaveClass("mono");
      await userEvent.click(screen.getByRole("button", { name: "Créer le compte" }));
      expect(await screen.findByRole("heading", { level: 1, name: "Connexion" })).toBeInTheDocument();
      expect(here(router)).toBe("/login");
      expect(calls.find((c) => c.key.startsWith("POST"))?.body).toEqual({
        email: "root@vds.test",
        display_name: "Root",
        password: "long-enough-secret",
        bootstrap_token: "tok",
      });
    });

    it("amorçage : succès", async () => {
      let created = false;
      handlers[`GET ${api("/m/auth.local/status")}`] = () => json(200, { initialized: created });
      handlers[`GET ${api("/me")}`] = () => (created ? json(200, ME) : err(401, "auth.unauthenticated"));
      handlers[`POST ${api("/m/auth.local/bootstrap")}`] = () => {
        created = true;
        return json(201, { redirect_to: root });
      };
      await setup(cfg, `${root}bootstrap`);
      await fill("Adresse e-mail", "root@vds.test");
      await fill("Nom affiché", "Root");
      await fill("Mot de passe", "long-enough-secret");
      await fill("Jeton d'amorçage", "tok");
      await userEvent.click(screen.getByRole("button", { name: "Créer le compte" }));
      expect(await screen.findByRole("heading", { level: 1, name: "Tableau de bord" })).toBeInTheDocument();
    });

    it("jeton invalide : message traduit, reste sur l'amorçage", async () => {
      handlers[`GET ${api("/m/auth.local/status")}`] = () => json(200, { initialized: false });
      handlers[`POST ${api("/m/auth.local/bootstrap")}`] = () => err(403, "auth_local.bootstrap_token_invalid");
      await setup(cfg, `${root}bootstrap`);
      await fill("Adresse e-mail", "root@vds.test");
      await fill("Nom affiché", "Root");
      await fill("Mot de passe", "long-enough-secret");
      await fill("Jeton d'amorçage", "bad");
      await userEvent.click(screen.getByRole("button", { name: "Créer le compte" }));
      expect(await screen.findByText("Jeton d'amorçage invalide.")).toBeInTheDocument();
    });

    it("déconnexion : POST puis retour à la connexion", async () => {
      handlers[`GET ${api("/me")}`] = () => json(200, ME);
      handlers[`POST ${api("/m/auth.local/logout")}`] = () => json(204);
      handlers[`GET ${api("/m/auth.local/status")}`] = () => json(200, { initialized: true });
      const router = await setup(cfg, root);
      await userEvent.click(await screen.findByRole("button", { name: "Se déconnecter" }));
      await waitFor(() => expect(here(router)).toBe("/login"));
      expect(calls.some((c) => c.key === `POST ${api("/m/auth.local/logout")}`)).toBe(true);
    });

    it("EN : libellés et erreurs traduits", async () => {
      handlers[`GET ${api("/me")}`] = () => err(401, "auth.unauthenticated");
      handlers[`GET ${api("/m/auth.local/status")}`] = () => json(200, { initialized: true });
      handlers[`POST ${api("/m/auth.local/login")}`] = () => err(401, "auth_local.invalid_credentials");
      await setup(cfg, `${root}login`, "en");
      await fill("Email address", "ada@vds.test");
      await fill("Password", "bad");
      await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
      expect(await screen.findByText("Incorrect credentials.")).toBeInTheDocument();
    });

    it("serveur injoignable : état d'échec avec reprise", async () => {
      handlers[`GET ${api("/me")}`] = () => json(503, { code: "errors.internal", params: {} });
      await setup(cfg, root);
      expect(await screen.findByText("Erreur interne du serveur.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Réessayer" })).toBeInTheDocument();
    });
  });
}

describe("provider hébergé (aucun écran fourni par un module)", () => {
  it("non connecté : redirige vers la loginUrl de la config, sans écran de connexion local", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const cfg = config("/espace/affichage", { provider: "auth.studio-allia", loginUrl: "/espace/connexion", logoutUrl: "/espace/deconnexion" });
    handlers["GET /espace/affichage/api/v1/me"] = () => err(401, "auth.unauthenticated");
    await setup(cfg, "/espace/affichage/");
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/espace/connexion"));
  });

  it("/login retombe sur l'accueil", async () => {
    const cfg = config("", { provider: "auth.studio-allia", loginUrl: null, logoutUrl: "/espace/deconnexion" });
    handlers["GET /api/v1/me"] = () => json(200, ME);
    const router = await setup(cfg, "/login");
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    expect(await screen.findByRole("link", { name: "Se déconnecter" })).toHaveAttribute("href", "/espace/deconnexion");
  });
});

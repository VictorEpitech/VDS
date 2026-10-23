import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { RuntimeConfig } from "@vds/sdk-ts";
import type { ReactNode } from "react";
import { I18nextProvider } from "react-i18next";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router-dom";
import { vi } from "vitest";
import { initI18n } from "../src/lib/i18n";
import { RuntimeConfigProvider } from "../src/lib/runtime-config";
import { contentTypesFixture } from "./content-types";

export function testConfig(basePath: string): RuntimeConfig {
  return {
    basePath,
    apiBase: `${basePath}/api/v1`,
    wsBase: `${basePath}/ws`,
    defaultLocale: "fr",
    version: "1.0.0",
    theme: { id: "theme.vtech", cssUrl: "/x.css", icons: null, logo: null },
    auth: { provider: "auth.local", loginUrl: null, logoutUrl: null, links: [] },
  };
}

export async function renderRoutes(routes: RouteObject[], path: string, basePath = "", locale = "fr") {
  const cfg = testConfig(basePath);
  const i18n = await initI18n("fr", locale);
  const router = createMemoryRouter(routes, { initialEntries: [`${basePath}${path}`], basename: basePath || "/" });
  return render(
    <RuntimeConfigProvider config={cfg}>
      <I18nextProvider i18n={i18n}>
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </I18nextProvider>
    </RuntimeConfigProvider>,
  );
}

export function renderElement(element: ReactNode, locale = "fr") {
  return renderRoutes([{ path: "*", element }], "/", "", locale);
}

type Handler = (url: string, init: RequestInit | undefined) => unknown;

/** fetch simulé : `routes` associe "METHOD /chemin" (sans base) à une réponse JSON, `null` (204) ou une Response. */
export function mockApi(routes: Record<string, Handler | unknown>, basePath = "") {
  const calls: { method: string; path: string; body: unknown }[] = [];
  const fn = vi.fn((input: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const path = String(input).slice(`${basePath}/api/v1`.length);
    calls.push({ method, path, body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined });
    if (method === "GET" && path === "/me" && !routes["GET /me"]) {
      return Promise.resolve(new Response(JSON.stringify({ id: "u1", display_name: "Ada", email: null, role: "admin", provider: "auth.local", locale: null })));
    }
    if (method === "GET" && path === "/registry/content-types" && !routes["GET /registry/content-types"]) {
      return Promise.resolve(new Response(JSON.stringify(contentTypesFixture())));
    }
    const entry = routes[`${method} ${path}`] ?? routes[`${method} ${path.split("?")[0]}`];
    if (entry === undefined) return Promise.resolve(new Response(JSON.stringify({ code: "errors.not_found", params: {} }), { status: 404 }));
    const out = typeof entry === "function" ? (entry as Handler)(path, init) : entry;
    if (out instanceof Response) return Promise.resolve(out);
    return Promise.resolve(out === null ? new Response(null, { status: 204 }) : new Response(JSON.stringify(out), { status: 200 }));
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

export const errorResponse = (status: number, code: string, params: Record<string, unknown> = {}) =>
  new Response(JSON.stringify({ code, params }), { status });

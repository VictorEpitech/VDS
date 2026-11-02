import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { errorResponse, mockApi, renderRoutes } from "../../../../test/render";
import { routes } from "../../../app/routes";
import { ApiError } from "../../../lib/api";
import { codeState, normalizeCode, pairMotive } from "./pairErrors";
import { verifyPhase } from "./VerifyStep";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };
const NEW = { id: "30000000-0000-4000-8000-000000000009", site_id: SITE.id, group_id: null, name: "Entrée", orientation: "landscape", archived_at: null, kiosk: null };
const HB = { revision: "r1", stale: false, missing_assets: 0, uptime_s: 12 };
const device = (presence: string, heartbeat: unknown) => ({ id: "d1", platform: "web", model: null, app_version: null, presence, paired_at: "2026-11-02T08:00:00Z", last_seen_at: null, revoked_at: null, heartbeat });
const PAIRED = { screen: { ...NEW, device: null }, pairing: { status: "claimed", expires_at: "2026-11-02T08:15:00Z" }, replayed: false };
const origin = (level: string, name: string) => ({ level, scope_id: null, name });
const cfg = (kiosk = "locked") => ({
  values: {
    "player.kiosk": { value: kiosk, inherited: kiosk, origin: origin("organization", "Org"), inherited_origin: origin("default", ""), overridable: true, secret: false },
    "power.cascade": { value: ["power.adb", "power.blank"], inherited: [], origin: origin("default", ""), inherited_origin: origin("default", ""), overridable: true, secret: false },
  },
  warnings: [],
});
const base = {
  "GET /sites": [SITE],
  [`GET /sites/${SITE.id}/groups`]: [],
  "GET /screens": [],
  "GET /provisioning/capabilities": { available: false, reason: "agent_missing", platforms: [], apk: null },
  [`GET /config/site/${SITE.id}/core`]: cfg(),
};

async function toVerify(user: ReturnType<typeof userEvent.setup>, next = "Continuer", submit = "Ajouter l'écran") {
  await user.click(await screen.findByRole("button", { name: next }));
  await user.type(await screen.findByRole("textbox"), "k7q-4mz");
  await user.click(screen.getByRole("button", { name: next }));
  await user.type(await screen.findByRole("textbox", { name: /Nom de|Screen name/ }), "Entrée");
  await screen.findByRole("option", { name: "Moulins" });
  await user.selectOptions(screen.getByLabelText(/^Site$/), SITE.id);
  await user.click(screen.getByRole("button", { name: submit }));
}

describe("motifs d'erreur", () => {
  it("dérive le motif UX du code serveur", () => {
    expect(pairMotive(new ApiError(404, "errors.pairing_code_invalid"))).toBe("code_unknown");
    expect(pairMotive(new ApiError(410, "errors.pairing_code_expired"))).toBe("code_expired");
    expect(pairMotive(new ApiError(409, "errors.pairing_code_claimed"))).toBe("already_paired");
    expect(pairMotive(new ApiError(429, "errors.too_many_requests", {}, 12))).toBe("too_many_attempts");
    expect(pairMotive(new ApiError(422, "screen.group_other_site"))).toBe("other");
    expect(pairMotive(new Error("x"))).toBe("other");
  });
  it("normalise et qualifie le code", () => {
    expect(normalizeCode(" k7q-4mz ")).toBe("K7Q4MZ");
    expect([codeState(""), codeState("K7Q"), codeState("K7Q-4MZ"), codeState("K7Q4MZK7Q4MZK")]).toEqual(["empty", "short", "ok", "long"]);
  });
  it("lit la vérification : en ligne, battement frais, aucun média manquant", () => {
    const s = (d: unknown) => ({ ...NEW, device: d }) as never;
    expect(verifyPhase(s(null))).toBe("waiting");
    expect(verifyPhase(s(device("online", null)))).toBe("waiting");
    expect(verifyPhase(s(device("offline", HB)))).toBe("waiting");
    expect(verifyPhase(s(device("online", { ...HB, stale: true })))).toBe("stale");
    expect(verifyPhase(s(device("online", { ...HB, missing_assets: 2 })))).toBe("assets");
    expect(verifyPhase(s(device("online", HB)))).toBe("ready");
  });
});

describe.each(["", "/espace/affichage"])("assistant Ajouter un écran, base « %s »", (b) => {
  it("redirige les anciennes URL en gardant la base et la requête", async () => {
    mockApi({ ...base, "GET /screens": [{ ...NEW, device: null }] }, b);
    await renderRoutes(routes, "/pairing", b);
    expect(await screen.findByRole("heading", { name: "Ajouter un écran" })).toBeInTheDocument();
  });

  it("parcours complet : liste des étapes, héritage en badge, vérification qui sonde l'écran", async () => {
    const calls = mockApi(
      {
        ...base,
        "POST /screens/pair": () => new Response(JSON.stringify(PAIRED), { status: 201 }),
        [`GET /screens/${NEW.id}`]: { ...NEW, device: device("online", HB) },
        [`GET /sites/${NEW.site_id}/agents`]: [],
        [`GET /config/screen/${NEW.id}/core`]: cfg(),
      },
      b,
    );
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/add", b);
    expect(await screen.findByRole("list", { name: "Étapes de l'assistant" })).toHaveTextContent("Appareil");
    await user.click(await screen.findByRole("button", { name: "Continuer" }));
    await user.type(await screen.findByRole("textbox"), "k7q-4mz");
    await user.click(screen.getByRole("button", { name: "Continuer" }));
    await user.type(await screen.findByRole("textbox", { name: "Nom de l'écran" }), "Entrée");
    await screen.findByRole("option", { name: "Moulins" });
    await user.selectOptions(screen.getByLabelText("Site"), SITE.id);
    expect(await screen.findByText("Hérité de l'organisation")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ajouter l'écran" }));
    expect(await screen.findByText("L'écran fonctionne")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/screens/pair")?.body).toEqual({ code: "K7Q4MZ", name: "Entrée", site_id: SITE.id, group_id: null });
    expect(screen.queryByRole("button", { name: "Tester l'allumage" })).toBeNull();
    expect(screen.getByRole("link", { name: "Programmer la diffusion" })).toHaveAttribute("href", `${b}/playback`);
  });

  it("propose le test d'allumage seulement avec un pilote réglé et un agent en ligne, sans power.set", async () => {
    const CMD = { id: "c1", name: "power.probe", status: "ok", code: null, result: {}, args: {}, screen_id: NEW.id, created_at: "", expires_at: "", done_at: null, sent_at: null, seq: 1 };
    const calls = mockApi(
      {
        ...base,
        "POST /screens/pair": () => new Response(JSON.stringify(PAIRED), { status: 201 }),
        [`GET /screens/${NEW.id}`]: { ...NEW, device: device("online", HB) },
        [`GET /sites/${SITE.id}/agents`]: [{ id: "a1", site_id: SITE.id, name: "Agent", status: "online", version: "1.5.0" }],
        [`GET /config/screen/${NEW.id}/core`]: cfg(),
        [`GET /config/screen/${NEW.id}/power.adb`]: { values: { host: { value: "192.168.1.50" } }, warnings: [] },
        [`POST /sites/${SITE.id}/agents/a1/commands`]: CMD,
      },
      b,
    );
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/add", b);
    await toVerify(user);
    await user.click(await screen.findByRole("button", { name: "Tester l'allumage" }));
    expect(await screen.findByText("L'appareil répond")).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("/commands"))?.body).toMatchObject({ name: "power.probe", screen_id: NEW.id });
    expect(calls.some((c) => JSON.stringify(c.body ?? "").includes("power.set"))).toBe(false);
  });

  it("attend la TV tant qu'elle n'est pas en ligne", async () => {
    mockApi({ ...base, "POST /screens/pair": () => new Response(JSON.stringify(PAIRED), { status: 201 }), [`GET /screens/${NEW.id}`]: { ...NEW, device: device("unseen", null) } }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/add", b);
    await toVerify(user);
    expect(await screen.findByText("En attente de la TV")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Programmer la diffusion" })).toBeNull();
  });

  it.each([
    ["errors.pairing_code_invalid", 404, /Code inconnu/, "Modifier le code"],
    ["errors.pairing_code_expired", 410, /Code expiré.*toutes les 15 minutes/, "Saisir le nouveau code"],
  ])("erreur %s : cause claire et retour à la saisie du code", async (code, status, text, action) => {
    mockApi({ ...base, "POST /screens/pair": () => errorResponse(status, code) }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/add", b);
    await toVerify(user);
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
    await user.click(screen.getByRole("button", { name: action }));
    expect(await screen.findByRole("textbox")).toHaveValue("K7Q-4MZ");
  });

  it("code déjà utilisé : renvoie vers les écrans, trop d'essais : délai annoncé", async () => {
    let n = 0;
    mockApi(
      { ...base, "POST /screens/pair": () => (n++ === 0 ? errorResponse(409, "errors.pairing_code_claimed") : new Response(JSON.stringify({ code: "errors.too_many_requests", params: {} }), { status: 429, headers: { "Retry-After": "30" } })) },
      b,
    );
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/add", b);
    await toVerify(user);
    expect(await screen.findByRole("link", { name: "Voir les écrans" })).toHaveAttribute("href", `${b}/screens`);
    await user.click(screen.getByRole("button", { name: "Ajouter l'écran" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/Trop d'essais.*30 secondes/));
  });

  it("en anglais", async () => {
    mockApi({ ...base, "POST /screens/pair": () => errorResponse(410, "errors.pairing_code_expired") }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/add", b, "en");
    await toVerify(user, "Continue", "Add the screen");
    expect(await screen.findByRole("alert")).toHaveTextContent(/Code expired.*every 15 minutes/);
  });

  it("clavier : le focus suit le titre de l'étape, Retour conserve le code, Entrée valide", async () => {
    mockApi({ ...base }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/add", b);
    await user.click(await screen.findByRole("button", { name: "Continuer" }));
    await user.type(await screen.findByRole("textbox"), "k7q4mz{Enter}");
    const title = await screen.findByRole("heading", { name: /Nom et emplacement/ });
    await waitFor(() => expect(title).toHaveFocus());
    await user.click(screen.getByRole("button", { name: "Retour" }));
    expect((await screen.findByRole("textbox")) as HTMLInputElement).toHaveProperty("value", expect.stringMatching(/^K7Q-?4MZ$/));
  });

  it("?screen= : deux étapes, /pairings/claim sans création, succès sur la vérification", async () => {
    const calls = mockApi(
      {
        ...base,
        "GET /screens": [{ ...NEW, device: null }],
        "POST /pairings/claim": () => new Response(JSON.stringify({}), { status: 200 }),
        [`GET /screens/${NEW.id}`]: { ...NEW, device: device("online", HB) },
        [`GET /sites/${SITE.id}/agents`]: [],
        [`GET /config/screen/${NEW.id}/core`]: cfg(),
      },
      b,
    );
    const user = userEvent.setup();
    await renderRoutes(routes, `/screens/add?screen=${NEW.id}`, b);
    await user.type(await screen.findByLabelText("Code affiché sur l'écran"), "k7q4mz");
    await user.click(screen.getByRole("button", { name: "Remplacer l'appareil" }));
    expect(await screen.findByText("L'écran fonctionne")).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/screens/pair")).toBe(false);
    expect(calls.find((c) => c.path === "/pairings/claim")?.body).toEqual({ code: "K7Q4MZ", screen_id: NEW.id });
  });

  it("?screen= inconnu : état « introuvable » avec retour, aucun appel à /screens/pair", async () => {
    const calls = mockApi({ ...base, "GET /screens": [{ ...NEW, device: null }] }, b);
    await renderRoutes(routes, "/screens/add?screen=30000000-0000-4000-8000-0000000000ff", b);
    expect(await screen.findByText(/n'existe plus ou est archivé/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Voir les écrans" })).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/screens/pair" || c.path === "/pairings/claim")).toBe(false);
  });

  it("/screens/android reste sous la base", async () => {
    mockApi({ ...base }, b);
    await renderRoutes(routes, "/screens/android", b);
    expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.every(([u]) => String(u).startsWith(`${b}/api/v1/`))).toBe(true);
  });

  it("l'éditeur n'a pas accès à l'assistant", async () => {
    mockApi({ ...base, "GET /me": { id: "u2", display_name: "Ed", email: null, role: "editor", provider: "auth.local", locale: null } }, b);
    await renderRoutes(routes, "/screens/add", b);
    expect(await screen.findByText("L'assistant est réservé aux administrateurs.")).toBeInTheDocument();
  });
});

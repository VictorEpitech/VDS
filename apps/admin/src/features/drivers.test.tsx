import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { errorResponse, mockApi, renderRoutes } from "../../test/render";
import { routes } from "../app/routes";
import type { Command } from "../lib/queries";
import { DriversSection } from "./power/DriversSection";
import { candidatesOf, DetectPanel } from "./supervision/DetectPanel";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };
const SCREEN = { id: "30000000-0000-4000-8000-000000000001", site_id: SITE.id, group_id: null, name: "Hall", orientation: "landscape", archived_at: null, device: null };
const origin = (level: string, name: string | null = null) => ({ level, id: null, name });
const v = (value: unknown, o = origin("default"), more: Record<string, unknown> = {}) => ({ value, origin: o, inherited: value, inherited_origin: origin("default"), overridable: true, secret: false, is_set: null, ...more });
const wolDir = path.resolve(__dirname, "../../../../modules/power.wol");
const read = (f: string) => JSON.parse(fs.readFileSync(path.join(wolDir, f), "utf8")) as Record<string, unknown>;
const settings = (id: string, schema: unknown, i18n: unknown = { fr: {}, en: {} }) => ({ id, version: "0.1.0", name: { fr: "Alimentation WoL", en: "WoL power" }, description: {}, config_schema: schema, i18n });
const CORE = `GET /config/screen/${SCREEN.id}/core`;
const WOL = `/config/screen/${SCREEN.id}/power.wol`;

const page = (more: Record<string, unknown> = {}, wolValues: Record<string, unknown> = {}) => ({
  "GET /screens": [SCREEN],
  [CORE]: { values: { "power.cascade": v(["power.wol", "power.cec"]) }, warnings: [] },
  "GET /registry/modules/power.wol/settings": settings("power.wol", read("config.schema.json"), { fr: read("i18n/fr.json"), en: read("i18n/en.json") }),
  [`GET ${WOL}`]: { values: { mac: v(""), broadcast: v("255.255.255.255", origin("site", "Moulins")), port: v(9), ...wolValues }, warnings: [] },
  ...more,
});

const urlsUnder = (b: string) => (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.every((c) => String(c[0]).startsWith(`${b}/api/v1/`));

describe.each([
  ["", "fr", "Pilotes de l'écran", "Écran", "Enregistrer", "Hérité du site Moulins"],
  ["/espace/affichage", "en", "Screen drivers", "Screen", "Save", "Inherited from site Moulins"],
])("pilotes d'un écran, base « %s » (%s)", (b, locale, title, screenLabel, saveLabel, inherited) => {
  it("formulaire généré, provenance héritée, enregistrement au niveau écran", async () => {
    const calls = mockApi(page({ [`PUT ${WOL}/mac`]: null }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/power", b, locale);
    expect(await screen.findByRole("heading", { level: 2, name: title })).toBeInTheDocument();
    await screen.findByRole("option", { name: "Hall" });
    await user.selectOptions(screen.getByLabelText(screenLabel, { selector: "select" }), SCREEN.id);
    await user.type(await screen.findByLabelText(/MAC/), "aa:bb:cc:dd:ee:ff");
    await user.click(await screen.findByText(locale === "fr" ? "Avancé" : "Advanced"));
    expect(screen.getAllByText(inherited).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /(Revenir à l'héritage|Revert to inherited)/ })).not.toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: saveLabel })[0]!);
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1));
    expect(calls.find((c) => c.method === "PUT")).toMatchObject({ path: `${WOL}/mac`, body: { value: "aa:bb:cc:dd:ee:ff" } });
    expect(urlsUnder(b)).toBe(true);
  });
});

describe("pilotes : surcharge, validation, cascade", () => {
  const pick = async (user: ReturnType<typeof userEvent.setup>) => {
    await screen.findByRole("option", { name: "Hall" });
    await user.selectOptions(screen.getByLabelText("Écran", { selector: "select" }), SCREEN.id);
  };

  it("revenir à l'héritage supprime la surcharge de l'écran", async () => {
    const calls = mockApi(page({ [`DELETE ${WOL}/port`]: null }, { port: v(8000, origin("screen", "Hall")) }));
    const user = userEvent.setup();
    await renderRoutes(routes, "/power", "");
    await pick(user);
    await user.click(await screen.findByText("Avancé"));
    await user.click(await screen.findByRole("button", { name: /Revenir à l'héritage : / }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path === `${WOL}/port`)).toBe(true));
  });

  it("refuse un port hors bornes sans appeler l'API", async () => {
    const calls = mockApi(page());
    const user = userEvent.setup();
    await renderRoutes(routes, "/power", "");
    await pick(user);
    await user.click(await screen.findByText("Avancé"));
    const port = await screen.findByLabelText(/port/i);
    await user.clear(port);
    await user.type(port, "70000");
    await user.click(screen.getAllByRole("button", { name: "Enregistrer" })[0]!);
    expect(document.querySelector(".field-error")).not.toBeNull();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("réordonne la cascade au clavier et l'enregistre", async () => {
    const calls = mockApi(page({ [`PUT /config/screen/${SCREEN.id}/core/power.cascade`]: null }));
    const user = userEvent.setup();
    await renderRoutes(routes, "/power", "");
    await pick(user);
    const down = await screen.findByRole("button", { name: "Descendre power.wol" });
    down.focus();
    await user.keyboard("{Enter}");
    const saves = screen.getAllByRole("button", { name: "Enregistrer" });
    await user.click(saves[saves.length - 1]!);
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ value: ["power.cec", "power.wol"] }));
    expect(await screen.findByText("Ordre enregistré.")).toBeInTheDocument();
  });

  it("liste tous les pilotes du registre et ajoute à la cascade un pilote absent", async () => {
    const catalog = [
      { id: "power.wol", name: { fr: "Alimentation WoL", en: "WoL power" }, enabled: true, has_settings: true },
      { id: "power.blank", name: { fr: "Sans réglage", en: "Blank" }, enabled: false, has_settings: false },
    ];
    const calls = mockApi(page({ "GET /registry/modules?kind=power-driver": catalog, [`PUT /config/screen/${SCREEN.id}/core/power.cascade`]: null }));
    const user = userEvent.setup();
    await renderRoutes(routes, "/power", "");
    await pick(user);
    expect(await screen.findByRole("region", { name: /Sans réglage/ })).toBeInTheDocument();
    expect(screen.getByText("Ce pilote est désactivé.")).toBeInTheDocument();
    expect(screen.getByText("Ce pilote n'a aucun réglage.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ajouter power.wol à la cascade" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ajouter power.blank à la cascade" }));
    const saves = screen.getAllByRole("button", { name: "Enregistrer" });
    await user.click(saves[saves.length - 1]!);
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ value: ["power.wol", "power.cec", "power.blank"] }));
  });

  it("erreur serveur : alerte affichée", async () => {
    mockApi(page({ [`PUT ${WOL}/mac`]: errorResponse(422, "config.value_invalid") }));
    const user = userEvent.setup();
    await renderRoutes(routes, "/power", "");
    await pick(user);
    await user.type(await screen.findByLabelText(/MAC/), "x");
    await user.click(screen.getAllByRole("button", { name: "Enregistrer" })[0]!);
    expect((await screen.findAllByRole("alert")).length).toBeGreaterThan(0);
  });

  it("secret : champ masqué, jamais relu ni envoyé", async () => {
    const schema = { type: "object", properties: { token: { type: "string", title: "t", "x-vds-level": "simple", "x-vds-secret": true, "x-vds-env": "VDS_TOKEN", "x-vds-scopes": ["screen"] } } };
    const calls = mockApi({
      "GET /screens": [SCREEN],
      [CORE]: { values: { "power.cascade": v(["power.x"]) }, warnings: [] },
      "GET /registry/modules/power.x/settings": settings("power.x", schema, { fr: { t: "Jeton" }, en: {} }),
      [`GET /config/screen/${SCREEN.id}/power.x`]: { values: { token: v(null, origin("env"), { secret: true, is_set: true, overridable: false }) }, warnings: [] },
    });
    const user = userEvent.setup();
    await renderRoutes(routes, "/power", "");
    await pick(user);
    const input = (await screen.findByLabelText("Jeton")) as HTMLInputElement;
    expect(input.type).toBe("password");
    expect(input.value).toBe("");
    expect(input.placeholder).toBe("••••••••");
    expect(input).toHaveAttribute("readonly");
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });
});

describe("détection vers formulaire de pilote", () => {
  const cmd = {
    id: "c1", seq: 1, name: "power.detect", screen_id: SCREEN.id, args: {}, status: "ok", code: null,
    result: { results: [{ driver: "power.wol", confidence: 0.9, proposed_config: { mac: "aa:bb:cc:dd:ee:ff" }, requires_user_action: null }] },
    created_at: "", sent_at: null, done_at: null, expires_at: "",
  } as unknown as Command;

  it("« Appliquer la proposition » préremplit sans rien enregistrer, puis enregistre après validation", async () => {
    const calls = mockApi(page({ [`PUT ${WOL}/mac`]: null }));
    const user = userEvent.setup();
    expect(candidatesOf(cmd)).toHaveLength(1);
    await renderRoutes(
      [
        { path: "/", element: <DetectPanel screenId={SCREEN.id} screenName="Hall" command={cmd} onClose={() => undefined} /> },
        { path: "/power", element: <DriversSection /> },
      ],
      "/",
      "",
    );
    await user.click(screen.getByRole("button", { name: "Appliquer la proposition du pilote power.wol" }));
    expect(await screen.findByDisplayValue("aa:bb:cc:dd:ee:ff")).toBeInTheDocument();
    expect(screen.getByText(/Proposition de la détection/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
    await user.click(screen.getAllByRole("button", { name: "Enregistrer" })[0]!);
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ value: "aa:bb:cc:dd:ee:ff" }));
  });
});

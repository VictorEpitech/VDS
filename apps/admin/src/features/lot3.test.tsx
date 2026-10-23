import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it, vi } from "vitest";
import { errorResponse, mockApi, renderRoutes } from "../../test/render";
import { routes } from "../app/routes";
import { displayState, isScheduledOff } from "./supervision/status";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };
const ORG = { id: "99999999-0000-4000-8000-000000000001", name: "Ville" };
const AGENT = {
  id: "70000000-0000-4000-8000-000000000001",
  site_id: SITE.id,
  name: "Salle technique",
  token_prefix: "vds_agt_AbCd",
  status: "online",
  version: "1.0.0",
  last_seen_at: "2026-10-24T09:00:00Z",
  last_heartbeat: null,
  created_at: "2026-10-01T08:00:00Z",
  rotated_at: null,
  revoked_at: null,
};
const SCREEN = { id: "30000000-0000-4000-8000-000000000001", site_id: SITE.id, group_id: null, name: "Hall", orientation: "landscape", archived_at: null, device: null };
const TOKEN = "vds_agt_SECRET-TOKEN-VALUE";
const origin = (level: string, name: string | null = null): { level: string; id: string | null; name: string | null } => ({ level, id: null, name });
const modules = path.resolve(__dirname, "../../../../modules/notify.mail");
const read = (f: string) => JSON.parse(fs.readFileSync(path.join(modules, f), "utf8")) as Record<string, unknown>;
const created = (body: unknown) => () => new Response(JSON.stringify(body), { status: 201 });

describe.each(["", "/espace/affichage"])("agents d'un site, base « %s »", (b) => {
  const site = { "GET /sites": [SITE], "GET /screens": [], [`GET /config/site/${SITE.id}/core`]: { values: {}, warnings: [] } };

  it("affiche le jeton une seule fois à la création puis le masque", async () => {
    const calls = mockApi({ ...site, [`GET /sites/${SITE.id}/agents`]: [], [`POST /sites/${SITE.id}/agents`]: created({ agent: AGENT, token: TOKEN }) }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, `/sites/${SITE.id}`, b);
    await user.type(await screen.findByLabelText("Nom de l'agent"), "Salle technique");
    await user.click(screen.getByRole("button", { name: "Créer l'agent" }));
    expect(await screen.findByTestId("agent-token")).toHaveTextContent(TOKEN);
    expect(screen.getByText(/ne sera plus jamais affiché/)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ name: "Salle technique" });
    await user.click(screen.getByRole("button", { name: "J'ai copié le jeton" }));
    expect(screen.queryByTestId("agent-token")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain(TOKEN);
    expect(vi.mocked(fetch).mock.calls.every((c) => String(c[0]).startsWith(`${b}/api/v1/`))).toBe(true);
  });

  it("renouvelle puis révoque un agent actif", async () => {
    const calls = mockApi(
      {
        ...site,
        [`GET /sites/${SITE.id}/agents`]: [AGENT],
        [`POST /sites/${SITE.id}/agents/${AGENT.id}/rotate`]: { agent: AGENT, token: TOKEN },
        [`POST /sites/${SITE.id}/agents/${AGENT.id}/revoke`]: { ...AGENT, status: "revoked" },
      },
      b,
    );
    const user = userEvent.setup();
    await renderRoutes(routes, `/sites/${SITE.id}`, b);
    expect(await screen.findByText("En ligne")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Renouveler le jeton" }));
    await user.click(screen.getByRole("button", { name: "Renouveler le jeton de Salle technique" }));
    expect(await screen.findByTestId("agent-token")).toHaveTextContent(TOKEN);
    await user.click(screen.getByRole("button", { name: "Révoquer l'agent" }));
    await user.click(screen.getByRole("button", { name: "Révoquer Salle technique" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/revoke"))).toBe(true));
  });

  it("traduit l'erreur « agent déjà actif » en anglais", async () => {
    mockApi({ ...site, [`GET /sites/${SITE.id}/agents`]: [{ ...AGENT, status: "revoked", revoked_at: "2026-10-02T08:00:00Z" }], [`POST /sites/${SITE.id}/agents`]: errorResponse(409, "agents.already_active") }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, `/sites/${SITE.id}`, b, "en");
    await user.type(await screen.findByLabelText("Agent name"), "Another");
    await user.click(screen.getByRole("button", { name: "Create agent" }));
    expect(await screen.findByText(/already has an active agent/)).toBeInTheDocument();
  });
});

describe("rattachement avec provenance", () => {
  const cfg = (planOrigin: ReturnType<typeof origin>) => ({
    values: {
      "power.plan_id": { value: "p1", origin: planOrigin, inherited: null, inherited_origin: origin("default"), overridable: true, secret: false, is_set: null },
      "power.calendars": { value: [], origin: origin("organization", "Ville"), inherited: [], inherited_origin: origin("default"), overridable: true, secret: false, is_set: null },
    },
    warnings: [],
  });
  const plan = { id: "p1", name: "Semaine", description: null, rules: [], created_at: "", updated_at: "" };
  const page = (planOrigin: ReturnType<typeof origin>, more: Record<string, unknown> = {}) => ({
    "GET /sites": [SITE],
    [`GET /sites/${SITE.id}/agents`]: [],
    "GET /screens": [],
    "GET /power/plans": [plan],
    "GET /power/calendars": [],
    [`GET /config/site/${SITE.id}/core`]: cfg(planOrigin),
    ...more,
  });

  it("valeur héritée : badge de provenance sans retour à l'héritage", async () => {
    mockApi(page(origin("organization", "Ville")));
    await renderRoutes(routes, `/sites/${SITE.id}`, "");
    expect(await screen.findAllByText("Hérité de l'organisation")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /Revenir à l'héritage/ })).not.toBeInTheDocument();
  });

  it("valeur locale : revenir à l'héritage supprime la surcharge du niveau", async () => {
    const calls = mockApi(page({ level: "site", id: SITE.id, name: "Moulins" }, { [`DELETE /config/site/${SITE.id}/core/power.plan_id`]: null }));
    const user = userEvent.setup();
    await renderRoutes(routes, `/sites/${SITE.id}`, "");
    expect(await screen.findByText("Défini ici")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Revenir à l'héritage : Plan d'alimentation" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path.endsWith("/power.plan_id"))).toBe(true));
  });

  it("choisir un plan écrit la surcharge au niveau courant", async () => {
    const calls = mockApi(page(origin("organization", "Ville"), { [`PUT /config/site/${SITE.id}/core/power.plan_id`]: null }));
    const user = userEvent.setup();
    await renderRoutes(routes, `/sites/${SITE.id}`, "", "en");
    await user.selectOptions(await screen.findByLabelText("Power plan"), "p1");
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ value: "p1" }));
    expect(await screen.findByText("Attachment saved.")).toBeInTheDocument();
  });
});

describe("incidents et commandes", () => {
  const incident = { id: "i1", kind: "agent.offline", severity: "critical", subject_type: "agent", subject_id: AGENT.id, subject_name: "Salle technique", site_id: SITE.id, site_name: "Moulins", details: {}, opened_at: "2026-10-24T08:00:00Z", resolved_at: null, acknowledged_at: null, acknowledged_by: null };
  const common = {
    "GET /sites": [SITE],
    "GET /screens": [SCREEN],
    "GET /incidents": [incident],
    [`GET /sites/${SITE.id}/agents`]: [AGENT],
    [`GET /power/screens/${SCREEN.id}`]: { screen_id: SCREEN.id, timezone: "Europe/Paris", plan_id: null, plan_id_origin: origin("default"), calendars: [], calendars_origin: origin("default"), slots: [] },
  };

  it("acquitte un incident ouvert", async () => {
    const calls = mockApi({ ...common, "POST /incidents/i1/acknowledge": { ...incident, acknowledged_at: "2026-10-24T09:00:00Z" } });
    const user = userEvent.setup();
    await renderRoutes(routes, "/supervision", "");
    expect(await screen.findByRole("heading", { name: "Agent hors ligne" })).toBeInTheDocument();
    expect(screen.getAllByText("Ouvert").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Acquitter : Agent hors ligne" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/incidents/i1/acknowledge")).toBe(true));
  });

  it("allume un écran : ordre power.set puis résultat de l'agent", async () => {
    const cmd = { id: "c1", seq: 1, name: "power.set", screen_id: SCREEN.id, args: {}, status: "pending", code: null, result: null, created_at: "", sent_at: null, done_at: null, expires_at: "" };
    const calls = mockApi({
      ...common,
      [`POST /sites/${SITE.id}/agents/${AGENT.id}/commands`]: () => new Response(JSON.stringify(cmd), { status: 202 }),
      [`GET /sites/${SITE.id}/agents/${AGENT.id}/commands`]: [{ ...cmd, status: "ok", result: { state: "on" } }],
    });
    const user = userEvent.setup();
    await renderRoutes(routes, "/supervision", "");
    await user.click(await screen.findByRole("button", { name: "Allumer Hall" }));
    expect(await screen.findByText("Hall est allumé.", {}, { timeout: 4000 })).toBeInTheDocument();
    const sent = calls.find((c) => c.method === "POST" && c.path.endsWith("/commands"))?.body;
    expect(sent).toMatchObject({ name: "power.set", screen_id: SCREEN.id, args: { state: "on", reason: "manual" } });
  });

  it("commandes indisponibles quand l'agent du site est hors ligne", async () => {
    mockApi({ ...common, [`GET /sites/${SITE.id}/agents`]: [{ ...AGENT, status: "offline" }] });
    await renderRoutes(routes, "/supervision", "");
    expect(await screen.findByText(/agent du site est hors ligne/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Allumer Hall" })).toBeDisabled();
  });

  it("un écran éteint par le planning n'est jamais une alerte", () => {
    const now = new Date("2026-10-24T22:00:00Z");
    const power = { slots: [{ from: "2026-10-24T20:00:00Z", to: "2026-10-25T06:00:00Z", state: "off" }] } as never;
    const offline = { ...SCREEN, device: { presence: "offline" } } as never;
    expect(isScheduledOff(power, now)).toBe(true);
    expect(displayState(offline, power, [], now)).toBe("scheduled_off");
    expect(displayState(offline, undefined, [], now)).toBe("offline");
  });
});

describe("réglages de notify.mail (formulaire généré)", () => {
  const v = (value: unknown, o = origin("default"), more: Record<string, unknown> = {}) => ({ value, origin: o, inherited: value, inherited_origin: origin("default"), overridable: true, secret: false, is_set: null, ...more });
  const values = (extra: Record<string, unknown> = {}) => ({
    values: {
      smtp_host: v("smtp.test", origin("env"), { overridable: false }),
      smtp_port: v(587, origin("env"), { overridable: false }),
      smtp_security: v("starttls", origin("env")),
      smtp_username: v("", origin("env")),
      smtp_password: v("", origin("env"), { secret: true, is_set: true }),
      from: v("vds@test", origin("env")),
      notify_admins: v(true),
      extra_recipients: v([]),
      kinds: v(["screen.offline"]),
      send_resolved: v(true),
      ...extra,
    },
    warnings: [],
  });
  const api = (more: Record<string, unknown> = {}) => ({
    "GET /organization": ORG,
    "GET /registry/modules/notify.mail/settings": { id: "notify.mail", version: "0.1.0", name: {}, description: {}, config_schema: read("config.schema.json"), i18n: { fr: read("i18n/fr.json"), en: read("i18n/en.json") } },
    [`GET /config/organization/${ORG.id}/notify.mail`]: values(),
    ...more,
  });

  it.each([
    ["", "fr", "Prévenir les administrateurs", "Avancé"],
    ["/espace/affichage", "en", "Notify administrators", "Advanced"],
  ])("champs simples visibles, avancés repliés, connexion SMTP verrouillée (base « %s », %s)", async (b, locale, label, advanced) => {
    mockApi(api(), b);
    await renderRoutes(routes, "/notifications", b, locale);
    expect(await screen.findByLabelText(label)).toBeVisible();
    const details = screen.getByText(advanced).closest("details")!;
    expect(details).not.toHaveAttribute("open");
    const locked = details.querySelectorAll<HTMLInputElement>("input[id$='smtp_host'],input[id$='smtp_port'],input[id$='from']");
    expect(locked).toHaveLength(3);
    for (const input of locked) expect(input).toBeDisabled();
    expect(within(details).getAllByText(locale === "fr" ? "Défini par l'environnement" : "Set by the environment").length).toBeGreaterThan(0);
  });

  it("enregistre une liste de destinataires et les cases modifiées", async () => {
    const calls = mockApi(api({ [`PUT /config/organization/${ORG.id}/notify.mail/extra_recipients`]: null, [`PUT /config/organization/${ORG.id}/notify.mail/notify_admins`]: null }));
    const user = userEvent.setup();
    await renderRoutes(routes, "/notifications", "");
    await user.type(await screen.findByLabelText("Destinataires supplémentaires"), "a@ex.fr{Enter}b@ex.fr");
    await user.click(screen.getByLabelText("Prévenir les administrateurs"));
    await user.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(2));
    expect(calls.find((c) => c.path.endsWith("/extra_recipients"))?.body).toEqual({ value: ["a@ex.fr", "b@ex.fr"] });
    expect(calls.find((c) => c.path.endsWith("/notify_admins"))?.body).toEqual({ value: false });
    expect(await screen.findByText("Réglages enregistrés.")).toBeInTheDocument();
  });

  it("refuse une adresse e-mail invalide sans appeler l'API", async () => {
    const calls = mockApi(api());
    const user = userEvent.setup();
    await renderRoutes(routes, "/notifications", "");
    await user.type(await screen.findByLabelText("Destinataires supplémentaires"), "pas-une-adresse");
    await user.click(screen.getByRole("button", { name: "Enregistrer" }));
    expect(await screen.findByText("Le format de cette valeur n'est pas valide.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("surcharge locale : provenance et retour à l'héritage", async () => {
    const own = v(false, { level: "organization", id: ORG.id, name: "Ville" });
    const calls = mockApi(api({ [`GET /config/organization/${ORG.id}/notify.mail`]: values({ send_resolved: own }), [`DELETE /config/organization/${ORG.id}/notify.mail/send_resolved`]: null }));
    const user = userEvent.setup();
    await renderRoutes(routes, "/notifications", "");
    await user.click(await screen.findByText("Avancé"));
    expect(await screen.findByText("Défini ici")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Revenir à l'héritage : Prévenir à la résolution/ }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });
});

describe("planning d'alimentation", () => {
  const empty = { "GET /sites": [SITE], "GET /screens": [], "GET /organization": ORG, "GET /power/exceptions": [], "GET /power/calendars": [], [`GET /sites/${SITE.id}/groups`]: [] };

  it("crée un plan avec ses plages et traduit « plan utilisé »", async () => {
    const plan = { id: "p1", name: "Semaine", description: null, rules: [{ weekdays: 31, start: "08:00", end: "18:00" }], created_at: "", updated_at: "" };
    const calls = mockApi({ ...empty, "GET /power/plans": [plan], "POST /power/plans": created(plan), "DELETE /power/plans/p1": errorResponse(409, "power.plan_in_use") });
    const user = userEvent.setup();
    await renderRoutes(routes, "/power", "");
    expect(await screen.findByText(/lun.?-ven.? 08:00-18:00/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Nouveau plan" }));
    await user.type(screen.getByLabelText("Nom"), "Semaine");
    await user.click(screen.getByRole("button", { name: "Ajouter un créneau" }));
    await user.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ name: "Semaine", description: null, rules: [{ weekdays: 31, start: "08:00", end: "18:00" }] }));
    await user.click(screen.getByRole("button", { name: "Supprimer" }));
    await user.click(screen.getByRole("button", { name: "Supprimer Semaine" }));
    expect(await screen.findByText(/rattaché à un site, un groupe ou un écran/)).toBeInTheDocument();
  });

  it("crée une exception d'un jour sur un site", async () => {
    const calls = mockApi({ ...empty, "GET /power/plans": [], "POST /power/exceptions": created({}) });
    const user = userEvent.setup();
    await renderRoutes(routes, "/power", "", "en");
    await user.click(await screen.findByRole("button", { name: "New exception" }));
    await user.type(screen.getByLabelText("Label"), "Public holiday");
    await user.selectOptions(screen.getAllByLabelText("Site").at(-1)!, SITE.id);
    await user.type(screen.getByLabelText("First day"), "2026-11-11");
    await user.type(screen.getByLabelText("Last day"), "2026-11-11");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ label: "Public holiday", scope_level: "site", scope_id: SITE.id, state: "off", starts_on: "2026-11-11", ends_on: "2026-11-11", start: null, end: null }),
    );
  });
});

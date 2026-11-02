import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { errorResponse, mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };
const NEW = { id: "30000000-0000-4000-8000-000000000009", site_id: SITE.id, group_id: null, name: "TCL Salon", orientation: "landscape", archived_at: null, device: null, kiosk: null };
const SID = "50000000-0000-4000-8000-000000000001";
const dev = (over: Record<string, unknown>) => ({
  id: crypto.randomUUID(), ip: "192.168.1.50", mac: "AA:BB:CC:DD:EE:01", vendor: "TCL", model: "S5400", name: null, platform: "android",
  protocols: ["mdns"], suggested_drivers: ["power.adb"], confidence: "high", known_screen_id: null, ...over,
});
const TRAP = '<img src=x onerror="alert(1)">';
const scan = (devices: unknown[], over: Record<string, unknown> = {}) => ({
  id: SID, status: "completed", error_code: null, created_at: "2026-10-30T10:00:00Z", finished_at: "2026-10-30T10:00:20Z", networks: ["192.168.1.0/24"],
  truncated: false, capped: false, devices, ...over,
});
const CAPS = { available: true, reason: null, probe_ports: true };
const ADB_SCHEMA = { config_schema: { type: "object", properties: { host: { type: "string", "x-vds-device-address": "ip" }, mac: { type: "string", "x-vds-device-address": "mac" }, port: { type: "integer" } } }, i18n: {} };
const NEW_CFG = `/config/screen/${NEW.id}`;

const base = (caps: unknown, result: unknown = scan([])) => ({
  "GET /sites": [SITE],
  [`GET /sites/${SITE.id}/groups`]: [],
  [`GET /sites/${SITE.id}/discovery/capabilities`]: caps,
  [`POST /sites/${SITE.id}/discovery/scans`]: { scan_id: SID, status: "pending" },
  [`GET /sites/${SITE.id}/discovery/scans/${SID}`]: result,
  "GET /provisioning/capabilities": { available: false, reason: "agent_missing", platforms: [], apk: null },
  "GET /screens": [],
  "POST /screens/pair": { screen: NEW, pairing: { status: "claimed", expires_at: "2026-10-30T10:15:00Z" }, replayed: false },
  "GET /registry/modules/power.adb/settings": ADB_SCHEMA,
  [`PUT ${NEW_CFG}/core/power.cascade`]: null,
  [`PUT ${NEW_CFG}/power.adb/host`]: null,
  [`PUT ${NEW_CFG}/power.adb/mac`]: null,
});

async function openForm(user: ReturnType<typeof userEvent.setup>, next = "Continuer") {
  await user.click(await screen.findByRole("button", { name: next }));
  await user.type(await screen.findByRole("textbox"), "AB3K7P");
  await user.click(screen.getByRole("button", { name: next }));
  await screen.findByRole("option", { name: "Moulins" });
  await user.selectOptions(screen.getByLabelText("Site"), SITE.id);
}

describe.each(["", "/espace/affichage"])("recherche des TV du réseau, base « %s »", (b) => {
  it("masque le bouton sans agent et ne lance jamais de recherche seul", async () => {
    const calls = mockApi(base({ available: false, reason: "agent_missing", probe_ports: true }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/add", b);
    await openForm(user);
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/discovery/capabilities"))).toBe(true));
    expect(screen.queryByRole("button", { name: "Rechercher les TV du réseau" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Nom de l'écran")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path.includes("/discovery/"))).toBe(false);
  });

  it("explique un agent trop ancien", async () => {
    mockApi(base({ available: false, reason: "agent_outdated", probe_ports: true }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/add", b);
    await openForm(user);
    expect(await screen.findByText(/Mettez à jour l'agent de site \(version 1\.5/)).toBeInTheDocument();
  });

  it("liste, rend les noms en texte, grise « Déjà ajouté » et préremplit l'ajout depuis le schéma", async () => {
    const known = dev({ ip: "192.168.1.60", mac: null, vendor: "LG", model: "OLED", known_screen_id: "30000000-0000-4000-8000-000000000002" });
    const calls = mockApi(base(CAPS, scan([dev({}), dev({ ip: "192.168.1.70", mac: null, name: TRAP, protocols: ["adb"], suggested_drivers: [] }), known])), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/add", b);
    await openForm(user);
    await user.click(await screen.findByRole("button", { name: "Rechercher les TV du réseau" }));
    expect(await screen.findByText("3 appareils trouvés")).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText(TRAP)).toBeInTheDocument();
    expect(screen.getByText(/débogage ADB/)).toBeInTheDocument();
    expect(screen.getByText("Déjà ajouté")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Voir l'écran déjà ajouté/ })).toHaveAttribute("href", expect.stringContaining("#screen-30000000-0000-4000-8000-000000000002"));
    const radios = screen.getAllByRole("radio");
    expect(radios[2]).toBeDisabled();
    await user.click(radios[0]!);
    expect(screen.getByLabelText("Nom de l'écran")).toHaveValue("TCL S5400");
    await user.click(screen.getByRole("button", { name: "Ajouter l'écran" }));
    expect(await screen.findByText(/préremplis depuis la recherche/)).toBeInTheDocument();
    const put = (p: string) => calls.find((c) => c.method === "PUT" && c.path.endsWith(p));
    expect(put("/core/power.cascade")?.body).toEqual({ value: ["power.adb"] });
    expect(put("/power.adb/host")?.body).toEqual({ value: "192.168.1.50" });
    expect(put("/power.adb/mac")?.body).toEqual({ value: "AA:BB:CC:DD:EE:01" });
    expect(calls.some((c) => c.path.endsWith("/power.adb/port"))).toBe(false);
  });

  it("affiche confiance et MAC, et exige une confirmation pour un appareil de confiance faible", async () => {
    mockApi(base(CAPS, scan([dev({}), dev({ ip: "192.168.1.80", mac: null, name: "Samsung QM55", confidence: "low" })])), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/add", b);
    await openForm(user);
    await user.click(await screen.findByRole("button", { name: "Rechercher les TV du réseau" }));
    expect(await screen.findByText("Confiance élevée")).toBeInTheDocument();
    expect(screen.getByText("AA:BB:CC:DD:EE:01")).toBeInTheDocument();
    expect(screen.getByText("MAC inconnue")).toBeInTheDocument();
    const radios = screen.getAllByRole("radio");
    expect(radios.every((r) => !(r as HTMLInputElement).checked)).toBe(true);
    await user.click(radios[1]!);
    expect(screen.getByRole("alert")).toHaveTextContent(/n'est pas fiable/);
    expect(screen.getByLabelText("Nom de l'écran")).not.toHaveValue("Samsung QM55");
    await user.click(screen.getByRole("checkbox"));
    expect(screen.getByLabelText("Nom de l'écran")).toHaveValue("Samsung QM55");
  });

  it("affiche l'état vide et la troncature", async () => {
    mockApi(base(CAPS, scan([], { truncated: true })), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/add", b);
    await openForm(user);
    await user.click(await screen.findByRole("button", { name: "Rechercher les TV du réseau" }));
    expect(await screen.findByText(/Aucune TV trouvée/)).toBeInTheDocument();
    expect(screen.getByText(/Seule une partie du réseau/)).toBeInTheDocument();
  });

  it("affiche l'échec de l'agent puis la limite de fréquence", async () => {
    let n = 0;
    mockApi(
      {
        ...base(CAPS, scan([], { status: "failed", error_code: "discovery.no_private_network" })),
        [`POST /sites/${SITE.id}/discovery/scans`]: () =>
          n++ === 0 ? { scan_id: SID, status: "pending" } : new Response(JSON.stringify({ code: "discovery.cooldown", params: {} }), { status: 429, headers: { "Retry-After": "42" } }),
      },
      b,
    );
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/add", b);
    await openForm(user);
    await user.click(await screen.findByRole("button", { name: "Rechercher les TV du réseau" }));
    expect(await screen.findByText(/aucun réseau local/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Relancer la recherche" }));
    expect(await screen.findByText(/Patientez 42 s/)).toBeInTheDocument();
  });

  it("traduit l'erreur en anglais", async () => {
    mockApi({ ...base(CAPS), [`POST /sites/${SITE.id}/discovery/scans`]: errorResponse(409, "discovery.busy") }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/add", b, "en");
    await openForm(user, "Continue");
    await user.click(await screen.findByRole("button", { name: "Find TVs on the network" }));
    expect(await screen.findByText("A search is already running on this site.")).toBeInTheDocument();
  });
});

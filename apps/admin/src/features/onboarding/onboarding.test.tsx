import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";

type Status = "todo" | "in_progress" | "done" | "skipped";
const IDS = ["account", "site", "screen", "media", "playlist", "schedule", "power"] as const;
const FLEET = {
  site_id: null,
  generated_at: "2026-10-30T09:12:00Z",
  screens: { total: 0, by_status: {} },
  to_handle: { screens: 0, by_reason: {} },
  incidents: { open: 0, unacknowledged: 0, by_severity: {}, by_kind: {} },
  agents: { total: 0, online: 0, offline: 0 },
};
const ALL_DONE: Status[] = ["done", "done", "done", "done", "done", "done"];

function onboarding(statuses: Status[], hidden = false) {
  const steps = IDS.map((step, i) => ({ step, status: statuses[i] ?? "todo", optional: step === "power", skippable: step !== "account", count: statuses[i] === "done" ? 1 : 0, details: {} }));
  const req = steps.filter((s) => !s.optional);
  const completed = req.filter((s) => s.status === "done" || s.status === "skipped").length;
  const next = req.find((s) => s.status !== "done" && s.status !== "skipped")?.step ?? null;
  return { steps, progress: { completed, total: req.length }, next_step: next, complete: completed === req.length, card: { hidden, hidden_at: hidden ? "x" : null } };
}
const base = { "GET /sites": [], "GET /media": [], "GET /playlists": [], "GET /schedules": [], "GET /fleet/summary": FLEET };
const THREE: Status[] = ["done", "done", "done", "todo"];

describe.each(["", "/espace/affichage"])("Premiers pas, base « %s »", (b) => {
  it("progression, étape suivante mise en avant, liens sous la base", async () => {
    mockApi({ ...base, "GET /onboarding": onboarding(THREE) }, b);
    await renderRoutes(routes, "/", b);
    expect(await screen.findByText("3 sur 6 étapes terminées")).toBeInTheDocument();
    const next = screen.getByRole("link", { name: /Importer un contenu/ });
    expect(next).toHaveAttribute("href", `${b}/content/media`);
    expect(next).toHaveClass("btn-filled");
    expect(screen.getByText("Étape suivante")).toBeInTheDocument();
    expect(screen.getAllByText("Fait")).toHaveLength(3);
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /Ignorer cette étape : Créer votre compte/ })).toBeNull();
  });

  it("Ignorer cette étape : PUT puis cache remplacé", async () => {
    const calls = mockApi({ ...base, "GET /onboarding": onboarding(THREE), "PUT /onboarding/steps/media": onboarding(["done", "done", "done", "skipped"]) }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/", b);
    await user.click(await screen.findByRole("button", { name: /Ignorer cette étape : Importer un contenu/ }));
    expect(await screen.findByText("4 sur 6 étapes terminées")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ skipped: true });
    expect(calls.filter((c) => c.path === "/onboarding" && c.method === "GET")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /Rouvrir cette étape : Importer un contenu/ })).toBeInTheDocument();
  });

  it("repliable au clavier", async () => {
    mockApi({ ...base, "GET /onboarding": onboarding(["done"]) }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/", b);
    const toggle = await screen.findByRole("button", { name: "Replier" });
    toggle.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: "Déplier" })).toHaveAttribute("aria-expanded", "false");
    expect(document.getElementById("onboarding-steps")).toHaveAttribute("hidden");
  });

  it("parcours terminé : réussite puis masquage de la carte", async () => {
    const calls = mockApi({ ...base, "GET /onboarding": onboarding(ALL_DONE), "PUT /onboarding/card": onboarding(ALL_DONE, true) }, b);
    await renderRoutes(routes, "/", b);
    expect(await screen.findByText(/votre installation est prête/)).toBeInTheDocument();
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ hidden: true }));
    expect(screen.queryByRole("heading", { name: "Premiers pas" })).toBeNull();
  });

  it("éditeur : lecture seule, aucune action d'écriture", async () => {
    mockApi({ ...base, "GET /me": { id: "u", display_name: "Eve", email: null, role: "editor", provider: "auth.local", locale: null }, "GET /onboarding": onboarding(["done", "todo"]) }, b);
    await renderRoutes(routes, "/", b);
    expect(await screen.findByRole("link", { name: /Créer un site/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Ignorer cette étape/ })).toBeNull();
  });

  it("Plus › Aide rouvre la carte", async () => {
    let hidden = true;
    const calls = mockApi({ ...base, "GET /onboarding": () => onboarding(["done"], hidden), "PUT /onboarding/card": () => onboarding(["done"], (hidden = false)) }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/more", b);
    const help = (await screen.findByRole("heading", { name: "Aide" })).closest("section")!;
    await user.click(within(help).getByRole("button", { name: "Afficher les Premiers pas" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ hidden: false }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Premiers pas" })).toBeInTheDocument());
  });

  it("anglais", async () => {
    mockApi({ ...base, "GET /onboarding": onboarding(THREE) }, b);
    await renderRoutes(routes, "/", b, "en");
    expect(await screen.findByText("3 of 6 steps completed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Skip this step : Import content/ })).toBeInTheDocument();
  });
});

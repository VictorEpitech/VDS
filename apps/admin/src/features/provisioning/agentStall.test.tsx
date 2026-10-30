import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };
const SCREEN = { id: "30000000-0000-4000-8000-000000000001", site_id: SITE.id, group_id: null, name: "Hall TV", orientation: "landscape", archived_at: null, device: null, kiosk: null };
const PID = "40000000-0000-4000-8000-000000000001";
const CAPS = { available: true, reason: null, platforms: ["android"], apk: { version_name: "1.3.0", version_code: 7 } };
const session = (status: string) => ({
  id: PID, screen_id: SCREEN.id, site_id: SITE.id, platform: "android", address: "192.168.1.50", port: 5555, status, code: null, steps: [],
  outcome: null, apk_version_code: 7, pairing_code_fallback: false, ticket_expires_at: null, created_at: "2026-10-26T10:00:00Z", finished_at: null,
});
const agents = (status: string) => [{ id: "a1", site_id: SITE.id, name: "Agent", status, last_seen_at: null, revoked_at: null, rotated_at: null, last_heartbeat: null, created_at: "2026-10-26T10:00:00Z" }];

describe.each(["", "/espace/affichage"])("agent muet pendant l'installation, base « %s »", (b) => {
  const start = async (agentStatus: string) => {
    const calls = mockApi({
      "GET /sites": [SITE], [`GET /sites/${SITE.id}/groups`]: [], "GET /provisioning/capabilities": CAPS, "POST /screens": SCREEN, "GET /screens": [SCREEN],
      [`GET /sites/${SITE.id}/agents`]: agents(agentStatus),
      [`POST /screens/${SCREEN.id}/provision`]: session("queued"), [`GET /provisioning/${PID}`]: session("queued"),
      [`POST /provisioning/${PID}/retry`]: session("queued"), [`POST /provisioning/${PID}/cancel`]: session("cancelled"),
    }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/android", b);
    await user.type(await screen.findByLabelText("Nom de l'écran"), "Hall TV");
    await screen.findByRole("option", { name: "Moulins" });
    await user.selectOptions(screen.getByLabelText("Site"), SITE.id);
    const next = await screen.findByRole("button", { name: "Continuer" });
    await waitFor(() => expect(next).toBeEnabled());
    await user.click(next);
    await user.click(await screen.findByRole("button", { name: "Continuer" }));
    await user.type(await screen.findByLabelText("Adresse IP de la TV"), "192.168.1.50");
    await user.click(screen.getByRole("button", { name: "Lancer l'installation" }));
    return { calls, user };
  };

  it("agent hors ligne : message actionnable, étape en attente de l'agent, réessai", async () => {
    const { calls, user } = await start("offline");
    expect(await screen.findByText("L'agent du site ne répond pas")).toBeInTheDocument();
    expect(screen.getAllByText("En attente de l'agent").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Voir l'état de l'agent" })).toHaveAttribute("href", `${b}/sites/${SITE.id}`);
    expect(screen.getByRole("button", { name: "Annuler l'installation" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Réessayer" }));
    await waitFor(() => expect(calls.some((c) => c.path === `/provisioning/${PID}/retry`)).toBe(true));
  });

  it("agent en ligne : pas d'alerte, mais l'étape reste en attente de l'agent", async () => {
    await start("online");
    expect((await screen.findAllByText("En attente de l'agent")).length).toBeGreaterThan(0);
    expect(screen.queryByText("L'agent du site ne répond pas")).not.toBeInTheDocument();
  });
});

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { errorResponse, mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };
const EID = "50000000-0000-4000-8000-000000000001";
const AID = "60000000-0000-4000-8000-000000000001";
const PRESENCE = { v: 1, agent_version: "1.4.0", hostname: "nuc", platform: "linux", state: "dormant", ts: "2026-10-30T10:00:00Z", agent_id: null, error: null };
const enr = (extra: Record<string, unknown> = {}) => ({
  id: EID, channel: "local", created_at: "2026-10-30T10:00:00Z", expires_at: "2026-10-30T10:10:00Z", state: "pending",
  consumed_ip: null, consumed_hostname: null, agent_id: null, ...extra,
});
const base = (local: unknown, list: unknown = []) => ({
  "GET /sites": [SITE],
  "GET /screens": [],
  [`GET /config/site/${SITE.id}/core`]: { values: {}, warnings: [] },
  [`GET /sites/${SITE.id}/agents`]: [],
  [`GET /sites/${SITE.id}/groups`]: [],
  "GET /agent/local": local,
  [`GET /sites/${SITE.id}/agent/enrollments`]: list,
});
const CREATED = {
  id: EID, code: "vds_enr_SECRETCODE", expires_at: "2026-10-30T11:00:00Z", server_url: "https://x", image: "img",
  commands: { docker_run: "docker run -e VDS_ENROLL_CODE=vds_enr_SECRETCODE img", docker_login: "docker login ghcr.io -u u" },
  files: { agent_env: "VDS_ENROLL_CODE=vds_enr_SECRETCODE" },
};

describe.each(["", "/espace/affichage"])("enrôlement d'agent, base « %s »", (b) => {
  it("masque « Ce serveur » quand le canal local est indisponible", async () => {
    mockApi(base({ available: false, detected: null, site_id: null }), b);
    await renderRoutes(routes, `/sites/${SITE.id}`, b);
    expect(await screen.findByRole("button", { name: "Une autre machine" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ce serveur" })).not.toBeInTheDocument();
  });

  it("active l'agent local, suit son arrivée puis permet de le rejeter", async () => {
    let state = "pending";
    const calls = mockApi(
      {
        ...base({ available: true, detected: PRESENCE, site_id: null }),
        [`GET /sites/${SITE.id}/agent/enrollments`]: () => [enr(state === "pending" ? {} : { state: "consumed", consumed_ip: "192.168.1.20", consumed_hostname: "nuc", agent_id: AID })],
        [`POST /sites/${SITE.id}/agent/local`]: () => {
          state = "pending";
          return new Response(JSON.stringify({ enrollment_id: EID, expires_at: "2026-10-30T10:10:00Z" }), { status: 202 });
        },
        [`POST /sites/${SITE.id}/agents/${AID}/revoke`]: {},
      },
      b,
    );
    const user = userEvent.setup();
    await renderRoutes(routes, `/sites/${SITE.id}`, b);
    const btn = await screen.findByRole("button", { name: "Ce serveur" });
    await waitFor(() => expect(btn).toBeEnabled());
    await user.click(btn);
    expect(await screen.findByText(/En attente de l'agent/)).toBeInTheDocument();
    state = "consumed";
    expect(await screen.findByText("Un agent est arrivé.", undefined, { timeout: 6000 })).toBeInTheDocument();
    expect(screen.getByText("192.168.1.20")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ce n'est pas mon agent" }));
    await user.click(screen.getByRole("button", { name: "Révoquer cet agent ?" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === `/sites/${SITE.id}/agents/${AID}/revoke`)).toBe(true));
  }, 15000);

  it("explique l'échec par code", async () => {
    mockApi({ ...base({ available: true, detected: PRESENCE, site_id: null }), [`POST /sites/${SITE.id}/agent/local`]: errorResponse(409, "agent.local.write_failed") }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, `/sites/${SITE.id}`, b);
    const btn = await screen.findByRole("button", { name: "Ce serveur" });
    await waitFor(() => expect(btn).toBeEnabled());
    await user.click(btn);
    expect(await screen.findByText(/dossier partagé avec l'agent/)).toBeInTheDocument();
  });

  it("désactive « Ce serveur » tant qu'aucun agent n'est détecté", async () => {
    mockApi(base({ available: true, detected: null, site_id: null }), b);
    await renderRoutes(routes, `/sites/${SITE.id}`, b);
    expect(await screen.findByRole("button", { name: "Ce serveur" })).toBeDisabled();
  });

  it("affiche le code distant une fois, expiration visible, en anglais aussi", async () => {
    const calls = mockApi({ ...base({ available: false, detected: null, site_id: null }), [`POST /sites/${SITE.id}/agent/enrollments`]: () => new Response(JSON.stringify(CREATED), { status: 201 }) }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, `/sites/${SITE.id}`, b, "en");
    await user.click(await screen.findByRole("button", { name: "Another machine" }));
    expect(await screen.findByText(/Expires on/)).toBeInTheDocument();
    expect(screen.getAllByTestId("enroll-command")[1]).toHaveTextContent("VDS_ENROLL_CODE=vds_enr_SECRETCODE");
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ ttl_min: 60 });
    expect(document.body.innerHTML).not.toContain("vds_agt_");
    await user.click(screen.getByRole("button", { name: "I copied the code" }));
    expect(document.body.textContent).not.toContain("SECRETCODE");
  });

  it("liste et révoque les codes en attente", async () => {
    const calls = mockApi({ ...base({ available: false, detected: null, site_id: null }, [enr({ channel: "remote" })]), [`DELETE /sites/${SITE.id}/agent/enrollments/${EID}`]: null }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, `/sites/${SITE.id}`, b);
    await user.click(await screen.findByRole("button", { name: "Révoquer" }));
    await user.click(screen.getByRole("button", { name: "Révoquer ce code ?" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE")).toBe(true));
  });

  it("renvoie vers le panneau Agent depuis /pairing", async () => {
    mockApi({ "GET /sites": [SITE], "GET /screens": [], [`GET /provisioning/capabilities`]: { available: false, reason: "agent_offline", platforms: [], apk: null } }, b);
    await renderRoutes(routes, "/screens/add", b);
    expect(await screen.findByRole("link", { name: "Configurer l'agent du site" })).toHaveAttribute("href", `${b}/sites`);
  });
});

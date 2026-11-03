import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";
import { ConnectAgentWizard } from "./ConnectAgentWizard";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };
const iso = (agoMs: number) => new Date(Date.now() - agoMs).toISOString();
const created = (b: string) => ({
  id: "e1", code: "X", expires_at: iso(-3_600_000), image: "img", server_url: `https://vds.test${b}`,
  commands: { docker_login: null, docker_run: `docker run -e VDS_SERVER_URL=https://vds.test${b} vds/agent` }, files: { agent_env: "A=1" },
});
const enrollment = (state: string) => [{ id: "e1", channel: "remote", state, agent_id: state === "consumed" ? "a1" : null, consumed_hostname: null, consumed_ip: null, created_at: iso(0), expires_at: iso(-3_600_000) }];
const agent = { id: "a1", site_id: SITE.id, name: "Agent", status: "online", last_seen_at: iso(3000), revoked_at: null, rotated_at: null, last_heartbeat: null, created_at: iso(10_000), token_prefix: "ab", version: "1.0" };

describe.each(["", "/espace/affichage"])("assistant agent, base « %s »", (b) => {
  const api = (state: string, extra: Record<string, unknown> = {}) =>
    mockApi({
      "GET /sites": [SITE], [`POST /sites/${SITE.id}/agent/enrollments`]: created(b), ["GET /sites/" + SITE.id + "/agent/enrollments"]: enrollment(state),
      ["GET /sites/" + SITE.id + "/agents"]: [agent], ...extra,
    }, b);

  it("commande copiable avec la base, puis battement reçu : étape suivante", async () => {
    api("consumed");
    const user = userEvent.setup();
    await renderRoutes(routes, `/sites/connect?site=${SITE.id}`, b);
    await user.click(await screen.findByRole("button", { name: "Générer la commande" }));
    expect(await screen.findByTestId("enroll-command")).toHaveTextContent(`https://vds.test${b} vds/agent`);
    expect(screen.getByRole("button", { name: "Copier" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "J'ai lancé la commande" }));
    expect(await screen.findByText(/Agent connecté il y a 3 s/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continuer" }));
    expect(await screen.findByRole("heading", { name: "Détecter les appareils du site" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Passer cette étape" }));
    expect(await screen.findByText("L'agent du site Moulins est connecté.")).toBeInTheDocument();
  });

  it("2 minutes sans battement : message sur le port sortant 443 et nouvelle commande", async () => {
    api("pending");
    const user = userEvent.setup();
    await renderRoutes([{ path: "*", element: <ConnectAgentWizard siteId={SITE.id} pollMs={50} stallMs={150} /> }], "/", b);
    await user.click(await screen.findByRole("button", { name: "Générer la commande" }));
    await user.click(await screen.findByRole("button", { name: "J'ai lancé la commande" }));
    expect(screen.getByText(/En attente de la première connexion/)).toBeInTheDocument();
    expect(await screen.findByText(/port sortant 443/, {}, { timeout: 2000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continuer" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Générer une nouvelle commande" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: /Installer l'agent/ })).toBeInTheDocument());
  });
});

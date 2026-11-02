import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { errorResponse, mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";
import { codeKey, STEP_ORDER } from "./hooks";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };
const SCREEN = { id: "30000000-0000-4000-8000-000000000001", site_id: SITE.id, group_id: null, name: "Hall TV", orientation: "landscape", archived_at: null, device: null, kiosk: null };
const PID = "40000000-0000-4000-8000-000000000001";
const OK_CAPS = { available: true, reason: null, platforms: ["android"], apk: { version_name: "1.3.0", version_code: 7 } };

const step = (id: string, status: string, code: string | null = null) => ({ step: id, status, code, at: null });
const session = (status: string, steps: unknown[], extra: Record<string, unknown> = {}) => ({
  id: PID, screen_id: SCREEN.id, site_id: SITE.id, platform: "android", address: "192.168.1.50", port: 5555, status, code: null, steps,
  outcome: null, apk_version_code: 7, pairing_code_fallback: false, ticket_expires_at: null, created_at: "2026-10-26T10:00:00Z", finished_at: null, ...extra,
});
const base = (caps: unknown = OK_CAPS) => ({
  "GET /sites": [SITE],
  [`GET /sites/${SITE.id}/groups`]: [],
  "GET /provisioning/capabilities": caps,
  "POST /screens": SCREEN,
  "GET /screens": [SCREEN],
});

async function toTutorial(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText("Nom de l'écran"), "Hall TV");
  await screen.findByRole("option", { name: "Moulins" });
  await user.selectOptions(screen.getByLabelText("Site"), SITE.id);
  const next = await screen.findByRole("button", { name: "Continuer" });
  await waitFor(() => expect(next).toBeEnabled());
  await user.click(next);
}

async function toAddress(user: ReturnType<typeof userEvent.setup>) {
  await toTutorial(user);
  await user.click(await screen.findByRole("button", { name: "Continuer" }));
}

const lan = async (user: ReturnType<typeof userEvent.setup>, ip: string) => {
  await user.type(await screen.findByLabelText("Adresse IP de la TV"), ip);
};

describe.each(["", "/espace/affichage"])("assistant TV Android, base « %s »", (b) => {
  it("masque la suite et explique quand l'agent est trop ancien", async () => {
    mockApi(base({ available: false, reason: "agent_outdated", platforms: [], apk: null }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/android", b);
    await screen.findByRole("option", { name: "Moulins" });
    await user.selectOptions(screen.getByLabelText("Site"), SITE.id);
    expect(await screen.findByText(/Mettez à jour l'agent de site/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continuer" })).not.toBeInTheDocument();
  });

  it("parcourt écran, tutoriel, IP et affiche l'invite RSA", async () => {
    const calls = mockApi({ ...base(), [`POST /screens/${SCREEN.id}/provision`]: session("waiting_user", [step("connect", "ok"), step("authorize", "waiting_user", "provision.android.accept_on_tv")]) }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/android", b);
    await toAddress(user);
    expect(calls.some((c) => c.method === "POST" && c.path === "/screens")).toBe(true);
    await lan(user, "192.168.1.50");
    await user.click(screen.getByRole("button", { name: "Lancer l'installation" }));
    expect((await screen.findAllByText("Acceptez l'invite sur la TV et cochez « Toujours autoriser ».")).length).toBeGreaterThan(0);
    const post = calls.find((c) => c.path === `/screens/${SCREEN.id}/provision`);
    expect(post?.body).toMatchObject({ platform: "android", address: "192.168.1.50", port: 5555, device_owner: "try", soft_kiosk_fallback: true });
    expect(screen.getAllByText("Action sur la TV").length).toBeGreaterThan(0);
  });

  it("continue en kiosque doux après un échec Device Owner, sans confirmation intermédiaire", async () => {
    const failed = session("failed", [step("connect", "ok"), step("device_owner", "failed", "provision.android.accounts_present")], { code: "provision.android.accounts_present" });
    const calls = mockApi({ ...base(), [`POST /screens/${SCREEN.id}/provision`]: failed, [`GET /provisioning/${PID}`]: failed, [`POST /provisioning/${PID}/retry`]: session("running", []) }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/android", b);
    await toAddress(user);
    await lan(user, "192.168.1.52");
    await user.click(screen.getByRole("button", { name: "Lancer l'installation" }));
    await user.click(await screen.findByRole("button", { name: "Continuer en kiosque doux" }));
    await waitFor(() => expect(calls.find((c) => c.path === `/provisioning/${PID}/retry`)?.body).toEqual({ from_step: "soft_kiosk" }));
    expect(calls.some((c) => c.path.endsWith("/non-public-ack"))).toBe(false);
  });

  it("montre le tutoriel avec le cas compte Google", async () => {
    mockApi(base(), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/android", b);
    await toTutorial(user);
    expect(await screen.findByRole("heading", { name: "Compte Google" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Activer le débogage réseau" })).toBeInTheDocument();
  });

  it("refuse une adresse mal formée sans appeler le serveur", async () => {
    const calls = mockApi(base(), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/android", b);
    await toAddress(user);
    await lan(user, "tv.local");
    await user.click(screen.getByRole("button", { name: "Lancer l'installation" }));
    expect(await screen.findByText("Saisissez une adresse IP valide.")).toBeInTheDocument();
    expect(calls.some((c) => c.path.endsWith("/provision"))).toBe(false);
  });

  it("explique tls_required et relance à l'étape en échec", async () => {
    const failed = session("failed", [step("connect", "failed", "provision.android.tls_required")], { code: "provision.android.tls_required", pairing_code_fallback: true });
    const calls = mockApi({ ...base(), [`POST /screens/${SCREEN.id}/provision`]: failed, [`GET /provisioning/${PID}`]: failed, [`POST /provisioning/${PID}/retry`]: session("running", [step("connect", "running")]) }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/android", b);
    await toAddress(user);
    await lan(user, "192.168.1.51");
    await user.click(screen.getByRole("button", { name: "Lancer l'installation" }));
    expect((await screen.findAllByText(/n'accepte que le débogage sans fil chiffré/)).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Réessayer à cette étape" }));
    await waitFor(() => expect(calls.find((c) => c.path === `/provisioning/${PID}/retry`)?.body).toEqual({ from_step: "connect" }));
  });

  it("propose le kiosque doux quand le Device Owner échoue", async () => {
    const failed = session("failed", [step("connect", "ok"), step("device_owner", "failed", "provision.android.accounts_present")], { code: "provision.android.accounts_present" });
    const calls = mockApi({ ...base(), [`POST /screens/${SCREEN.id}/provision`]: failed, [`GET /provisioning/${PID}`]: failed, [`POST /provisioning/${PID}/retry`]: session("running", []) }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/android", b);
    await toAddress(user);
    await lan(user, "192.168.1.52");
    await user.click(screen.getByRole("button", { name: "Lancer l'installation" }));
    await user.click(await screen.findByRole("button", { name: "Continuer en kiosque doux" }));
    await waitFor(() => expect(calls.find((c) => c.path === `/provisioning/${PID}/retry`)?.body).toEqual({ from_step: "soft_kiosk" }));
  });

  it("valide automatiquement quand l'écran est en ligne, avec le bandeau kiosque doux", async () => {
    let polls = 0;
    mockApi(
      {
        ...base(),
        [`POST /screens/${SCREEN.id}/provision`]: session("running", [step("connect", "running")]),
        [`GET /provisioning/${PID}`]: () => {
          polls += 1;
          return polls < 2
            ? session("waiting_online", STEP_ORDER.map((id) => step(id, "ok")))
            : session("done", STEP_ORDER.map((id) => step(id, id === "device_owner" ? "skipped" : "ok", id === "device_owner" ? "provision.android.accounts_present" : null)), {
                outcome: { ok: true, kiosk: "soft", installed_version_code: 7, code: null },
              });
        },
      },
      b,
    );
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/android", b);
    await toAddress(user);
    await lan(user, "192.168.1.50");
    await user.click(screen.getByRole("button", { name: "Lancer l'installation" }));
    expect(await screen.findByText("L'écran est en ligne : l'installation est validée.", {}, { timeout: 7000 })).toBeInTheDocument();
    expect(screen.getByText(/Kiosque doux : les touches Accueil/)).toBeInTheDocument();
    expect(screen.getAllByText("En ligne").length).toBeGreaterThan(0);
  }, 12_000);

  it("affiche l'erreur serveur d'une adresse publique", async () => {
    mockApi({ ...base(), [`POST /screens/${SCREEN.id}/provision`]: errorResponse(422, "provision.address_not_lan") }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/android", b);
    await toAddress(user);
    await lan(user, "8.8.8.8");
    await user.click(screen.getByRole("button", { name: "Lancer l'installation" }));
    expect(await screen.findByText("Utilisez l'adresse IP locale de la TV (réseau privé).")).toBeInTheDocument();
  });

  it("est réservé au rôle admin", async () => {
    mockApi({ ...base(), "GET /me": { id: "u2", display_name: "Ed", email: null, role: "editor", provider: "auth.local", locale: null } }, b);
    await renderRoutes(routes, "/screens/android", b);
    expect(await screen.findByText("L'assistant est réservé aux administrateurs.")).toBeInTheDocument();
    expect(screen.queryByLabelText("Site")).not.toBeInTheDocument();
  });

  it("fonctionne en anglais", async () => {
    mockApi(base({ available: false, reason: "agent_offline", platforms: [], apk: null }), b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/android", b, "en");
    expect(await screen.findByRole("heading", { name: "Install on Android TV" })).toBeInTheDocument();
    await screen.findByRole("option", { name: "Moulins" });
    await user.selectOptions(screen.getByLabelText("Site"), SITE.id);
    expect(await screen.findByText(/site's agent is offline/)).toBeInTheDocument();
  });
});

describe("codes", () => {
  it("retire le préfixe de module", () => {
    expect(codeKey("provision.android.tls_required")).toBe("tls_required");
    expect(codeKey("provision.ticket_expired")).toBe("ticket_expired");
  });
});

describe.each(["", "/espace/affichage"])("installation auto depuis /screens/add, base « %s »", (b) => {
  it("est un lien vers l'assistant quand un agent est disponible", async () => {
    mockApi({ ...base(), "GET /pairings": [] }, b);
    await renderRoutes(routes, "/screens/add", b);
    await userEvent.click(await screen.findByRole("radio", { name: /Android TV/ }));
    const link = await screen.findByRole("link", { name: "Installer depuis le réseau" });
    expect(link).toHaveAttribute("href", `${b}/screens/add/android`);
  });

  it("est désactivé quand aucun agent n'est disponible", async () => {
    mockApi({ ...base({ available: false, reason: "agent_too_old", platforms: [], apk: null }) }, b);
    await renderRoutes(routes, "/screens/add", b);
    await userEvent.click(await screen.findByRole("radio", { name: /Android TV/ }));
    expect(await screen.findByRole("button", { name: "Installer depuis le réseau" })).toBeDisabled();
    expect(screen.queryByRole("link", { name: "Installer depuis le réseau" })).toBeNull();
  });

  it("saute l'étape 1 pour un écran existant et préremplit l'adresse connue", async () => {
    const calls = mockApi({ ...base(), [`GET /config/screen/${SCREEN.id}/power.adb`]: { values: { host: { value: "192.168.1.77", inherited: null, origin: { level: "screen", scope_id: SCREEN.id }, inherited_origin: { level: "default", scope_id: null }, overridable: true } }, warnings: [] } }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, `/screens/android?screen=${SCREEN.id}`, b);
    expect(await screen.findByText("Écran existant")).toBeInTheDocument();
    expect(screen.getByText("Hall TV")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Continuer" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Continuer" }));
    expect(await screen.findByLabelText("Adresse IP de la TV")).toHaveValue("192.168.1.77");
    expect(calls.some((c) => c.method === "POST" && c.path === "/screens")).toBe(false);
  });

  it("explique et ramène à la liste quand l'écran n'existe pas", async () => {
    mockApi(base(), b);
    await renderRoutes(routes, "/screens/android?screen=30000000-0000-4000-8000-0000000000ff", b);
    expect(await screen.findByText(/n'existe plus ou est archivé/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Voir les écrans" })).toBeInTheDocument();
  });

  it("propose Réinstaller dans la liste des écrans (admin, agent disponible)", async () => {
    mockApi(base(), b);
    await renderRoutes(routes, "/screens", b);
    const link = await screen.findByRole("link", { name: "Réinstaller ou installer l'app sur Hall TV" });
    expect(link).toHaveAttribute("href", `${b}/screens/add/android?screen=${SCREEN.id}`);
  });

  it("masque Réinstaller sans agent disponible", async () => {
    mockApi(base({ available: false, reason: "no_agent", platforms: [], apk: null }), b);
    await renderRoutes(routes, "/screens", b);
    await screen.findByText("Hall TV");
    expect(screen.queryByRole("link", { name: /Réinstaller ou installer/ })).toBeNull();
  });

  it("propose Réinstaller l'app après une annulation", async () => {
    const cancelled = session("cancelled", [step("connect", "ok")]);
    mockApi({ ...base(), [`POST /screens/${SCREEN.id}/provision`]: cancelled, [`GET /provisioning/${PID}`]: cancelled }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/screens/android", b);
    await toAddress(user);
    await lan(user, "192.168.1.53");
    await user.click(screen.getByRole("button", { name: "Lancer l'installation" }));
    await user.click(await screen.findByRole("button", { name: "Réinstaller l'app" }));
    expect(await screen.findByText("À faire sur la TV avant de continuer. Comptez quelques minutes.")).toBeInTheDocument();
  });
});

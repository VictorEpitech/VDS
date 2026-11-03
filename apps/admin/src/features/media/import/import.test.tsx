import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mockApi, renderRoutes } from "../../../../test/render";
import { routes } from "../../../app/routes";
import { warningsFor } from "./inspect";

/** XMLHttpRequest simulé : le nom du fichier décide de la réponse (le serveur garde le dernier mot, ici simulé). */
class FakeXhr {
  static sent: string[] = [];
  static active = 0;
  static peak = 0;
  status = 0;
  responseText = "";
  withCredentials = false;
  upload: Record<string, unknown> = {};
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  open() {}
  getResponseHeader() {
    return null;
  }
  send(form: FormData) {
    const file = form.get("file") as File;
    FakeXhr.sent.push(file.name);
    FakeXhr.active += 1;
    FakeXhr.peak = Math.max(FakeXhr.peak, FakeXhr.active);
    setTimeout(() => {
      FakeXhr.active -= 1;
      if (file.name.endsWith(".txt")) {
        this.status = 415;
        this.responseText = JSON.stringify({ code: "media.type_invalid", params: {} });
      } else if (file.name.startsWith("big")) {
        this.status = 413;
        this.responseText = JSON.stringify({ code: "media.too_large", params: { limit: 209715200, size: 503316480 } });
      } else {
        this.status = 201;
        this.responseText = JSON.stringify({ id: `m-${file.name}`, kind: "image", title: file.name });
      }
      this.onload?.();
    }, 5);
  }
}

const png = (name: string) => new File(["x"], name, { type: "image/png" });
const setup = () => userEvent.setup({ applyAccept: false });

beforeEach(() => {
  FakeXhr.sent = [];
  FakeXhr.active = 0;
  FakeXhr.peak = 0;
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
});
afterEach(() => vi.unstubAllGlobals());

describe("avertissements d'import (F3)", () => {
  it("1080x1920 avec cible paysage : ratio_mismatch, sans résolution basse", () => {
    expect(warningsFor(1080, 1920, "landscape")).toEqual(["ratio_mismatch"]);
    expect(warningsFor(1920, 1080, "landscape")).toEqual([]);
    expect(warningsFor(640, 480)).toEqual(["low_resolution"]);
    expect(warningsFor(1000, 1000, "portrait")).toEqual(["low_resolution"]);
  });
});

describe.each(["", "/espace/affichage"])("assistant d'import, base « %s »", (b) => {
  const open = async (extra: Record<string, unknown> = {}) => {
    const calls = mockApi({ "GET /playlists": { items: [{ id: "pl1", name: "Accueil" }], total: 1 }, ...extra }, b);
    const user = setup();
    await renderRoutes(routes, "/content/media/add", b);
    await screen.findByRole("heading", { name: "Choisissez vos fichiers" });
    return { calls, user };
  };
  const pick = async (user: ReturnType<typeof setup>, files: File[]) => user.upload(screen.getByLabelText("Fichiers à envoyer"), files);

  it("3 fichiers dont 1 refusé : 2 envoyés, 1 erreur claire avec Retirer ; 2 envois en parallèle au plus", async () => {
    const { user } = await open();
    await pick(user, [png("a.png"), png("b.png"), new File(["x"], "note.txt", { type: "text/plain" })]);
    await user.click(screen.getByRole("button", { name: "Envoyer 3 fichiers" }));
    expect(await screen.findByText("Ce type de fichier n'est pas accepté.")).toBeInTheDocument();
    expect(screen.getAllByText("Envoyé")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Retirer note.txt" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Réessayer note.txt" })).not.toBeInTheDocument();
    expect(FakeXhr.peak).toBeLessThanOrEqual(2);
    await user.click(screen.getByRole("button", { name: "Retirer note.txt" }));
    expect(screen.queryByText("note.txt")).not.toBeInTheDocument();
  });

  it("fichier trop lourd : taille et limite en Mo", async () => {
    const { user } = await open();
    await pick(user, [png("big.png")]);
    await user.click(screen.getByRole("button", { name: "Envoyer 1 fichier" }));
    expect(await screen.findByText(/Fichier trop lourd : 480 Mo, la limite est de 200 Mo/)).toBeInTheDocument();
  });

  it("avertissement d'orientation sans blocage de l'envoi", async () => {
    vi.stubGlobal("createImageBitmap", () => Promise.resolve({ width: 1080, height: 1920, close() {} }));
    const { user } = await open();
    await user.selectOptions(screen.getByLabelText("Écrans visés"), "landscape");
    await pick(user, [png("portrait.png")]);
    expect(await screen.findByText("Orientation différente des écrans visés")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Envoyer 1 fichier" })).toBeEnabled();
  });

  it("ajout à une playlist existante dans l'ordre d'envoi", async () => {
    const { calls, user } = await open({ "POST /playlists/pl1/items": { id: "i" } });
    await pick(user, [png("a.png"), png("b.png")]);
    await user.click(screen.getByRole("button", { name: "Envoyer 2 fichiers" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Continuer" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Continuer" }));
    await user.click(screen.getByRole("radio", { name: "Dans une playlist existante" }));
    await user.selectOptions(await screen.findByLabelText("Playlist"), "pl1");
    await user.click(screen.getByRole("button", { name: "Ajouter à la playlist" }));
    expect(await screen.findByText(/2 éléments ajoutés à la playlist « Accueil »/)).toBeInTheDocument();
    const items = calls.filter((c) => c.method === "POST" && c.path === "/playlists/pl1/items");
    expect(items.map((c) => (c.body as { props: { media: string } }).props.media)).toEqual(["m-a.png", "m-b.png"]);
    expect((items[0]!.body as { content_type: string }).content_type).toBe("content.image");
  });

  it("nouvelle playlist avec un nom par défaut", async () => {
    const { calls, user } = await open({ "POST /playlists": { id: "pl9", name: "Import" }, "POST /playlists/pl9/items": { id: "i" } });
    await pick(user, [png("a.png")]);
    await user.click(screen.getByRole("button", { name: "Envoyer 1 fichier" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Continuer" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Continuer" }));
    expect(screen.getByLabelText("Nom de la playlist")).toHaveDisplayValue(/^Import du /);
    await user.click(screen.getByRole("button", { name: "Ajouter à la playlist" }));
    const link = await screen.findByRole("link", { name: "Ouvrir la playlist" });
    expect(link).toHaveAttribute("href", `${b}/content/playlists/pl9`);
    const created = calls.find((c) => c.method === "POST" && c.path === "/playlists");
    expect((created!.body as { name: string }).name).toMatch(/^Import du /);
    expect(within(link.closest("section")!).getByRole("heading", { name: "Import terminé" })).toBeInTheDocument();
  });
});

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { errorResponse, mockApi, renderElement, renderRoutes } from "../../test/render";
import { routes } from "../app/routes";
import { contentTypesFixture } from "../../test/content-types";
import { useContentTypes } from "../lib/content-types";
import { ALL_DAYS, dayRanges, hasDay, ruleProblem, toggleDay } from "./schedule/rules";
import { initialValues, toProps, validate, type Values } from "./schema-form/schema";
import { SchemaForm } from "./schema-form/SchemaForm";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };
const GROUP = { id: "20000000-0000-4000-8000-000000000001", site_id: SITE.id, name: "Accueil" };
const DEVICE = {
  id: "60000000-0000-4000-8000-000000000001",
  platform: "android",
  model: "Tab A9",
  app_version: "1.2.0",
  presence: "online",
  paired_at: "2026-10-01T08:00:00Z",
  last_seen_at: "2026-10-23T09:30:00Z",
  revoked_at: null,
  heartbeat: null,
};
const SCREEN = { id: "30000000-0000-4000-8000-000000000001", site_id: SITE.id, group_id: GROUP.id, name: "Hall", orientation: "landscape", archived_at: null, device: DEVICE };
const itemSchema = (id: string) => contentTypesFixture().find((c) => c.id === id)!.item_schema as never;
const IMG = { id: "40000000-0000-4000-8000-000000000001", title: "Affiche", kind: "image", mime: "image/png", bytes: 2048, status: "ready", error: null };
const PL = { id: "50000000-0000-4000-8000-000000000001", name: "Accueil boucle", description: null, item_count: 0, created_at: "", updated_at: "" };
const base = { "GET /sites": [SITE], [`GET /sites/${SITE.id}/groups`]: [GROUP], "GET /screens": [SCREEN] };

function Harness({ type }: { type: string }) {
  const { byId, query } = useContentTypes();
  const ct = byId(type);
  return ct ? <Form type={ct} /> : <p>{query.isError ? "erreur" : "chargement"}</p>;
}

function Form({ type: ct }: { type: NonNullable<ReturnType<ReturnType<typeof useContentTypes>["byId"]>> }) {
  const [v, setV] = useState<Values>(() => initialValues(ct.schema));
  const [errs, setErrs] = useState(validate(ct.schema, v));
  return (
    <SchemaForm
      schema={ct.schema}
      ns={ct.id}
      value={v}
      onChange={(n) => {
        setV(n);
        setErrs(validate(ct.schema, n));
      }}
      errors={errs}
    />
  );
}

describe("types de contenu et formulaire généré", () => {
  it.each(["", "/espace/affichage"])("lit les types depuis le registre de l'API sous la base « %s »", async (b) => {
    mockApi({ "GET /media": [] }, b);
    await renderRoutes(routes, "/media", b);
    expect(await screen.findByText(/Types acceptés.*Image.*PDF.*Page web.*Vidéo/s)).toBeInTheDocument();
    const urls = vi.mocked(fetch).mock.calls.map((c) => String(c[0]));
    expect(urls).toContain(`${b}/api/v1/registry/content-types`);
    expect(contentTypesFixture().map((c) => c.id)).toEqual(["content.image", "content.pdf", "content.url", "content.video"]);
  });

  it("champs simples visibles, champs avancés dans le volet « Avancé » (FR)", async () => {
    mockApi({});
    await renderElement(<Harness type="content.url" />);
    const url = await screen.findByLabelText(/Adresse/);
    expect(url).toBeVisible();
    const details = screen.getByText("Avancé").closest("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByLabelText(/Rechargement/)).toBeInTheDocument();
    await userEvent.type(url, "http://x");
    expect(await screen.findByText("Le format de cette valeur n'est pas valide.")).toBeInTheDocument();
  });

  it("libellés du module en anglais", async () => {
    mockApi({});
    await renderElement(<Harness type="content.video" />, "en");
    expect(await screen.findByText("Advanced")).toBeInTheDocument();
    expect(screen.getByLabelText(/Video/)).toBeInTheDocument();
  });

  it("validation : obligatoire, bornes, props sans valeur vide", () => {
    const pdf = itemSchema("content.pdf");
    expect(validate(pdf, {}).media?.key).toBe("required");
    expect(validate(pdf, { media: "m", page_duration_ms: 10 }).page_duration_ms?.key).toBe("range");
    expect(toProps(pdf, { media: "m", page_duration_ms: "" })).toEqual({ media: "m" });
  });
});

describe("règles hebdomadaires", () => {
  it("masque lundi = 1 ... dimanche = 64", () => {
    expect(toggleDay(0, 0)).toBe(1);
    expect(toggleDay(0, 6)).toBe(64);
    expect(hasDay(ALL_DAYS, 3)).toBe(true);
    expect(dayRanges(31)).toEqual([[0, 4]]);
    expect(dayRanges(1 | 4 | 8)).toEqual([
      [0, 0],
      [2, 3],
    ]);
  });
  it("une règle sans jour ou à l'envers est refusée", () => {
    expect(ruleProblem({ weekdays: 0, start: "08:00", end: "18:00" })).toBe("days");
    expect(ruleProblem({ weekdays: 1, start: "18:00", end: "08:00" })).toBe("range");
    expect(ruleProblem({ weekdays: 1, start: "08:00", end: "24:00" })).toBeNull();
  });
});

describe.each(["", "/espace/affichage"])("écrans sous la base « %s »", (b) => {
  it("appairage : code normalisé, écran choisi, POST sous la base", async () => {
    const calls = mockApi({ ...base, "POST /pairings/claim": { screen_id: SCREEN.id } }, b);
    await renderRoutes(routes, "/pairing", b);
    await userEvent.type(await screen.findByLabelText("Code affiché sur l'écran"), "ab3 k7p");
    await screen.findByRole("option", { name: "Hall" });
    await userEvent.selectOptions(screen.getByLabelText("Écran à associer"), SCREEN.id);
    await userEvent.click(screen.getByRole("button", { name: "Appairer" }));
    expect(await screen.findByText(/Appairage validé pour l'écran Hall/)).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/pairings/claim")?.body).toEqual({ code: "AB3K7P", screen_id: SCREEN.id });
    expect(vi.mocked(fetch).mock.calls.every(([u]) => String(u).startsWith(`${b}/api/v1/`))).toBe(true);
  });

  it("appairage : champs obligatoires signalés, code inconnu traduit", async () => {
    mockApi({ ...base, "POST /pairings/claim": () => errorResponse(404, "errors.pairing_code_invalid") }, b);
    await renderRoutes(routes, `/pairing?screen=${SCREEN.id}`, b);
    await screen.findByRole("option", { name: "Hall" });
    await userEvent.click(screen.getByRole("button", { name: "Appairer" }));
    expect(screen.getAllByText("Ce champ est obligatoire.")).toHaveLength(1);
    await userEvent.type(screen.getByLabelText("Code affiché sur l'écran"), "XXXXXX");
    await userEvent.click(screen.getByRole("button", { name: "Appairer" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Code inconnu");
  });

  it("état de l'appareil : présence, plateforme, dernière activité ; révocation seulement si actif", async () => {
    const away = { ...SCREEN, id: "30000000-0000-4000-8000-000000000002", name: "Totem", device: null };
    const gone = { ...SCREEN, id: "30000000-0000-4000-8000-000000000003", name: "Vestiaire", device: { ...DEVICE, presence: "revoked", revoked_at: "2026-10-22T10:00:00Z" } };
    const fresh = { ...SCREEN, id: "30000000-0000-4000-8000-000000000004", name: "Couloir", device: { ...DEVICE, presence: "unseen", last_seen_at: null, model: null, app_version: null } };
    mockApi({ ...base, "GET /screens": [SCREEN, away, gone, fresh] }, b);
    await renderRoutes(routes, "/screens", b);
    const card = (name: string) => screen.getByRole("heading", { name }).closest("li")!;
    await screen.findByRole("heading", { name: "Hall" });
    expect(within(card("Hall")).getByText("Appareil en ligne")).toBeInTheDocument();
    expect(within(card("Hall")).getByText("android Tab A9")).toBeInTheDocument();
    expect(within(card("Hall")).getByRole("button", { name: "Révoquer l'appareil" })).toBeInTheDocument();
    expect(within(card("Totem")).getByText("Aucun appareil appairé.")).toBeInTheDocument();
    expect(within(card("Totem")).queryByRole("button", { name: "Révoquer l'appareil" })).toBeNull();
    expect(within(card("Vestiaire")).getByText("Appareil révoqué")).toBeInTheDocument();
    expect(within(card("Vestiaire")).queryByRole("button", { name: "Révoquer l'appareil" })).toBeNull();
    expect(within(card("Couloir")).getByText("Appairé, jamais connecté")).toBeInTheDocument();
    expect(within(card("Couloir")).getByText("Jamais")).toBeInTheDocument();
    expect(within(card("Couloir")).getByRole("button", { name: "Révoquer l'appareil" })).toBeInTheDocument();
  });

  it("état de l'appareil en anglais", async () => {
    mockApi({ ...base }, b);
    await renderRoutes(routes, "/screens", b, "en");
    expect(await screen.findByText("Device online")).toBeInTheDocument();
    expect(screen.getByText("Last seen")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revoke device" })).toBeInTheDocument();
  });

  it("révocation de l'appareil après confirmation", async () => {
    const calls = mockApi({ ...base, [`DELETE /screens/${SCREEN.id}/device`]: null }, b);
    await renderRoutes(routes, "/screens", b);
    await userEvent.click(await screen.findByRole("button", { name: "Révoquer l'appareil" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Révoquer l'appareil de Hall" }));
    expect(await screen.findByText(/appareil de l'écran Hall est révoqué/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "DELETE" && c.path === `/screens/${SCREEN.id}/device`)).toBe(true);
  });

  it("médiathèque : statuts avec icône et libellé, suppression refusée (409) avec les playlists", async () => {
    const failed = { ...IMG, id: "40000000-0000-4000-8000-000000000002", title: "Cassé", status: "failed", error: "media.type_mismatch" };
    mockApi(
      {
        "GET /media": [IMG, failed],
        "GET /playlists": [PL],
        [`DELETE /media/${IMG.id}`]: () => errorResponse(409, "media.in_use", { playlists: [PL.id] }),
      },
      b,
    );
    await renderRoutes(routes, "/media", b);
    expect(await screen.findByText("Prêt")).toBeInTheDocument();
    expect(screen.getByText("Échec")).toBeInTheDocument();
    expect(screen.getByText("Le contenu du fichier ne correspond pas à son type.")).toBeInTheDocument();
    const row = screen.getByRole("heading", { name: "Affiche" }).closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: "Supprimer" }));
    await userEvent.click(within(row).getByRole("button", { name: "Supprimer Affiche" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Ce média est utilisé et ne peut pas être supprimé.");
    expect(await within(alert).findByText("Accueil boucle")).toBeInTheDocument();
  });

  it("médiathèque : envoi avec progression puis confirmation", async () => {
    const posts: { url: string; file: string }[] = [];
    class FakeXhr {
      upload: { onprogress: ((e: ProgressEvent) => void) | null } = { onprogress: null };
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      status = 201;
      responseText = JSON.stringify(IMG);
      private url = "";
      withCredentials = false;
      open(_m: string, url: string) {
        this.url = url;
      }
      getResponseHeader() {
        return null;
      }
      send(form: FormData) {
        posts.push({ url: this.url, file: (form.get("file") as File).name });
        setTimeout(() => {
          this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 } as ProgressEvent);
          setTimeout(() => this.onload?.(), 150);
        }, 5);
      }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    mockApi({ "GET /media": [], "GET /playlists": [] }, b);
    await renderRoutes(routes, "/media", b);
    const input = await screen.findByLabelText("Fichiers à envoyer");
    await userEvent.upload(input, new File(["x"], "affiche.png", { type: "image/png" }), { applyAccept: false });
    const bar = await screen.findByRole("progressbar", { name: "Envoi de affiche.png" });
    await waitFor(() => expect(bar).toHaveValue(50));
    expect(await screen.findByText("Envoyé")).toBeInTheDocument();
    expect(posts).toEqual([{ url: `${b}/api/v1/media`, file: "affiche.png" }]);
  });

  it("playlist : l'ordre se change avec les boutons et part en PUT", async () => {
    const items = ["A", "B"].map((n, i) => ({
      id: `60000000-0000-4000-8000-00000000000${i}`,
      content_type: "content.url",
      duration_ms: 5000,
      enabled: true,
      position: i,
      props: { url: `https://${n}.test` },
      media_ids: [],
    }));
    const detail = { ...PL, item_count: 2, items };
    const calls = mockApi({ [`GET /playlists/${PL.id}`]: detail, "GET /media": [], [`PUT /playlists/${PL.id}/items/order`]: detail }, b);
    await renderRoutes(routes, `/playlists/${PL.id}`, b);
    await screen.findByText("Page web - https://A.test");
    expect(screen.getByRole("button", { name: "Monter Page web - https://A.test" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Descendre Page web - https://A.test" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ item_ids: [items[1]!.id, items[0]!.id] }));
  });

  it("playlist : ajout d'un élément avec le formulaire généré (média, durée)", async () => {
    const calls = mockApi({ [`GET /playlists/${PL.id}`]: { ...PL, items: [] }, "GET /media": [IMG], [`POST /playlists/${PL.id}/items`]: {} }, b);
    await renderRoutes(routes, `/playlists/${PL.id}`, b);
    await userEvent.click(await screen.findByRole("button", { name: "Ajouter un élément" }));
    await userEvent.click(screen.getByRole("button", { name: "Ajouter" }));
    expect(await screen.findByText("Ce champ est obligatoire.")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText(/^Image/), IMG.id);
    await userEvent.type(screen.getByLabelText("Durée d'affichage (secondes)"), "12");
    await userEvent.click(screen.getByRole("button", { name: "Ajouter" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST")?.body).toEqual({
        content_type: "content.image",
        props: { media: IMG.id, fit: "contain" },
        duration_ms: 12000,
      }),
    );
  });

  it("programmation : cible, playlist, créneau hebdomadaire et priorité envoyés", async () => {
    const calls = mockApi({ ...base, "GET /schedules": [], "GET /playlists": [PL], "POST /schedules": {} }, b);
    await renderRoutes(routes, "/schedules", b);
    await userEvent.click(await screen.findByRole("button", { name: "Nouvelle programmation" }));
    await userEvent.type(screen.getByLabelText("Nom"), "Semaine");
    await userEvent.click(screen.getByRole("radio", { name: "Groupe" }));
    await screen.findByRole("option", { name: "Accueil" });
    await userEvent.selectOptions(screen.getByLabelText("Groupe", { selector: "select" }), GROUP.id);
    expect(await screen.findByText("1 écran touché")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Playlist"), PL.id);
    await userEvent.click(screen.getByRole("button", { name: "Ajouter un créneau" }));
    await userEvent.click(screen.getByRole("button", { name: "samedi" }));
    await userEvent.click(screen.getByText("Avancé"));
    await userEvent.clear(screen.getByLabelText("Priorité"));
    await userEvent.type(screen.getByLabelText("Priorité"), "5");
    await userEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")).toBeTruthy());
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      name: "Semaine",
      target_type: "group",
      target_id: GROUP.id,
      playlist_id: PL.id,
      priority: 5,
      rules: [{ weekdays: 31 | 32, start: "08:00", end: "18:00" }],
      starts_on: null,
      ends_on: null,
      enabled: true,
    });
  });

  it("programmation : créneau à l'envers refusé avant l'envoi", async () => {
    const calls = mockApi({ ...base, "GET /schedules": [], "GET /playlists": [PL], "POST /schedules": {} }, b);
    await renderRoutes(routes, "/schedules", b);
    await userEvent.click(await screen.findByRole("button", { name: "Nouvelle programmation" }));
    await userEvent.type(screen.getByLabelText("Nom"), "X");
    await screen.findByRole("option", { name: "Hall" });
    await userEvent.selectOptions(screen.getByLabelText("Écran", { selector: "select" }), SCREEN.id);
    await userEvent.selectOptions(screen.getByLabelText("Playlist"), PL.id);
    await userEvent.click(screen.getByRole("button", { name: "Ajouter un créneau" }));
    const end = screen.getByLabelText("À");
    await userEvent.clear(end);
    await userEvent.type(end, "07:00");
    await userEvent.click(screen.getByRole("button", { name: "Enregistrer" }));
    expect(await screen.findByText("L'heure de fin doit être après l'heure de début.")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });
});

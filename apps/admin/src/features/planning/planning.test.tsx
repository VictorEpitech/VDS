import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { mockApi, renderElement, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";
import { PriorityPicker } from "./PriorityPicker";
import { addDays, browserTimeZone, dayBounds, localInstantToUtc, utcToLocalInstant, weekStart, zonedToUtc } from "./time";
import { nextCell } from "./gridNav";
import { useState } from "react";

const SITE = { id: "10000000-0000-4000-8000-000000000001", name: "Moulins", slug: "moulins", archived_at: null };
const GROUP = { id: "20000000-0000-4000-8000-000000000001", site_id: SITE.id, name: "Accueil" };
const SCREEN = { id: "30000000-0000-4000-8000-000000000001", site_id: SITE.id, group_id: GROUP.id, name: "Hall", orientation: "landscape", archived_at: null, device: null };
const PL_A = "50000000-0000-4000-8000-00000000000a";
const SCH_A = "60000000-0000-4000-8000-00000000000a";
const SCH_B = "60000000-0000-4000-8000-00000000000b";
const TZ = "Europe/Paris";

const srcA = { kind: "schedule", id: SCH_A, name: "Accueil", target_type: "site", target_id: SITE.id, target_name: "Moulins", inherited: true, priority: 0, priority_level: "normal" };
const srcB = { kind: "schedule", id: SCH_B, name: "Promo", target_type: "screen", target_id: SCREEN.id, target_name: "Hall", inherited: false, priority: 120, priority_level: "high" };
const none = { kind: "none", id: null, name: null, target_type: null, target_id: null, target_name: null, inherited: false, priority: null, priority_level: null };
const contentA = { kind: "playlist", id: PL_A, name: "Boucle accueil" };
const contentB = { kind: "playlist", id: "50000000-0000-4000-8000-00000000000b", name: "Boucle promo" };

/** Lundi 2 novembre 2026, heure de Paris (UTC+1). */
const at = (h: number, m = 0) => new Date(Date.UTC(2026, 10, 2, h - 1, m)).toISOString();
const seg = (s: number, e: number, state: string, content: unknown, source: unknown, reason: string | null, losers: unknown[] = []) => ({ start: at(s), end: at(e), state, content, source, reason, losers });
const SEGMENTS = [
  seg(0, 8, "off", null, none, "no_candidate"),
  seg(8, 12, "play", contentA, srcA, "only_candidate"),
  seg(12, 14, "play", contentB, srcB, "higher_priority", [{ source: srcA, content: contentA, reason: "higher_priority" }]),
  seg(14, 24, "scheduled_off", null, { ...none, kind: "power_plan" }, "scheduled_off"),
];
const timeline = (tz = TZ) => ({
  format: 1,
  target_type: "screen",
  target_id: SCREEN.id,
  window: [at(0), at(24)],
  timezone: tz,
  mixed_timezones: false,
  lanes: [
    { source: srcB, content: contentB, occurrences: [[at(12), at(14)]], effective_ratio: 1 },
    { source: srcA, content: contentA, occurrences: [[at(8), at(14)]], effective_ratio: 0.66 },
  ],
  screens: [
    {
      screen_id: SCREEN.id,
      screen_name: "Hall",
      timezone: tz,
      segments: SEGMENTS,
      markers: [{ at: at(16), kind: "playlist_expires", ref_id: PL_A, name: "Boucle accueil", impact: "gap" }],
      conflicts: [
        { kind: "partially_masked", severity: "info", source: srcA, other: srcB, first_at: at(12), occurrences: 1 },
        { kind: "content_expires", severity: "warning", source: srcA, other: null, first_at: at(16), occurrences: 1 },
      ],
    },
  ],
  next_cursor: null,
  computed_at: at(9),
});
const atOut = { at: at(12, 30), next_cursor: null, screens: [{ screen_id: SCREEN.id, screen_name: "Hall", segment: SEGMENTS[2], next_change_at: at(14), presence: "online" }] };

const api = (tz = TZ) => ({
  "GET /sites": [SITE],
  [`GET /sites/${SITE.id}/groups`]: [GROUP],
  "GET /screens": [SCREEN],
  "GET /schedules": [],
  "GET /overrides": [],
  "GET /playlists": { items: [], total: 0 },
  "GET /planning/timeline": timeline(tz),
  "GET /planning/at": atOut,
});

const WEEK = "/playback?target=screen:" + SCREEN.id + "&date=2026-11-02";

describe.each(["", "/espace/affichage"])("planning, base « %s »", (b) => {
  it("vue Liste : segments, niveaux (texte + valeur) et états ; URL sous la base", async () => {
    const calls = mockApi(api(), b);
    await renderRoutes(routes, `${WEEK}&view=list`, b);
    const table = await screen.findByRole("table", { name: "Ce qui joue sur Hall" });
    expect(within(table).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Début", "Fin", "Contenu", "Provenance", "Niveau", "État"]);
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(5);
    expect(within(rows[3]!).getByText("Boucle promo")).toBeInTheDocument();
    expect(within(rows[3]!).getByText("Prioritaire")).toBeInTheDocument();
    expect(within(rows[3]!).getByText("· 120")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("Normale")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("Hérité : site Moulins")).toBeInTheDocument();
    expect(within(rows[4]!).getAllByText("Hors service prévu").length).toBeGreaterThan(0);
    expect(within(rows[1]!).getAllByText("Rien à diffuser").length).toBeGreaterThan(0);
    const tl = calls.find((c) => c.path.startsWith("/planning/timeline"));
    expect(tl?.path).toBe(`/planning/timeline?target_type=screen&target_id=${SCREEN.id}&from=2026-11-02&to=2026-11-08&explain=true`);
    expect((globalThis.fetch as unknown as { mock: { calls: string[][] } }).mock.calls.some((c) => String(c[0]).startsWith(`${b}/api/v1/planning/timeline`))).toBe(true);
  });

  it("inspecteur : gagnant, chaîne d'explication, raison et règle de spécificité", async () => {
    const calls = mockApi(api(), b);
    await renderRoutes(routes, `${WEEK}&view=list`, b);
    await userEvent.click(await screen.findByRole("button", { name: /Voir ce qui joue le .*: Boucle promo/ }));
    const panel = await screen.findByRole("complementary", { name: /Ce qui joue le/ });
    expect(await within(panel).findByText("Accueil (site Moulins, Normale) est masqué par Promo (écran Hall, Prioritaire) : Priorité plus haute.")).toBeInTheDocument();
    expect(within(panel).getByText("Priorité plus haute.")).toBeInTheDocument();
    expect(within(panel).getByText(/La cible la plus précise passe avant la priorité/)).toBeInTheDocument();
    expect(within(panel).getByText("Défini ici")).toBeInTheDocument();
    expect(calls.find((c) => c.path.startsWith("/planning/at"))?.path).toContain(`target_id=${SCREEN.id}`);
    expect(calls.find((c) => c.path.startsWith("/planning/at"))?.path).toContain(encodeURIComponent("2026-11-02T11:00:00.000Z"));
    await userEvent.click(within(panel).getByRole("button", { name: "Fermer" }));
    expect(screen.queryByRole("complementary", { name: /Ce qui joue le/ })).toBeNull();
  });

  it("grille semaine : pistes Effectif + une par source, clavier (flèches, Entrée), repère d'expiration sous la base", async () => {
    mockApi(api(), b);
    await renderRoutes(routes, `${WEEK}&view=week`, b);
    const grids = await screen.findAllByRole("grid");
    expect(grids).toHaveLength(7);
    const monday = grids[0]!;
    expect(within(monday).getAllByRole("rowheader").map((r) => r.textContent?.split("Prioritaire")[0]?.split("Normale")[0])).toEqual(["Effectif", "Promo", "Accueil"].map((x) => expect.stringContaining(x)));
    const cells = within(monday).getAllByRole("button");
    expect(cells.filter((c) => c.tabIndex === 0)).toHaveLength(1);
    cells[0]!.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(cells[1]);
    expect(cells[1]!.getAttribute("aria-label")).toMatch(/Boucle accueil.*Accueil \(site Moulins, Normale\), Hérité/);
    await userEvent.keyboard("{ArrowDown}");
    expect(within(monday).getByRole("rowheader", { name: /Promo/ }).closest("[role=row]")?.contains(document.activeElement)).toBe(true);
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByRole("complementary", { name: /Ce qui joue le/ })).toBeInTheDocument();
    const expiry = screen.getByRole("link", { name: /Playlist « Boucle accueil » expire à .*trou de diffusion/ });
    expect(expiry.getAttribute("href")).toBe(`${b}/content/playlists/${PL_A}`);
  });

  it("conflits : avertissement d'abord, une action par entrée", async () => {
    mockApi(api(), b);
    await renderRoutes(routes, `${WEEK}&view=list`, b);
    const panel = await screen.findByRole("region", { name: /point/ });
    const items = within(panel).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("À vérifier");
    expect(within(items[0]!).getByRole("link", { name: "Prolonger l'expiration de la playlist" }).getAttribute("href")).toBe(`${b}/content/playlists`);
    expect(within(items[1]!).getByRole("button", { name: "Modifier la priorité" })).toBeInTheDocument();
  });
});

describe("planning : fuseau et langues", () => {
  it("mentionne le fuseau du site s'il diffère du navigateur, sinon rien", async () => {
    mockApi(api("Pacific/Auckland"));
    await renderRoutes(routes, `${WEEK}&view=list`, "");
    expect(await screen.findByText(/Heures affichées dans le fuseau du site \(Pacific\/Auckland\)/)).toBeInTheDocument();
  });

  it("aucune mention quand le fuseau est celui du navigateur", async () => {
    mockApi(api(browserTimeZone()));
    await renderRoutes(routes, `${WEEK}&view=list`, "");
    await screen.findByRole("table");
    expect(screen.queryByText(/Heures affichées dans le fuseau/)).toBeNull();
  });

  it("EN : raisons, niveaux, conflits et grille traduits", async () => {
    mockApi(api());
    await renderRoutes(routes, `${WEEK}&view=list`, "", "en");
    await userEvent.click(await screen.findByRole("button", { name: /See what plays on .*: Boucle promo/ }));
    expect(await screen.findByText("Accueil (site Moulins, Normal) is masked by Promo (screen Hall, High): Higher priority.")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Source" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Extend the playlist expiry" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Week" })).toBeInTheDocument();
  });

  it("sous 600 px la vue Liste est la vue par défaut", async () => {
    const original = window.matchMedia;
    window.matchMedia = ((q: string) => ({ matches: q.includes("max-width"), media: q, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
    try {
      mockApi(api());
      await renderRoutes(routes, `/playback?target=screen:${SCREEN.id}&date=2026-11-02`, "");
      expect(await screen.findByRole("table")).toBeInTheDocument();
      expect(screen.queryByRole("grid")).toBeNull();
    } finally {
      window.matchMedia = original;
    }
  });
});

describe("PriorityPicker", () => {
  function Harness({ initial = "0", savedLevel, savedValue }: { initial?: string; savedLevel?: "background" | "normal" | "high"; savedValue?: number }) {
    const [v, setV] = useState(initial);
    return (
      <>
        <PriorityPicker value={v} onChange={setV} savedLevel={savedLevel} savedValue={savedValue} />
        <output data-testid="v">{v}</output>
      </>
    );
  }

  it("3 niveaux : un choix envoie la valeur par défaut du niveau", async () => {
    await renderElement(<Harness />);
    expect(screen.getAllByRole("radio").map((r) => (r as HTMLInputElement).labels?.[0]?.textContent)).toEqual(["Fond", "Normale", "Prioritaire"]);
    expect(screen.getByRole("radio", { name: "Normale" })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: "Prioritaire" }));
    expect(screen.getByTestId("v")).toHaveTextContent("100");
    await userEvent.click(screen.getByRole("radio", { name: "Fond" }));
    expect(screen.getByTestId("v")).toHaveTextContent("-100");
  });

  it("valeur personnalisée : aucun niveau coché tant que le serveur n'a pas calculé la bande", async () => {
    await renderElement(<Harness initial="120" />);
    expect(screen.getAllByRole("radio").every((r) => !(r as HTMLInputElement).checked)).toBe(true);
    expect(screen.getByText(/Valeur personnalisée : 120/)).toBeInTheDocument();
  });

  it("valeur enregistrée : le niveau vient du serveur", async () => {
    await renderElement(<Harness initial="120" savedLevel="high" savedValue={120} />);
    expect(screen.getByRole("radio", { name: "Prioritaire" })).toBeChecked();
  });
});

describe("temps et clavier (présentation seulement)", () => {
  it("heures murales dans le fuseau du site, changements d'heure compris", () => {
    expect(zonedToUtc("2026-11-02", "08:00", TZ)).toBe(Date.UTC(2026, 10, 2, 7));
    const [lo, hi] = dayBounds("2026-10-25", TZ);
    expect((hi - lo) / 3_600_000).toBe(25);
    expect(utcToLocalInstant(Date.UTC(2026, 10, 2, 7), TZ)).toBe("2026-11-02T08:00");
    expect(localInstantToUtc("2026-11-02T08:00", TZ)).toBe(Date.UTC(2026, 10, 2, 7));
  });

  it("premier jour de la semaine : lundi (FR) ou dimanche (EN)", () => {
    expect(weekStart("2026-11-04", 1)).toBe("2026-11-02");
    expect(weekStart("2026-11-04", 0)).toBe("2026-11-01");
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
  });

  it("nextCell : flèches dans la piste, entre pistes, aux bords de jour", () => {
    const mk = (row: number, start: number) => {
      const el = document.createElement("button");
      el.dataset.cell = "";
      el.dataset.row = String(row);
      el.dataset.start = String(start);
      return el;
    };
    const [a, b, c, d] = [mk(0, 0), mk(0, 10), mk(1, 5), mk(2, 0)];
    const cells = [a, b, c, d];
    expect(nextCell(cells, a, "ArrowRight", 2)).toBe(b);
    expect(nextCell(cells, b, "ArrowRight", 2)).toBe(d);
    expect(nextCell(cells, a, "ArrowDown", 2)).toBe(c);
    expect(nextCell(cells, b, "ArrowDown", 2)).toBe(c);
    expect(nextCell(cells, c, "ArrowUp", 2)).toBe(a);
    expect(nextCell(cells, a, "End", 2)).toBe(b);
    expect(nextCell(cells, a, "ArrowLeft", 2)).toBeNull();
  });
});

describe("garde-fous", () => {
  const srcRoot = path.resolve(__dirname, "../..");
  const files: string[] = [];
  (function walk(dir: string) {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else if (/\.tsx?$/.test(e.name) && !/\.test\./.test(e.name)) files.push(p);
    }
  })(srcRoot);

  it("aucune fonction de résolution dans apps/admin : le serveur est l'unique résolveur", () => {
    const declared = /(?:\bfunction\s+|\b(?:const|let|var)\s+)(?:resolve|resolver|resolve[A-Z]\w*|arbitrat\w*|pickWinner|computeWinner|winnerOf|effectiveSegments?\w*|computeLosers|explainLocal\w*)\b/;
    const compares = /\.priority(?:_level)?\s*(?:>=|<=|>|<|===|!==)\s*(?!null|undefined)[\w.-]/;
    const offenders = files.flatMap((f) => fs.readFileSync(f, "utf8").split("\n").flatMap((line, i) => (declared.test(line) || compares.test(line) ? [`${path.relative(srcRoot, f)}:${i + 1}: ${line.trim()}`] : [])));
    expect(offenders).toEqual([]);
    expect(files.length).toBeGreaterThan(50);
  });

  it("aucun libellé en dur dans le dossier planning (aucun texte littéral dans le JSX)", () => {
    const dir = path.join(srcRoot, "features/planning");
    const offenders = fs
      .readdirSync(dir)
      .filter((f) => /\.tsx$/.test(f) && !/\.test\./.test(f))
      .flatMap((f) => fs.readFileSync(path.join(dir, f), "utf8").split("\n").flatMap((line, i) => (!line.includes("=>") && /[>]\s*[A-Za-zÀ-ÿ][^<>{}=]{2,}</.test(line) ? [`${f}:${i + 1}: ${line.trim()}`] : [])));
    expect(offenders).toEqual([]);
  });

  it("aucune ombre dans la feuille de style du planning", () => {
    const css = fs.readFileSync(path.join(srcRoot, "styles/app.css"), "utf8");
    expect(/box-shadow\s*:(?!\s*none)/.test(css) || /drop-shadow\(/.test(css)).toBe(false);
  });
});


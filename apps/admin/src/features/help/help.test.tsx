import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi, renderRoutes } from "../../../test/render";
import { routes } from "../../app/routes";
import { ToneProvider } from "../../lib/tone";
import { HelpButton } from "./HelpPanel";
import { Term } from "./Term";
import { GLOSSARY_KEYS, HELP_TOPICS } from "./topics";

const page = (node: React.ReactNode, tone: "neutral" | "formal" = "neutral") => renderRoutes([{ path: "*", element: <ToneProvider value={tone}>{node}</ToneProvider> }], "/");

describe("HelpButton / HelpPanel", () => {
  it("s'ouvre au clavier, se ferme avec Échap et rend le focus au bouton", async () => {
    mockApi({});
    const user = userEvent.setup();
    await page(<HelpButton topic="override" />);
    const button = await screen.findByRole("button", { name: "Aide : Forçage" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    button.focus();
    await user.keyboard("{Enter}");
    expect(button).toHaveAttribute("aria-expanded", "true");
    const heading = screen.getByRole("heading", { name: "Forçage" });
    expect(heading).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("heading", { name: "Forçage" })).not.toBeInTheDocument();
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button).toHaveFocus();
  });

  it("texte neutre avec un thème neutre, vouvoiement avec un thème formel", async () => {
    mockApi({});
    const user = userEvent.setup();
    const { unmount } = await page(<HelpButton topic="screens" />, "neutral");
    await user.click(await screen.findByRole("button", { name: "Aide : Écrans" }));
    expect(screen.getByText(/Un clic sur un écran ouvre/)).toBeInTheDocument();
    unmount();
    await page(<HelpButton topic="screens" />, "formal");
    await user.click(await screen.findByRole("button", { name: "Aide : Écrans" }));
    expect(screen.getByText(/Vous voyez son état/)).toBeInTheDocument();
  });
});

describe("Term (glossaire)", () => {
  it("définition reliée par aria-describedby, au focus clavier, au toucher, fermée par Échap", async () => {
    mockApi({});
    const user = userEvent.setup();
    await page(<p>Un <Term k="override">forçage</Term> est temporaire.</p>);
    const term = await screen.findByRole("button", { name: "forçage" });
    const tip = document.getElementById(term.getAttribute("aria-describedby")!)!;
    expect(tip).toHaveAttribute("role", "tooltip");
    expect(tip).toHaveAttribute("hidden");
    await user.tab();
    expect(term).toHaveFocus();
    expect(tip).not.toHaveAttribute("hidden");
    expect(tip).toHaveTextContent(/Remplace temporairement la programmation/);
    await user.keyboard("{Escape}");
    await user.click(document.body);
    expect(tip).toHaveAttribute("hidden");
    await user.click(term);
    expect(tip).not.toHaveAttribute("hidden");
  });
});

describe.each(["", "/espace/affichage"])("page Aide, base « %s »", (b) => {
  it("liste les sujets et le glossaire, et rouvre Premiers pas", async () => {
    const calls = mockApi({
      "GET /onboarding": { steps: [], card: { hidden: true }, complete: false, next_step: null, progress: { completed: 0, total: 0 } },
      "PUT /onboarding/card": { steps: [], card: { hidden: false }, complete: false, next_step: null, progress: { completed: 0, total: 0 } },
    }, b);
    const user = userEvent.setup();
    await renderRoutes(routes, "/help", b);
    expect(await screen.findByRole("heading", { level: 1, name: "Aide" })).toBeInTheDocument();
    expect(document.querySelectorAll("h3")).toHaveLength(HELP_TOPICS.length);
    expect(document.querySelectorAll("dt")).toHaveLength(GLOSSARY_KEYS.length);
    await user.click(await screen.findByRole("button", { name: "Afficher les Premiers pas" }));
    expect(calls.some((c) => c.method === "PUT" && c.path === "/onboarding/card" && (c.body as { hidden: boolean }).hidden === false)).toBe(true);
  });
});

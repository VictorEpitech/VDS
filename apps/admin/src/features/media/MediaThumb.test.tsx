import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RuntimeConfigProvider } from "../../lib/runtime-config";
import { testConfig } from "../../../test/render";
import { MediaThumb } from "./MediaThumb";

describe.each(["/", "/espace/affichage"])("MediaThumb sous la base « %s »", (b) => {
  const cfg = { ...testConfig(""), basePath: b };
  const wrap = (kind: string, thumb: string | null) =>
    render(
      <RuntimeConfigProvider config={cfg}>
        <MediaThumb media={{ kind, thumb } as never} />
      </RuntimeConfigProvider>,
    );

  it("image : URL de la vignette sans double slash initial", () => {
    const { container } = wrap("image", "media/x/thumb");
    const src = container.querySelector("img")!.getAttribute("src")!;
    expect(src).toBe(`${b === "/" ? "" : b}/media/x/thumb`);
    expect(src.startsWith("//")).toBe(false);
  });

  it.each(["video", "document"])("%s sans vignette : pictogramme du type", (kind) => {
    const { container } = wrap(kind, null);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(`.vds-icon[data-icon="${kind}"]`)).not.toBeNull();
  });
});

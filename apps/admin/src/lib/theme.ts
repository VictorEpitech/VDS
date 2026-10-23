/** Charge la feuille du thème actif (tokens, polices, icônes, logo) avant le premier rendu. */
export function loadStylesheet(url: string, doc: Document = document): Promise<void> {
  return new Promise((resolve) => {
    const link = doc.createElement("link");
    link.rel = "stylesheet";
    link.href = url;
    link.dataset.vdsTheme = "";
    // On ne bloque jamais l'interface si le thème est indisponible.
    link.addEventListener("load", () => resolve());
    link.addEventListener("error", () => resolve());
    doc.head.appendChild(link);
  });
}

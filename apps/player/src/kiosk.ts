/** Kiosque : aucune voie de sortie pour le public (clavier, souris, tactile, navigation, sélection). */
export function lockDown(): void {
  const block = (e: Event): void => {
    e.preventDefault();
    e.stopPropagation();
  };
  for (const type of ["contextmenu", "dragstart", "selectstart", "keydown", "keypress", "keyup", "wheel", "auxclick", "dblclick"]) {
    window.addEventListener(type, block, { capture: true, passive: false });
  }
  // Touche Retour / geste précédent : on reste sur la page.
  history.pushState(null, "", location.href);
  window.addEventListener("popstate", () => history.pushState(null, "", location.href));
  window.addEventListener("beforeunload", () => undefined);
}

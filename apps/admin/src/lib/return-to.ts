/**
 * `return_to` : chemin interne uniquement, sous le préfixe VDS_BASE_PATH (même règle que le serveur).
 * Retourne le chemin relatif au routeur (préfixe retiré) ou null si la valeur est refusée.
 */
export function safeReturnTo(basePath: string, raw: string | null | undefined): string | null {
  if (!raw || raw.length > 2048 || !raw.startsWith("/") || raw.startsWith("//")) return null;
  // eslint-disable-next-line no-control-regex
  if (/[\\\u0000-\u001f\u007f]/.test(raw)) return null;
  let url: URL;
  try {
    url = new URL(raw, "http://vds.invalid");
  } catch {
    return null;
  }
  if (url.origin !== "http://vds.invalid") return null;
  const { pathname } = url;
  if (basePath !== "" && pathname !== basePath && !pathname.startsWith(`${basePath}/`)) return null;
  const rel = `${pathname.slice(basePath.length) || "/"}${url.search}${url.hash}`;
  // Pas de boucle vers les écrans d'accès.
  return /^\/(login|bootstrap|change-password)(\/|\?|#|$)/.test(rel) ? "/" : rel;
}

/** Chemin complet (préfixe compris) à envoyer comme `return_to`. */
export function toReturnTo(basePath: string, relative: string): string {
  return `${basePath}${relative.startsWith("/") ? relative : `/${relative}`}`;
}

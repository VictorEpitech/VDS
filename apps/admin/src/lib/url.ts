/** Toutes les URL internes passent par la base de la config d'exécution (VDS_BASE_PATH). */
export function joinBase(basePath: string, path: string): string {
  const rel = path.startsWith("/") ? path : `/${path}`;
  return `${basePath}${rel}`;
}

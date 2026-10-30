import { Navigate, useLocation, useParams } from "react-router-dom";

/**
 * Redirection d'une ancienne URL vers la nouvelle (relative à la base du routeur, donc valable
 * sous `/` comme sous `/espace/affichage`). `:param` est repris de l'URL d'origine ; la requête
 * d'origine est conservée sauf si la cible en porte une.
 */
export function Redirect({ to }: { to: string }) {
  const params = useParams();
  const { search } = useLocation();
  const [path = "/", query] = to.replace(/:(\w+)/g, (_m, k: string) => params[k] ?? "").split("?");
  return <Navigate replace to={{ pathname: path, search: query ? `?${query}` : search }} />;
}

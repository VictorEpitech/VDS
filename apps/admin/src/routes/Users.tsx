import { useSession } from "../auth/session";
import { UserDetail } from "../features/users/UserDetail";
import { UsersPage } from "../features/users/UsersPage";
import { NotFound } from "./NotFound";

/** Réglages › Utilisateurs : réservé aux administrateurs (le serveur refuse les autres, la page ne se montre pas). */
export function Users({ detail = false }: { detail?: boolean }) {
  const session = useSession();
  if (session.isSuccess && session.data.role !== "admin") return <NotFound />;
  return detail ? <UserDetail /> : <UsersPage />;
}

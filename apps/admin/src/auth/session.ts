import { useQuery } from "@tanstack/react-query";
import type { Me } from "@vds/sdk-ts";
import { useApi } from "../lib/api";

export const SESSION_KEY = ["me"] as const;

/** Session courante : GET /me (401 = non connecté, 403 = refus du provider, par ex. changement de mot de passe requis). */
export function useSession() {
  const api = useApi();
  return useQuery({
    queryKey: SESSION_KEY,
    queryFn: () => api.get<Me>("/me"),
    retry: false,
    staleTime: 60_000,
  });
}

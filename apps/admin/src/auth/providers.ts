import { authLocalUi } from "../modules/auth-local";
import type { AuthUi } from "./types";

/**
 * Modules d'authentification qui fournissent leurs propres écrans dans l'admin.
 * Un provider absent d'ici (mode hébergé) délègue connexion et déconnexion aux URL de la config d'exécution.
 */
const REGISTRY: Record<string, AuthUi> = { "auth.local": authLocalUi };

export function authUiFor(provider: string): AuthUi | undefined {
  return REGISTRY[provider];
}

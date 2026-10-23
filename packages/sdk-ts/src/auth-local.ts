import type { components } from "./api.generated";

/**
 * Contrat HTTP du module auth.local (`<apiBase>/m/auth.local/...`).
 * Les routes de module sont montées dynamiquement et absentes de l'OpenAPI générée :
 * ces types reprennent les modèles Pydantic de modules/auth.local/server/module.py.
 */
export type Me = components["schemas"]["MeOut"];
export type Health = components["schemas"]["HealthOut"];

/** Corps d'erreur de l'API : code stable, l'admin le traduit. */
export interface ApiErrorBody {
  code: string;
  params: Record<string, unknown>;
}

export interface AuthLocalStatus {
  initialized: boolean;
}
export interface AuthLocalLoginIn {
  email: string;
  password: string;
  return_to?: string | null;
}
export interface AuthLocalBootstrapIn {
  email: string;
  display_name: string;
  password: string;
  bootstrap_token: string;
}
export interface AuthLocalPasswordIn {
  current: string;
  new: string;
}
export interface AuthLocalLoginOut {
  redirect_to: string;
}

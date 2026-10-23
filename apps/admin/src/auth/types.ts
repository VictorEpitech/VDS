import type { RuntimeConfig } from "@vds/sdk-ts";
import type { Api } from "../lib/api";

export interface AuthFormField {
  name: string;
  type: "email" | "text" | "password";
  autoComplete: string;
  /** Donnée technique (jeton) : police mono. */
  mono?: boolean;
}

/** Formulaire décrit par le module d'authentification ; libellés sous `<i18n>.{title,intro,submit,fields.<name>}`. */
export interface AuthFormSpec {
  i18n: string;
  /** Chemin sous `apiBase`. */
  endpoint: string;
  fields: AuthFormField[];
}

/** Écrans fournis par un module d'authentification qui porte son propre formulaire. */
export interface AuthUi {
  statusEndpoint: string;
  login: AuthFormSpec;
  bootstrap: AuthFormSpec;
  changePassword: AuthFormSpec;
  alreadyInitializedCode: string;
  passwordChangeRequiredCode: string;
  logout(config: RuntimeConfig, api: Api): Promise<void>;
}

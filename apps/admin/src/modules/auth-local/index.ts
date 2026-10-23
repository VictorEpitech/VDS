import type { AuthUi } from "../../auth/types";

const BASE = "/m/auth.local";

/** Écrans du module auth.local : formulaires, codes d'erreur et déconnexion (POST). */
export const authLocalUi: AuthUi = {
  statusEndpoint: `${BASE}/status`,
  login: {
    i18n: "auth.login",
    endpoint: `${BASE}/login`,
    fields: [
      { name: "email", type: "email", autoComplete: "username" },
      { name: "password", type: "password", autoComplete: "current-password" },
    ],
  },
  bootstrap: {
    i18n: "auth.bootstrap",
    endpoint: `${BASE}/bootstrap`,
    fields: [
      { name: "email", type: "email", autoComplete: "email" },
      { name: "display_name", type: "text", autoComplete: "name" },
      { name: "password", type: "password", autoComplete: "new-password" },
      { name: "bootstrap_token", type: "text", autoComplete: "off", mono: true },
    ],
  },
  changePassword: {
    i18n: "auth.change_password",
    endpoint: `${BASE}/password`,
    fields: [
      { name: "current", type: "password", autoComplete: "current-password" },
      { name: "new", type: "password", autoComplete: "new-password" },
    ],
  },
  alreadyInitializedCode: "auth_local.already_initialized",
  passwordChangeRequiredCode: "auth_local.password_change_required",
  async logout(config, api) {
    // L'URL de déconnexion vient de la config d'exécution (le provider la fournit) ; POST, pas un lien.
    const url = config.auth.logoutUrl;
    if (url) {
      await fetch(url, { method: "POST", credentials: "same-origin" });
      return;
    }
    await api.post(`${BASE}/logout`);
  },
};

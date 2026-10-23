import { useQueryClient } from "@tanstack/react-query";
import type { AuthLocalPasswordIn } from "@vds/sdk-ts";
import { useTranslation } from "react-i18next";
import { Navigate, useNavigate } from "react-router-dom";
import { AuthForm } from "../auth/AuthForm";
import { AuthCard } from "../auth/PublicLayout";
import { authUiFor } from "../auth/providers";
import { SESSION_KEY } from "../auth/session";
import { useApi } from "../lib/api";
import { useRuntimeConfig } from "../lib/runtime-config";

/** Changement de mot de passe (exigé par le 403 du provider après un compte créé par un administrateur). */
export function ChangePassword() {
  const { t } = useTranslation();
  const config = useRuntimeConfig();
  const ui = authUiFor(config.auth.provider);
  const api = useApi();
  const navigate = useNavigate();
  const qc = useQueryClient();

  if (!ui) return <Navigate replace to="/" />;

  return (
    <AuthCard title={t("auth.change_password.title")} intro={t("auth.change_password.intro")}>
      <AuthForm
        spec={ui.changePassword}
        onSubmit={async (v) => {
          const body: AuthLocalPasswordIn = { current: v.current ?? "", new: v.new ?? "" };
          await api.post<void>(ui.changePassword.endpoint, body);
          qc.removeQueries({ queryKey: SESSION_KEY });
          navigate("/", { replace: true });
        }}
        onError={(err) => {
          // Pas de session : retour à la connexion.
          if (err.status !== 401 || err.code !== "auth.unauthenticated") return false;
          navigate("/login", { replace: true });
          return true;
        }}
      />
    </AuthCard>
  );
}

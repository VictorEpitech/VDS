import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { AuthLocalLoginIn, AuthLocalLoginOut, AuthLocalStatus } from "@vds/sdk-ts";
import { useTranslation } from "react-i18next";
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { Icon } from "../components/Icon";
import { AuthForm } from "../auth/AuthForm";
import { AuthCard } from "../auth/PublicLayout";
import { authUiFor } from "../auth/providers";
import { SESSION_KEY } from "../auth/session";
import { useApi } from "../lib/api";
import { safeReturnTo } from "../lib/return-to";
import { useRuntimeConfig } from "../lib/runtime-config";

export function Login() {
  const { t } = useTranslation();
  const config = useRuntimeConfig();
  const ui = authUiFor(config.auth.provider);
  const api = useApi();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const location = useLocation();
  const [params] = useSearchParams();
  const rawReturn = params.get("return_to");
  const returnTo = safeReturnTo(config.basePath, rawReturn);
  const notice = (location.state as { notice?: string } | null)?.notice;

  const status = useQuery({
    queryKey: ["auth-status"],
    queryFn: () => api.get<AuthLocalStatus>(ui!.statusEndpoint),
    enabled: ui !== undefined,
    retry: false,
  });

  // Le provider n'a pas d'écran de connexion dans l'admin : l'accueil redirige vers sa loginUrl.
  if (!ui) return <Navigate replace to="/" />;

  return (
    <AuthCard title={t("auth.login.title")} intro={t("auth.login.intro")}>
      {notice === "already_initialized" ? (
        <p className="alert alert-info" role="status">
          <Icon name="pairing" />
          <span>{t("auth.login.already_initialized")}</span>
        </p>
      ) : null}
      <AuthForm
        spec={ui.login}
        onSubmit={async (v) => {
          const body: AuthLocalLoginIn = {
            email: v.email ?? "",
            password: v.password ?? "",
            return_to: returnTo === null ? null : rawReturn,
          };
          const out = await api.post<AuthLocalLoginOut>(ui.login.endpoint, body);
          qc.removeQueries({ queryKey: SESSION_KEY });
          navigate(safeReturnTo(config.basePath, out.redirect_to) ?? "/", { replace: true });
        }}
      />
      {status.data?.initialized === false ? (
        <p>
          <Link to="/bootstrap">{t("auth.login.first_start")}</Link>
        </p>
      ) : null}
    </AuthCard>
  );
}

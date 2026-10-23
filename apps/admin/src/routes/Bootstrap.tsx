import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { AuthLocalBootstrapIn, AuthLocalLoginOut, AuthLocalStatus } from "@vds/sdk-ts";
import { useTranslation } from "react-i18next";
import { Navigate, useNavigate } from "react-router-dom";
import { AccessFailure, Checking } from "../auth/AccessState";
import { AuthForm } from "../auth/AuthForm";
import { AuthCard } from "../auth/PublicLayout";
import { authUiFor } from "../auth/providers";
import { SESSION_KEY } from "../auth/session";
import { ApiError, useApi } from "../lib/api";
import { safeReturnTo } from "../lib/return-to";
import { useRuntimeConfig } from "../lib/runtime-config";

const ALREADY_INITIALIZED = { notice: "already_initialized" };

/** Amorçage : créer le premier administrateur, uniquement tant qu'aucun compte n'existe. */
export function Bootstrap() {
  const { t } = useTranslation();
  const config = useRuntimeConfig();
  const ui = authUiFor(config.auth.provider);
  const api = useApi();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const status = useQuery({
    queryKey: ["auth-status"],
    queryFn: () => api.get<AuthLocalStatus>(ui!.statusEndpoint),
    enabled: ui !== undefined,
    retry: false,
    staleTime: 0,
  });

  if (!ui) return <Navigate replace to="/" />;
  if (status.isPending) return <Checking />;
  if (status.isError) {
    const code = status.error instanceof ApiError ? status.error.code : "errors.unknown";
    return <AccessFailure code={code} onRetry={() => void status.refetch()} />;
  }
  if (status.data.initialized) return <Navigate replace to="/login" state={ALREADY_INITIALIZED} />;

  return (
    <AuthCard title={t("auth.bootstrap.title")} intro={t("auth.bootstrap.intro")}>
      <AuthForm
        spec={ui.bootstrap}
        onSubmit={async (v) => {
          const body: AuthLocalBootstrapIn = {
            email: v.email ?? "",
            display_name: v.display_name ?? "",
            password: v.password ?? "",
            bootstrap_token: v.bootstrap_token ?? "",
          };
          const out = await api.post<AuthLocalLoginOut>(ui.bootstrap.endpoint, body);
          qc.removeQueries({ queryKey: SESSION_KEY });
          qc.removeQueries({ queryKey: ["auth-status"] });
          navigate(safeReturnTo(config.basePath, out.redirect_to) ?? "/", { replace: true });
        }}
        onError={(err) => {
          if (err.code !== ui.alreadyInitializedCode) return false;
          qc.removeQueries({ queryKey: ["auth-status"] });
          navigate("/login", { replace: true, state: ALREADY_INITIALIZED });
          return true;
        }}
      />
    </AuthCard>
  );
}

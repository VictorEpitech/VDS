import { useEffect, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { StatusBadge } from "../components/StatusBadge";
import { ApiError } from "../lib/api";
import { useRuntimeConfig } from "../lib/runtime-config";
import { toReturnTo } from "../lib/return-to";
import { AccessFailure, Checking } from "./AccessState";
import { AuthCard, PublicFrame } from "./PublicLayout";
import { authUiFor } from "./providers";
import { useSession } from "./session";

/** Redirection vers l'URL de connexion fournie par la config (provider hébergé). */
function ExternalLogin({ url }: { url: string }) {
  const { t } = useTranslation();
  useEffect(() => {
    window.location.assign(url);
  }, [url]);
  return (
    <AuthCard title={t("auth.checking.title")}>
      <div role="status">
        <StatusBadge state="pairing" label={t("auth.redirecting")} />
      </div>
    </AuthCard>
  );
}

/** Garde de routes : non connecté => connexion ; changement de mot de passe exigé => écran dédié. */
export function RequireAuth() {
  const config = useRuntimeConfig();
  const ui = authUiFor(config.auth.provider);
  const location = useLocation();
  const session = useSession();

  if (session.isSuccess) return <Outlet />;

  const framed = (node: ReactNode) => <PublicFrame>{node}</PublicFrame>;
  if (session.isPending) return framed(<Checking />);

  const err = session.error instanceof ApiError ? session.error : new ApiError(0, "errors.unknown");
  if (err.status === 401) {
    if (ui) {
      const rt = toReturnTo(config.basePath, `${location.pathname}${location.search}`);
      return <Navigate replace to={{ pathname: "/login", search: `?return_to=${encodeURIComponent(rt)}` }} />;
    }
    if (config.auth.loginUrl) return framed(<ExternalLogin url={config.auth.loginUrl} />);
  }
  if (ui && err.status === 403 && err.code === ui.passwordChangeRequiredCode) {
    return <Navigate replace to="/change-password" />;
  }
  return framed(<AccessFailure code={err.code} onRetry={() => void session.refetch()} />);
}

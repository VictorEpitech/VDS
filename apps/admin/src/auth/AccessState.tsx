import { useTranslation } from "react-i18next";
import { StatusBadge } from "../components/StatusBadge";
import { AuthCard } from "./PublicLayout";
import { useErrorText } from "./error-text";

/** Vérification en cours : couleur + icône + libellé. */
export function Checking() {
  const { t } = useTranslation();
  return (
    <AuthCard title={t("auth.checking.title")}>
      <div role="status">
        <StatusBadge state="pairing" label={t("auth.checking.label")} />
      </div>
    </AuthCard>
  );
}

/** Échec (serveur injoignable, accès refusé) avec reprise. */
export function AccessFailure({ code, onRetry }: { code: string; onRetry?: () => void }) {
  const { t } = useTranslation();
  const errorText = useErrorText();
  return (
    <AuthCard title={t("auth.failure.title")}>
      <div role="alert">
        <StatusBadge state="offline" label={errorText(code)} />
      </div>
      {onRetry ? (
        <p>
          <button type="button" className="btn btn-filled" onClick={onRetry}>
            {t("auth.failure.retry")}
          </button>
        </p>
      ) : null}
    </AuthCard>
  );
}

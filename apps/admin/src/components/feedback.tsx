import type { UseQueryResult } from "@tanstack/react-query";
import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useErrorText } from "../auth/error-text";
import { ApiError } from "../lib/api";
import { Icon, type IconName } from "./Icon";

/** Erreur d'API traduite : couleur + icône + libellé, annoncée aux lecteurs d'écran. */
export function ErrorAlert({ error, children }: { error: unknown; children?: ReactNode }) {
  const errorText = useErrorText();
  const err = error instanceof ApiError ? error : new ApiError(0, "errors.unknown");
  return (
    <div className="alert alert-error" role="alert">
      <Icon name="warning" />
      <div>
        <p className="alert-text">{errorText(err.code, err.params)}</p>
        {children}
      </div>
    </div>
  );
}

export function SuccessAlert({ children }: { children: ReactNode }) {
  return (
    <p className="alert alert-success" role="status">
      <Icon name="online" />
      <span>{children}</span>
    </p>
  );
}

/** Chargement et échec d'une requête (avec « Réessayer ») ; rien une fois les données là. */
export function QueryStatus({ query }: { query: Pick<UseQueryResult, "isPending" | "isError" | "error" | "refetch"> }) {
  const { t } = useTranslation();
  if (query.isPending) {
    return (
      <p className="alert alert-info" role="status">
        <Icon name="processing" />
        <span>{t("common.loading")}</span>
      </p>
    );
  }
  if (query.isError) {
    return (
      <ErrorAlert error={query.error}>
        <button type="button" className="btn btn-text" onClick={() => void query.refetch()}>
          {t("common.retry")}
        </button>
      </ErrorAlert>
    );
  }
  return null;
}

export function Empty({ icon, children }: { icon: IconName; children: ReactNode }) {
  return (
    <p className="empty">
      <Icon name={icon} />
      <span>{children}</span>
    </p>
  );
}

/** Action destructive en deux temps : la confirmation reçoit le focus, Échap annule. */
export function ConfirmAction({
  label,
  confirmLabel,
  onConfirm,
  pending = false,
  icon = "delete",
}: {
  label: string;
  confirmLabel: string;
  onConfirm: () => void;
  pending?: boolean;
  icon?: IconName;
}) {
  const { t } = useTranslation();
  const [asking, setAsking] = useState(false);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const openRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (asking) confirmRef.current?.focus();
  }, [asking]);
  const cancel = () => {
    setAsking(false);
    queueMicrotask(() => openRef.current?.focus());
  };
  const onEscape = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === "Escape") cancel();
  };
  if (!asking) {
    return (
      <button ref={openRef} type="button" className="btn btn-text btn-danger" onClick={() => setAsking(true)}>
        <Icon name={icon} />
        {label}
      </button>
    );
  }
  return (
    <span className="confirm" role="group" aria-label={label}>
      <button
        ref={confirmRef}
        type="button"
        className="btn btn-danger-filled"
        disabled={pending}
        onKeyDown={onEscape}
        onClick={() => {
          setAsking(false);
          onConfirm();
        }}
      >
        <Icon name={icon} />
        {confirmLabel}
      </button>
      <button type="button" className="btn btn-text" onKeyDown={onEscape} onClick={cancel}>
        {t("common.cancel")}
      </button>
    </span>
  );
}

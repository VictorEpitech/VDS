import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";
import { useSession } from "../../auth/session";
import { ErrorAlert, SuccessAlert } from "../../components/feedback";
import { Icon, type IconName } from "../../components/Icon";
import { StatusBadge } from "../../components/StatusBadge";
import { useApi } from "../../lib/api";
import { keys, useOnboarding, type Onboarding, type OnboardingStep } from "../../lib/queries";

type StepId = OnboardingStep["step"];
type Status = OnboardingStep["status"];

const STEP_UI: Record<StepId, { icon: IconName; to: string; also?: string }> = {
  account: { icon: "user", to: "/more" },
  site: { icon: "site", to: "/sites", also: "/sites/connect" },
  screen: { icon: "screen", to: "/screens/add" },
  media: { icon: "media", to: "/content/media/add" },
  playlist: { icon: "playlist", to: "/content/playlists" },
  schedule: { icon: "schedule", to: "/playback" },
  power: { icon: "power", to: "/power" },
};

const STATUS_UI: Record<Status, { state: "online" | "pairing" | "revoked"; icon: IconName }> = {
  done: { state: "online", icon: "check" },
  in_progress: { state: "pairing", icon: "processing" },
  todo: { state: "revoked", icon: "schedule" },
  skipped: { state: "revoked", icon: "move_down" },
};

/** Écritures de l'état Premiers pas (admin) : la réponse remplace le cache, sans second GET. */
export function useOnboardingWrites() {
  const api = useApi();
  const qc = useQueryClient();
  const onSuccess = (data: Onboarding) => qc.setQueryData(keys.onboarding, data);
  const skip = useMutation({
    mutationFn: (v: { step: StepId; skipped: boolean }) => api.put<Onboarding>(`/onboarding/steps/${v.step}`, { skipped: v.skipped }),
    onSuccess,
  });
  const card = useMutation({
    mutationFn: (hidden: boolean) => api.put<Onboarding>("/onboarding/card", { hidden }),
    onSuccess,
  });
  return { skip, card };
}

/** Carte « Premiers pas » : étapes auto-cochées d'après l'état réel, jamais de case manuelle. */
export function OnboardingCard() {
  const { t } = useTranslation();
  const query = useOnboarding();
  const session = useSession();
  const location = useLocation();
  const { skip, card } = useOnboardingWrites();
  const isAdmin = session.data?.role === "admin";
  const [open, setOpen] = useState(true);
  const [celebrate, setCelebrate] = useState(false);
  const hiding = useRef(false);
  const data = query.data;
  const reopened = (location.state as { onboardingReopened?: boolean } | null)?.onboardingReopened === true;
  const visible = data !== undefined && !data.card.hidden;
  const complete = visible && data.complete;

  // Fin du parcours : message de réussite, puis la carte est masquée (admin ; l'éditeur ne peut pas écrire).
  useEffect(() => {
    if (!complete || reopened) return;
    setCelebrate(true);
    if (isAdmin && !hiding.current) {
      hiding.current = true;
      card.mutate(true, { onSettled: () => (hiding.current = false) });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [complete, reopened, isAdmin]);

  if (!data) return null;
  if (data.card.hidden && !celebrate) return null;

  const success = celebrate ? <SuccessAlert>{t("onboarding.done")}</SuccessAlert> : null;
  if (data.card.hidden) return <section className="card onboarding" aria-labelledby="onboarding-title">{success}</section>;

  const { completed, total } = data.progress;
  return (
    <section className="card onboarding" aria-labelledby="onboarding-title">
      <div className="page-head">
        <h2 id="onboarding-title">
          <Icon name="home" />
          {t("onboarding.title")}
        </h2>
        <button type="button" className="btn btn-text" aria-expanded={open} aria-controls="onboarding-steps" onClick={() => setOpen((o) => !o)}>
          <Icon name={open ? "move_up" : "move_down"} />
          {open ? t("onboarding.collapse") : t("onboarding.expand")}
        </button>
      </div>
      <p className="meta" role="status" aria-live="polite">
        {t("onboarding.progress", { completed, total })}
      </p>
      {success}
      {skip.isError ? <ErrorAlert error={skip.error} /> : null}
      {card.isError ? <ErrorAlert error={card.error} /> : null}
      <ol id="onboarding-steps" className="onboarding-steps list-reset" hidden={!open}>
        {data.steps.map((s) => {
          const ui = STEP_UI[s.step];
          const st = STATUS_UI[s.status];
          const next = s.step === data.next_step && !data.complete;
          const name = t(`onboarding.step.${s.step}.title`);
          const actionable = s.status !== "done" && s.status !== "skipped";
          return (
            <li key={s.step} className="onboarding-step" data-next={next} data-status={s.status}>
              <div className="onboarding-step-main">
                <h3>
                  <Icon name={ui.icon} />
                  {name}
                  {s.optional ? <span className="meta"> ({t("onboarding.optional")})</span> : null}
                </h3>
                <p className="meta">{t(`onboarding.step.${s.step}.hint`)}</p>
                <div className="badge-row">
                  <StatusBadge state={st.state} icon={st.icon} label={t(`onboarding.status.${s.status}`)} />
                  {next ? <StatusBadge state="pairing" icon="detect" label={t("onboarding.next")} /> : null}
                  {s.status === "done" && s.count > 0 && s.step !== "account" ? <span className="meta">{t(`onboarding.count.${s.step}`, { count: s.count })}</span> : null}
                </div>
              </div>
              <div className="actions">
                {actionable ? (
                  <Link className={next ? "btn btn-filled" : "btn btn-text"} to={ui.to} aria-label={`${t(`onboarding.step.${s.step}.action`)} : ${name}`}>
                    {t(`onboarding.step.${s.step}.action`)}
                  </Link>
                ) : null}
                {isAdmin && ui.also && s.status !== "skipped" ? (
                  <Link className="btn btn-text" to={ui.also} aria-label={`${t("onboarding.connect_agent")} : ${name}`}>
                    {t("onboarding.connect_agent")}
                  </Link>
                ) : null}
                {isAdmin && s.skippable && actionable ? (
                  <button type="button" className="btn btn-text" disabled={skip.isPending} aria-label={`${t("onboarding.skip")} : ${name}`} onClick={() => skip.mutate({ step: s.step, skipped: true })}>
                    {t("onboarding.skip")}
                  </button>
                ) : null}
                {isAdmin && s.status === "skipped" ? (
                  <button type="button" className="btn btn-text" disabled={skip.isPending} aria-label={`${t("onboarding.reopen")} : ${name}`} onClick={() => skip.mutate({ step: s.step, skipped: false })}>
                    {t("onboarding.reopen")}
                  </button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/** Plus › Aide : rouvre la carte masquée (admin). */
export function ReopenOnboarding({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const query = useOnboarding();
  const { card } = useOnboardingWrites();
  return (
    <>
      {card.isError ? <ErrorAlert error={card.error} /> : null}
      <div className="actions">
        <button type="button" className="btn btn-filled" disabled={card.isPending || query.isPending} onClick={() => card.mutate(false, { onSuccess: onDone })}>
          {t("onboarding.reopen_card")}
        </button>
      </div>
    </>
  );
}

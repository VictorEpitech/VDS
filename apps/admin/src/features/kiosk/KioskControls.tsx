import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { useSession } from "../../auth/session";
import { ErrorAlert, SuccessAlert } from "../../components/feedback";
import { Icon, type IconName } from "../../components/Icon";
import { useApi } from "../../lib/api";
import { configKey, keys, type Screen } from "../../lib/queries";

type Kiosk = NonNullable<Screen["kiosk"]>;
const ICONS: Record<Kiosk["requested"], IconName> = { locked: "lock", unlocked: "unlock", maintenance: "maintenance" };

export const MIN_MINUTES = 5;
export const MAX_MINUTES = 240;
const DEFAULT_MINUTES = 30;
const MAX_REASON = 200;

/** État du kiosque demandé : couleur + icône + libellé (« Maintenance jusqu'à 14:30 »). */
export function KioskBadge({ kiosk }: { kiosk: Kiosk }) {
  const { t, i18n } = useTranslation();
  const until = kiosk.until ? new Intl.DateTimeFormat(i18n.language, { hour: "2-digit", minute: "2-digit" }).format(new Date(kiosk.until)) : "";
  return (
    <span className="status-badge" data-state={`kiosk-${kiosk.requested}`}>
      <Icon name={ICONS[kiosk.requested]} />
      <span>{kiosk.requested === "maintenance" ? t("kiosk.state.maintenance", { time: until }) : t(`kiosk.state.${kiosk.requested}`)}</span>
    </span>
  );
}

/** Mode appliqué par l'appareil ; alerte visible quand le kiosque n'est pas natif (« doux » ou « web »). */
function Enforcement({ kiosk }: { kiosk: Kiosk }) {
  const { t } = useTranslation();
  if (!kiosk.enforcement) return <p className="meta">{t("kiosk.enforcement.unknown")}</p>;
  const weak = kiosk.enforcement === "soft" || kiosk.enforcement === "web";
  return (
    <>
      <p className="meta">{t("kiosk.enforcement.applied", { mode: t(`kiosk.enforcement.${kiosk.enforcement}`) })}</p>
      {weak ? (
        <p className="alert alert-warning" role="status">
          <Icon name="warning" />
          <span>{t(`kiosk.enforcement.alert_${kiosk.enforcement}`)}</span>
        </p>
      ) : null}
    </>
  );
}

/**
 * Kiosque d'un écran : badge d'état, mode appliqué, mode maintenance (durée, motif) et « Terminer ».
 * Réservé à l'admin : sans rôle admin ou sans champ `kiosk` (l'API ne le sert qu'aux admins), rien n'est rendu.
 */
export function KioskControls({ screen }: { screen: Screen }) {
  const { t } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const uid = useId();
  const session = useSession();
  const [open, setOpen] = useState(false);
  const [minutes, setMinutes] = useState(String(DEFAULT_MINUTES));
  const [reason, setReason] = useState("");
  const [done, setDone] = useState<"started" | "ended" | null>(null);
  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: keys.screens });
    await qc.invalidateQueries({ queryKey: configKey("screen", screen.id, "core") });
  };
  const start = useMutation({
    mutationFn: (body: { duration_min: number; reason?: string }) => api.post(`/screens/${screen.id}/kiosk/maintenance`, body),
    onMutate: () => setDone(null),
    onSuccess: async () => {
      setOpen(false);
      setReason("");
      setDone("started");
      await refresh();
    },
  });
  const end = useMutation({
    mutationFn: () => api.delete(`/screens/${screen.id}/kiosk/maintenance`),
    onMutate: () => setDone(null),
    onSuccess: async () => {
      setDone("ended");
      await refresh();
    },
  });
  const kiosk = screen.kiosk;
  if (session.data?.role !== "admin" || !kiosk) return null;
  const n = Number(minutes);
  const valid = Number.isInteger(n) && n >= MIN_MINUTES && n <= MAX_MINUTES;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!valid) return;
    const r = reason.trim();
    start.mutate(r ? { duration_min: n, reason: r } : { duration_min: n });
  };
  return (
    <div className="kiosk" role="group" aria-label={t("kiosk.group_for", { name: screen.name })}>
      <p>
        <KioskBadge kiosk={kiosk} />
      </p>
      <Enforcement kiosk={kiosk} />
      <div className="actions">
        {kiosk.requested === "maintenance" ? (
          <button type="button" className="btn btn-text" disabled={end.isPending} aria-busy={end.isPending} onClick={() => end.mutate()} aria-label={t("kiosk.maintenance.end_for", { name: screen.name })}>
            <Icon name="lock" />
            {t("kiosk.maintenance.end")}
          </button>
        ) : (
          <button type="button" className="btn btn-text" aria-expanded={open} aria-controls={`${uid}-form`} onClick={() => setOpen(!open)} aria-label={t("kiosk.maintenance.start_for", { name: screen.name })}>
            <Icon name="maintenance" />
            {t("kiosk.maintenance.start")}
          </button>
        )}
      </div>
      {open && kiosk.requested !== "maintenance" ? (
        <form className="inline-form" id={`${uid}-form`} onSubmit={submit} noValidate aria-label={t("kiosk.maintenance.title", { name: screen.name })}>
          <div className="field">
            <label htmlFor={`${uid}-min`}>{t("kiosk.maintenance.duration")}</label>
            <input id={`${uid}-min`} type="number" inputMode="numeric" min={MIN_MINUTES} max={MAX_MINUTES} step={1} required value={minutes} aria-invalid={!valid} aria-describedby={`${uid}-min-help`} onChange={(e) => setMinutes(e.target.value)} />
            <p className={valid ? "field-help" : "field-error"} id={`${uid}-min-help`}>
              {!valid ? <Icon name="warning" /> : null}
              <span>{t("kiosk.maintenance.duration_help", { min: MIN_MINUTES, max: MAX_MINUTES })}</span>
            </p>
          </div>
          <div className="field">
            <label htmlFor={`${uid}-reason`}>{t("kiosk.maintenance.reason")}</label>
            <input id={`${uid}-reason`} maxLength={MAX_REASON} autoComplete="off" value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
          <div className="actions">
            <button type="submit" className="btn btn-filled" disabled={start.isPending || !valid} aria-busy={start.isPending}>
              <Icon name="unlock" />
              {t("kiosk.maintenance.submit")}
            </button>
            <button type="button" className="btn btn-text" onClick={() => setOpen(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      ) : null}
      {start.isError ? <ErrorAlert error={start.error} /> : null}
      {end.isError ? <ErrorAlert error={end.error} /> : null}
      {done ? <SuccessAlert>{t(`kiosk.maintenance.${done}`, { name: screen.name })}</SuccessAlert> : null}
    </div>
  );
}

import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useErrorText } from "../../auth/error-text";
import { Empty, ErrorAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { StatusBadge } from "../../components/StatusBadge";
import { ApiError } from "../../lib/api";
import { useDiscoveryCapabilities, useDiscoveryScan, isRunning, type DiscoveredDevice } from "./hooks";

/** Erreur de lancement : 409 (indisponible, occupé) et 429 (délai, quota) avec Retry-After. */
function StartError({ error }: { error: unknown }) {
  const { t } = useTranslation();
  const errorText = useErrorText();
  if (!(error instanceof ApiError)) return <ErrorAlert error={error} />;
  const reason = typeof error.params.reason === "string" ? error.params.reason : null;
  const limit = error.status === 429;
  return (
    <p className={`alert ${limit ? "alert-warning" : "alert-error"}`} role="alert">
      <Icon name="warning" />
      <span>
        {reason ? t(`discovery.reason.${reason}`, { defaultValue: errorText(error.code) }) : errorText(error.code, { ...error.params, seconds: error.retryAfter ?? 60 })}
      </span>
    </p>
  );
}

/**
 * Recherche des TV du réseau d'un site, au clic seulement. Absente sans agent (`agent_missing`) : la saisie d'adresse reste.
 * Les noms et modèles viennent du réseau : rendus en texte uniquement.
 */
export function DiscoveryPanel({ siteId, selected, onSelect }: { siteId: string; selected: string | null; onSelect: (d: DiscoveredDevice | null) => void }) {
  const { t } = useTranslation();
  const uid = useId();
  const caps = useDiscoveryCapabilities(siteId);
  const { start, scan, started, reset } = useDiscoveryScan(siteId);
  if (!siteId || !caps.data) return null;
  if (!caps.data.available) {
    if (caps.data.reason === "agent_missing" || !caps.data.reason) return null;
    return (
      <p className="alert alert-info" role="status">
        <Icon name="detect" />
        <span>{t(`discovery.reason.${caps.data.reason}`)}</span>
      </p>
    );
  }
  const data = scan.data;
  const running = start.isPending || (started && (!data || isRunning(data.status)));
  const devices = data?.devices ?? [];
  const done = data?.status === "completed";
  const launch = () => {
    onSelect(null);
    reset();
    start.mutate();
  };

  return (
    <section className="discovery" aria-labelledby={`${uid}-h`}>
      <h3 id={`${uid}-h`}>{t("discovery.title")}</h3>
      <p className="field-help">{caps.data.probe_ports ? t("discovery.help") : t("discovery.help_no_ports")}</p>
      <div className="actions">
        <button type="button" className="btn btn-tonal" onClick={launch} disabled={running} aria-busy={running}>
          <Icon name="detect" />
          {started ? t("discovery.again") : t("discovery.action")}
        </button>
      </div>
      {start.isError ? <StartError error={start.error} /> : null}
      {scan.isError ? <ErrorAlert error={scan.error} /> : null}
      {running ? (
        <p className="alert alert-info" role="status">
          <Icon name="processing" />
          <span>{t("discovery.running", { count: devices.length })}</span>
        </p>
      ) : null}
      {data?.status === "failed" ? (
        <p className="alert alert-error" role="alert">
          <Icon name="failed" />
          <span>{t(`discovery.failed.${(data.error_code ?? "unknown").replace(/^discovery\./, "")}`, { defaultValue: t("discovery.failed.unknown") })}</span>
        </p>
      ) : null}
      {done && devices.length === 0 ? <Empty icon="screen">{t("discovery.empty")}</Empty> : null}
      {done && data.truncated ? (
        <p className="alert alert-warning" role="status">
          <Icon name="warning" />
          <span>{t("discovery.truncated")}</span>
        </p>
      ) : null}
      {done && data.capped ? (
        <p className="alert alert-warning" role="status">
          <Icon name="warning" />
          <span>{t("discovery.capped")}</span>
        </p>
      ) : null}
      {devices.some((d) => d.protocols.includes("adb")) ? (
        <p className="alert alert-warning" role="status">
          <Icon name="lock" />
          <span>{t("discovery.adb_notice")}</span>
        </p>
      ) : null}
      {devices.length > 0 ? (
        <fieldset className="discovery-list">
          <legend>{t("discovery.found", { count: devices.length })}</legend>
          <ul className="list-reset rows">
            {devices.map((d) => {
              const label = d.name || [d.vendor, d.model].filter(Boolean).join(" ") || t("discovery.unnamed");
              const known = d.known_screen_id !== null;
              return (
                <li key={d.id} className="row-line discovery-device">
                  <label className="discovery-choice">
                    <input type="radio" name={`${uid}-device`} checked={selected === d.id} disabled={known || running} onChange={() => onSelect(d)} />
                    <span>
                      <strong>{label}</strong>
                      {d.vendor || d.model ? <span className="meta"> {[d.vendor, d.model].filter(Boolean).join(" ")}</span> : null}
                    </span>
                  </label>
                  <span className="discovery-facts">
                    <span className="mono">{d.ip}</span>
                    {d.mac ? <span className="mono">{d.mac}</span> : null}
                    {d.suggested_drivers.length > 0 ? <span className="meta">{t("discovery.drivers", { drivers: d.suggested_drivers.join(", ") })}</span> : null}
                    {known ? (
                      <>
                        <StatusBadge state="online" icon="check" label={t("discovery.known")} />
                        <Link className="btn btn-text" to={`/screens#screen-${d.known_screen_id}`} aria-label={t("discovery.known_open", { name: label })}>
                          {t("discovery.known_link")}
                        </Link>
                      </>
                    ) : null}
                  </span>
                </li>
              );
            })}
          </ul>
        </fieldset>
      ) : null}
    </section>
  );
}

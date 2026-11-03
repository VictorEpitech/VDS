import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "@vds/sdk-ts";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ErrorAlert, SuccessAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { WizardSteps } from "../../components/WizardSteps";
import { useApi } from "../../lib/api";
import { agentKeys, keys, useSites, type Agent, type Screen } from "../../lib/queries";
import { DiscoveryPanel } from "../discovery/DiscoveryPanel";
import { applyDiscoveredDevice, suggestedName, type DiscoveredDevice } from "../discovery/hooks";
import { CopyBlock } from "../sites/EnrollPanel";

type S = components["schemas"];
type Enrollment = S["EnrollmentOut"];
type Created = S["EnrollmentCreated"];

export const POLL_MS = 3000;
/** Sans battement depuis 2 minutes : le message d'aide s'affiche. */
export const STALL_MS = 120_000;
const STEPS = 4;

interface Added {
  id: string;
  name: string;
  configFailed: boolean;
}

/** « il y a 3 s » : unité choisie selon l'âge, langue de l'interface. */
function useAgo(iso: string | null, now: number): string | null {
  const { i18n } = useTranslation();
  if (!iso) return null;
  const seconds = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  const rtf = new Intl.RelativeTimeFormat(i18n.language, { numeric: "always", style: "short" });
  return seconds < 60 ? rtf.format(-seconds, "second") : rtf.format(-Math.round(seconds / 60), "minute");
}

/** Assistant « Connecter un agent de site » : commande copiable, attente du premier battement, détection des appareils. */
export function ConnectAgentWizard({ siteId, pollMs = POLL_MS, stallMs = STALL_MS }: { siteId: string; pollMs?: number; stallMs?: number }) {
  const { t } = useTranslation();
  const uid = useId();
  const api = useApi();
  const qc = useQueryClient();
  const sites = useSites();
  const site = sites.data?.find((s) => s.id === siteId);
  const [step, setStep] = useState(1);
  const [created, setCreated] = useState<Created | null>(null);
  const [stalled, setStalled] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [device, setDevice] = useState<DiscoveredDevice | null>(null);
  const [added, setAdded] = useState<Added[]>([]);
  const heading = useRef<HTMLHeadingElement>(null);
  const mounted = useRef(false);

  useEffect(() => {
    if (mounted.current) heading.current?.focus();
    mounted.current = true;
  }, [step]);

  const generate = useMutation({
    mutationFn: () => api.post<Created>(`/sites/${siteId}/agent/enrollments`, { ttl_min: 60 }),
    onSuccess: (c) => {
      setCreated(c);
      setStalled(false);
    },
  });

  const waiting = step === 2 && created !== null;
  const enrollments = useQuery({
    queryKey: ["enrollments", siteId],
    enabled: waiting,
    queryFn: () => api.get<Enrollment[]>(`/sites/${siteId}/agent/enrollments`),
    refetchInterval: pollMs,
  });
  const agents = useQuery({
    queryKey: agentKeys.site(siteId),
    enabled: waiting,
    queryFn: () => api.get<Agent[]>(`/sites/${siteId}/agents`),
    refetchInterval: pollMs,
  });
  const mine = enrollments.data?.find((e) => e.id === created?.id);
  const agent = mine?.state === "consumed" && mine.agent_id ? agents.data?.find((a) => a.id === mine.agent_id) : undefined;
  const connected = agent?.status === "online" ? agent : undefined;
  const lapsed = mine?.state === "expired" || mine?.state === "revoked";

  // Aucune connexion au bout de 2 minutes : message actionnable ; l'horloge ne tourne que pendant l'attente.
  useEffect(() => {
    if (!waiting || connected) return;
    const timer = setTimeout(() => setStalled(true), stallMs);
    return () => clearTimeout(timer);
  }, [waiting, connected, stallMs, created?.id]);
  useEffect(() => {
    if (!connected) return;
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(tick);
  }, [connected]);
  const ago = useAgo(connected?.last_seen_at ?? null, now);

  const addDevice = useMutation({
    mutationFn: async (d: DiscoveredDevice) => {
      const screen = await api.post<Screen>("/screens", { name: suggestedName(d), site_id: siteId, group_id: null, orientation: "landscape" });
      let configFailed = false;
      try {
        await applyDiscoveredDevice(api, screen.id, d);
      } catch {
        configFailed = true;
      }
      return { id: screen.id, name: screen.name, configFailed };
    },
    onSuccess: (r) => {
      setAdded((list) => [...list, r]);
      setDevice(null);
      void qc.invalidateQueries({ queryKey: keys.screens });
    },
  });

  const names = [1, 2, 3, 4].map((n) => t(`agents.connect.step.${n}`));
  const regenerate = () => {
    setCreated(null);
    setStalled(false);
    setStep(1);
  };

  return (
    <>
      <WizardSteps label={t("agents.connect.progress_label")} steps={names} current={step} />
      <p className="visually-hidden" role="status" aria-live="polite">
        {t("agents.connect.live", { step, total: STEPS, name: names[step - 1] })}
      </p>

      {step === 1 ? (
        <section className="card" aria-labelledby={`${uid}-h`}>
          <h2 id={`${uid}-h`} ref={heading} tabIndex={-1}>
            {t("agents.connect.step1.title", { site: site?.name ?? "" })}
          </h2>
          <p>{t("agents.connect.step1.intro")}</p>
          {generate.isError ? <ErrorAlert error={generate.error} /> : null}
          {created ? (
            <>
              <p className="alert alert-warning">
                <Icon name="warning" />
                <span>{t("agents.connect.step1.once")}</span>
              </p>
              {created.commands.docker_login ? <CopyBlock label={t("enroll.remote.login")} value={created.commands.docker_login} /> : null}
              <CopyBlock label={t("enroll.remote.run")} value={created.commands.docker_run} />
              <p className="meta">{t("agents.connect.step1.outbound")}</p>
            </>
          ) : null}
          <div className="actions">
            {created ? (
              <button type="button" className="btn btn-filled" onClick={() => setStep(2)}>
                <Icon name="next" />
                {t("agents.connect.step1.launched")}
              </button>
            ) : (
              <button type="button" className="btn btn-filled" disabled={generate.isPending} aria-busy={generate.isPending} onClick={() => generate.mutate()}>
                <Icon name="agent" />
                {t("agents.connect.step1.generate")}
              </button>
            )}
          </div>
        </section>
      ) : null}

      {step === 2 ? (
        <section className="card" aria-labelledby={`${uid}-h`}>
          <h2 id={`${uid}-h`} ref={heading} tabIndex={-1}>
            {t("agents.connect.step2.title")}
          </h2>
          {connected ? (
            <SuccessAlert>{t("agents.connect.step2.connected", { ago: ago ?? "" })}</SuccessAlert>
          ) : lapsed ? (
            <p className="alert alert-warning" role="status">
              <Icon name="warning" />
              <span>{t("agents.connect.step2.lapsed")}</span>
            </p>
          ) : stalled ? (
            <p className="alert alert-warning" role="status">
              <Icon name="warning" />
              <span>{t("agents.connect.step2.stalled")}</span>
            </p>
          ) : (
            <p className="alert alert-info" role="status">
              <Icon name="processing" />
              <span>{t("agents.connect.step2.waiting")}</span>
            </p>
          )}
          {enrollments.isError ? <ErrorAlert error={enrollments.error} /> : null}
          <div className="actions">
            <button type="button" className="btn btn-text" onClick={() => setStep(1)}>
              <Icon name="back" />
              {t("common.back")}
            </button>
            {stalled || lapsed ? (
              <button type="button" className="btn btn-tonal" onClick={regenerate}>
                <Icon name="rotate" />
                {t("agents.connect.step2.regenerate")}
              </button>
            ) : null}
            <button type="button" className="btn btn-filled" disabled={!connected} onClick={() => setStep(3)}>
              <Icon name="next" />
              {t("common.continue")}
            </button>
          </div>
        </section>
      ) : null}

      {step === 3 ? (
        <section className="card" aria-labelledby={`${uid}-h`}>
          <h2 id={`${uid}-h`} ref={heading} tabIndex={-1}>
            {t("agents.connect.step3.title")}
          </h2>
          <p className="meta">{t("agents.connect.step3.intro")}</p>
          <DiscoveryPanel siteId={siteId} selected={device?.id ?? null} onSelect={setDevice} />
          {addDevice.isError ? <ErrorAlert error={addDevice.error} /> : null}
          {added.length > 0 ? (
            <ul className="list-reset" aria-label={t("agents.connect.step3.added_list")}>
              {added.map((a) => (
                <li key={a.id} role="status">
                  <Icon name="online" /> {t("agents.connect.step3.added", { name: a.name })}
                  {a.configFailed ? <span className="meta"> {t("agents.connect.step3.config_failed")}</span> : null}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="actions">
            <button type="button" className="btn btn-text" onClick={() => setStep(2)}>
              <Icon name="back" />
              {t("common.back")}
            </button>
            <button type="button" className="btn btn-tonal" disabled={!device || addDevice.isPending} aria-busy={addDevice.isPending} onClick={() => device && addDevice.mutate(device)}>
              <Icon name="add" />
              {t("agents.connect.step3.add")}
            </button>
            <button type="button" className="btn btn-filled" onClick={() => setStep(4)}>
              <Icon name="next" />
              {added.length > 0 ? t("common.continue") : t("agents.connect.step3.skip")}
            </button>
          </div>
        </section>
      ) : null}

      {step === 4 ? (
        <section className="card" aria-labelledby={`${uid}-h`}>
          <h2 id={`${uid}-h`} ref={heading} tabIndex={-1}>
            {t("agents.connect.step4.title")}
          </h2>
          <SuccessAlert>{t("agents.connect.step4.agent", { site: site?.name ?? "" })}</SuccessAlert>
          <p role="status">{t("agents.connect.step4.screens", { count: added.length })}</p>
          <div className="actions">
            {added.length > 0 ? (
              <Link className="btn btn-filled" to={`/screens/add?screen=${added[0]!.id}`}>
                <Icon name="pairing" />
                {t("agents.connect.step4.pair")}
              </Link>
            ) : null}
            <Link className={added.length > 0 ? "btn btn-text" : "btn btn-filled"} to={`/sites/${siteId}`}>
              <Icon name="site" />
              {t("agents.connect.step4.open_site")}
            </Link>
          </div>
        </section>
      ) : null}
    </>
  );
}

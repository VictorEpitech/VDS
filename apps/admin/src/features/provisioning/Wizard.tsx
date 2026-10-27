import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ConfirmAction, ErrorAlert, QueryStatus, SuccessAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { StatusBadge } from "../../components/StatusBadge";
import { useApi } from "../../lib/api";
import { keys, useAllGroups, useSites, type Screen } from "../../lib/queries";
import { isActive, STEP_ORDER, useCapabilities, useProvision, useProvisionActions, type Provision, type StepId } from "./hooks";
import { StepList, useCodeText } from "./StepList";
import { Tutorial } from "./Tutorial";

type Orientation = "landscape" | "portrait";
const STEP_COUNT = 5;
const DEVICE_OWNER_DOC = "docs/demos/kiosque-android-tv.md";
const IPV4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/;
/** Contrôle de saisie seulement : le serveur et l'agent revalident (adresse privée uniquement). */
const looksLikeAddress = (v: string) => {
  const m = IPV4.exec(v);
  return m ? m.slice(1).every((n) => Number(n) <= 255) : /^[0-9a-fA-F:]+$/.test(v) && v.includes(":");
};

/** Étape courante dérivée de l'état : 1-3 avant la session, 4 pendant l'exécution, 5 en attente puis succès. */
function runStep(p: Provision | undefined): 4 | 5 {
  return p && (p.status === "waiting_online" || p.status === "done") ? 5 : 4;
}

export function ProvisionWizard() {
  const { t } = useTranslation();
  const uid = useId();
  const api = useApi();
  const qc = useQueryClient();
  const sites = useSites();
  const groups = useAllGroups();
  const [view, setView] = useState<1 | 2 | 3>(1);
  const [name, setName] = useState("");
  const [siteId, setSiteId] = useState("");
  const [groupId, setGroupId] = useState("");
  const [orientation, setOrientation] = useState<Orientation>("landscape");
  const [address, setAddress] = useState("");
  const [port, setPort] = useState("5555");
  const [deviceOwner, setDeviceOwner] = useState<"try" | "skip">("try");
  const [softKiosk, setSoftKiosk] = useState(true);
  const [missing, setMissing] = useState<{ name?: boolean; site?: boolean; address?: boolean; port?: boolean }>({});
  const [screenId, setScreenId] = useState<string | null>(null);
  const [pid, setPid] = useState<string | null>(null);

  const caps = useCapabilities(siteId);
  const session = useProvision(pid);
  const actions = useProvisionActions(pid, (p) => setPid(p.id));
  const createScreen = useMutation({
    mutationFn: (b: { name: string; site_id: string; group_id: string | null; orientation: Orientation }) => api.post<Screen>("/screens", b),
    onSuccess: (s) => {
      setScreenId(s.id);
      setView(2);
      void qc.invalidateQueries({ queryKey: keys.screens });
    },
  });

  const activeSites = (sites.data ?? []).filter((s) => !s.archived_at);
  const siteGroups = (groups.data ?? []).filter((g) => g.site_id === siteId);
  const available = caps.data?.available === true;
  const current: number = pid !== null ? runStep(session.data) : view;

  const submitScreen = (e: FormEvent) => {
    e.preventDefault();
    const next = { name: !name.trim(), site: !siteId };
    setMissing(next);
    if (next.name || next.site || !available) return;
    if (screenId) return setView(2);
    createScreen.mutate({ name: name.trim(), site_id: siteId, group_id: groupId || null, orientation });
  };

  const submitAddress = (e: FormEvent) => {
    e.preventDefault();
    const portNum = Number(port);
    const next = { address: !looksLikeAddress(address.trim()), port: !Number.isInteger(portNum) || portNum < 1 || portNum > 65535 };
    setMissing(next);
    if (next.address || next.port || !screenId) return;
    actions.create.mutate({
      screenId,
      body: { platform: "android", address: address.trim(), port: portNum, device_owner: deviceOwner, soft_kiosk_fallback: softKiosk, reprovision: false },
    });
  };

  const fieldError = (id: string, shown: boolean, text: string) =>
    shown ? (
      <p className="field-error" id={id}>
        <Icon name="warning" />
        <span>{text}</span>
      </p>
    ) : null;

  return (
    <>
      <div className="page-head">
        <h1>{t("provision.title")}</h1>
        <Link className="btn btn-text" to={screenId ? `/pairing?screen=${screenId}` : "/pairing"}>
          <Icon name="pairing" />
          {t("provision.manual_code")}
        </Link>
      </div>
      <p className="lead">{t("provision.intro")}</p>
      <ol className="wizard-steps list-reset" aria-label={t("provision.progress_label")}>
        {Array.from({ length: STEP_COUNT }, (_, i) => i + 1).map((n) => (
          <li key={n} aria-current={n === current ? "step" : undefined} data-done={n < current ? "true" : undefined}>
            <span className="wizard-num">{n < current ? <Icon name="check" /> : n}</span>
            <span>{t(`provision.wizard.${n}`)}</span>
          </li>
        ))}
      </ol>

      {pid === null && current === 1 ? (
        <form className="card form-narrow" onSubmit={submitScreen} noValidate aria-label={t("provision.wizard.1")}>
          <div className="field">
            <label htmlFor={`${uid}-name`}>{t("screens.create.name")}</label>
            <input id={`${uid}-name`} value={name} maxLength={120} required autoComplete="off" disabled={screenId !== null} aria-invalid={missing.name ? true : undefined} onChange={(e) => setName(e.target.value)} />
            {fieldError(`${uid}-name-err`, !!missing.name, t("errors.field_required"))}
          </div>
          <div className="field">
            <label htmlFor={`${uid}-site`}>{t("screens.create.site")}</label>
            <select id={`${uid}-site`} value={siteId} required disabled={screenId !== null} aria-invalid={missing.site ? true : undefined} onChange={(e) => { setSiteId(e.target.value); setGroupId(""); }}>
              <option value="">{t("form.choose")}</option>
              {activeSites.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            {fieldError(`${uid}-site-err`, !!missing.site, t("errors.field_required"))}
          </div>
          <div className="field">
            <label htmlFor={`${uid}-group`}>{t("screens.create.group")}</label>
            <select id={`${uid}-group`} value={groupId} disabled={!siteId || screenId !== null} onChange={(e) => setGroupId(e.target.value)}>
              <option value="">{t("screens.create.no_group")}</option>
              {siteGroups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor={`${uid}-orientation`}>{t("screens.create.orientation")}</label>
            <select id={`${uid}-orientation`} value={orientation} disabled={screenId !== null} onChange={(e) => setOrientation(e.target.value as Orientation)}>
              <option value="landscape">{t("screens.orientation.landscape")}</option>
              <option value="portrait">{t("screens.orientation.portrait")}</option>
            </select>
          </div>
          {siteId ? <QueryStatus query={caps} /> : null}
          {caps.isSuccess && !caps.data.available ? (
            <div className="alert alert-warning" role="status">
              <Icon name="warning" />
              <div>
                <p className="alert-text">{t(`provision.unavailable.${caps.data.reason ?? "unknown"}`)}</p>
                <Link className="btn btn-text" to={screenId ? `/pairing?screen=${screenId}` : "/pairing"}>
                  <Icon name="pairing" />
                  {t("provision.manual_code")}
                </Link>
              </div>
            </div>
          ) : null}
          {caps.data?.available && caps.data.apk ? (
            <p className="meta">
              {t("provision.apk")} <span className="mono">{caps.data.apk.version_name}</span>
            </p>
          ) : null}
          {createScreen.isError ? <ErrorAlert error={createScreen.error} /> : null}
          {available || !siteId ? (
            <div className="actions">
              <button type="submit" className="btn btn-filled" disabled={createScreen.isPending || (siteId !== "" && !available)} aria-busy={createScreen.isPending}>
                {t("provision.next")}
              </button>
            </div>
          ) : null}
        </form>
      ) : null}

      {pid === null && current === 2 ? (
        <section className="card" aria-labelledby={`${uid}-tuto`}>
          <h2 id={`${uid}-tuto`}>{t("provision.wizard.2")}</h2>
          <p className="meta">{t("provision.tutorial.intro")}</p>
          <Tutorial />
          <div className="actions">
            <button type="button" className="btn btn-filled" onClick={() => setView(3)}>
              {t("provision.next")}
            </button>
            <button type="button" className="btn btn-text" onClick={() => setView(1)}>
              {t("provision.previous")}
            </button>
          </div>
        </section>
      ) : null}

      {pid === null && current === 3 ? (
        <form className="card form-narrow" onSubmit={submitAddress} noValidate aria-label={t("provision.wizard.3")}>
          <div className="field">
            <label htmlFor={`${uid}-ip`}>{t("provision.address.label")}</label>
            <input id={`${uid}-ip`} className="mono" value={address} inputMode="decimal" autoComplete="off" spellCheck={false} required aria-invalid={missing.address ? true : undefined} aria-describedby={`${uid}-ip-help`} onChange={(e) => { setAddress(e.target.value); actions.create.reset(); }} />
            <p className="field-help" id={`${uid}-ip-help`}>
              {t("provision.address.help")}
            </p>
            {fieldError(`${uid}-ip-err`, !!missing.address, t("provision.address.invalid"))}
          </div>
          <details className="advanced">
            <summary>{t("form.advanced")}</summary>
            <div className="advanced-body">
              <div className="field">
                <label htmlFor={`${uid}-port`}>{t("provision.port")}</label>
                <input id={`${uid}-port`} className="mono" value={port} inputMode="numeric" aria-invalid={missing.port ? true : undefined} onChange={(e) => setPort(e.target.value)} />
                {fieldError(`${uid}-port-err`, !!missing.port, t("provision.port_invalid"))}
              </div>
              <div className="field">
                <label htmlFor={`${uid}-owner`}>{t("provision.device_owner.label")}</label>
                <select id={`${uid}-owner`} value={deviceOwner} onChange={(e) => setDeviceOwner(e.target.value as "try" | "skip")}>
                  <option value="try">{t("provision.device_owner.try")}</option>
                  <option value="skip">{t("provision.device_owner.skip")}</option>
                </select>
              </div>
              <div className="field field-check">
                <input id={`${uid}-soft`} type="checkbox" checked={softKiosk} onChange={(e) => setSoftKiosk(e.target.checked)} />
                <label htmlFor={`${uid}-soft`}>{t("provision.soft_kiosk")}</label>
              </div>
            </div>
          </details>
          {actions.create.isError ? <ErrorAlert error={actions.create.error} /> : null}
          <div className="actions">
            <button type="submit" className="btn btn-filled" disabled={actions.create.isPending} aria-busy={actions.create.isPending}>
              <Icon name="detect" />
              {t("provision.start")}
            </button>
            <button type="button" className="btn btn-text" onClick={() => setView(2)}>
              {t("provision.previous")}
            </button>
          </div>
        </form>
      ) : null}

      {pid !== null ? (
        <Run
          pid={pid}
          screenId={screenId}
          session={session}
          actions={actions}
          onChangeAddress={() => {
            actions.create.reset();
            setPid(null);
            setView(3);
          }}
        />
      ) : null}
    </>
  );
}

function Run({
  pid,
  screenId,
  session,
  actions,
  onChangeAddress,
}: {
  pid: string;
  screenId: string | null;
  session: ReturnType<typeof useProvision>;
  actions: ReturnType<typeof useProvisionActions>;
  onChangeAddress: () => void;
}) {
  const { t } = useTranslation();
  const codeText = useCodeText();
  const p = session.data;
  if (!p) return <QueryStatus query={session} />;
  const failedStep: StepId | null = p.steps.find((s) => s.status === "failed")?.step ?? null;
  const resumeAt = failedStep ?? (STEP_ORDER.find((id) => !p.steps.some((s) => s.step === id && (s.status === "ok" || s.status === "skipped"))) ?? null);
  const busy = actions.retry.isPending || actions.cancel.isPending;
  const waitingUser = p.status === "waiting_user" || p.steps.some((s) => s.status === "waiting_user");
  const stepTitle = `${pid}-run`;
  const retry = (fromStep: StepId | null) => actions.retry.mutate({ fromStep });

  return (
    <section className="card" aria-labelledby={stepTitle}>
      <h2 id={stepTitle}>{t(p.status === "waiting_online" || p.status === "done" ? "provision.wizard.5" : "provision.wizard.4")}</h2>
      <div className="live" aria-live="polite">
        {waitingUser ? (
          <div className="alert alert-warning" role="status">
            <Icon name="pairing" />
            <p className="alert-text">{t("provision.waiting_user")}</p>
          </div>
        ) : null}
        {p.status === "waiting_online" ? (
          <p className="alert alert-info" role="status">
            <Icon name="processing" />
            <span>{t("provision.waiting_online")}</span>
          </p>
        ) : null}
        {p.status === "done" ? (
          <>
            <SuccessAlert>{t("provision.done")}</SuccessAlert>
            <p>
              <StatusBadge state="online" />
            </p>
          </>
        ) : null}
        {p.status === "cancelled" ? (
          <p className="alert alert-info" role="status">
            <Icon name="back" />
            <span>{t("provision.cancelled")}</span>
          </p>
        ) : null}
        {p.status === "failed" ? (
          <div className="alert alert-error" role="alert">
            <Icon name="failed" />
            <p className="alert-text">{codeText(p.code ?? p.steps.find((s) => s.status === "failed")?.code ?? null) ?? t("provision.codes.unknown")}</p>
          </div>
        ) : null}
        {p.status === "done" && p.outcome?.kiosk === "soft" ? (
          <div className="alert alert-warning" role="status">
            <Icon name="lock" />
            <div>
              <p className="alert-text">{t("provision.kiosk.soft")}</p>
              <p className="meta">{t("provision.kiosk.procedure")} <span className="mono">{DEVICE_OWNER_DOC}</span></p>
            </div>
          </div>
        ) : null}
        {p.status === "done" && p.outcome?.kiosk === "none" ? (
          <div className="alert alert-warning" role="status">
            <Icon name="unlock" />
            <p className="alert-text">{t("provision.kiosk.none")}</p>
          </div>
        ) : null}
        {p.status === "done" && p.outcome?.kiosk === "device_owner" ? (
          <p className="meta">{t("provision.kiosk.device_owner")}</p>
        ) : null}
      </div>
      <StepList steps={p.steps} />
      {actions.retry.isError ? <ErrorAlert error={actions.retry.error} /> : null}
      {actions.cancel.isError ? <ErrorAlert error={actions.cancel.error} /> : null}
      {session.isError ? <QueryStatus query={session} /> : null}
      <div className="actions">
        {p.status === "failed" ? (
          <>
            <button type="button" className="btn btn-filled" disabled={busy} onClick={() => retry(resumeAt)}>
              <Icon name="rotate" />
              {t("provision.retry_here")}
            </button>
            {failedStep === "device_owner" ? (
              <button type="button" className="btn btn-text" disabled={busy} onClick={() => retry("soft_kiosk")}>
                <Icon name="lock" />
                {t("provision.continue_soft")}
              </button>
            ) : null}
            <button type="button" className="btn btn-text" disabled={busy} onClick={onChangeAddress}>
              {t("provision.change_address")}
            </button>
          </>
        ) : null}
        {isActive(p.status) && p.status !== "waiting_online" ? (
          <ConfirmAction icon="revoke" label={t("provision.cancel.action")} confirmLabel={t("provision.cancel.confirm")} pending={busy} onConfirm={() => actions.cancel.mutate()} />
        ) : null}
        {p.status === "done" ? (
          <Link className="btn btn-filled" to="/screens">
            <Icon name="screen" />
            {t("provision.go_screens")}
          </Link>
        ) : (
          <Link className="btn btn-text" to={screenId ? `/pairing?screen=${screenId}` : "/pairing"}>
            <Icon name="pairing" />
            {t("provision.manual_code")}
          </Link>
        )}
      </div>
    </section>
  );
}

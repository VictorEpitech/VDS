import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { components } from "@vds/sdk-ts";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { ErrorAlert, QueryStatus } from "../../../components/feedback";
import { Icon, type IconName } from "../../../components/Icon";
import { useApi } from "../../../lib/api";
import { keys, useScreens, useSites, type Screen } from "../../../lib/queries";
import { DiscoveryPanel } from "../../discovery/DiscoveryPanel";
import { applyDiscoveredDevice, suggestedName, type DiscoveredDevice } from "../../discovery/hooks";
import { useProvisioningAvailable } from "../../provisioning/hooks";
import { AgentSetupLink } from "../../sites/EnrollPanel";
import { InheritedSettings } from "./InheritedSettings";
import { codeState, normalizeCode, pairMotive, type PairMotive } from "./pairErrors";
import { VerifyStep } from "./VerifyStep";
import { useWritableTargets } from "../../scope/scope";

type Device = "browser" | "android" | "pi" | "other";
type StepId = "device" | "code" | "details" | "verify";
type Orientation = "" | "landscape" | "portrait";
type Paired = components["schemas"]["ScreenPaired"];

const DEVICES: { id: Device; icon: IconName }[] = [
  { id: "browser", icon: "screen" },
  { id: "android", icon: "detect" },
  { id: "pi", icon: "agent" },
  { id: "other", icon: "more" },
];
/** Durée de vie d'un code affiché par la TV (annoncée dans le message « code expiré »). */
const CODE_TTL_MIN = 15;

/** Cause en langage clair + une action ; jamais le message brut du serveur. */
function PairError({ error, onEditCode, canEditCode }: { error: unknown; onEditCode: () => void; canEditCode: boolean }) {
  const { t } = useTranslation();
  const motive: PairMotive = pairMotive(error);
  if (motive === "other") return <ErrorAlert error={error} />;
  const seconds = (error as { retryAfter?: number | null }).retryAfter ?? 60;
  return (
    <div className="alert alert-error" role="alert">
      <Icon name="warning" />
      <div>
        <p className="alert-text">{t(`add.error.${motive}.text`, { minutes: CODE_TTL_MIN, seconds })}</p>
        <div className="actions">
          {motive === "already_paired" ? (
            <Link className="btn btn-tonal" to="/screens">
              <Icon name="screen" />
              {t("add.error.already_paired.action")}
            </Link>
          ) : canEditCode ? (
            <button type="button" className="btn btn-tonal" onClick={onEditCode}>
              <Icon name="pairing" />
              {t(`add.error.${motive}.action`)}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/**
 * Assistant « Ajouter un écran » : appareil, code vu sur la TV, nom/site/groupe, vérification.
 * Avec `?screen=<id>` (depuis la fiche d'un écran) il remplace l'appareil de cet écran : code puis vérification.
 */
export function AddScreen() {
  const { t } = useTranslation();
  const uid = useId();
  const api = useApi();
  const qc = useQueryClient();
  const sites = useSites();
  const targets = useWritableTargets();
  const screens = useScreens();
  const wanted = useSearchParams()[0].get("screen") ?? "";
  const existing = wanted ? screens.data?.find((s) => s.id === wanted && !s.archived_at) : undefined;
  const steps: StepId[] = wanted ? ["code", "verify"] : ["device", "code", "details", "verify"];
  const [index, setIndex] = useState(0);
  const [device, setDevice] = useState<Device>("browser");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [siteId, setSiteId] = useState("");
  const [groupId, setGroupId] = useState("");
  const [orientation, setOrientation] = useState<Orientation>("");
  const [discovered, setDiscovered] = useState<DiscoveredDevice | null>(null);
  const [autoName, setAutoName] = useState(false);
  const [missing, setMissing] = useState<{ name?: boolean; site?: boolean }>({});
  const [result, setResult] = useState<{ screen: Screen; replayed: boolean; configFailed: boolean; prefilled: boolean } | null>(null);
  const provisionable = useProvisioningAvailable((sites.data ?? []).map((x) => x.id));
  const heading = useRef<HTMLHeadingElement>(null);
  const codeInput = useRef<HTMLInputElement>(null);
  const mounted = useRef(false);

  const step: StepId = result ? "verify" : (steps[Math.min(index, steps.length - 1)] ?? "code");
  const current = steps.indexOf(step) + 1;

  useEffect(() => {
    if (mounted.current) heading.current?.focus();
    mounted.current = true;
  }, [step]);

  const pair = useMutation({
    mutationFn: async () => {
      if (existing) {
        await api.post("/pairings/claim", { code: normalizeCode(code), screen_id: existing.id });
        return { screen: existing, replayed: false, configFailed: false, prefilled: false };
      }
      const body: components["schemas"]["ScreenPair"] = {
        code: normalizeCode(code),
        name: name.trim(),
        site_id: siteId,
        group_id: groupId || null,
        ...(orientation ? { orientation } : {}),
      };
      const out = await api.post<Paired>("/screens/pair", body);
      let configFailed = false;
      if (discovered) {
        try {
          await applyDiscoveredDevice(api, out.screen.id, discovered);
        } catch {
          configFailed = true;
        }
      }
      return { screen: out.screen, replayed: out.replayed, configFailed, prefilled: !!discovered && discovered.suggested_drivers.length > 0 };
    },
    onSuccess: async (r) => {
      setResult(r);
      await qc.invalidateQueries({ queryKey: keys.screens });
      await qc.invalidateQueries({ queryKey: ["config"] });
    },
  });

  const activeSites = targets.sites.filter((s) => !s.archived_at);
  const siteGroups = targets.groups.filter((g) => g.site_id === siteId);
  const cs = codeState(code);

  const goCode = () => {
    pair.reset();
    setIndex(steps.indexOf("code"));
  };
  const restart = () => {
    pair.reset();
    setResult(null);
    setIndex(0);
    setCode("");
    setName("");
    setGroupId("");
    setOrientation("");
    setDiscovered(null);
    setMissing({});
  };
  const pick = (d: DiscoveredDevice | null) => {
    setDiscovered(d);
    if (d && (name.trim() === "" || autoName)) {
      setName(suggestedName(d));
      setAutoName(true);
    }
  };

  const submitCode = (e: FormEvent) => {
    e.preventDefault();
    if (cs !== "ok") return codeInput.current?.focus();
    if (existing) pair.mutate();
    else setIndex(steps.indexOf("details"));
  };
  const submitDetails = (e: FormEvent) => {
    e.preventDefault();
    const next = { name: !name.trim(), site: !siteId };
    setMissing(next);
    if (next.name || next.site) return;
    pair.mutate();
  };

  if (wanted && screens.isPending) return <QueryStatus query={screens} />;
  if (wanted && !existing) {
    return (
      <>
        <h1>{t("add.title")}</h1>
        <div className="alert alert-error" role="alert">
          <Icon name="failed" />
          <div>
            <p className="alert-text">{t("provision.screen_missing")}</p>
            <div className="actions">
              <Link className="btn btn-filled" to="/screens">
                <Icon name="screen" />
                {t("provision.go_screens")}
              </Link>
            </div>
          </div>
        </div>
      </>
    );
  }

  const fieldError = (id: string, shown: boolean, text: string) =>
    shown ? (
      <p className="field-error" id={id}>
        <Icon name="warning" />
        <span>{text}</span>
      </p>
    ) : null;
  const back = current > 1 && step !== "verify" ? (
    <button type="button" className="btn btn-text" onClick={() => { pair.reset(); setIndex(index - 1); }}>
      <Icon name="back" />
      {t("add.previous")}
    </button>
  ) : (
    <Link className="btn btn-text" to="/screens">
      {t("pairing.back")}
    </Link>
  );

  return (
    <>
      <div className="page-head">
        <h1>{existing ? t("add.title_replace", { name: existing.name }) : t("add.title")}</h1>
      </div>
      <p className="lead">{existing ? t("add.intro_replace") : t("add.intro")}</p>
      <ol className="wizard-steps list-reset" aria-label={t("add.progress_label")}>
        {steps.map((id, i) => (
          <li key={id} aria-current={id === step ? "step" : undefined} data-done={i + 1 < current ? "true" : undefined}>
            <span className="wizard-num">{i + 1 < current ? <Icon name="check" /> : i + 1}</span>
            <span>{t(`add.step.${id}`)}</span>
          </li>
        ))}
      </ol>
      <p className="visually-hidden" role="status" aria-live="polite">
        {t("add.step_of", { current, total: steps.length, name: t(`add.step.${step}`) })}
      </p>

      {step === "device" ? (
        <form className="card form-narrow" onSubmit={(e) => { e.preventDefault(); setIndex(1); }} aria-labelledby={`${uid}-h`}>
          <h2 id={`${uid}-h`} ref={heading} tabIndex={-1}>
            {t("add.device.title")}
          </h2>
          <fieldset className="field device-choices">
            <legend>{t("add.device.legend")}</legend>
            {DEVICES.map((d) => (
              <label key={d.id} className="choice">
                <input type="radio" name={`${uid}-device`} value={d.id} checked={device === d.id} onChange={() => setDevice(d.id)} />
                <Icon name={d.icon} />
                <span>
                  <strong>{t(`add.device.${d.id}.label`)}</strong>
                  <span className="field-help">{t(`add.device.${d.id}.help`)}</span>
                </span>
              </label>
            ))}
          </fieldset>
          {device === "android" ? (
            <div className="card-inner">
              <p>{t("add.device.android_choice")}</p>
              {provisionable ? (
                <Link className="btn btn-tonal" to="/screens/add/android">
                  <Icon name="detect" />
                  {t("add.device.android_install")}
                </Link>
              ) : (
                <>
                  <button type="button" className="btn btn-tonal" disabled aria-describedby={`${uid}-auto-help`}>
                    <Icon name="detect" />
                    {t("add.device.android_install")}
                  </button>
                  <p className="field-help" id={`${uid}-auto-help`}>
                    {t("pairing.auto.unavailable")}
                  </p>
                  <AgentSetupLink />
                </>
              )}
            </div>
          ) : null}
          <div className="actions">
            <button type="submit" className="btn btn-filled">
              {device === "android" ? t("add.device.have_code") : t("add.next")}
            </button>
            {back}
          </div>
        </form>
      ) : null}

      {step === "code" ? (
        <form className="card form-narrow" onSubmit={submitCode} aria-labelledby={`${uid}-h`} noValidate>
          <h2 id={`${uid}-h`} ref={heading} tabIndex={-1}>
            {t("add.code.title")}
          </h2>
          <div className="field">
            <label htmlFor={`${uid}-code`}>{t("pairing.code.label")}</label>
            <input
              id={`${uid}-code`}
              ref={codeInput}
              className="mono code-input"
              value={code}
              onChange={(e) => {
                setCode(e.target.value.toUpperCase());
                pair.reset();
              }}
              maxLength={16}
              autoComplete="off"
              autoCapitalize="characters"
              spellCheck={false}
              inputMode="text"
              required
              aria-invalid={cs === "long" ? true : undefined}
              aria-describedby={`${uid}-code-help ${uid}-code-state`}
            />
            <p className="field-help" id={`${uid}-code-help`}>
              {t(`add.code.help.${existing ? "other" : device}`, { minutes: CODE_TTL_MIN })}
            </p>
            <p className="field-help" id={`${uid}-code-state`} role="status" aria-live="polite">
              {cs === "ok" ? (
                <span className="state-ok">
                  <Icon name="check" /> {t("add.code.state.ok")}
                </span>
              ) : cs === "long" ? (
                <span className="state-bad">
                  <Icon name="warning" /> {t("add.code.state.long")}
                </span>
              ) : cs === "short" ? (
                <span>
                  <Icon name="processing" /> {t("add.code.state.short", { count: normalizeCode(code).length })}
                </span>
              ) : null}
            </p>
          </div>
          {pair.isError ? <PairError error={pair.error} onEditCode={goCode} canEditCode={false} /> : null}
          <div className="actions">
            <button type="submit" className="btn btn-filled" disabled={pair.isPending} aria-busy={pair.isPending}>
              <Icon name={existing ? "pairing" : "check"} />
              {existing ? t("add.code.submit_replace") : t("add.next")}
            </button>
            {back}
          </div>
        </form>
      ) : null}

      {step === "details" ? (
        <form className="card form-narrow" onSubmit={submitDetails} aria-labelledby={`${uid}-h`} noValidate>
          <h2 id={`${uid}-h`} ref={heading} tabIndex={-1}>
            {t("add.details.title")}
          </h2>
          <div className="field">
            <label htmlFor={`${uid}-name`}>{t("screens.create.name")}</label>
            <input
              id={`${uid}-name`}
              value={name}
              maxLength={120}
              required
              autoComplete="off"
              aria-invalid={missing.name ? true : undefined}
              onChange={(e) => {
                setName(e.target.value);
                setAutoName(false);
              }}
            />
            {fieldError(`${uid}-name-err`, !!missing.name, t("errors.field_required"))}
          </div>
          <div className="field">
            <label htmlFor={`${uid}-site`}>{t("screens.create.site")}</label>
            <select
              id={`${uid}-site`}
              value={siteId}
              required
              aria-invalid={missing.site ? true : undefined}
              onChange={(e) => {
                setSiteId(e.target.value);
                setGroupId("");
                setDiscovered(null);
              }}
            >
              <option value="">{t("form.choose")}</option>
              {activeSites.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            {fieldError(`${uid}-site-err`, !!missing.site, t("errors.field_required"))}
            {activeSites.length === 0 && sites.isSuccess ? (
              <p className="meta">
                {t("screens.create.no_site")} <Link to="/sites">{t("screens.create.go_sites")}</Link>
              </p>
            ) : null}
          </div>
          <div className="field">
            <label htmlFor={`${uid}-group`}>{t("screens.create.group")}</label>
            <select id={`${uid}-group`} value={groupId} disabled={!siteId} onChange={(e) => setGroupId(e.target.value)}>
              <option value="">{t("screens.create.no_group")}</option>
              {siteGroups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          </div>
          <InheritedSettings siteId={siteId} groupId={groupId} />
          <DiscoveryPanel siteId={siteId} selected={discovered?.id ?? null} onSelect={pick} />
          <details className="advanced">
            <summary>{t("form.advanced")}</summary>
            <div className="advanced-body">
              <div className="field">
                <label htmlFor={`${uid}-orientation`}>{t("screens.create.orientation")}</label>
                <select id={`${uid}-orientation`} value={orientation} onChange={(e) => setOrientation(e.target.value as Orientation)}>
                  <option value="">{t("add.details.orientation_auto")}</option>
                  <option value="landscape">{t("screens.orientation.landscape")}</option>
                  <option value="portrait">{t("screens.orientation.portrait")}</option>
                </select>
                <p className="field-help">{t("add.details.orientation_help")}</p>
              </div>
            </div>
          </details>
          {pair.isError ? <PairError error={pair.error} onEditCode={goCode} canEditCode /> : null}
          <div className="actions">
            <button type="submit" className="btn btn-filled" disabled={pair.isPending} aria-busy={pair.isPending}>
              <Icon name="online" />
              {t("add.details.submit")}
            </button>
            {back}
          </div>
        </form>
      ) : null}

      {step === "verify" && result ? (
        <>
          <h2 className="visually-hidden" ref={heading} tabIndex={-1}>
            {t("add.step.verify")}
          </h2>
          <VerifyStep screen={result.screen} configFailed={result.configFailed} prefilled={result.prefilled} replayed={result.replayed} onAnother={restart} />
        </>
      ) : null}
    </>
  );
}

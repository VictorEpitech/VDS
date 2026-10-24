import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";
import { ErrorAlert, QueryStatus, SuccessAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { ProvenanceBadge, RevertButton } from "../../components/Provenance";
import { useApi } from "../../lib/api";
import { configKey, useConfig, useScreens } from "../../lib/queries";
import { ModuleSettingsForm, useModuleSettings } from "../settings/ModuleSettingsForm";
import type { Values } from "../schema-form/schema";

/** Proposition de détection transmise par la supervision (état de navigation, jamais persistée). */
export interface DriverProposal {
  screenId: string;
  driver: string;
  config: Values;
}

/** Pilote d'alimentation du registre (activé ou non, avec ou sans réglages). */
interface DriverInfo {
  id: string;
  name: Record<string, string>;
  enabled: boolean;
  has_settings: boolean;
}

function useDriverCatalog() {
  const api = useApi();
  return useQuery({ queryKey: ["registry", "modules", "power-driver"], queryFn: () => api.get<DriverInfo[]>("/registry/modules?kind=power-driver") });
}

/** Entrée du registre et réglages d'un pilote ; les réglages ne sont demandés que si le registre en annonce. */
function useDriver(id: string) {
  const catalog = useDriverCatalog();
  const info = catalog.data?.find((d) => d.id === id);
  const q = useModuleSettings(id, !catalog.isPending && info?.has_settings !== false);
  return { info, q };
}

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

/** Nom localisé d'un pilote (manifeste du registre), sinon son identifiant. */
function useDriverName(id: string): string {
  const { i18n } = useTranslation();
  const { info, q } = useDriver(id);
  const names = (info?.name ?? q.data?.name ?? {}) as Record<string, string>;
  return names[i18n.language] ?? names.en ?? id;
}

function DriverLabel({ id }: { id: string }) {
  const name = useDriverName(id);
  return (
    <span>
      {name} {name !== id ? <code>{id}</code> : null}
    </span>
  );
}

/** Ordre d'essai des pilotes (power.cascade) : liste ordonnée, déplacement au clavier, provenance et retour à l'héritage. */
function CascadeEditor({ screenId, known, draft, setDraft }: { screenId: string; known: string[]; draft: string[] | null; setDraft: (v: string[] | null) => void }) {
  const { t } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const uid = useId();
  const cfg = useConfig("screen", screenId, "core");
  const [saved, setSaved] = useState(false);
  const done = async () => {
    setDraft(null);
    setSaved(true);
    await qc.invalidateQueries({ queryKey: configKey("screen", screenId, "core") });
    await qc.invalidateQueries({ queryKey: ["power", "screen"] });
  };
  const save = useMutation({
    mutationFn: (value: string[]) => api.put(`/config/screen/${screenId}/core/power.cascade`, { value }),
    onMutate: () => setSaved(false),
    onSuccess: done,
  });
  const revert = useMutation({
    mutationFn: () => api.delete(`/config/screen/${screenId}/core/power.cascade`),
    onMutate: () => setSaved(false),
    onSuccess: done,
  });
  const cur = cfg.data?.values["power.cascade"];
  if (!cur) return <QueryStatus query={cfg} />;
  const order = draft ?? strings(cur.value);
  const addable = known.filter((k) => !order.includes(k));
  const move = (i: number, d: -1 | 1) => {
    const next = [...order];
    [next[i], next[i + d]] = [next[i + d]!, next[i]!];
    setDraft(next);
  };
  return (
    <div className="card-inner">
      <h3>{t("power.drivers.cascade.title")}</h3>
      <p className="field-help">{t("power.drivers.cascade.help")}</p>
      {order.length === 0 ? (
        <p className="meta" role="status">
          {t("power.drivers.cascade.empty")}
        </p>
      ) : (
        <ol className="list-reset rows" aria-label={t("power.drivers.cascade.title")}>
          {order.map((id, i) => (
            <li key={id} className="row-line">
              <DriverLabel id={id} />
              <span className="actions">
                <button type="button" className="btn btn-text" disabled={i === 0} onClick={() => move(i, -1)} aria-label={t("power.drivers.cascade.up", { driver: id })}>
                  <Icon name="move_up" />
                  {t("power.drivers.cascade.up_short")}
                </button>
                <button type="button" className="btn btn-text" disabled={i === order.length - 1} onClick={() => move(i, 1)} aria-label={t("power.drivers.cascade.down", { driver: id })}>
                  <Icon name="move_down" />
                  {t("power.drivers.cascade.down_short")}
                </button>
                <button type="button" className="btn btn-text" onClick={() => setDraft(order.filter((x) => x !== id))} aria-label={t("power.drivers.cascade.remove", { driver: id })}>
                  <Icon name="delete" />
                  {t("power.drivers.cascade.remove_short")}
                </button>
              </span>
            </li>
          ))}
        </ol>
      )}
      {addable.length > 0 ? (
        <div className="field">
          <label htmlFor={`${uid}-add`}>{t("power.drivers.cascade.add")}</label>
          <select id={`${uid}-add`} value="" onChange={(e) => e.target.value && setDraft([...order, e.target.value])}>
            <option value="">{t("form.choose")}</option>
            {addable.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      <div className="provenance-row">
        <ProvenanceBadge origin={cur.origin} level="screen" />
        <RevertButton origin={cur.origin} level="screen" field={t("power.drivers.cascade.title")} pending={revert.isPending} onRevert={() => revert.mutate()} />
      </div>
      {save.isError ? <ErrorAlert error={save.error} /> : null}
      {revert.isError ? <ErrorAlert error={revert.error} /> : null}
      {saved ? <SuccessAlert>{t("power.drivers.cascade.saved")}</SuccessAlert> : null}
      <div className="actions">
        <button type="button" className="btn btn-filled" disabled={draft === null || save.isPending} aria-busy={save.isPending} onClick={() => draft && save.mutate(draft)}>
          <Icon name="check" />
          {t("common.save")}
        </button>
      </div>
    </div>
  );
}

/** Réglages d'un pilote : formulaire généré depuis son schéma. Un pilote sans schéma (404) n'a rien à régler. */
function DriverCard({ id, screenId, proposal, onSaved, onAdd }: { id: string; screenId: string; proposal?: Values; onSaved: () => void; onAdd?: () => void }) {
  const { t } = useTranslation();
  const { info, q } = useDriver(id);
  const name = useDriverName(id);
  if (q.isError && (q.error as { status?: number }).status === 404 && !info) return null;
  const noSettings = info ? !info.has_settings : false;
  return (
    <section className="card-inner" aria-label={t("power.drivers.card", { name })}>
      <h3>{name}</h3>
      {info && !info.enabled ? <p className="meta">{t("power.drivers.disabled")}</p> : null}
      {onAdd ? (
        <div className="actions">
          <button type="button" className="btn btn-tonal" onClick={onAdd} aria-label={t("power.drivers.cascade.add_driver", { driver: id })}>
            <Icon name="add" />
            {t("power.drivers.cascade.add_short")}
          </button>
        </div>
      ) : null}
      {noSettings ? <p className="meta">{t("power.drivers.no_settings")}</p> : q.data ? <ModuleSettingsForm moduleId={id} level="screen" targetId={screenId} proposal={proposal} onSaved={onSaved} /> : <QueryStatus query={q} />}
    </section>
  );
}

/** Pilotes d'alimentation d'un écran : réglages de chaque pilote (hérités ou surchargés) et ordre de la cascade. */
export function DriversSection() {
  const { t } = useTranslation();
  const uid = useId();
  const incoming = (useLocation().state as { proposal?: DriverProposal } | null)?.proposal;
  const screens = useScreens();
  const heading = useRef<HTMLHeadingElement>(null);
  const [screenId, setScreenId] = useState(incoming?.screenId ?? "");
  const [proposal, setProposal] = useState<DriverProposal | undefined>(incoming);
  const cfg = useConfig("screen", screenId, "core");
  const catalog = useDriverCatalog();
  const [draft, setDraft] = useState<string[] | null>(null);
  useEffect(() => {
    if (incoming) heading.current?.focus();
  }, [incoming]);
  const cascade = cfg.data?.values["power.cascade"];
  const order = draft ?? strings(cascade?.value);
  const known = [...new Set([...(catalog.data ?? []).map((d) => d.id), ...strings(cascade?.value), ...strings(cascade?.inherited), ...(proposal?.screenId === screenId && proposal ? [proposal.driver] : [])])];
  return (
    <section className="section" aria-labelledby="drivers-h">
      <h2 id="drivers-h" tabIndex={-1} ref={heading}>
        <Icon name="power" />
        {t("power.drivers.title")}
      </h2>
      <p className="lead">{t("power.drivers.intro")}</p>
      <div className="card">
        <div className="field">
          <label htmlFor={`${uid}-screen`}>{t("power.drivers.screen")}</label>
          <select
            id={`${uid}-screen`}
            value={screenId}
            onChange={(e) => {
              setScreenId(e.target.value);
              setDraft(null);
              setProposal(undefined);
            }}
          >
            <option value="">{t("form.choose")}</option>
            {(screens.data ?? [])
              .filter((s) => !s.archived_at)
              .map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
          </select>
        </div>
        {!screenId ? (
          <p className="meta">{t("power.drivers.pick")}</p>
        ) : !cascade ? (
          <QueryStatus query={cfg} />
        ) : (
          <>
            {known.map((id) => (
              <DriverCard key={`${screenId}-${id}`} id={id} screenId={screenId} onAdd={order.includes(id) ? undefined : () => setDraft([...order, id])} proposal={proposal?.driver === id && proposal.screenId === screenId ? proposal.config : undefined} onSaved={() => setProposal(undefined)} />
            ))}
            <CascadeEditor key={screenId} screenId={screenId} known={known} draft={draft} setDraft={setDraft} />
          </>
        )}
      </div>
    </section>
  );
}

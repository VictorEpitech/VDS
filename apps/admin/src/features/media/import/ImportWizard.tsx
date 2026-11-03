import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type DragEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useErrorText } from "../../../auth/error-text";
import { ErrorAlert, SuccessAlert } from "../../../components/feedback";
import { Icon } from "../../../components/Icon";
import { WizardSteps } from "../../../components/WizardSteps";
import { useApi } from "../../../lib/api";
import { useContentTypes, type ContentTypes } from "../../../lib/content-types";
import { keys, usePlaylists, type Media, type PlaylistDetail } from "../../../lib/queries";
import { OwnerField, useOwnerChoice } from "../../scope/Owner";
import type { TargetOrientation } from "./inspect";
import { useImportQueue, type ImportFileState } from "./queue";

type Dest = "new" | "existing" | "none";
interface Outcome {
  playlist: { id: string; name: string } | null;
  added: number;
  skipped: number;
}
const FINAL = 4;
/** Erreurs que réessayer ne résout pas : le fichier lui-même est en cause. */
const FILE_ISSUES = new Set(["media.type_invalid", "media.type_mismatch", "media.too_large", "media.empty"]);

/** Type de contenu du module qui sait lire ce genre de média (champ `x-vds-media` de son schéma d'élément). */
export function contentTypeFor(types: ContentTypes["types"], kind: Media["kind"]): string | undefined {
  return types.find((c) => c.schema.properties?.media?.["x-vds-media"] === kind)?.id;
}

const mb = (bytes: unknown, lng: string) => (typeof bytes === "number" ? new Intl.NumberFormat(lng, { maximumFractionDigits: bytes < 10 * 1048576 ? 1 : 0 }).format(bytes / 1048576) : "?");

function FileProblem({ f }: { f: ImportFileState }) {
  const { t, i18n } = useTranslation();
  const errorText = useErrorText();
  const code = f.errorCode ?? "errors.unknown";
  const p = f.errorParams ?? {};
  const text = code === "media.too_large" && typeof p.limit === "number" ? t("import.error.too_large", { size: mb(p.size, i18n.language), limit: mb(p.limit, i18n.language) }) : errorText(code, p);
  return (
    <span className="status-badge" data-state="offline" role="alert">
      <Icon name="failed" />
      <span>{text}</span>
    </span>
  );
}

function Wizard({ targetOrientation, onRestart }: { targetOrientation?: TargetOrientation; onRestart: () => void }) {
  const { t, i18n } = useTranslation();
  const uid = useId();
  const api = useApi();
  const qc = useQueryClient();
  const contentTypes = useContentTypes();
  const owner = useOwnerChoice();
  const playlists = usePlaylists();
  const [target, setTarget] = useState<TargetOrientation | "">(targetOrientation ?? "");
  const queue = useImportQueue({ target: target || undefined, owner: owner.owner });
  const [step, setStep] = useState(1);
  const [dragging, setDragging] = useState(false);
  const [dest, setDest] = useState<Dest>("new");
  const defaultName = t("import.playlist.default_name", { date: new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium" }).format(new Date()) });
  const [name, setName] = useState(defaultName);
  const [existingId, setExistingId] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const mounted = useRef(false);

  useEffect(() => {
    if (mounted.current) heading.current?.focus();
    mounted.current = true;
  }, [step]);

  const done = queue.files.filter((f) => f.status === "done");
  const failed = queue.files.filter((f) => f.status === "error");
  const sending = queue.files.filter((f) => f.status === "uploading" || f.status === "pending").length;
  const stepNames = [1, 2, 3, 4].map((n) => t(`import.step.${n}`));

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    queue.add([...e.dataTransfer.files]);
  };
  const send = () => {
    queue.start();
    setStep(2);
  };

  const finish = async () => {
    setBusy(true);
    setFailure(null);
    try {
      let target_: { id: string; name: string } | null = null;
      if (dest === "new") {
        const body = owner.owner === undefined ? { name: name.trim() } : { name: name.trim(), owner: owner.owner };
        const created = await api.post<PlaylistDetail>("/playlists", body);
        target_ = { id: created.id, name: created.name };
      } else if (dest === "existing") {
        const found = playlists.data?.items.find((p) => p.id === existingId);
        if (found) target_ = { id: found.id, name: found.name };
      }
      let added = 0;
      let skipped = 0;
      if (target_) {
        // Dans l'ordre d'envoi, un par un : l'ordre de la playlist est celui de la liste.
        for (const f of done) {
          const type = f.kind ? contentTypeFor(contentTypes.types, f.kind) : undefined;
          if (!type || !f.mediaId) {
            skipped += 1;
            continue;
          }
          await api.post(`/playlists/${target_.id}/items`, { content_type: type, props: { media: f.mediaId }, duration_ms: null });
          added += 1;
        }
        void qc.invalidateQueries({ queryKey: keys.playlists });
      }
      setOutcome({ playlist: target_, added, skipped });
      setStep(FINAL);
    } catch (err) {
      setFailure(err);
    } finally {
      setBusy(false);
    }
  };

  const canFinish = dest === "none" || (dest === "new" ? name.trim() !== "" : existingId !== "");

  return (
    <>
      <WizardSteps label={t("import.progress_label")} steps={stepNames} current={step} />
      <p className="visually-hidden" role="status" aria-live="polite">
        {t("import.live", { step, total: FINAL, name: stepNames[step - 1] })}
      </p>

      {step === 1 ? (
        <section className="card" aria-labelledby={`${uid}-h`}>
          <h2 id={`${uid}-h`} ref={heading} tabIndex={-1}>
            {t("import.step1.title")}
          </h2>
          <OwnerField choice={owner} />
          <div className="field">
            <label htmlFor={`${uid}-target`}>{t("import.target.label")}</label>
            <select id={`${uid}-target`} value={target} onChange={(e) => setTarget(e.target.value as TargetOrientation | "")} aria-describedby={`${uid}-target-h`}>
              <option value="">{t("import.target.any")}</option>
              <option value="landscape">{t("import.target.landscape")}</option>
              <option value="portrait">{t("import.target.portrait")}</option>
            </select>
            <p className="field-help" id={`${uid}-target-h`}>
              {t("import.target.help")}
            </p>
          </div>
          <div
            className="dropzone"
            data-dragging={dragging || undefined}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <Icon name="upload" />
            <p>{t("media.upload.drop")}</p>
            <button type="button" className="btn btn-filled" onClick={() => input.current?.click()}>
              <Icon name="upload" />
              {t("media.upload.choose")}
            </button>
            <input
              ref={input}
              type="file"
              multiple
              accept={contentTypes.acceptedMime.join(",")}
              className="visually-hidden"
              aria-label={t("media.upload.input")}
              tabIndex={-1}
              onChange={(e) => {
                queue.add([...(e.target.files ?? [])]);
                e.target.value = "";
              }}
            />
          </div>
          <p className="field-help">{t("import.checks")}</p>
          {queue.files.length === 0 ? (
            <p className="meta" role="status">
              {t("import.empty")}
            </p>
          ) : (
            <ul className="uploads list-reset" aria-label={t("import.list")}>
              {queue.files.map((f) => (
                <li key={f.id} className="upload">
                  <span className="upload-name">{f.file.name}</span>
                  <span className="meta">{mb(f.file.size, i18n.language)} {t("import.mb")}</span>
                  {f.warnings.map((w) => (
                    <span key={w} className="status-badge" data-state="power_failed">
                      <Icon name="warning" />
                      <span>{t(`import.warning.${w}`)}</span>
                    </span>
                  ))}
                  <button type="button" className="btn btn-text" onClick={() => queue.remove(f.id)} aria-label={t("import.remove_file", { name: f.file.name })}>
                    <Icon name="delete" />
                    {t("import.remove")}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="actions">
            <button type="button" className="btn btn-filled" disabled={queue.files.length === 0} onClick={send}>
              <Icon name="upload" />
              {t("import.send", { count: queue.files.length })}
            </button>
          </div>
        </section>
      ) : null}

      {step === 2 ? (
        <section className="card" aria-labelledby={`${uid}-h`}>
          <h2 id={`${uid}-h`} ref={heading} tabIndex={-1}>
            {t("import.step2.title")}
          </h2>
          <p className="meta" role="status" aria-live="polite">
            {sending > 0 ? t("import.progress_count", { done: done.length + failed.length, total: queue.files.length }) : t("import.finished", { done: done.length, failed: failed.length })}
          </p>
          <ul className="uploads list-reset" aria-label={t("media.upload.list")}>
            {queue.files.map((f) => (
              <li key={f.id} className="upload" data-state={f.status}>
                <span className="upload-name">{f.file.name}</span>
                {f.status === "uploading" || f.status === "pending" ? (
                  <>
                    <progress max={100} value={Math.round(f.progress * 100)} aria-label={t("media.upload.progress", { name: f.file.name })} />
                    <span className="upload-pct">{f.status === "pending" ? t("import.queued") : t("media.upload.percent", { percent: Math.round(f.progress * 100) })}</span>
                  </>
                ) : f.status === "done" ? (
                  <span className="status-badge" data-state="online">
                    <Icon name="online" />
                    <span>{t("media.upload.done")}</span>
                  </span>
                ) : (
                  <>
                    <FileProblem f={f} />
                    {f.errorCode && FILE_ISSUES.has(f.errorCode) ? null : (
                      <button type="button" className="btn btn-text" onClick={() => queue.retry(f.id)} aria-label={t("import.retry_file", { name: f.file.name })}>
                        <Icon name="rotate" />
                        {t("import.retry")}
                      </button>
                    )}
                    <button type="button" className="btn btn-text" onClick={() => queue.remove(f.id)} aria-label={t("import.remove_file", { name: f.file.name })}>
                      <Icon name="delete" />
                      {t("import.remove")}
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
          <div className="actions">
            <button type="button" className="btn btn-text" disabled={sending > 0} onClick={() => setStep(1)}>
              <Icon name="back" />
              {t("common.back")}
            </button>
            <button type="button" className="btn btn-filled" disabled={sending > 0 || done.length === 0} onClick={() => setStep(3)}>
              <Icon name="next" />
              {t("common.continue")}
            </button>
          </div>
        </section>
      ) : null}

      {step === 3 ? (
        <section className="card form-narrow" aria-labelledby={`${uid}-h`}>
          <h2 id={`${uid}-h`} ref={heading} tabIndex={-1}>
            {t("import.step3.title")}
          </h2>
          <p className="meta">{t("import.step3.intro", { count: done.length })}</p>
          <fieldset className="field">
            <legend>{t("import.dest.label")}</legend>
            {(["new", "existing", "none"] as const).map((d) => (
              <label key={d} className="choice">
                <input type="radio" name={`${uid}-dest`} checked={dest === d} onChange={() => setDest(d)} />
                <span>{t(`import.dest.${d}`)}</span>
              </label>
            ))}
          </fieldset>
          {dest === "new" ? (
            <div className="field">
              <label htmlFor={`${uid}-name`}>{t("import.playlist.name")}</label>
              <input id={`${uid}-name`} value={name} maxLength={120} autoComplete="off" onChange={(e) => setName(e.target.value)} aria-invalid={name.trim() === "" ? true : undefined} />
            </div>
          ) : null}
          {dest === "existing" ? (
            <div className="field">
              <label htmlFor={`${uid}-existing`}>{t("import.playlist.existing")}</label>
              <select id={`${uid}-existing`} value={existingId} onChange={(e) => setExistingId(e.target.value)}>
                <option value="">{t("import.playlist.choose")}</option>
                {(playlists.data?.items ?? []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              {playlists.isSuccess && playlists.data.items.length === 0 ? <p className="field-help">{t("import.playlist.none")}</p> : null}
            </div>
          ) : null}
          {failure ? <ErrorAlert error={failure} /> : null}
          <div className="actions">
            <button type="button" className="btn btn-text" disabled={busy} onClick={() => setStep(2)}>
              <Icon name="back" />
              {t("common.back")}
            </button>
            <button type="button" className="btn btn-filled" disabled={busy || !canFinish} aria-busy={busy} onClick={() => void finish()}>
              <Icon name={dest === "none" ? "check" : "playlist"} />
              {dest === "none" ? t("import.finish") : t("import.add_to_playlist")}
            </button>
          </div>
        </section>
      ) : null}

      {step === FINAL ? (
        <section className="card" aria-labelledby={`${uid}-h`}>
          <h2 id={`${uid}-h`} ref={heading} tabIndex={-1}>
            {t("import.step4.title")}
          </h2>
          <SuccessAlert>{t("import.summary.sent", { count: done.length })}</SuccessAlert>
          {outcome?.playlist ? <p role="status">{t("import.summary.added", { count: outcome.added, name: outcome.playlist.name })}</p> : null}
          {outcome && outcome.skipped > 0 ? (
            <p className="alert alert-warning" role="status">
              <Icon name="warning" />
              <span>{t("import.summary.skipped", { count: outcome.skipped })}</span>
            </p>
          ) : null}
          {failed.length > 0 ? <p className="meta">{t("import.summary.failed", { count: failed.length })}</p> : null}
          <div className="actions">
            {outcome?.playlist ? (
              <Link className="btn btn-filled" to={`/content/playlists/${outcome.playlist.id}`}>
                <Icon name="playlist" />
                {t("import.summary.open_playlist")}
              </Link>
            ) : null}
            <Link className={outcome?.playlist ? "btn btn-text" : "btn btn-filled"} to="/content/media">
              <Icon name="media" />
              {t("import.summary.open_library")}
            </Link>
            <button type="button" className="btn btn-text" onClick={onRestart}>
              <Icon name="add" />
              {t("import.summary.again")}
            </button>
          </div>
        </section>
      ) : null}
    </>
  );
}

/** Assistant « Ajouter un contenu » : fichiers, envoi (2 en parallèle), playlist, résumé. Tout passe par les API existantes. */
export function ImportWizard({ targetOrientation }: { targetOrientation?: TargetOrientation }) {
  const [run, setRun] = useState(0);
  return <Wizard key={run} targetOrientation={targetOrientation} onRestart={() => setRun((n) => n + 1)} />;
}

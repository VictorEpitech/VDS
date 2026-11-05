import { useRef, useState, type DragEvent } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useErrorText } from "../auth/error-text";
import { Icon } from "../components/Icon";
import { MediaBrowser } from "../features/media/MediaBrowser";
import { OwnerField, useOwnerChoice } from "../features/scope/Owner";
import { useUploads } from "../features/media/upload";
import { useContentTypes } from "../lib/content-types";
import { PageTitle } from "../features/help/HelpPanel";

export function MediaLibrary() {
  const { t, i18n } = useTranslation();
  const errorText = useErrorText();
  const contentTypes = useContentTypes();
  const { jobs, start: send, dismiss } = useUploads();
  const owner = useOwnerChoice();
  const start = (files: File[]) => send(files, owner.owner);
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    setShowUpload(true);
    start([...e.dataTransfer.files]);
  };

  return (
    <>
      <div className="page-head">
        <PageTitle topic="content">{t("media.title")}</PageTitle>
        <div className="actions">
          <button
            type="button"
            className={`btn ${showUpload ? "btn-tonal" : "btn-text"}`}
            onClick={() => setShowUpload((prev) => !prev)}
            aria-expanded={showUpload || jobs.length > 0}
          >
            <Icon name="upload" />
            {t("media.upload.title")}
          </button>
          <Link className="btn btn-filled" to="/content/media/add">
            <Icon name="add" />
            {t("media.add_content")}
          </Link>
        </div>
      </div>
      <p className="lead">{t("media.intro")}</p>

      {(showUpload || jobs.length > 0 || dragging) ? (
        <section className="card media-upload-section" aria-labelledby="upload-title">
        <div className="media-upload-head">
          <h2 id="upload-title">{t("media.upload.title")}</h2>
          <button
            type="button"
            className="btn btn-text btn-sm"
            onClick={() => setShowUpload(false)}
            aria-label={t("common.close")}
          >
            ✕
          </button>
        </div>
        <OwnerField choice={owner} />
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
          <button type="button" className="btn btn-filled" onClick={() => inputRef.current?.click()}>
            <Icon name="upload" />
            {t("media.upload.choose")}
          </button>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={contentTypes.acceptedMime.join(",")}
            className="visually-hidden"
            aria-label={t("media.upload.input")}
            tabIndex={-1}
            onChange={(e) => {
              start([...(e.target.files ?? [])]);
              e.target.value = "";
            }}
          />
        </div>
        <p className="field-help">{t("media.upload.accepts", { types: contentTypes.types.map((c) => c.name[i18n.language === "en" ? "en" : "fr"]).join(", ") })}</p>
        {jobs.length > 0 ? (
          <ul className="uploads list-reset" aria-label={t("media.upload.list")}>
            {jobs.map((j) => (
              <li key={j.key} className="upload" data-state={j.state}>
                <span className="upload-name">{j.name}</span>
                {j.state === "uploading" ? (
                  <>
                    <progress max={100} value={Math.round(j.progress * 100)} aria-label={t("media.upload.progress", { name: j.name })} />
                    <span className="upload-pct">{t("media.upload.percent", { percent: Math.round(j.progress * 100) })}</span>
                  </>
                ) : j.state === "done" ? (
                  <span className="status-badge" data-state="online">
                    <Icon name="online" />
                    <span>{t("media.upload.done")}</span>
                  </span>
                ) : (
                  <>
                    <span className="status-badge" data-state="offline" role="alert">
                      <Icon name="failed" />
                      <span>{errorText(j.error?.code ?? "errors.unknown", j.error?.params)}</span>
                    </span>
                  </>
                )}
                {j.state !== "uploading" ? (
                  <button type="button" className="btn btn-text" onClick={() => dismiss(j.key)}>
                    {t("common.dismiss")}
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </section>
      ) : null}

      <MediaBrowser onEmptyAction={() => { setShowUpload(true); inputRef.current?.click(); }} />
    </>
  );
}

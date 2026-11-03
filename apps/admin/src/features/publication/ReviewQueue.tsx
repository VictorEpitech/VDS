import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ErrorAlert, Empty, QueryStatus } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { ApiError } from "../../lib/api";
import { formatDateTime } from "../../lib/media-view";
import { PublicationBadge } from "./PublicationBadge";
import { usePublicationActions, usePublicationList, useRefreshPublications, type Publication } from "./publication";

const EDIT_PATH = { playlist: "/content/playlists", layout: "/content/layouts", schedule: "/playback" } as const;

function ReviewRow({ p }: { p: Publication }) {
  const { t, i18n } = useTranslation();
  const uid = useId();
  const { approve, reject } = usePublicationActions(p.entity_type, p.entity_id);
  const refresh = useRefreshPublications();
  const [rejecting, setRejecting] = useState(false);
  const [comment, setComment] = useState("");
  const [missing, setMissing] = useState(false);
  const [withDeps, setWithDeps] = useState(false);
  const depsError = approve.error instanceof ApiError && approve.error.code.endsWith("publication.dependency_unpublished");
  const stale = approve.error instanceof ApiError && approve.error.code.endsWith("publication.stale");
  const deps = (p.blocked_by?.length ?? 0) > 0 || depsError;
  const name = p.name ?? t(`publication.type.${p.entity_type}`);
  const link = p.entity_type === "schedule" ? EDIT_PATH.schedule : `${EDIT_PATH[p.entity_type]}/${p.entity_id}`;
  return (
    <li className="row card" aria-labelledby={`${uid}-n`}>
      <div className="row-main">
        <h3 id={`${uid}-n`}>{name}</h3>
        <div className="badges">
          <span className="meta">{t(`publication.type.${p.entity_type}`)}</span>
          <PublicationBadge publication={p} />
        </div>
        {p.submitted_at ? (
          <p className="meta">{t("publication.submitted", { who: p.submitted_by?.display_name ?? t("publication.someone"), date: formatDateTime(p.submitted_at, i18n.language) })}</p>
        ) : null}
        <p>
          <Link to={link}>{t("publication.review.open", { name })}</Link>
        </p>
        {deps ? (
          <label className="field-check">
            <input type="checkbox" checked={withDeps} onChange={(e) => setWithDeps(e.target.checked)} />
            <span>{t("publication.review.include_deps")}</span>
          </label>
        ) : null}
        {approve.isError ? (
          <ErrorAlert error={approve.error}>
            {stale ? (
              <button type="button" className="btn btn-text" onClick={() => { approve.reset(); void refresh(); }}>
                {t("publication.review.reload")}
              </button>
            ) : null}
          </ErrorAlert>
        ) : null}
        {rejecting ? (
          <form
            className="form-narrow"
            noValidate
            onSubmit={(e) => {
              e.preventDefault();
              const c = comment.trim();
              setMissing(c === "");
              if (c) reject.mutate(c);
            }}
          >
            <div className="field">
              <label htmlFor={`${uid}-c`}>{t("publication.review.reason")}</label>
              <textarea id={`${uid}-c`} rows={3} maxLength={500} value={comment} required aria-invalid={missing ? true : undefined} onChange={(e) => setComment(e.target.value)} />
              {missing ? (
                <p className="field-error">
                  <Icon name="warning" />
                  <span>{t("errors.field_required")}</span>
                </p>
              ) : null}
            </div>
            {reject.isError ? <ErrorAlert error={reject.error} /> : null}
            <div className="actions">
              <button type="submit" className="btn btn-danger" disabled={reject.isPending}>
                <Icon name="revoke" />
                {t("publication.review.reject_confirm")}
              </button>
              <button type="button" className="btn btn-text" onClick={() => setRejecting(false)}>
                {t("common.cancel")}
              </button>
            </div>
          </form>
        ) : null}
      </div>
      {!rejecting ? (
        <div className="actions">
          <button
            type="button"
            className="btn btn-filled"
            disabled={approve.isPending}
            aria-label={t("publication.review.approve_named", { name })}
            onClick={() => approve.mutate({ expected_rev: p.working_rev, include_dependencies: withDeps })}
          >
            <Icon name="check" />
            {t("publication.review.approve")}
          </button>
          <button type="button" className="btn btn-text" aria-label={t("publication.review.reject_named", { name })} onClick={() => { reject.reset(); setRejecting(true); }}>
            {t("publication.review.reject")}
          </button>
        </div>
      ) : null}
    </li>
  );
}

/** File « À valider » (administrateur) : approuve exactement la révision vue, refuse avec un motif obligatoire. */
export function ReviewQueue() {
  const { t } = useTranslation();
  const list = usePublicationList({ state: "in_review" });
  return (
    <section aria-labelledby="review-h">
      <QueryStatus query={list} />
      {list.isSuccess && list.data.length === 0 ? <Empty icon="check">{t("publication.review.empty")}</Empty> : null}
      <h2 id="review-h" className="visually-hidden">
        {t("publication.review.list")}
      </h2>
      <ul className="rows list-reset">
        {list.data?.map((p) => (
          <ReviewRow key={`${p.entity_type}-${p.entity_id}-${p.working_rev}`} p={p} />
        ))}
      </ul>
    </section>
  );
}

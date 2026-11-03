import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ErrorAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { formatDateTime } from "../../lib/media-view";
import { PublicationBadge } from "./PublicationBadge";
import { usePublication, usePublicationActions, useIsAdmin, viewOf, type PublicationType } from "./publication";

/** Badge d'état, motif de refus et actions de l'éditeur (soumettre / retirer). Le serveur décide ; l'admin masque seulement les boutons. */
export function PublicationPanel({ type, id, writable = true }: { type: PublicationType; id: string; writable?: boolean }) {
  const { t, i18n } = useTranslation();
  const uid = useId();
  const pub = usePublication(type, id);
  const actions = usePublicationActions(type, id);
  const isAdmin = useIsAdmin();
  const [asking, setAsking] = useState(false);
  const [comment, setComment] = useState("");
  const p = pub.data;
  if (!p) return null;
  const view = viewOf(p);
  const canSubmit = writable && p.state === "draft";
  const canWithdraw = writable && p.state === "in_review";
  const error = actions.submit.error ?? actions.withdraw.error;
  return (
    <section className="card publication-panel" aria-labelledby={`${uid}-t`}>
      <h2 id={`${uid}-t`}>{t("publication.title")}</h2>
      <div className="badges">
        <PublicationBadge publication={p} />
      </div>
      <p className="meta">{t(`publication.hint.${view}`)}</p>
      {p.state === "in_review" && p.submitted_at ? (
        <p className="meta">{t("publication.submitted", { who: p.submitted_by?.display_name ?? t("publication.someone"), date: formatDateTime(p.submitted_at, i18n.language) })}</p>
      ) : null}
      {p.state === "draft" && p.review_comment ? (
        <div className="alert alert-warning" role="status">
          <Icon name="warning" />
          <div>
            <p className="alert-text">{t("publication.rejected")}</p>
            <blockquote className="publication-comment">{p.review_comment}</blockquote>
          </div>
        </div>
      ) : null}
      {p.state === "draft" && (p.blocked_by?.length ?? 0) > 0 ? (
        <p className="alert alert-warning" role="status">
          <Icon name="warning" />
          <span>{t("publication.blocked", { count: p.blocked_by?.length ?? 0 })}</span>
        </p>
      ) : null}
      {error ? <ErrorAlert error={error} /> : null}
      <div className="actions">
        {canSubmit && !asking ? (
          <button type="button" className="btn btn-filled" onClick={() => { actions.submit.reset(); setAsking(true); }}>
            <Icon name="next" />
            {t("publication.submit")}
          </button>
        ) : null}
        {canWithdraw ? (
          <button type="button" className="btn btn-text" disabled={actions.withdraw.isPending} onClick={() => actions.withdraw.mutate()}>
            <Icon name="back" />
            {t("publication.withdraw")}
          </button>
        ) : null}
        {isAdmin && p.state === "in_review" ? (
          <Link className="btn btn-text" to="/playback/review">
            {t("publication.open_queue")}
          </Link>
        ) : null}
      </div>
      {canSubmit && asking ? (
        <form
          className="form-narrow"
          onSubmit={(e) => {
            e.preventDefault();
            actions.submit.mutate(comment.trim() || undefined, { onSuccess: () => { setAsking(false); setComment(""); } });
          }}
        >
          <div className="field">
            <label htmlFor={`${uid}-c`}>{t("publication.comment")}</label>
            <textarea id={`${uid}-c`} value={comment} maxLength={500} rows={3} onChange={(e) => setComment(e.target.value)} />
          </div>
          <div className="actions">
            <button type="submit" className="btn btn-filled" disabled={actions.submit.isPending}>
              <Icon name="next" />
              {t("publication.submit_confirm")}
            </button>
            <button type="button" className="btn btn-text" onClick={() => setAsking(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      ) : null}
    </section>
  );
}

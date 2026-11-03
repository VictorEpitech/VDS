import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useErrorText } from "../../auth/error-text";
import { Icon } from "../../components/Icon";
import { ApiError } from "../../lib/api";
import { causeOf } from "../commands/CommandPanel";
import { reasonOf } from "../supervision/DetectPanel";
import { useActionQueue } from "./ActionQueueProvider";
import { isPlayerKind, type Batch, type Outcome } from "./queue";

/** Bandeau des actions en cours : attente annulable, progression, résultat par écran. Visible sur toutes les pages. */
export function ActionTray() {
  const { batches } = useActionQueue();
  if (!batches.length) return null;
  return (
    <div className="action-tray">
      {batches.map((b) => (
        <BatchView key={b.id} batch={b} />
      ))}
    </div>
  );
}

function BatchView({ batch }: { batch: Batch }) {
  const { t } = useTranslation();
  const errorText = useErrorText();
  const { queue } = useActionQueue();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (batch.phase !== "waiting") return;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [batch.phase]);

  const summary = t(`actions.summary.${batch.kind}`, { count: batch.targets.length });
  const skipped = batch.skipped > 0 ? <p className="meta">{t(isPlayerKind(batch.kind) ? "actions.skipped_player" : "actions.skipped", { count: batch.skipped })}</p> : null;
  const outcomes = batch.targets.map((x) => [x, batch.results[x.screenId]] as const);
  const failed = outcomes.filter((o): o is readonly [(typeof o)[0], Extract<Outcome, { status: "failed" }>] => o[1]?.status === "failed");
  const finished = outcomes.filter((o) => o[1]?.status !== "pending").length;

  if (batch.phase === "waiting") {
    const left = Math.max(0, Math.ceil((batch.dueAt - now) / 1000));
    return (
      <section className="alert alert-info" aria-label={summary}>
        <Icon name="processing" />
        <div>
          <p role="status">{t("actions.waiting", { summary })}</p>
          <p className="meta" aria-hidden="true">
            {t("actions.countdown", { seconds: left })}
          </p>
          {skipped}
          <p className="meta">{t("actions.unload_hint")}</p>
          <div className="actions">
            <button type="button" className="btn btn-text" onClick={() => queue.cancel(batch.id)}>
              <Icon name="back" />
              {t("actions.cancel")}
            </button>
          </div>
        </div>
      </section>
    );
  }

  if (batch.phase === "running") {
    return (
      <section className="alert alert-info" aria-label={summary}>
        <Icon name="processing" />
        <div>
          <p role="status">{t("actions.running", { summary, done: finished, total: batch.targets.length })}</p>
          {skipped}
        </div>
      </section>
    );
  }

  const reason = (o: Extract<Outcome, { status: "failed" }>) =>
    o.playerCommand ? (causeOf(t, o.playerCommand) ?? t("supervision.command.reasons.unknown")) : o.command ? reasonOf(t, o.command) : o.error instanceof ApiError ? errorText(o.error.code, o.error.params) : t("supervision.command.reasons.unknown");
  const ok = batch.targets.length - failed.length;
  return (
    <section className={`alert ${failed.length ? "alert-error" : "alert-success"}`} aria-label={summary} role={failed.length ? "alert" : "status"}>
      <Icon name={failed.length ? "warning" : "online"} />
      <div>
        <p>{failed.length ? t("actions.done_partial", { summary, ok, failed: failed.length }) : t("actions.done_all", { summary })}</p>
        {skipped}
        {failed.length ? (
          <ul className="list-reset">
            {failed.map(([x, o]) => (
              <li key={x.screenId}>{t("actions.failure_line", { name: x.name, reason: reason(o) })}</li>
            ))}
          </ul>
        ) : null}
        <div className="actions">
          {failed.length ? (
            <button type="button" className="btn btn-text" onClick={() => queue.retryFailed(batch.id)}>
              <Icon name="rotate" />
              {t("actions.retry_failed", { count: failed.length })}
            </button>
          ) : null}
          <button type="button" className="btn btn-text" onClick={() => queue.dismiss(batch.id)}>
            {t("common.dismiss")}
          </button>
        </div>
      </div>
    </section>
  );
}

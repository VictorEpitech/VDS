import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Icon } from "../../components/Icon";
import { useSourceText } from "./parts";
import type { Conflict } from "./planning";
import { timeFormatter } from "./time";

/** Conflits calculés par le serveur, triés par gravité ; chaque entrée = cause en clair + une action. */
export function ConflictPanel({ conflicts, tz, onEdit }: { conflicts: Conflict[]; tz: string; onEdit: (scheduleId: string) => void }) {
  const { t, i18n } = useTranslation();
  const src = useSourceText();
  const fmt = useMemo(() => timeFormatter(i18n.language, tz), [i18n.language, tz]);
  const sorted = useMemo(() => [...conflicts].sort((a, b) => Number(b.severity === "warning") - Number(a.severity === "warning")), [conflicts]);
  if (sorted.length === 0) return null;
  return (
    <section className="conflicts card" aria-labelledby="conflicts-h">
      <h2 id="conflicts-h">{t("planning.conflicts.title", { count: sorted.length })}</h2>
      <ul className="list-reset conflict-list">
        {sorted.map((c, i) => {
          const name = c.source.name ?? "";
          const other = c.other?.name ?? "";
          const editable = c.source.kind === "schedule" && c.source.id;
          const action =
            c.kind === "content_expires" ? (
              <Link className="btn btn-text" to="/content/playlists">
                <Icon name="playlist" />
                {t("planning.conflict.action.extend_expiry")}
              </Link>
            ) : c.kind === "content_empty" ? (
              <Link className="btn btn-text" to="/content/media">
                <Icon name="media" />
                {t("planning.conflict.action.open_media")}
              </Link>
            ) : editable ? (
              <button type="button" className="btn btn-text" onClick={() => onEdit(c.source.id!)}>
                <Icon name="schedule" />
                {c.kind === "gap" ? t("planning.conflict.action.edit") : t("planning.conflict.action.edit_priority")}
              </button>
            ) : null;
          return (
            <li key={`${c.kind}-${c.source.id}-${i}`} className="conflict" data-severity={c.severity}>
              <span className="conflict-sev">
                <Icon name={c.severity === "warning" ? "warning" : "detect"} />
                <span>{t(`planning.conflict.severity.${c.severity}`)}</span>
              </span>
              <p>
                {t(`planning.conflict.${c.kind}`, { name, other, hasOther: other ? "yes" : "no", count: c.occurrences, when: fmt.full(c.first_at), scope: src.scope(c.source) })}
              </p>
              {action}
            </li>
          );
        })}
      </ul>
      <p className="field-help">{t("planning.precedence")}</p>
    </section>
  );
}

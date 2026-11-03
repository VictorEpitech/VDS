import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Icon } from "../../components/Icon";
import { useNowAll } from "./now";
import { targetParam } from "./planning";

const MAX = 5;

/** Tableau de bord : les prochains changements d'affichage du parc, tels que le serveur les résout (`/planning/at`). */
export function NextChanges() {
  const { t, i18n } = useTranslation();
  const now = useNowAll();
  const list = useMemo(
    () =>
      (now.data ?? [])
        .filter((s) => s.next_change_at)
        .sort((a, b) => Date.parse(a.next_change_at!) - Date.parse(b.next_change_at!))
        .slice(0, MAX),
    [now.data],
  );
  const when = useMemo(() => new Intl.DateTimeFormat(i18n.language, { weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit" }), [i18n.language]);
  if (now.isPending || now.isError || list.length === 0) return null;
  return (
    <section className="card next-changes" aria-labelledby="next-changes-h">
      <h2 id="next-changes-h">
        <Icon name="next" />
        {t("home.next_changes.title")}
      </h2>
      <ul className="list-reset rows-compact">
        {list.map((s) => {
          const playing = s.segment.state === "off" ? t("planning.segment_state.off") : s.segment.state === "scheduled_off" ? t("states.scheduled_off") : (s.segment.content?.name ?? s.segment.source.name ?? "");
          return (
            <li key={s.screen_id}>
              <Link to={`/playback?target=${targetParam({ type: "screen", id: s.screen_id })}`}>{s.screen_name}</Link>
              <span className="meta">{` · ${t("home.next_changes.now", { name: playing })} · ${t("home.next_changes.at", { when: when.format(Date.parse(s.next_change_at!)) })}`}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

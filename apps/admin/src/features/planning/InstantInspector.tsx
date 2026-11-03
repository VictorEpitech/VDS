import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { QueryStatus } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { StatusBadge, type DevicePresence } from "../../components/StatusBadge";
import { useSession } from "../../auth/session";
import { useScreens, type Screen } from "../../lib/queries";
import { OverrideControls } from "../overrides/OverrideControls";
import { canForce, useOverrides } from "../overrides/overrides";
import { PriorityBadge, SourceOrigin, useSourceText } from "./parts";
import { usePlanningAt, type Loser, type Segment, type Target } from "./planning";
import { timeFormatter, utcToLocalInstant } from "./time";

interface Props {
  target: Target;
  atMs: number;
  tz: string;
  onChangeAt: (local: string) => void;
  onClose: () => void;
  onEdit: (scheduleId: string) => void;
}

function Chain({ winner, losers }: { winner: Segment; losers: Loser[] }) {
  const { t } = useTranslation();
  const src = useSourceText();
  if (losers.length === 0) return <p className="meta">{t("planning.chain.none")}</p>;
  return (
    <ul className="chain">
      {losers.map((l, i) => (
        <li key={i}>
          {t("planning.chain.line", { loser: src.label(l.source), winner: src.label(winner.source), reason: t(`planning.reason.${l.reason}`) })}
        </li>
      ))}
    </ul>
  );
}

function ScreenAt({ name, segment, nextChange, presence, fmt, allScreens, screenId, onEdit }: { name: string; segment: Segment; nextChange: string | null; presence: string; fmt: ReturnType<typeof timeFormatter>; allScreens: readonly Screen[]; screenId: string; onEdit: (id: string) => void }) {
  const { t } = useTranslation();
  const session = useSession();
  const allowed = canForce(session.data?.role);
  const overrides = useOverrides(allowed && segment.source.kind === "override");
  const override = overrides.data?.find((o) => o.id === segment.source.id);
  const [notice, setNotice] = useState("");
  const src = segment.source;
  const title = segment.state === "off" ? t("planning.segment_state.off") : segment.state === "scheduled_off" ? t("states.scheduled_off") : (segment.content?.name ?? src.name ?? "");
  return (
    <article className="inspector-screen" aria-labelledby={`ins-${screenId}`}>
      <h3 id={`ins-${screenId}`}>{name}</h3>
      <p className="inspector-what">
        {segment.state === "overridden" || segment.state === "scheduled_off" ? <StatusBadge state={segment.state} /> : null} <strong>{title}</strong>
      </p>
      <p className="meta">
        <span className="tabular">{`${fmt.time(segment.start)}–${fmt.time(segment.end)}`}</span>
        {src.priority_level ? <> {" · "}<PriorityBadge level={src.priority_level} value={src.priority} /></> : null}
      </p>
      <p>
        <SourceOrigin source={src} />
        {src.kind === "schedule" && src.id ? (
          <button type="button" className="btn btn-text" onClick={() => onEdit(src.id!)}>
            <Icon name="schedule" />
            {t("planning.inspector.open_schedule")}
          </button>
        ) : null}
      </p>
      {segment.reason ? <p className="meta">{t(`planning.reason.${segment.reason}`)}</p> : null}
      <h4>{t("planning.chain.title")}</h4>
      <Chain winner={segment} losers={segment.losers} />
      {override ? <OverrideControls override={override} screens={allScreens} label={name} isAdmin={session.data?.role === "admin"} onDone={setNotice} /> : null}
      {notice ? (
        <p className="alert alert-success" role="status">
          <Icon name="online" />
          <span>{notice}</span>
        </p>
      ) : null}
      <p className="meta">
        {nextChange ? t("planning.inspector.next_change", { when: fmt.full(nextChange) }) : t("planning.inspector.no_change")}
        {" · "}
        {t(`screens.device.presence.${(["online", "offline", "unseen", "revoked"] as DevicePresence[]).includes(presence as DevicePresence) ? presence : "unseen"}`)}
      </p>
    </article>
  );
}

/** « Ce qui joue à tel instant » : gagnant, chaîne d'explication, provenance ; feuille basse à 375 px. */
export function InstantInspector({ target, atMs, tz, onChangeAt, onClose, onEdit }: Props) {
  const { t, i18n } = useTranslation();
  const fmt = useMemo(() => timeFormatter(i18n.language, tz), [i18n.language, tz]);
  const at = usePlanningAt(target, new Date(Math.floor(atMs / 60_000) * 60_000).toISOString());
  const screens = useScreens();
  return (
    <aside className="inspector card" aria-labelledby="inspector-h">
      <div className="inspector-head">
        <h2 id="inspector-h">{t("planning.inspector.title", { when: fmt.full(atMs) })}</h2>
        <button type="button" className="btn btn-text" onClick={onClose}>
          {t("common.dismiss")}
        </button>
      </div>
      <div className="field">
        <label htmlFor="inspector-at">{t("planning.inspector.instant")}</label>
        <input id="inspector-at" type="datetime-local" value={utcToLocalInstant(atMs, tz)} onChange={(e) => e.target.value && onChangeAt(e.target.value)} />
        <p className="field-help">{t("planning.tz.shown_in", { tz })}</p>
      </div>
      <QueryStatus query={at} />
      {at.data?.screens.map((s) => (
        <ScreenAt key={s.screen_id} name={s.screen_name} segment={s.segment} nextChange={s.next_change_at} presence={s.presence} fmt={fmt} allScreens={screens.data ?? []} screenId={s.screen_id} onEdit={onEdit} />
      ))}
      <p className="field-help">{t("planning.precedence")}</p>
    </aside>
  );
}

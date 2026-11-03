import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useErrorText } from "../../auth/error-text";
import { ConfirmAction, ErrorAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { ApiError } from "../../lib/api";
import type { Screen, ScreenPlayback } from "../../lib/queries";
import { CommandBadge } from "./CommandBadge";
import { availableActions, canScreenshot, isCancellable, isTerminal, refreshAfterTerminal, relativeAge, useCancelCommand, useScreenCommands, useScreenshots, useSendCommand, type CommandAction, type PlayerCommand } from "./commands";
import { ScreenshotView, ShotImage } from "./ScreenshotView";

interface Props {
  screen: Screen;
  playback?: ScreenPlayback;
  isAdmin: boolean;
}

/** Cause traduite d'un échec ; les codes inconnus tombent sur un libellé générique (jamais le code brut). */
export function causeOf(t: (k: string, o?: Record<string, unknown>) => string, c: PlayerCommand): string | null {
  if (c.status === "ok" || c.status === "queued" || c.status === "sent" || c.status === "cancelled") return null;
  const key = `screens.commands.cause.${(c.code ?? c.status).replace(/\./g, "_")}`;
  return t(key, { defaultValue: t(`screens.commands.cause.${c.status}`, { defaultValue: t("screens.commands.cause.unknown") }) });
}

/** Actions de commande, capture et commandes récentes d'un écran. Ce que le serveur refuse n'est pas proposé. */
export function CommandPanel({ screen, playback, isAdmin }: Props) {
  const { t, i18n } = useTranslation();
  const errorText = useErrorText();
  const qc = useQueryClient();
  const uid = useId();
  const actions = availableActions(screen, isAdmin);
  const capable = canScreenshot(screen);
  const hasDevice = !!screen.device && screen.device.presence !== "revoked";
  const commands = useScreenCommands(screen.id, hasDevice);
  const shots = useScreenshots(screen.id, hasDevice && capable);
  const send = useSendCommand(screen.id);
  const cancel = useCancelCommand(screen.id);
  const [announce, setAnnounce] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const list = useMemo(() => commands.data ?? [], [commands.data]);

  // Annonce polie des changements d'état et rafraîchit la carte quand une capture aboutit.
  const seen = useRef(new Map<string, string>());
  useEffect(() => {
    for (const c of list) {
      const before = seen.current.get(c.id);
      seen.current.set(c.id, c.status);
      if (before === undefined || before === c.status) continue;
      setAnnounce(t("screens.commands.announce", { action: t(`screens.commands.name.${c.name.replace(".", "_")}`), status: t(`screens.commands.status.${c.status}`) }));
      if (c.status === "ok" && c.name === "screenshot") refreshAfterTerminal(qc, screen.id);
    }
  }, [list, qc, screen.id, t]);

  if (!hasDevice) return null;

  const run = (a: CommandAction) => send.mutate(a);
  const pendingFor = (a: CommandAction) => list.some((c) => !isTerminal(c) && c.name === a.name && JSON.stringify(c.args) === JSON.stringify(a.args));
  const label = (a: CommandAction) => t(`screens.commands.action.${a.id.replace(".", "_")}`);
  const visible = actions.filter((a) => !a.advanced);
  const adv = actions.filter((a) => a.advanced);

  const button = (a: CommandAction) => {
    const busy = send.isPending || pendingFor(a);
    if (a.destructive) {
      return (
        <ConfirmAction
          key={a.id}
          icon={a.icon}
          label={label(a)}
          confirmLabel={t(`screens.commands.confirm.${a.id.replace(".", "_")}`, { name: screen.name })}
          pending={busy}
          onConfirm={() => run(a)}
        />
      );
    }
    return (
      <button key={a.id} type="button" className="btn btn-text" disabled={busy} aria-label={t("screens.commands.action_for", { action: label(a), name: screen.name })} onClick={() => run(a)}>
        <Icon name={a.icon} />
        {label(a)}
      </button>
    );
  };

  return (
    <section className="command-panel" aria-labelledby={`${uid}-h`}>
      <h3 id={`${uid}-h`}>{t("screens.commands.title")}</h3>
      <ScreenshotView screen={screen} playback={playback} />
      {actions.length ? (
        <div className="actions" role="group" aria-label={t("screens.commands.group", { name: screen.name })}>
          {visible.map(button)}
          {adv.length ? (
            <button type="button" className="btn btn-text" aria-expanded={advanced} aria-controls={`${uid}-adv`} onClick={() => setAdvanced(!advanced)}>
              <Icon name="maintenance" />
              {t("screens.commands.advanced")}
            </button>
          ) : null}
        </div>
      ) : (
        <p className="meta">{t("screens.commands.none")}</p>
      )}
      {adv.length && advanced ? (
        <div className="actions" id={`${uid}-adv`}>
          {adv.map(button)}
        </div>
      ) : null}
      {capable && shots.data && shots.data.length > 1 ? (
        <ul className="shot-history list-reset" aria-label={t("screens.commands.shot.history")}>
          {shots.data.slice(1, 10).map((s) => (
            <li key={s.id}>
              <ShotImage shot={s} alt={t("screens.commands.shot.previous", { name: screen.name, age: relativeAge(s.captured_at, i18n.language) })} />
            </li>
          ))}
        </ul>
      ) : null}
      {send.isError ? (
        <p className="alert alert-error" role="alert">
          <Icon name="warning" />
          <span>{send.error instanceof ApiError ? errorText(send.error.code, send.error.params) : t("errors.unknown")}</span>
        </p>
      ) : null}
      {cancel.isError ? <ErrorAlert error={cancel.error} /> : null}
      <p className="visually-hidden" role="status" aria-live="polite">
        {announce}
      </p>
      {list.length ? (
        <>
          <CommandList commands={list.slice(0, 3)} onCancel={(id) => cancel.mutate(id)} cancelling={cancel.isPending} />
          {list.length > 3 ? (
            <details className="command-recent">
              <summary>{t("screens.commands.recent", { count: list.length - 3 })}</summary>
              <CommandList commands={list.slice(3)} onCancel={(id) => cancel.mutate(id)} cancelling={cancel.isPending} />
            </details>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function CommandList({ commands, onCancel, cancelling }: { commands: PlayerCommand[]; onCancel: (id: string) => void; cancelling: boolean }) {
  const { t, i18n } = useTranslation();
  return (
    <ul className="command-list list-reset" aria-label={t("screens.commands.recent_label")}>
      {commands.map((c) => {
        const cause = causeOf(t, c);
        return (
          <li key={c.id}>
            <span>{t(`screens.commands.name.${c.name.replace(".", "_")}`)}</span> <CommandBadge status={c.status} />{" "}
            <span className="meta">
              {c.requested_by ? `${c.requested_by} · ` : ""}
              <time dateTime={c.created_at}>{relativeAge(c.created_at, i18n.language)}</time>
            </span>
            {cause ? <span className="meta"> · {cause}</span> : null}
            {isCancellable(c) ? (
              <button type="button" className="btn btn-text" disabled={cancelling} aria-label={t("screens.commands.cancel_for", { action: t(`screens.commands.name.${c.name.replace(".", "_")}`) })} onClick={() => onCancel(c.id)}>
                <Icon name="back" />
                {t("screens.commands.cancel")}
              </button>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

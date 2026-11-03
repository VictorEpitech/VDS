import { useTranslation } from "react-i18next";
import { useErrorText } from "../../auth/error-text";
import { Icon } from "../../components/Icon";
import { ApiError } from "../../lib/api";
import type { Screen } from "../../lib/queries";
import { actionById, canScreenshot, useScreenCommands, useSendCommand } from "./commands";

/** « Actualiser la capture » : visible seulement si l'appareil sait capturer ; une seule demande à la fois. */
export function RefreshShot({ screen }: { screen: Screen }) {
  const { t } = useTranslation();
  const errorText = useErrorText();
  const send = useSendCommand(screen.id);
  const commands = useScreenCommands(screen.id, canScreenshot(screen) && send.isSuccess);
  if (!canScreenshot(screen)) return null;
  const action = actionById("screenshot")!;
  const busy = send.isPending || (commands.data ?? []).some((c) => c.name === "screenshot" && (c.status === "queued" || c.status === "sent"));
  return (
    <>
      <button type="button" className="btn btn-text" disabled={busy} aria-label={t("screens.commands.action_for", { action: t("screens.commands.action.screenshot"), name: screen.name })} onClick={() => send.mutate(action)}>
        <Icon name="media" />
        {t("screens.commands.action.screenshot")}
      </button>
      {busy ? (
        <span className="meta" role="status">
          {t("screens.commands.status.queued_hint")}
        </span>
      ) : null}
      {send.isError ? (
        <span className="alert alert-error" role="alert">
          <Icon name="warning" />
          <span>{send.error instanceof ApiError ? errorText(send.error.code, send.error.params) : t("errors.unknown")}</span>
        </span>
      ) : null}
    </>
  );
}

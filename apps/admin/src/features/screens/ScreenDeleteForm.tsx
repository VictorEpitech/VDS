import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ErrorAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { useApi } from "../../lib/api";
import { keys, type Screen } from "../../lib/queries";

/** Suppression définitive d'un écran (DELETE /screens/{id}) : le nom doit être retapé. Propose d'archiver à la place. */
export function ScreenDeleteForm({
  screen,
  onClose,
  onDone,
  onArchive,
}: {
  screen: Screen;
  onClose: () => void;
  onDone: (message: string) => void;
  onArchive: () => void;
}) {
  const { t } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const uid = useId();
  const [typed, setTyped] = useState("");
  const remove = useMutation({
    mutationFn: () => api.delete(`/screens/${screen.id}`),
    onSuccess: async () => {
      qc.setQueryData<Screen[]>(keys.screens, (old) => old?.filter((s) => s.id !== screen.id));
      await qc.invalidateQueries({ queryKey: keys.screens });
      onDone(t("screens.delete.done", { name: screen.name }));
    },
  });
  const matches = typed.trim() === screen.name;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (matches) remove.mutate();
  };

  return (
    <form className="card inline-form" onSubmit={submit} aria-label={t("screens.delete.title", { name: screen.name })}>
      <h3>{t("screens.delete.title", { name: screen.name })}</h3>
      <p role="alert">
        <Icon name="warning" /> {t("screens.delete.warning")}
      </p>
      <p className="meta">{t("screens.delete.archive_hint")}</p>
      <div className="field">
        <label htmlFor={`${uid}-confirm`}>{t("screens.delete.type_name", { name: screen.name })}</label>
        <input id={`${uid}-confirm`} value={typed} autoComplete="off" onChange={(e) => setTyped(e.target.value)} />
      </div>
      {remove.isError ? <ErrorAlert error={remove.error} /> : null}
      <div className="actions">
        <button type="submit" className="btn btn-danger-filled" disabled={!matches || remove.isPending} aria-busy={remove.isPending}>
          <Icon name="delete" />
          {remove.isError ? t("common.retry") : t("screens.delete.confirm")}
        </button>
        <button type="button" className="btn btn-text" onClick={onArchive}>
          {t("screens.delete.archive_instead")}
        </button>
        <button type="button" className="btn btn-text" onClick={onClose}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

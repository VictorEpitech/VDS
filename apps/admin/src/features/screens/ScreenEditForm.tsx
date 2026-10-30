import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ErrorAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { useApi } from "../../lib/api";
import { keys, useAllGroups, type Screen } from "../../lib/queries";

type Orientation = "landscape" | "portrait";

/** Modification d'un écran (PATCH /screens/{id}) : nom, orientation, groupe, archivage. Admin seulement. */
export function ScreenEditForm({ screen, onClose, onDone, initialArchived = false }: { screen: Screen; onClose: () => void; onDone: (message: string) => void; initialArchived?: boolean }) {
  const { t } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const uid = useId();
  const groups = useAllGroups();
  const [name, setName] = useState(screen.name);
  const [orientation, setOrientation] = useState<Orientation>(screen.orientation);
  const [groupId, setGroupId] = useState(screen.group_id ?? "");
  const [archived, setArchived] = useState(initialArchived);
  const [empty, setEmpty] = useState(false);
  const save = useMutation({
    mutationFn: (b: { name: string; orientation: Orientation; group_id: string | null; archived: boolean }) => api.patch<Screen>(`/screens/${screen.id}`, b),
    onSuccess: async (s, b) => {
      await qc.invalidateQueries({ queryKey: keys.screens });
      onDone(t(b.archived ? "screens.edit.archived" : "screens.edit.saved", { name: s.name }));
    },
  });
  const siteGroups = (groups.data ?? []).filter((g) => g.site_id === screen.site_id);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setEmpty(!name.trim());
    if (!name.trim()) return;
    save.mutate({ name: name.trim(), orientation, group_id: groupId || null, archived });
  };

  return (
    <form className="card inline-form" onSubmit={submit} aria-label={t("screens.edit.title", { name: screen.name })} noValidate>
      <h3>{t("screens.edit.title", { name: screen.name })}</h3>
      <div className="field">
        <label htmlFor={`${uid}-name`}>{t("screens.create.name")}</label>
        <input id={`${uid}-name`} value={name} maxLength={120} required aria-invalid={empty ? true : undefined} autoComplete="off" onChange={(e) => setName(e.target.value)} />
        {empty ? (
          <p className="field-error">
            <Icon name="warning" />
            <span>{t("errors.field_required")}</span>
          </p>
        ) : null}
      </div>
      <div className="field">
        <label htmlFor={`${uid}-orientation`}>{t("screens.create.orientation")}</label>
        <select id={`${uid}-orientation`} value={orientation} onChange={(e) => setOrientation(e.target.value as Orientation)}>
          <option value="landscape">{t("screens.orientation.landscape")}</option>
          <option value="portrait">{t("screens.orientation.portrait")}</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor={`${uid}-group`}>{t("screens.create.group")}</label>
        <select id={`${uid}-group`} value={groupId} onChange={(e) => setGroupId(e.target.value)}>
          <option value="">{t("screens.create.no_group")}</option>
          {siteGroups.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
      </div>
      <details className="field" open={initialArchived}>
        <summary>{t("form.advanced")}</summary>
        <label>
          <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} /> {t("screens.edit.archive")}
        </label>
        <p className="meta">{t("screens.edit.archive_hint")}</p>
      </details>
      {save.isError ? <ErrorAlert error={save.error} /> : null}
      <div className="actions">
        <button type="submit" className="btn btn-filled" disabled={save.isPending} aria-busy={save.isPending}>
          <Icon name="check" />
          {t("common.save")}
        </button>
        <button type="button" className="btn btn-text" onClick={onClose}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

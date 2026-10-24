import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ErrorAlert, QueryStatus } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { useSession } from "../../auth/session";
import { useApi } from "../../lib/api";
import { KioskSetting } from "../kiosk/KioskSetting";
import { keys, type Group } from "../../lib/queries";

/** Groupes d'un site et création d'un groupe (POST /sites/{id}/groups). */
export function GroupsPanel({ siteId }: { siteId: string }) {
  const { t } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const uid = useId();
  const session = useSession();
  const [name, setName] = useState("");
  const groups = useQuery({ queryKey: keys.groups(siteId), queryFn: () => api.get<Group[]>(`/sites/${siteId}/groups`) });
  const create = useMutation({
    mutationFn: (n: string) => api.post<Group>(`/sites/${siteId}/groups`, { name: n }),
    onSuccess: async () => {
      setName("");
      await qc.invalidateQueries({ queryKey: keys.groups(siteId) });
      await qc.invalidateQueries({ queryKey: ["groups", "all"] });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (name.trim()) create.mutate(name.trim());
  };
  return (
    <section className="section" aria-labelledby={`${uid}-h`}>
      <h2 id={`${uid}-h`}>
        <Icon name="screen" />
        {t("groups.title")}
      </h2>
      <p className="meta">{t("groups.intro")}</p>
      <QueryStatus query={groups} />
      {groups.isSuccess && groups.data.length === 0 ? <p className="meta">{t("groups.empty")}</p> : null}
      <ul className="list-reset">
        {(groups.data ?? []).map((g) => (
          <li key={g.id}>
            <span>{g.name}</span>
            {session.data?.role === "admin" ? <KioskSetting level="group" id={g.id} /> : null}
          </li>
        ))}
      </ul>
      {session.data?.role === "admin" ? (
        <form className="inline-form" onSubmit={submit} aria-label={t("groups.create.title")}>
          <div className="field">
            <label htmlFor={`${uid}-name`}>{t("groups.create.name")}</label>
            <input id={`${uid}-name`} value={name} maxLength={120} required autoComplete="off" onChange={(e) => setName(e.target.value)} />
          </div>
          {create.isError ? <ErrorAlert error={create.error} /> : null}
          <div className="actions">
            <button type="submit" className="btn btn-filled" disabled={create.isPending} aria-busy={create.isPending}>
              <Icon name="add" />
              {t("groups.create.submit")}
            </button>
          </div>
        </form>
      ) : null}
    </section>
  );
}

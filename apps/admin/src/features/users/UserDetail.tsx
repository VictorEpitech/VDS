import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";
import { useSession } from "../../auth/session";
import { ErrorAlert, QueryStatus, SuccessAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { ApiError, useApi } from "../../lib/api";
import { useOrganization } from "../../lib/queries";
import { useScopeTree, usersKey, type NodeRef, type UserOut, type UserPage } from "../scope/scope";
import { ScopePicker, useScopeSummary } from "./ScopePicker";
import { ActiveBadge, RoleBadge } from "./UsersPage";

/** Nombre d'administrateurs actifs : garde « dernier admin » côté interface (le serveur reste l'autorité). */
function useAdminCount() {
  const api = useApi();
  return useQuery({ queryKey: [...usersKey, "admins"] as const, queryFn: () => api.get<UserPage>("/users?role=admin&limit=2") });
}

const sameNodes = (a: readonly NodeRef[], b: readonly NodeRef[]) => a.length === b.length && a.every((n) => b.some((m) => m.type === n.type && m.id === n.id));

function AccountSection({ user }: { user: UserOut }) {
  const { t } = useTranslation();
  const uid = useId();
  const api = useApi();
  const qc = useQueryClient();
  const session = useSession();
  const admins = useAdminCount();
  const [role, setRole] = useState<UserOut["role"]>(user.role);
  const [active, setActive] = useState(user.is_active);
  const [saved, setSaved] = useState(false);
  const isSelf = session.data?.id === user.id;
  const lastAdmin = user.role === "admin" && user.is_active && (admins.data?.items.filter((a) => a.is_active).length ?? 2) <= 1;
  const providerRole = user.role_source === "provider";
  const dirty = role !== user.role || active !== user.is_active;
  const save = useMutation({
    mutationFn: () => api.patch<UserOut>(`/users/${user.id}`, { ...(role !== user.role ? { role } : {}), ...(active !== user.is_active ? { is_active: active } : {}) }),
    onSuccess: (next) => {
      setSaved(true);
      qc.setQueryData([...usersKey, user.id], next);
      void qc.invalidateQueries({ queryKey: usersKey });
    },
  });
  const roleLocked = providerRole || isSelf || lastAdmin;
  const hint = providerRole ? "users.role_hint.provider" : isSelf ? "users.role_hint.self" : lastAdmin ? "users.role_hint.last_admin" : null;

  return (
    <form
      className="card form-narrow"
      aria-labelledby={`${uid}-h`}
      onSubmit={(e) => {
        e.preventDefault();
        setSaved(false);
        save.mutate();
      }}
    >
      <h2 id={`${uid}-h`}>{t("users.account")}</h2>
      <div className="field">
        <label htmlFor={`${uid}-role`}>{t("users.field.role")}</label>
        <select id={`${uid}-role`} value={role} disabled={roleLocked} aria-describedby={hint ? `${uid}-role-h` : undefined} onChange={(e) => setRole(e.target.value as UserOut["role"])}>
          <option value="admin">{t("users.role.admin")}</option>
          <option value="editor">{t("users.role.editor")}</option>
        </select>
        {hint ? (
          <p className="field-help" id={`${uid}-role-h`}>
            <Icon name="lock" /> {t(hint)}
          </p>
        ) : null}
      </div>
      <div className="field field-check">
        <input id={`${uid}-active`} type="checkbox" checked={active} disabled={isSelf || lastAdmin} onChange={(e) => setActive(e.target.checked)} />
        <label htmlFor={`${uid}-active`}>{t("users.field.active")}</label>
      </div>
      {save.isError ? <ErrorAlert error={save.error} /> : null}
      {saved && !dirty ? <SuccessAlert>{t("users.saved")}</SuccessAlert> : null}
      <button type="submit" className="btn btn-filled" disabled={!dirty || save.isPending} aria-busy={save.isPending}>
        <Icon name="check" />
        {t("common.save")}
      </button>
    </form>
  );
}

function ScopeSection({ user }: { user: UserOut }) {
  const { t } = useTranslation();
  const uid = useId();
  const api = useApi();
  const qc = useQueryClient();
  const tree = useScopeTree();
  const org = useOrganization();
  const summary = useScopeSummary();
  const initial: NodeRef[] = user.scopes.map((n) => ({ type: n.type, id: n.id }));
  const [draft, setDraft] = useState<NodeRef[]>(initial);
  const [saved, setSaved] = useState(false);
  const dirty = !sameNodes(draft, initial);
  const save = useMutation({
    mutationFn: () => api.put<UserOut>(`/users/${user.id}/scopes`, { scopes: draft }, { "If-Match": user.scopes_version }),
    onSuccess: (next) => {
      setSaved(true);
      qc.setQueryData([...usersKey, user.id], next);
      void qc.invalidateQueries({ queryKey: usersKey });
    },
  });
  /** Conflit 412 : quelqu'un d'autre a modifié le périmètre ; « Recharger » reprend la version du serveur. */
  const conflict = save.error instanceof ApiError && save.error.status === 412;
  const reload = async () => {
    save.reset();
    const fresh = await api.get<UserOut>(`/users/${user.id}`);
    qc.setQueryData([...usersKey, user.id], fresh);
    setDraft(fresh.scopes.map((n) => ({ type: n.type, id: n.id })));
  };

  if (user.role === "admin") {
    return (
      <section className="card form-narrow" aria-labelledby={`${uid}-h`}>
        <h2 id={`${uid}-h`}>{t("users.scope.title")}</h2>
        <p className="meta">{t("users.scope.admin_all")}</p>
      </section>
    );
  }
  return (
    <form
      className="card form-narrow"
      aria-labelledby={`${uid}-h`}
      onSubmit={(e) => {
        e.preventDefault();
        setSaved(false);
        save.mutate();
      }}
    >
      <h2 id={`${uid}-h`}>{t("users.scope.title")}</h2>
      {user.scopes.length === 0 && !dirty ? (
        <p className="alert alert-warning" role="status">
          <Icon name="warning" />
          <span>{t("users.scope.empty")}</span>
        </p>
      ) : null}
      <QueryStatus query={tree} />
      {tree.data ? <ScopePicker tree={tree.data} organization={org.data} value={draft} onChange={(v) => { setSaved(false); setDraft(v); }} /> : null}
      <p className="meta" role="status">
        {t("users.scope.summary", { summary: summary(draft) })}
      </p>
      {conflict ? (
        <div className="alert alert-warning" role="alert">
          <Icon name="warning" />
          <div>
            <p className="alert-text">{t("users.scope.conflict")}</p>
            <button type="button" className="btn btn-text" onClick={() => void reload()}>
              <Icon name="rotate" />
              {t("users.scope.reload")}
            </button>
          </div>
        </div>
      ) : save.isError ? (
        <ErrorAlert error={save.error} />
      ) : null}
      {saved && !dirty ? <SuccessAlert>{t("users.scope.saved")}</SuccessAlert> : null}
      <button type="submit" className="btn btn-filled" disabled={!dirty || save.isPending} aria-busy={save.isPending}>
        <Icon name="check" />
        {t("common.save")}
      </button>
    </form>
  );
}

/** Fiche utilisateur (admin) : compte (rôle, actif) puis périmètre. */
export function UserDetail() {
  const { id = "" } = useParams();
  const { t } = useTranslation();
  const api = useApi();
  const user = useQuery({ queryKey: [...usersKey, id] as const, queryFn: () => api.get<UserOut>(`/users/${id}`) });
  const u = user.data;
  return (
    <>
      <p>
        <Link to="/users">{t("users.back")}</Link>
      </p>
      <h1>{u?.display_name ?? t("users.detail")}</h1>
      <QueryStatus query={user} />
      {u ? (
        <>
          <div className="badges">
            <RoleBadge user={u} />
            <ActiveBadge active={u.is_active} />
          </div>
          {u.email ? <p className="meta">{u.email}</p> : null}
          <AccountSection user={u} />
          <ScopeSection user={u} />
        </>
      ) : null}
    </>
  );
}

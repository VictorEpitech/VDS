import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Empty, QueryStatus } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { useApi } from "../../lib/api";
import { formatDateTime } from "../../lib/media-view";
import { useScopeTree, usersKey, type UserOut, type UserPage } from "../scope/scope";
import { useScopeSummary } from "./ScopePicker";

const PAGE_SIZE = 24;

export function RoleBadge({ user }: { user: Pick<UserOut, "role"> }) {
  const { t } = useTranslation();
  return (
    <span className="status-badge" data-state={user.role === "admin" ? "overridden" : "pairing"}>
      <Icon name={user.role === "admin" ? "lock" : "user"} />
      <span>{t(`users.role.${user.role}`)}</span>
    </span>
  );
}

export function ActiveBadge({ active }: { active: boolean }) {
  const { t } = useTranslation();
  return (
    <span className="status-badge" data-state={active ? "online" : "revoked"}>
      <Icon name={active ? "online" : "revoke"} />
      <span>{t(active ? "users.active.yes" : "users.active.no")}</span>
    </span>
  );
}

/** Réglages › Utilisateurs (admin) : liste filtrable, cartes à 375 px. */
export function UsersPage() {
  const { t, i18n } = useTranslation();
  const uid = useId();
  const api = useApi();
  const tree = useScopeTree();
  const summary = useScopeSummary();
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [siteId, setSiteId] = useState("");
  const [page, setPage] = useState(0);
  const users = useQuery({
    queryKey: [...usersKey, { q, role, siteId, page }] as const,
    queryFn: () => {
      const qs = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) });
      if (q.trim()) qs.set("q", q.trim());
      if (role) qs.set("role", role);
      if (siteId) qs.set("site_id", siteId);
      return api.get<UserPage>(`/users?${qs.toString()}`);
    },
  });
  const total = users.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const filtered = q.trim() !== "" || role !== "" || siteId !== "";
  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    set(v);
    setPage(0);
  };

  return (
    <>
      <h1>{t("users.title")}</h1>
      <p className="lead">{t("users.intro")}</p>
      <div className="filters" role="search" aria-label={t("users.filters.label")}>
        <div className="field">
          <label htmlFor={`${uid}-q`}>{t("users.filters.search")}</label>
          <input id={`${uid}-q`} type="search" value={q} onChange={(e) => reset(setQ)(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor={`${uid}-role`}>{t("users.filters.role")}</label>
          <select id={`${uid}-role`} value={role} onChange={(e) => reset(setRole)(e.target.value)}>
            <option value="">{t("users.filters.all_roles")}</option>
            <option value="admin">{t("users.role.admin")}</option>
            <option value="editor">{t("users.role.editor")}</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor={`${uid}-site`}>{t("users.filters.site")}</label>
          <select id={`${uid}-site`} value={siteId} onChange={(e) => reset(setSiteId)(e.target.value)}>
            <option value="">{t("users.filters.all_sites")}</option>
            {tree.data?.sites.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
      </div>
      <QueryStatus query={users} />
      {users.isSuccess && total === 0 ? <Empty icon="user">{filtered ? t("users.no_match") : t("users.empty")}</Empty> : null}
      <ul className="rows list-reset" aria-label={t("users.title")}>
        {users.data?.items.map((u) => (
          <li key={u.id} className="row card">
            <div className="row-main">
              <h2 className="row-title">
                <Link to={`/users/${u.id}`}>{u.display_name}</Link>
              </h2>
              {u.email ? <p className="meta">{u.email}</p> : null}
              <div className="badges">
                <RoleBadge user={u} />
                <ActiveBadge active={u.is_active} />
              </div>
              <p className="meta">
                {u.role === "admin" ? t("users.scope.admin_all") : `${t("users.scope.title")} : ${summary(u.scopes)}`}
              </p>
              <p className="meta">{u.last_seen_at ? t("users.last_seen", { date: formatDateTime(u.last_seen_at, i18n.language) }) : t("users.never_seen")}</p>
            </div>
            <Link className="btn btn-text" to={`/users/${u.id}`}>
              {t("users.manage")}
              <span className="visually-hidden"> {u.display_name}</span>
            </Link>
          </li>
        ))}
      </ul>
      {total > PAGE_SIZE ? (
        <nav className="pager" aria-label={t("media.pager.label")}>
          <button type="button" className="btn btn-text" disabled={page === 0} onClick={() => setPage(page - 1)}>
            <Icon name="back" />
            {t("media.pager.previous")}
          </button>
          <span role="status">{t("media.pager.page", { page: page + 1, pages, total })}</span>
          <button type="button" className="btn btn-text" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>
            {t("media.pager.next")}
          </button>
        </nav>
      ) : null}
    </>
  );
}

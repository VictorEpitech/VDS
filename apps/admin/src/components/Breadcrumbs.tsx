import { Fragment } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";
import { Icon } from "./Icon";

export interface Crumb {
  to?: string;
  label: string;
}

/** Chemin de l'écran courant (clés `nav.*`), `null` sur l'Accueil. Les pages de niveau 1 n'ont pas de chemin. */
export function trailFor(pathname: string): Crumb[] | null {
  const p = pathname.replace(/\/+$/, "") || "/";
  const m = (to: string, label: string): Crumb => ({ to, label });
  const trails: [RegExp, Crumb[]][] = [
    [/^\/screens\/captures$/, [m("/screens", "nav.screens"), { label: "nav.captures" }]],
    [/^\/screens\/add$/, [m("/screens", "nav.screens"), { label: "nav.add_screen" }]],
    [/^\/screens\/add\/android$/, [m("/screens", "nav.screens"), { label: "nav.install_android" }]],
    [/^\/content\/playlists\/[^/]+$/, [m("/content/media", "nav.content"), m("/content/playlists", "nav.playlists"), { label: "nav.playlist_edit" }]],
    [/^\/content\/playlists$/, [m("/content/media", "nav.content"), { label: "nav.playlists" }]],
    [/^\/content\/media\/add$/, [m("/content/media", "nav.content"), m("/content/media", "nav.media"), { label: "nav.import" }]],
    [/^\/content\/media$/, [m("/content/media", "nav.content"), { label: "nav.media" }]],
    [/^\/sites\/connect$/, [m("/more", "nav.more"), m("/sites", "nav.sites"), { label: "nav.connect_agent" }]],
    [/^\/sites\/[^/]+$/, [m("/more", "nav.more"), m("/sites", "nav.sites"), { label: "nav.site_detail" }]],
    [/^\/sites$/, [m("/more", "nav.more"), { label: "nav.sites" }]],
    [/^\/users\/[^/]+$/, [m("/more", "nav.more"), m("/users", "nav.users"), { label: "nav.user_detail" }]],
    [/^\/users$/, [m("/more", "nav.more"), { label: "nav.users" }]],
    [/^\/power$/, [m("/more", "nav.more"), { label: "nav.power" }]],
    [/^\/notifications$/, [m("/more", "nav.more"), { label: "nav.notifications" }]],
  ];
  for (const [re, trail] of trails) if (re.test(p)) return trail;
  return null;
}

export function Breadcrumbs() {
  const { t } = useTranslation();
  const { pathname } = useLocation();
  // Le pathname du routeur est déjà relatif à la base (basename).
  const trail = trailFor(pathname);
  if (!trail) return null;
  return (
    <nav className="breadcrumbs" aria-label={t("nav.breadcrumb")}>
      <ol className="list-reset">
        {trail.map((c, i) => {
          const last = i === trail.length - 1;
          return (
            <Fragment key={c.label}>
              <li>
                {c.to && !last ? (
                  <Link to={c.to}>{t(c.label)}</Link>
                ) : (
                  <span aria-current={last ? "page" : undefined}>{t(c.label)}</span>
                )}
              </li>
              {last ? null : (
                <li aria-hidden="true" className="crumb-sep">
                  <Icon name="back" />
                </li>
              )}
            </Fragment>
          );
        })}
      </ol>
    </nav>
  );
}

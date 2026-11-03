import { useTranslation } from "react-i18next";
import { QueryStatus } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { useLayoutUsage } from "./layouts";

/** Usage d'une mise en page : programmations, forçages (d'écran et de zone), écrans d'orientation différente. */
export function LayoutUsage({ id }: { id: string }) {
  const { t } = useTranslation();
  const usage = useLayoutUsage(id);
  const u = usage.data;
  return (
    <section className="section" aria-labelledby="layout-usage-h">
      <h2 id="layout-usage-h">{t("layouts.usage.title")}</h2>
      <QueryStatus query={usage} />
      {u ? (
        <>
          {u.screens_orientation_mismatch > 0 ? (
            <p className="alert alert-warning" role="status">
              <Icon name="warning" />
              <span>{t("layouts.usage.mismatch", { count: u.screens_orientation_mismatch, total: u.screens_total })}</span>
            </p>
          ) : null}
          {u.schedules.length + u.overrides.length + u.zone_overrides.length === 0 ? <p className="meta">{t("layouts.usage.none")}</p> : null}
          {u.schedules.length > 0 ? (
            <>
              <h3>{t("layouts.usage.schedules")}</h3>
              <ul className="list-reset rows-compact">
                {u.schedules.map((s) => (
                  <li key={s.id}>{s.name}</li>
                ))}
              </ul>
            </>
          ) : null}
          {u.overrides.length > 0 ? (
            <>
              <h3>{t("layouts.usage.overrides")}</h3>
              <ul className="list-reset rows-compact">
                {u.overrides.map((o) => (
                  <li key={o.id}>{o.name}</li>
                ))}
              </ul>
            </>
          ) : null}
          {u.zone_overrides.length > 0 ? (
            <>
              <h3>{t("layouts.usage.zone_overrides")}</h3>
              <ul className="list-reset rows-compact">
                {u.zone_overrides.map((o) => (
                  <li key={o.id}>
                    {o.name}{" "}
                    <span className="meta">
                      (
                      {(o.zone_ids ?? []).map((k) => (
                        <span key={k} className="mono">
                          {k}{" "}
                        </span>
                      ))}
                      )
                    </span>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

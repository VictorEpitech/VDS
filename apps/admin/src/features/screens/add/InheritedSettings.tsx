import { useTranslation } from "react-i18next";
import { ProvenanceBadge } from "../../../components/Provenance";
import { useConfig } from "../../../lib/queries";

const KIOSK = "player.kiosk";
const CASCADE = "power.cascade";

/**
 * Réglages que l'écran reprendra de sa cible (groupe, sinon site), avec leur provenance.
 * L'écran n'a aucune surcharge à sa création : tout est hérité, donc rien à « revenir à l'héritage » ici.
 */
export function InheritedSettings({ siteId, groupId }: { siteId: string; groupId: string }) {
  const { t } = useTranslation();
  const level = groupId ? "group" : "site";
  const cfg = useConfig(level, groupId || siteId, "core", siteId !== "");
  const kiosk = cfg.data?.values[KIOSK];
  const cascade = cfg.data?.values[CASCADE];
  if (!siteId || !kiosk || !cascade) return null;
  const drivers = Array.isArray(cascade.value) ? cascade.value.filter((x): x is string => typeof x === "string") : [];
  return (
    <section className="inherited" aria-label={t("add.inherit.title")}>
      <h3>{t("add.inherit.title")}</h3>
      <p className="field-help">{t("add.inherit.help")}</p>
      <dl className="device-facts">
        <div>
          <dt>{t("add.inherit.kiosk")}</dt>
          <dd>
            {t(`config.core.player.kiosk.enum.${String(kiosk.value) === "unlocked" ? "unlocked" : "locked"}`)}
            <span className="provenance-row">
              <ProvenanceBadge origin={kiosk.origin} level="screen" />
            </span>
          </dd>
        </div>
        <div>
          <dt>{t("add.inherit.power")}</dt>
          <dd>
            <span className="mono">{drivers.join(", ")}</span>
            <span className="provenance-row">
              <ProvenanceBadge origin={cascade.origin} level="screen" />
            </span>
          </dd>
        </div>
      </dl>
    </section>
  );
}

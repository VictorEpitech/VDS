import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import type { JsonSchema } from "../../lib/content-types";
import { ModuleSettingsForm, useModuleSettings } from "../settings/ModuleSettingsForm";

const MODULE_ID = "discovery.lan";

/** Réglages de la découverte au niveau du site (sondes de ports), générés depuis le schéma du module ; rien si le module n'en expose pas. */
export function SiteDiscoverySettings({ siteId }: { siteId: string }) {
  const { t } = useTranslation();
  const settings = useModuleSettings(MODULE_ID);
  const props = Object.values((settings.data?.config_schema as JsonSchema | undefined)?.properties ?? {});
  if (!props.some((f) => !f["x-vds-scopes"] || f["x-vds-scopes"].includes("site"))) return null;
  return (
    <section className="section" aria-labelledby="site-discovery-h">
      <h2 id="site-discovery-h">
        <Icon name="detect" />
        {t("discovery.settings.title")}
      </h2>
      <p className="lead">{t("discovery.settings.intro")}</p>
      <ModuleSettingsForm moduleId={MODULE_ID} level="site" targetId={siteId} />
    </section>
  );
}

import { useTranslation } from "react-i18next";
import { Icon } from "../components/Icon";
import { AttachmentSection } from "../features/power/AttachmentSection";
import { CalendarsSection } from "../features/power/CalendarsSection";
import { DriversSection } from "../features/power/DriversSection";
import { ExceptionsSection } from "../features/power/ExceptionsSection";
import { PlansSection } from "../features/power/PlansSection";
import { PageTitle } from "../features/help/HelpPanel";

export function Power() {
  const { t } = useTranslation();
  return (
    <>
      <PageTitle topic="power">{t("power.title")}</PageTitle>
      <p className="lead">{t("power.intro")}</p>
      <div className="power-jump-nav" role="navigation" aria-label={t("power.sections_nav")}>
        <a href="#plans" className="power-jump-link">
          <Icon name="schedule" />
          <span>{t("power.nav_plans")}</span>
        </a>
        <a href="#attachments" className="power-jump-link">
          <Icon name="screen" />
          <span>{t("power.nav_attachments")}</span>
        </a>
        <a href="#exceptions" className="power-jump-link">
          <Icon name="calendar" />
          <span>{t("power.nav_exceptions")}</span>
        </a>
        <a href="#drivers" className="power-jump-link">
          <Icon name="maintenance" />
          <span>{t("power.nav_drivers")}</span>
        </a>
      </div>
      <div id="plans" className="power-section-wrap">
        <PlansSection />
      </div>
      <div id="attachments" className="power-section-wrap">
        <AttachmentSection />
      </div>
      <div id="exceptions" className="power-section-wrap">
        <ExceptionsSection />
        <CalendarsSection />
      </div>
      <div id="drivers" className="power-section-wrap">
        <DriversSection />
      </div>
    </>
  );
}

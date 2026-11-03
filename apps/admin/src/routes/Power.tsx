import { useTranslation } from "react-i18next";
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
      <PlansSection />
      <AttachmentSection />
      <DriversSection />
      <ExceptionsSection />
      <CalendarsSection />
    </>
  );
}

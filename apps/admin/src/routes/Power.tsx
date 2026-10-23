import { useTranslation } from "react-i18next";
import { AttachmentSection } from "../features/power/AttachmentSection";
import { CalendarsSection } from "../features/power/CalendarsSection";
import { ExceptionsSection } from "../features/power/ExceptionsSection";
import { PlansSection } from "../features/power/PlansSection";

export function Power() {
  const { t } = useTranslation();
  return (
    <>
      <h1>{t("power.title")}</h1>
      <p className="lead">{t("power.intro")}</p>
      <PlansSection />
      <AttachmentSection />
      <ExceptionsSection />
      <CalendarsSection />
    </>
  );
}

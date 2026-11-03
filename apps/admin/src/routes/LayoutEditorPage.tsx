import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";
import { QueryStatus } from "../components/feedback";
import { LayoutEditor } from "../features/layouts/LayoutEditor";
import { LayoutUsage } from "../features/layouts/LayoutUsage";
import { layoutKeys, useLayout } from "../features/layouts/layouts";
import { PublicationPanel } from "../features/publication/PublicationPanel";
import { useMaxZones } from "../features/layouts/warnings";

export function LayoutEditorPage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const layout = useLayout(id);
  const [epoch, setEpoch] = useState(0);
  const maxZones = useMaxZones();
  return (
    <>
      <h1>{layout.data?.name ?? t("layouts.title")}</h1>
      <QueryStatus query={layout} />
      {layout.data ? (
        <>
          <PublicationPanel type="layout" id={layout.data.id} />
          <LayoutEditor
            key={`${layout.data.id}-${epoch}`}
            layout={layout.data}
            maxZones={maxZones}
            onReload={() => {
              void qc.invalidateQueries({ queryKey: layoutKeys.one(id) }).then(() => setEpoch((n) => n + 1));
            }}
            onGone={(to) => navigate(to)}
          />
          <LayoutUsage id={layout.data.id} />
        </>
      ) : null}
    </>
  );
}

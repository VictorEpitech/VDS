import { useQuery } from "@tanstack/react-query";
import type { Health } from "@vds/sdk-ts";
import { useTranslation } from "react-i18next";
import { StatusBadge, type ScreenState } from "../components/StatusBadge";
import { useAppUrl } from "../lib/runtime-config";

const STATES: ScreenState[] = ["online", "scheduled_off", "offline", "power_failed", "overridden", "pairing"];

export function Home() {
  const { t } = useTranslation();
  const url = useAppUrl();
  const health = useQuery({
    queryKey: ["health"],
    queryFn: async () => {
      const res = await fetch(url("/health"), { credentials: "same-origin" });
      if (!res.ok) throw new Error("errors.server_unreachable");
      const body = (await res.json()) as Health;
      if (body.status !== "ok") throw new Error("errors.server_unreachable");
      return true;
    },
    // État réel du serveur : pas de nouvelle tentative silencieuse, rafraîchi régulièrement.
    retry: false,
    refetchInterval: 30_000,
  });
  const server: { state: ScreenState; label: string } = health.isPending
    ? { state: "pairing", label: t("home.server.checking") }
    : health.isError
      ? { state: "offline", label: t("home.server.offline") }
      : { state: "online", label: t("home.server.online") };

  return (
    <>
      <h1>{t("home.title")}</h1>
      <p className="lead">{t("home.intro")}</p>
      <div className="cards">
        <section className="card" aria-labelledby="server-title">
          <h2 id="server-title">{t("home.server.title")}</h2>
          <StatusBadge state={server.state} label={server.label} />
        </section>
        <section className="card" aria-labelledby="legend-title">
          <h2 id="legend-title">{t("home.legend.title")}</h2>
          <ul className="badge-list">
            {STATES.map((s) => (
              <li key={s}>
                <StatusBadge state={s} />
              </li>
            ))}
          </ul>
        </section>
      </div>
    </>
  );
}

import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { components } from "@vds/sdk-ts";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { ErrorAlert, QueryStatus, SuccessAlert } from "../components/feedback";
import { Icon } from "../components/Icon";
import { useApi } from "../lib/api";
import { keys, useScreens, useSites } from "../lib/queries";

/** Code d'appairage : 6 caractères affichés sur l'écran (sans espaces, insensible à la casse). */
export const normalizeCode = (raw: string) => raw.replace(/[\s-]/g, "").toUpperCase();

export function Pairing() {
  const { t } = useTranslation();
  const uid = useId();
  const api = useApi();
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const screens = useScreens();
  const sites = useSites();
  const [code, setCode] = useState("");
  const [screenId, setScreenId] = useState(params.get("screen") ?? "");
  const [missing, setMissing] = useState<{ code?: boolean; screen?: boolean }>({});
  const claim = useMutation({
    mutationFn: (body: components["schemas"]["PairingClaim"]) => api.post<components["schemas"]["PairingClaimed"]>("/pairings/claim", body),
    onSuccess: () => {
      setCode("");
      void qc.invalidateQueries({ queryKey: keys.screens });
    },
  });

  const active = (screens.data ?? []).filter((s) => !s.archived_at);
  const bySite = (sites.data ?? []).map((site) => ({ site, screens: active.filter((s) => s.site_id === site.id) })).filter((g) => g.screens.length > 0);
  const claimed = claim.isSuccess ? active.find((s) => s.id === claim.data.screen_id) : undefined;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const clean = normalizeCode(code);
    const next = { code: clean.length === 0, screen: screenId === "" };
    setMissing(next);
    if (next.code || next.screen) return;
    claim.mutate({ code: clean, screen_id: screenId });
  };

  return (
    <>
      <h1>{t("pairing.title")}</h1>
      <p className="lead">{t("pairing.intro")}</p>
      <form className="card form-narrow" onSubmit={submit} noValidate>
        <div className="field">
          <label htmlFor={`${uid}-code`}>{t("pairing.code.label")}</label>
          <input
            id={`${uid}-code`}
            className="mono code-input"
            value={code}
            onChange={(e) => {
              setCode(e.target.value.toUpperCase());
              claim.reset();
            }}
            maxLength={12}
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            required
            aria-invalid={missing.code ? true : undefined}
            aria-describedby={`${uid}-code-help`}
          />
          <p className="field-help" id={`${uid}-code-help`}>
            {t("pairing.code.help")}
          </p>
          {missing.code ? (
            <p className="field-error">
              <Icon name="warning" />
              <span>{t("errors.field_required")}</span>
            </p>
          ) : null}
        </div>
        <div className="field">
          <label htmlFor={`${uid}-screen`}>{t("pairing.screen.label")}</label>
          <select
            id={`${uid}-screen`}
            value={screenId}
            onChange={(e) => setScreenId(e.target.value)}
            required
            aria-invalid={missing.screen ? true : undefined}
          >
            <option value="">{t("form.choose")}</option>
            {bySite.map(({ site, screens: list }) => (
              <optgroup key={site.id} label={site.name}>
                {list.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
          {missing.screen ? (
            <p className="field-error">
              <Icon name="warning" />
              <span>{t("errors.field_required")}</span>
            </p>
          ) : null}
        </div>
        <QueryStatus query={screens} />
        {claim.isError ? <ErrorAlert error={claim.error} /> : null}
        {claim.isSuccess ? <SuccessAlert>{t("pairing.done", { name: claimed?.name ?? "" })}</SuccessAlert> : null}
        <div className="actions">
          <button type="submit" className="btn btn-filled" disabled={claim.isPending} aria-busy={claim.isPending}>
            <Icon name="pairing" />
            {t("pairing.submit")}
          </button>
          <Link className="btn btn-text" to="/screens">
            {t("pairing.back")}
          </Link>
        </div>
      </form>
    </>
  );
}

import { useId } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import type { Playlist } from "../../lib/queries";
import { fromPercent, inBounds, toPercent } from "./geometry";
import { KEY_RE, type LayoutZone } from "./layouts";

interface Props {
  zone: LayoutZone;
  playlists: readonly Playlist[];
  otherKeys: readonly string[];
  onChange: (patch: Partial<LayoutZone>) => void;
}

const toLocal = (iso: string | null): string => (iso ? new Date(new Date(iso).getTime() - new Date(iso).getTimezoneOffset() * 60_000).toISOString().slice(0, 16) : "");
const toIso = (v: string): string | null => (v && !Number.isNaN(new Date(v).getTime()) ? new Date(v).toISOString() : null);

/** Propriétés de la zone sélectionnée. Simple : nom, playlist, position. Avancé : clé, ajustement, durée, fenêtre. */
export function ZoneProps({ zone, playlists, otherKeys, onChange }: Props) {
  const { t } = useTranslation();
  const uid = useId();
  const keyError = !KEY_RE.test(zone.key) ? t("layouts.errors.key_format") : otherKeys.includes(zone.key) ? t("layouts.errors.key_duplicate") : null;
  const bounds = inBounds(zone);
  const windowBad = zone.active_from !== null && zone.active_until !== null && new Date(zone.active_until) <= new Date(zone.active_from);
  const num = (id: string, label: string, field: "x" | "y" | "w" | "h") => (
    <div className="field">
      <label htmlFor={`${uid}-${id}`}>{label}</label>
      <input
        id={`${uid}-${id}`}
        className="mono"
        type="number"
        inputMode="decimal"
        min={0}
        max={100}
        step={0.01}
        value={toPercent(zone[field])}
        aria-invalid={!bounds ? true : undefined}
        aria-describedby={!bounds ? `${uid}-bounds` : undefined}
        onChange={(e) => e.target.value !== "" && onChange({ [field]: fromPercent(Number(e.target.value)) })}
      />
    </div>
  );
  return (
    <section className="zone-props" aria-labelledby={`${uid}-h`}>
      <h2 id={`${uid}-h`}>{t("layouts.props.title", { name: zone.name })}</h2>
      <div className="field">
        <label htmlFor={`${uid}-name`}>{t("layouts.props.name")}</label>
        <input id={`${uid}-name`} value={zone.name} maxLength={80} required aria-invalid={zone.name.trim() === "" ? true : undefined} onChange={(e) => onChange({ name: e.target.value })} />
      </div>
      <div className="field">
        <label htmlFor={`${uid}-playlist`}>{t("layouts.props.playlist")}</label>
        <select id={`${uid}-playlist`} value={zone.playlist_id ?? ""} onChange={(e) => onChange({ playlist_id: e.target.value || null })}>
          <option value="">{t("layouts.props.no_playlist")}</option>
          {playlists.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        {zone.playlist_id === null ? <p className="field-help">{t("layouts.props.no_playlist_help")}</p> : null}
        {zone.h <= 0.22 ? (
          <p className="field-hint zone-widget-hint">
            <Icon name="playlist" />
            <span>{t("layouts.props.widget_hint_banner")}</span>
          </p>
        ) : zone.w <= 0.35 && zone.h >= 0.4 ? (
          <p className="field-hint zone-widget-hint">
            <Icon name="layout" />
            <span>{t("layouts.props.widget_hint_side")}</span>
          </p>
        ) : null}
      </div>
      <fieldset className="field coords">
        <legend>{t("layouts.props.position")}</legend>
        <div className="coords-grid">
          {num("x", t("layouts.props.x"), "x")}
          {num("y", t("layouts.props.y"), "y")}
          {num("w", t("layouts.props.w"), "w")}
          {num("h", t("layouts.props.h"), "h")}
        </div>
        {!bounds ? (
          <p className="field-error" id={`${uid}-bounds`}>
            <Icon name="warning" />
            <span>{t("layouts.errors.out_of_bounds")}</span>
          </p>
        ) : null}
      </fieldset>
      <details className="advanced">
        <summary>{t("form.advanced")}</summary>
        <div className="advanced-body">
          <div className="field">
            <label htmlFor={`${uid}-key`}>{t("layouts.props.key")}</label>
            <input
              id={`${uid}-key`}
              className="mono"
              value={zone.key}
              maxLength={32}
              aria-invalid={keyError ? true : undefined}
              aria-describedby={`${uid}-key-h`}
              onChange={(e) => onChange({ key: e.target.value })}
            />
            <p className="field-help" id={`${uid}-key-h`}>
              {t("layouts.props.key_help")}
            </p>
            {keyError ? (
              <p className="field-error">
                <Icon name="warning" />
                <span>{keyError}</span>
              </p>
            ) : null}
          </div>
          <div className="field">
            <label htmlFor={`${uid}-fit`}>{t("layouts.props.fit")}</label>
            <select id={`${uid}-fit`} value={zone.fit} onChange={(e) => onChange({ fit: e.target.value as LayoutZone["fit"] })}>
              {(["contain", "cover", "stretch"] as const).map((f) => (
                <option key={f} value={f}>
                  {t(`layouts.props.fits.${f}`)}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor={`${uid}-dur`}>{t("layouts.props.duration")}</label>
            <input
              id={`${uid}-dur`}
              type="number"
              inputMode="numeric"
              min={1}
              max={86400}
              step={1}
              value={zone.default_item_duration_ms === null ? "" : Math.round(zone.default_item_duration_ms / 1000)}
              aria-describedby={`${uid}-dur-h`}
              onChange={(e) => onChange({ default_item_duration_ms: e.target.value === "" ? null : Math.max(1, Number(e.target.value)) * 1000 })}
            />
            <p className="field-help" id={`${uid}-dur-h`}>
              {t("layouts.props.duration_help")}
            </p>
          </div>
          <div className="field">
            <label htmlFor={`${uid}-from`}>{t("layouts.props.active_from")}</label>
            <input id={`${uid}-from`} type="datetime-local" value={toLocal(zone.active_from)} onChange={(e) => onChange({ active_from: toIso(e.target.value) })} />
          </div>
          <div className="field">
            <label htmlFor={`${uid}-until`}>{t("layouts.props.active_until")}</label>
            <input
              id={`${uid}-until`}
              type="datetime-local"
              value={toLocal(zone.active_until)}
              aria-invalid={windowBad ? true : undefined}
              onChange={(e) => onChange({ active_until: toIso(e.target.value) })}
            />
            {windowBad ? (
              <p className="field-error">
                <Icon name="warning" />
                <span>{t("layouts.errors.window")}</span>
              </p>
            ) : null}
          </div>
        </div>
      </details>
    </section>
  );
}

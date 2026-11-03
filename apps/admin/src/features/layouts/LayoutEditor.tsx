import { useId, useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { OwnerBadge, OwnerEdit } from "../scope/Owner";
import { useCanWrite, type NodeRef } from "../scope/scope";
import { ConfirmAction, ErrorAlert, SuccessAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { ApiError } from "../../lib/api";
import { usePlaylists } from "../../lib/queries";
import { useNarrow } from "../../lib/use-narrow";
import { LayoutCanvas } from "./LayoutCanvas";
import { inBounds, type Rect } from "./geometry";
import { DEFAULT_BACKGROUND, DEFAULT_MAX_ZONES, KEY_RE, freeKey, toLayoutIn, useDeleteLayout, useDuplicateLayout, useSaveLayout, type Layout, type LayoutZone } from "./layouts";
import { restack } from "./stack";
import { useVideoWarning } from "./warnings";
import { ZoneList } from "./ZoneList";
import { ZoneProps } from "./ZoneProps";

interface Props {
  layout: Layout;
  maxZones?: number;
  onReload: () => void;
  onGone: (to: string) => void;
}

/** Problèmes bloquant l'envoi (le serveur reste l'autorité) : clés, noms, bornes, fenêtres. */
export function zoneProblems(zones: readonly LayoutZone[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const z of zones) {
    const bad =
      z.name.trim() === "" || !KEY_RE.test(z.key) || seen.has(z.key) || !inBounds(z) || (z.active_from !== null && z.active_until !== null && new Date(z.active_until) <= new Date(z.active_from));
    seen.add(z.key);
    if (bad) out.push(z.key);
  }
  return out;
}

/** Éditeur visuel : canevas, panneau Zones, propriétés, avertissements, enregistrement conditionnel (If-Match). */
export function LayoutEditor({ layout, maxZones = DEFAULT_MAX_ZONES, onReload, onGone }: Props) {
  const { t } = useTranslation();
  const uid = useId();
  const narrow = useNarrow();
  const playlists = usePlaylists();
  const save = useSaveLayout(layout.id);
  const duplicate = useDuplicateLayout();
  const remove = useDeleteLayout();
  const [name, setName] = useState(layout.name);
  const [orientation, setOrientation] = useState(layout.orientation);
  const [background, setBackground] = useState<string | null>(layout.background_color);
  const [zones, setZones] = useState<LayoutZone[]>(layout.zones);
  const [owner, setOwner] = useState<NodeRef | null | undefined>(layout.owner);
  const canWrite = useCanWrite();
  const writable = canWrite(layout.owner);
  const [selectedAt, setSelectedAt] = useState(0);
  const [saved, setSaved] = useState(false);
  const [invalid, setInvalid] = useState(false);

  const body = useMemo(
    () => toLayoutIn({ name: name.trim(), orientation, background_color: background, background_media_id: layout.background_media_id, tags: layout.tags, owner }, zones),
    [name, orientation, background, zones, layout, owner],
  );
  const dirty = JSON.stringify(body) !== JSON.stringify(toLayoutIn(layout, layout.zones));
  const playlistList = playlists.data?.items ?? [];
  const playlistName = (id: string | null) => (id ? (playlistList.find((p) => p.id === id)?.name ?? "") : t("layouts.props.no_playlist"));
  const video = useVideoWarning(zones);
  const problems = zoneProblems(zones);
  const current = zones[selectedAt] ?? null;
  const selected = current?.key ?? null;
  const select = (key: string) => setSelectedAt(Math.max(0, zones.findIndex((z) => z.key === key)));
  const conflict = save.error instanceof ApiError && (save.error.status === 412 || save.error.code.endsWith("revision_conflict"));

  const patchAt = (at: number, p: Partial<LayoutZone>) => {
    setSaved(false);
    setZones((zs) => zs.map((z, i) => (i === at ? { ...z, ...p } : z)));
  };
  const patch = (key: string, p: Partial<LayoutZone>) => patchAt(Math.max(0, zones.findIndex((z) => z.key === key)), p);
  const add = () => {
    const key = freeKey(zones);
    setSaved(false);
    setZones([
      ...zones,
      {
        key,
        name: t("layouts.zones.new_name", { n: zones.length + 1 }),
        x: 0.1,
        y: 0.1,
        w: 0.3,
        h: 0.3,
        z: Math.min(99, zones.length),
        playlist_id: null,
        fit: "contain",
        default_item_duration_ms: null,
        active_from: null,
        active_until: null,
      },
    ]);
    setSelectedAt(zones.length);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (name.trim() === "" || problems.length > 0) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    setSaved(false);
    save.mutate({ body, revision: layout.revision }, { onSuccess: () => setSaved(true) });
  };

  return (
    <form className="layout-editor" onSubmit={submit} noValidate aria-label={t("layouts.editor.label")}>
      {writable ? null : (
        <p className="alert alert-info" role="status">
          <Icon name="lock" />
          <span>{t("scope.read_only")}</span>
        </p>
      )}
      <fieldset className="fieldset-plain" disabled={!writable}>
      <div className="layout-meta card">
        <div className="field">
          <span className="field-label">{t("scope.owner.label")}</span>
          <OwnerBadge owner={owner} />
        </div>
        <OwnerEdit
          owner={owner}
          onSave={(o) => {
            setOwner(o);
            setSaved(false);
          }}
        />
        <div className="field">
          <label htmlFor={`${uid}-name`}>{t("layouts.editor.name")}</label>
          <input
            id={`${uid}-name`}
            value={name}
            maxLength={120}
            required
            aria-invalid={invalid && name.trim() === "" ? true : undefined}
            onChange={(e) => {
              setName(e.target.value);
              setSaved(false);
            }}
          />
        </div>
        <div className="field">
          <label htmlFor={`${uid}-orientation`}>{t("layouts.editor.orientation")}</label>
          <select
            id={`${uid}-orientation`}
            value={orientation}
            onChange={(e) => {
              setOrientation(e.target.value as Layout["orientation"]);
              setSaved(false);
            }}
          >
            <option value="landscape">{t("layouts.orientation.landscape")}</option>
            <option value="portrait">{t("layouts.orientation.portrait")}</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor={`${uid}-bg`}>{t("layouts.editor.background")}</label>
          <span className="layout-bg">
            <input
              id={`${uid}-bg`}
              type="color"
              value={background ?? DEFAULT_BACKGROUND}
              onChange={(e) => {
                setBackground(e.target.value);
                setSaved(false);
              }}
            />
            {background ? (
              <button
                type="button"
                className="btn btn-text"
                onClick={() => {
                  setBackground(null);
                  setSaved(false);
                }}
              >
                {t("layouts.editor.background_clear")}
              </button>
            ) : (
              <span className="meta">{t("layouts.editor.background_default")}</span>
            )}
          </span>
        </div>
      </div>

      {video.exceeded ? (
        <p className="alert alert-warning" role="status">
          <Icon name="warning" />
          <span>{t("layouts.warnings.videos", { count: video.videoZones.length, max: video.max })}</span>
        </p>
      ) : null}

      <div className="layout-grid">
        <LayoutCanvas
          zones={zones}
          orientation={orientation}
          background={background}
          selected={selected}
          onSelect={select}
          onChange={(key, r: Rect) => {
            setSaved(false);
            patch(key, r);
          }}
          readOnly={narrow}
          playlistName={playlistName}
        />
        <div className="layout-side">
          <ZoneList
            zones={zones}
            selected={selected}
            max={maxZones}
            onSelect={select}
            onMove={(key, dir) => {
              setSaved(false);
              setZones((zs) => restack(zs, key, dir));
            }}
            onRemove={(key) => {
              setSaved(false);
              setZones(zones.filter((z) => z.key !== key));
              setSelectedAt(0);
            }}
            onAdd={add}
            playlistName={playlistName}
          />
          {current ? (
            <ZoneProps key={selectedAt} zone={current} playlists={playlistList} otherKeys={zones.filter((_, i) => i !== selectedAt).map((z) => z.key)} onChange={(p) => patchAt(selectedAt, p)} />
          ) : null}
        </div>
      </div>
      </fieldset>

      {invalid && (problems.length > 0 || name.trim() === "") ? (
        <p className="alert alert-error" role="alert">
          <Icon name="warning" />
          <span>{t("layouts.errors.invalid", { count: problems.length })}</span>
        </p>
      ) : null}
      {save.isError ? (
        <ErrorAlert error={save.error}>
          {conflict ? (
            <button type="button" className="btn btn-text" onClick={onReload}>
              <Icon name="rotate" />
              {t("layouts.editor.reload")}
            </button>
          ) : null}
        </ErrorAlert>
      ) : null}
      {duplicate.isError ? <ErrorAlert error={duplicate.error} /> : null}
      {remove.isError ? <ErrorAlert error={remove.error} /> : null}
      {saved && !dirty ? <SuccessAlert>{t("layouts.editor.saved")}</SuccessAlert> : null}
      <div className="actions">
        {writable ? (
        <>
        <button type="submit" className="btn btn-filled" disabled={save.isPending || !dirty} aria-busy={save.isPending}>
          <Icon name="online" />
          {t("common.save")}
        </button>
        <button
          type="button"
          className="btn btn-text"
          disabled={duplicate.isPending || dirty}
          onClick={() => duplicate.mutate(layout.id, { onSuccess: (copy) => onGone(`/content/layouts/${copy.id}`) })}
        >
          <Icon name="copy" />
          {t("layouts.duplicate")}
        </button>
        <ConfirmAction
          label={t("layouts.delete.action")}
          confirmLabel={t("layouts.delete.confirm", { name: layout.name })}
          pending={remove.isPending}
          onConfirm={() => remove.mutate(layout.id, { onSuccess: () => onGone("/content/layouts") })}
        />
        </>
        ) : null}
        <Link className="btn btn-text" to="/content/layouts">
          <Icon name="back" />
          {t("layouts.back")}
        </Link>
      </div>
      {dirty ? (
        <p className="field-help" role="status">
          {t("layouts.editor.unsaved")}
        </p>
      ) : null}
    </form>
  );
}

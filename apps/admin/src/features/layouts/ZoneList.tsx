import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import type { LayoutZone } from "./layouts";
import { stackOrder } from "./stack";

interface Props {
  zones: readonly LayoutZone[];
  selected: string | null;
  max: number;
  onSelect: (key: string) => void;
  onMove: (key: string, dir: "up" | "down") => void;
  onRemove: (key: string) => void;
  onAdd: () => void;
  playlistName: (id: string | null) => string;
}

/** Panneau Zones : ordre du dessus vers le dessous, avec Monter / Descendre (alternative au glisser). */
export function ZoneList({ zones, selected, max, onSelect, onMove, onRemove, onAdd, playlistName }: Props) {
  const { t } = useTranslation();
  const ordered = stackOrder(zones);
  return (
    <section className="zone-list" aria-labelledby="zone-list-h">
      <div className="zone-list-head">
        <h2 id="zone-list-h">{t("layouts.zones.title", { count: zones.length })}</h2>
        <button type="button" className="btn btn-text" onClick={onAdd} disabled={zones.length >= max}>
          <Icon name="add" />
          {t("layouts.zones.add")}
        </button>
      </div>
      {zones.length >= max ? <p className="field-help">{t("layouts.zones.max", { max })}</p> : null}
      <ul className="list-reset zone-rows">
        {ordered.map((z, i) => (
          <li key={z.key} className="zone-row" data-selected={selected === z.key ? "true" : undefined}>
            <button type="button" className="zone-select" aria-pressed={selected === z.key} onClick={() => onSelect(z.key)}>
              <strong>{z.name}</strong>
              <span className="meta">{playlistName(z.playlist_id)}</span>
            </button>
            <span className="zone-actions">
              <button type="button" className="btn btn-text btn-icon" aria-label={t("layouts.zones.up", { name: z.name })} disabled={i === 0} onClick={() => onMove(z.key, "up")}>
                <Icon name="move_up" />
              </button>
              <button type="button" className="btn btn-text btn-icon" aria-label={t("layouts.zones.down", { name: z.name })} disabled={i === ordered.length - 1} onClick={() => onMove(z.key, "down")}>
                <Icon name="move_down" />
              </button>
              <button type="button" className="btn btn-text btn-icon btn-danger" aria-label={t("layouts.zones.remove", { name: z.name })} disabled={zones.length <= 1} onClick={() => onRemove(z.key)}>
                <Icon name="delete" />
              </button>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

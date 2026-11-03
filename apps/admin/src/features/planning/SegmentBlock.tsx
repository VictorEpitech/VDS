import { useTranslation } from "react-i18next";
import { Icon, type IconName } from "../../components/Icon";
import { PriorityBadge, useSourceText } from "./parts";
import { toneOf, type Block } from "./planning";
import type { Fmt } from "./time";

interface Props {
  block: Block;
  fmt: Fmt;
  tabbable: boolean;
  selected: boolean;
  /** Données de navigation clavier (index de piste global, début en ms). */
  row: number;
  onSelect: (block: Block) => void;
  onFocusBlock: (key: string) => void;
  /** Un clic sur un créneau vide ouvre l'assistant de programmation (pas de glisser-déposer). */
  creates?: boolean;
}

/**
 * Bloc de la grille : aplat tonal + bordure, aucune ombre. L'état se lit par l'icône et le libellé, jamais par la couleur seule :
 * `scheduled_off` atténué (Secondary), `overridden` (Tertiary + icône), `off` vide bordé, brouillon pointillé, masqué hachuré.
 */
export function SegmentBlock({ block, fmt, tabbable, selected, row, onSelect, onFocusBlock, creates = false }: Props) {
  const { t } = useTranslation();
  const src = useSourceText();
  const { segment, lane } = block;
  const range = `${fmt.time(block.startMs)}–${fmt.time(block.endMs)}`;

  let name: string;
  let state: string;
  let stateLabel: string;
  let icon: IconName | null = null;
  let level = (segment?.source ?? lane?.source)?.priority_level ?? null;
  const source = segment?.source ?? lane?.source;
  if (segment) {
    state = segment.state;
    stateLabel = t(`planning.segment_state.${segment.state}`);
    name = segment.state === "scheduled_off" ? t("states.scheduled_off") : segment.state === "off" ? t("planning.segment_state.off") : (segment.content?.name ?? segment.source.name ?? stateLabel);
    icon = segment.state === "overridden" ? "overridden" : segment.state === "scheduled_off" ? "scheduled_off" : creates && segment.state === "off" ? "add" : null;
    if (segment.state === "off" || segment.state === "scheduled_off") level = null;
  } else {
    state = lane?.effective_ratio === 0 || block.maskedBy ? "masked" : "candidate";
    stateLabel = block.maskedBy ? t("planning.masked_by", { name: block.maskedBy }) : state === "masked" ? t("planning.never_wins") : t("planning.segment_state.candidate");
    name = lane?.content?.name ?? lane?.source.name ?? "";
    if (lane?.source.kind === "override") icon = "overridden";
  }
  const draft = source?.kind === "draft";
  const aria = [
    name,
    t("planning.block.range", { range }),
    stateLabel,
    source && source.kind !== "none" ? `${t("planning.block.source")} ${src.label(source)}, ${src.origin(source)}` : "",
    draft ? t("planning.draft") : "",
    creates && segment?.state === "off" ? t("planning.block.create_hint") : "",
  ]
    .filter(Boolean)
    .join(". ");
  const masked = state === "masked";

  return (
    <div role="gridcell" className="tl-cell" style={{ insetInlineStart: `${block.left}%`, inlineSize: `${block.width}%` }}>
      <button
        type="button"
        className="tl-block"
        data-cell=""
        data-row={row}
        data-start={block.startMs}
        data-end={block.endMs}
        data-key={block.key}
        data-state={state}
        data-tone={segment?.state === "play" || lane ? toneOf((segment?.content ?? lane?.content)?.id) : undefined}
        data-draft={draft ? "" : undefined}
        data-masked={masked ? "" : undefined}
        aria-current={selected ? "true" : undefined}
        tabIndex={tabbable ? 0 : -1}
        aria-label={aria}
        onClick={() => onSelect(block)}
        onFocus={() => onFocusBlock(block.key)}
      >
        {icon ? <Icon name={icon} /> : null}
        <span className="tl-name">{name}</span>
        <span className="tl-time">{range}</span>
        {masked ? <span className="tl-masked">{stateLabel}</span> : null}
        {level && level !== "normal" ? (
          <span className="tl-level">
            <PriorityBadge level={level} compact />
          </span>
        ) : null}
      </button>
    </div>
  );
}

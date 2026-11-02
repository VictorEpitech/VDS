import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import { useTagSuggestions } from "../../lib/queries";

export const MAX_TAGS = 20;
const TAG_RE = /^[\p{L}\p{N}][\p{L}\p{N} _-]{0,31}$/u;

/** Même normalisation que le serveur : espaces rognés, minuscules. */
export const normalizeTag = (raw: string) => raw.trim().toLowerCase();
export const isValidTag = (tag: string) => TAG_RE.test(tag);

interface Props {
  scope: "media" | "playlists";
  tags: readonly string[];
  /** Appelé avec la liste complète après ajout ou retrait. */
  onChange: (tags: string[]) => void;
  pending?: boolean;
  label?: string;
}

/** Tags libres : puces retirables et champ d'ajout avec suggestions. */
export function TagEditor({ scope, tags, onChange, pending = false, label }: Props) {
  const { t } = useTranslation();
  const uid = useId();
  const [draft, setDraft] = useState("");
  const [invalid, setInvalid] = useState<"invalid" | "too_many" | null>(null);
  const suggestions = useTagSuggestions(scope, normalizeTag(draft));

  const add = (e: FormEvent) => {
    e.preventDefault();
    const tag = normalizeTag(draft);
    if (!tag) return;
    if (!isValidTag(tag)) return setInvalid("invalid");
    if (tags.includes(tag)) return setDraft("");
    if (tags.length >= MAX_TAGS) return setInvalid("too_many");
    setInvalid(null);
    setDraft("");
    onChange([...tags, tag].sort());
  };

  return (
    <div className="field tag-editor">
      <label htmlFor={`${uid}-in`}>{label ?? t("tags.label")}</label>
      {tags.length > 0 ? (
        <ul className="tag-list list-reset" aria-label={t("tags.current")}>
          {tags.map((tag) => (
            <li key={tag} className="tag">
              <span>{tag}</span>
              <button type="button" className="btn btn-text btn-icon" disabled={pending} aria-label={t("tags.remove", { tag })} onClick={() => onChange(tags.filter((x) => x !== tag))}>
                <Icon name="delete" />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="field-help">{t("tags.none")}</p>
      )}
      <form className="inline-form" onSubmit={add} noValidate>
        <input
          id={`${uid}-in`}
          value={draft}
          maxLength={32}
          list={`${uid}-list`}
          aria-invalid={invalid ? true : undefined}
          aria-describedby={invalid ? `${uid}-err` : `${uid}-help`}
          onChange={(e) => {
            setDraft(e.target.value);
            setInvalid(null);
          }}
        />
        <datalist id={`${uid}-list`}>
          {(suggestions.data ?? []).filter((s) => !tags.includes(s)).map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        <button type="submit" className="btn btn-text" disabled={pending || draft.trim() === ""}>
          <Icon name="add" />
          {t("tags.add")}
        </button>
      </form>
      <p id={`${uid}-help`} className="field-help">
        {t("tags.help")}
      </p>
      {invalid ? (
        <p id={`${uid}-err`} className="field-error" role="alert">
          <Icon name="warning" />
          <span>{invalid === "too_many" ? t("errors.tags.too_many", { max: MAX_TAGS }) : t("errors.tags.invalid")}</span>
        </p>
      ) : null}
    </div>
  );
}

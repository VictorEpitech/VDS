import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, useSyncExternalStore, type KeyboardEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { Icon } from "../../components/Icon";
import { useScope } from "../scope/scope";
import { collectEntries, matches, paletteVersion, registerPaletteSource, subscribePalette, type PaletteEntry } from "./registry";
import { coreEntries, usePaletteDataSources } from "./sources";

interface PaletteApi {
  open(): void;
  close(): void;
  isOpen: boolean;
}
const Ctx = createContext<PaletteApi | null>(null);

export function useCommandPalette(): PaletteApi {
  const api = useContext(Ctx);
  if (!api) throw new Error("PaletteProvider manquant");
  return api;
}

const MAX_RESULTS = 30;

/** Entrées visibles pour ce rôle : une action interdite n'apparaît pas (le serveur reste juge). */
export function visibleEntries(entries: PaletteEntry[], role: { isAdmin: boolean; restricted: boolean }): PaletteEntry[] {
  return entries.filter((e) => (e.requires === "admin" ? role.isAdmin : e.requires === "unrestricted" ? !role.restricted : true));
}

/** Données (écrans, sites, médias…) : lues seulement tant que la palette est ouverte. */
function DataSources() {
  usePaletteDataSources();
  return null;
}

function Dialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const scope = useScope();
  const uid = useId();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const version = useSyncExternalStore(subscribePalette, paletteVersion);

  const label = useCallback(
    // Le préfixe littéral permet à i18n:check de suivre les clés `palette.*`.
    (e: PaletteEntry) => t(`palette.${e.labelKey.replace(/^palette\./, "")}`, e.labelParams),
    [t],
  );
  const results = useMemo(() => {
    const all = visibleEntries(collectEntries(), scope);
    const q = query.trim();
    const kept = q ? all.filter((e) => matches(q, `${label(e)} ${e.keywords.join(" ")}`)) : all.filter((e) => e.kind !== "navigate" || e.id.startsWith("nav:"));
    return kept.slice(0, MAX_RESULTS);
    // `version` : une source (re)déclarée met la liste à jour.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, scope.isAdmin, scope.restricted, label, version]);

  useEffect(() => setActive(0), [query]);
  useEffect(() => input.current?.focus(), []);

  const run = (e: PaletteEntry) => {
    onClose();
    e.run(navigate);
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (results.length ? (i + 1) % results.length : 0));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (results.length ? (i - 1 + results.length) % results.length : 0));
    } else if (e.key === "Enter") {
      const chosen = results[active];
      if (chosen && (e.target as HTMLElement).tagName !== "BUTTON") {
        e.preventDefault();
        run(chosen);
      }
    } else if (e.key === "Tab") {
      const items = root.current?.querySelectorAll<HTMLElement>("input, button");
      if (!items || items.length === 0) return;
      const first = items[0]!;
      const last = items[items.length - 1]!;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  };

  return (
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions -- clic hors de la boîte = fermer ; l'équivalent clavier est Échap
    <div className="palette-backdrop" onMouseDown={onClose}>
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- le clavier arrive par le champ combobox ; la boîte ne fait que le déléguer */}
      <div ref={root} className="palette" role="dialog" aria-modal="true" aria-label={t("palette.title")} onMouseDown={(e) => e.stopPropagation()} onKeyDown={onKey}>
        <DataSources />
        <div className="palette-head">
          <Icon name="search" />
          <label className="visually-hidden" htmlFor={`${uid}-q`}>
            {t("palette.label")}
          </label>
          <input
            id={`${uid}-q`}
            ref={input}
            type="text"
            role="combobox"
            aria-expanded="true"
            aria-controls={`${uid}-list`}
            aria-activedescendant={results[active] ? `${uid}-o${active}` : undefined}
            aria-autocomplete="list"
            autoComplete="off"
            spellCheck={false}
            placeholder={t("palette.placeholder")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button type="button" className="btn btn-text" onClick={onClose}>
            <Icon name="close" />
            {t("help.close")}
          </button>
        </div>
        <ul id={`${uid}-list`} role="listbox" aria-label={t("palette.results")} className="palette-list list-reset">
          {results.map((e, i) => (
            // eslint-disable-next-line jsx-a11y/click-events-have-key-events -- option d'un combobox : Entrée est gérée par le champ
            <li key={e.id} id={`${uid}-o${i}`} role="option" aria-selected={i === active} data-active={i === active || undefined} onClick={() => run(e)} onMouseMove={() => setActive(i)}>
              <Icon name={e.icon} />
              <span className="palette-name">{label(e)}</span>
              <span className="meta">{t(`palette.kind.${e.kind}`)}</span>
            </li>
          ))}
        </ul>
        <p className="meta" role="status" aria-live="polite">
          {results.length === 0 ? t("palette.none") : t("palette.count", { count: results.length })}
        </p>
      </div>
    </div>
  );
}

/** Palette Ctrl/Cmd+K : recherche côté client sur les listes déjà chargées, plus navigation et créations. */
export function PaletteProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [isOpen, setOpen] = useState(false);
  const returnTo = useRef<HTMLElement | null>(null);

  const open = useCallback(() => {
    returnTo.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setOpen(true);
  }, []);
  const close = useCallback(() => {
    setOpen(false);
    returnTo.current?.focus();
  }, []);

  useEffect(() => registerPaletteSource(() => coreEntries(t)), [t]);
  const isOpenRef = useRef(false);
  isOpenRef.current = isOpen;
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        if (isOpenRef.current) close();
        else open();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);

  const api = useMemo(() => ({ open, close, isOpen }), [open, close, isOpen]);
  return (
    <Ctx.Provider value={api}>
      {children}
      {isOpen ? <Dialog onClose={close} /> : null}
    </Ctx.Provider>
  );
}

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import { useTone } from "../../lib/tone";
import type { HelpTopic } from "./topics";

/** Texte d'un sujet d'aide : variante `_formal` si le module de thème déclare ce ton, sinon texte neutre. */
export function useHelpBody(): (topic: HelpTopic) => string {
  const { t, i18n } = useTranslation();
  const tone = useTone();
  return (topic) => (tone === "formal" && i18n.exists(`help.${topic}.body_formal`) ? t(`help.${topic}.body_formal`) : t(`help.${topic}.body`));
}

/** Panneau latéral court (3 phrases) : Échap ou « Fermer » le ferme ; le focus entre dans le panneau à l'ouverture. */
export function HelpPanel({ topic, onClose, id }: { topic: HelpTopic | null; onClose: () => void; id?: string }) {
  const { t } = useTranslation();
  const body = useHelpBody();
  const heading = useRef<HTMLHeadingElement>(null);
  const uid = useId();
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (!topic) return;
    heading.current?.focus();
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") closeRef.current();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [topic]);
  if (!topic) return <></>;
  return (
    <aside id={id} className="help-panel" aria-labelledby={`${uid}-t`}>
      <div className="help-panel-head">
        <h2 id={`${uid}-t`} ref={heading} tabIndex={-1}>
          {t(`help.${topic}.title`)}
        </h2>
        <button type="button" className="btn btn-text" onClick={onClose}>
          <Icon name="close" />
          {t("help.close")}
        </button>
      </div>
      <p>{body(topic)}</p>
      <p className="meta">{t("help.more")}</p>
    </aside>
  );
}

/** Icône « ? » à côté d'un titre : ouvre le panneau du sujet, rend le focus au bouton à la fermeture. */
export function HelpButton({ topic }: { topic: HelpTopic }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const close = () => {
    setOpen(false);
    button.current?.focus();
  };
  return (
    <>
      <button
        ref={button}
        type="button"
        className="btn btn-text help-button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-label={t("help.open", { topic: t(`help.${topic}.title`) })}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <Icon name="help" />
      </button>
      <HelpPanel id={panelId} topic={open ? topic : null} onClose={close} />
    </>
  );
}

/** Titre de page (h1) suivi de son bouton d'aide. */
export function PageTitle({ topic, children }: { topic: HelpTopic; children: ReactNode }) {
  return (
    <div className="page-title">
      <h1>{children}</h1>
      <HelpButton topic={topic} />
    </div>
  );
}

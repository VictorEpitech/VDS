import { useId, useRef, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import type { NodeRef, ScopeTree } from "../scope/scope";

interface Props {
  tree: ScopeTree;
  /** Nœud organisation (id requis par l'API). */
  organization: { id: string; name: string } | undefined;
  value: readonly NodeRef[];
  onChange: (next: NodeRef[]) => void;
  disabled?: boolean;
}

const has = (v: readonly NodeRef[], type: NodeRef["type"], id: string) => v.some((n) => n.type === type && n.id === id);

/** Résumé « 2 sites, 1 groupe » ; « Toute l'organisation » ; « Aucun périmètre ». */
export function useScopeSummary(): (v: readonly { type: string }[]) => string {
  const { t } = useTranslation();
  return (v) => {
    if (v.some((n) => n.type === "organization")) return t("users.scope.all");
    const sites = v.filter((n) => n.type === "site").length;
    const groups = v.filter((n) => n.type === "group").length;
    if (sites + groups === 0) return t("users.scope.none_short");
    return [sites > 0 ? t("users.scope.sites", { count: sites }) : "", groups > 0 ? t("users.scope.groups", { count: groups }) : ""].filter(Boolean).join(", ");
  };
}

/**
 * Périmètre d'un éditeur : « Toute l'organisation » ou arbre sites -> groupes à cases.
 * Cases natives (espace coche) ; flèches haut/bas déplacent le focus d'une case à l'autre.
 * Cocher un site couvre ses groupes (cases cochées et verrouillées) : le périmètre ne stocke que des nœuds.
 */
export function ScopePicker({ tree, organization, value, onChange, disabled }: Props) {
  const { t } = useTranslation();
  const uid = useId();
  const box = useRef<HTMLDivElement>(null);
  const all = value.some((n) => n.type === "organization");

  const toggleOrg = (on: boolean) => onChange(on && organization ? [{ type: "organization", id: organization.id }] : []);
  const toggleSite = (id: string, groupIds: string[], on: boolean) => {
    const rest = value.filter((n) => !(n.type === "site" && n.id === id) && !(n.type === "group" && groupIds.includes(n.id)));
    onChange(on ? [...rest, { type: "site", id }] : rest);
  };
  const toggleGroup = (id: string, on: boolean) => {
    const rest = value.filter((n) => !(n.type === "group" && n.id === id));
    onChange(on ? [...rest, { type: "group", id }] : rest);
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const boxes = [...(box.current?.querySelectorAll<HTMLInputElement>("input[type=checkbox]:not(:disabled)") ?? [])];
    const i = boxes.indexOf(document.activeElement as HTMLInputElement);
    if (i < 0) return;
    e.preventDefault();
    boxes[Math.min(boxes.length - 1, Math.max(0, i + (e.key === "ArrowDown" ? 1 : -1)))]?.focus();
  };

  return (
    <fieldset className="scope-picker" disabled={disabled}>
      <legend>{t("users.scope.title")}</legend>
      {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions -- délégation des flèches aux cases natives (déjà focalisables) */}
      <div ref={box} onKeyDown={onKeyDown}>
        <div className="field field-check">
          <input id={`${uid}-org`} type="checkbox" checked={all} disabled={!organization} onChange={(e) => toggleOrg(e.target.checked)} />
          <label htmlFor={`${uid}-org`}>{t("users.scope.all")}</label>
        </div>
        {tree.sites.length === 0 ? <p className="meta">{t("users.scope.no_sites")}</p> : null}
        <ul className="list-reset scope-tree">
          {tree.sites.map((s) => {
            const siteOn = has(value, "site", s.id);
            const groupIds = s.groups.map((g) => g.id);
            return (
              <li key={s.id}>
                <div className="field field-check">
                  <input id={`${uid}-s-${s.id}`} type="checkbox" checked={all || siteOn} disabled={all} onChange={(e) => toggleSite(s.id, groupIds, e.target.checked)} />
                  <label htmlFor={`${uid}-s-${s.id}`}>{s.name}</label>
                </div>
                {s.groups.length > 0 ? (
                  <ul className="list-reset scope-tree-groups" aria-label={t("users.scope.groups_of", { site: s.name })}>
                    {s.groups.map((g) => (
                      <li key={g.id} className="field field-check">
                        <input
                          id={`${uid}-g-${g.id}`}
                          type="checkbox"
                          checked={all || siteOn || has(value, "group", g.id)}
                          disabled={all || siteOn}
                          onChange={(e) => toggleGroup(g.id, e.target.checked)}
                        />
                        <label htmlFor={`${uid}-g-${g.id}`}>{g.name}</label>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      </div>
      <p className="field-help">{t("users.scope.help")}</p>
    </fieldset>
  );
}

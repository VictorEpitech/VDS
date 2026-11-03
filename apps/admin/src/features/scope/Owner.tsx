import { useId, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import { isShared, ownerKey, ownerOptions, useCanWrite, useOwnerLabel, useScopeTree, type NodeRef, type Owner } from "./scope";

/** Badge propriétaire : « Partagé » ou nom du site/groupe (icône + libellé, jamais la couleur seule). */
export function OwnerBadge({ owner }: { owner: Owner }) {
  const { t } = useTranslation();
  const label = useOwnerLabel();
  const shared = isShared(owner);
  return (
    <span className="status-badge owner-badge" data-state={shared ? "revoked" : "scheduled_off"} data-owner={shared ? "shared" : owner?.type}>
      <Icon name={shared ? "inherit" : "site"} />
      <span>
        <span className="visually-hidden">{t("scope.owner.label")} : </span>
        {label(owner)}
      </span>
    </span>
  );
}

/** Choix du propriétaire à la création : `owner` est `undefined` tant que l'arbre est inconnu (le serveur applique alors son défaut). */
export function useOwnerChoice() {
  const { t } = useTranslation();
  const tree = useScopeTree();
  const options = useMemo(() => ownerOptions(tree.data, t("scope.owner.shared")), [tree.data, t]);
  const [key, setKey] = useState<string | null>(null);
  const current = options.find((o) => o.key === key) ?? options[0];
  return { options, current, setKey, owner: current ? current.owner : undefined };
}

type Choice = ReturnType<typeof useOwnerChoice>;

/** Sélecteur « Propriétaire » : affiché seulement s'il y a plus d'un choix (« Partagé » si `can_share`, sites et groupes modifiables). */
export function OwnerField({ choice }: { choice: Choice }) {
  const { t } = useTranslation();
  const id = useId();
  if (choice.options.length < 2) return null;
  return (
    <div className="field">
      <label htmlFor={id}>{t("scope.owner.label")}</label>
      <select id={id} value={choice.current?.key ?? ""} onChange={(e) => choice.setKey(e.target.value)} aria-describedby={`${id}-help`}>
        {choice.options.map((o) => (
          <option key={o.key} value={o.key}>
            {o.label}
          </option>
        ))}
      </select>
      <p className="field-help" id={`${id}-help`}>
        {t("scope.owner.help")}
      </p>
    </div>
  );
}

/** Changement de propriétaire d'un objet existant ; rien si l'objet n'est pas modifiable ou s'il n'y a pas d'autre choix. */
export function OwnerEdit({ owner, pending, onSave }: { owner: Owner; pending?: boolean; onSave: (next: NodeRef | null) => void }) {
  const { t } = useTranslation();
  const id = useId();
  const tree = useScopeTree();
  const canWrite = useCanWrite();
  const options = useMemo(() => ownerOptions(tree.data, t("scope.owner.shared")), [tree.data, t]);
  const [key, setKey] = useState<string | null>(null);
  if (!canWrite(owner) || options.length < 2) return null;
  const value = key ?? ownerKey(owner);
  const changed = value !== ownerKey(owner);
  const next = options.find((o) => o.key === value);
  return (
    <div className="field owner-edit">
      <label htmlFor={id}>{t("scope.owner.label")}</label>
      <div className="owner-edit-row">
        <select id={id} value={value} onChange={(e) => setKey(e.target.value)}>
          {options.some((o) => o.key === ownerKey(owner)) ? null : <option value={ownerKey(owner)}>{t("scope.owner.current")}</option>}
          {options.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
        <button type="button" className="btn btn-text" disabled={!changed || !next || pending} onClick={() => next && onSave(next.owner)}>
          <Icon name="check" />
          {t("scope.owner.apply")}
        </button>
      </div>
    </div>
  );
}

/** Usages hors périmètre : seulement un nombre, jamais de nom (J-D5). */
export function HiddenUsage({ count }: { count: number | undefined }) {
  const { t } = useTranslation();
  if (!count) return null;
  return (
    <p className="meta hidden-usage">
      <Icon name="lock" /> {t("scope.hidden_count", { count })}
    </p>
  );
}

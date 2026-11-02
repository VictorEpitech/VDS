import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useErrorText } from "../../auth/error-text";
import { ConfirmAction, ErrorAlert } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { useApi } from "../../lib/api";
import type { MediaBulkResult } from "../../lib/queries";
import { ExpiryField } from "./ExpiryField";
import { TagEditor } from "./TagEditor";

type Action = "set_expiry" | "add_tags" | "remove_tags" | "delete";
type Mode = "expiry" | "add_tags" | "remove_tags" | null;

interface Props {
  /** Médias sélectionnés : id -> titre. */
  selected: ReadonlyMap<string, string>;
  onClear: () => void;
  /** Appelé avec le résultat : retire les ids traités de la sélection et rafraîchit la liste. */
  onResult: (result: MediaBulkResult) => void;
}

/** Actions groupées sur la sélection (au plus 200 médias par appel). */
export function BulkBar({ selected, onClear, onResult }: Props) {
  const { t } = useTranslation();
  const errorText = useErrorText();
  const api = useApi();
  const [mode, setMode] = useState<Mode>(null);
  const [tags, setTags] = useState<string[]>([]);
  const [result, setResult] = useState<MediaBulkResult | null>(null);
  const run = useMutation({
    mutationFn: (body: { action: Action; expires_at?: string | null; tags?: string[] }) =>
      api.post<MediaBulkResult>("/media/bulk", { ids: [...selected.keys()], ...body }),
    onSuccess: (r) => {
      setResult(r);
      setMode(null);
      setTags([]);
      onResult(r);
    },
  });
  const count = selected.size;
  const tooMany = count > 200;
  const open = (m: Exclude<Mode, null>) => {
    setMode(mode === m ? null : m);
    setTags([]);
    setResult(null);
  };

  return (
    <>
      {count > 0 ? (
        <section className="card bulk-bar" aria-label={t("media.bulk.label")}>
          <p className="bulk-count" role="status">
            {t("media.bulk.selected", { count })}
          </p>
          <div className="actions">
            <button type="button" className="btn btn-text" aria-expanded={mode === "expiry"} onClick={() => open("expiry")}>
              <Icon name="calendar" />
              {t("media.bulk.expiry")}
            </button>
            <button type="button" className="btn btn-text" aria-expanded={mode === "add_tags"} onClick={() => open("add_tags")}>
              <Icon name="add" />
              {t("media.bulk.add_tags")}
            </button>
            <button type="button" className="btn btn-text" aria-expanded={mode === "remove_tags"} onClick={() => open("remove_tags")}>
              <Icon name="delete" />
              {t("media.bulk.remove_tags")}
            </button>
            <ConfirmAction
              label={t("media.bulk.delete")}
              confirmLabel={t("media.bulk.delete_confirm", { count })}
              pending={run.isPending}
              onConfirm={() => run.mutate({ action: "delete" })}
            />
            <button type="button" className="btn btn-text" onClick={onClear}>
              {t("media.bulk.clear")}
            </button>
          </div>
          {tooMany ? (
            <p className="field-error" role="alert">
              <Icon name="warning" />
              <span>{t("media.bulk.too_many")}</span>
            </p>
          ) : null}
          {mode === "expiry" ? <ExpiryField value={null} allowClear pending={run.isPending || tooMany} onSave={(iso) => run.mutate({ action: "set_expiry", expires_at: iso })} /> : null}
          {mode === "add_tags" || mode === "remove_tags" ? (
            <div>
              <TagEditor scope="media" tags={tags} onChange={setTags} label={t(mode === "add_tags" ? "media.bulk.tags_to_add" : "media.bulk.tags_to_remove")} />
              <button
                type="button"
                className="btn btn-filled"
                disabled={tags.length === 0 || run.isPending || tooMany}
                onClick={() => run.mutate({ action: mode, tags })}
              >
                {t("media.bulk.apply")}
              </button>
            </div>
          ) : null}
        </section>
      ) : null}
      {run.isError ? <ErrorAlert error={run.error} /> : null}
      {result ? (
        <div className={result.failed.length > 0 ? "alert alert-warning" : "alert alert-success"} role={result.failed.length > 0 ? "alert" : "status"}>
          <Icon name={result.failed.length > 0 ? "warning" : "check"} />
          <div>
            <p>{t("media.bulk.result", { done: result.done.length, failed: result.failed.length })}</p>
            {result.failed.length > 0 ? (
              <ul>
                {result.failed.map((f) => (
                  <li key={f.id}>
                    {t("media.bulk.failed_item", { title: selected.get(f.id ?? "") ?? f.id, reason: errorText(f.code ?? "errors.unknown") })}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </div>
      ) : null}
    </>
  );
}

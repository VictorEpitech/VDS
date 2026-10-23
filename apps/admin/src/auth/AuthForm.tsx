import { useId, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../components/Icon";
import { ApiError } from "../lib/api";
import { useErrorText } from "./error-text";
import type { AuthFormField, AuthFormSpec } from "./types";

type Values = Record<string, string>;

interface Props {
  spec: AuthFormSpec;
  onSubmit: (values: Values) => Promise<void>;
  /** Retourne true si l'erreur est déjà traitée (redirection) : rien n'est affiché. */
  onError?: (err: ApiError) => boolean;
}

/** Formulaire généré depuis la description fournie par le module d'authentification. */
export function AuthForm({ spec, onSubmit, onError }: Props) {
  const { t } = useTranslation();
  const errorText = useErrorText();
  const uid = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const alertRef = useRef<HTMLDivElement>(null);
  const [values, setValues] = useState<Values>({});
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [failure, setFailure] = useState<ApiError | null>(null);
  const [pending, setPending] = useState(false);

  const validate = (): Record<string, string> => {
    const out: Record<string, string> = {};
    for (const f of spec.fields) {
      const v = values[f.name] ?? "";
      if (v.trim() === "") out[f.name] = "errors.field_required";
      else if (f.type === "email" && !/^[^@\s]+@[^@\s]+$/.test(v.trim())) out[f.name] = "errors.field_email";
    }
    return out;
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (pending) return;
    setFailure(null);
    const invalid = validate();
    setFieldErrors(invalid);
    const first = spec.fields.find((f) => invalid[f.name]);
    if (first) {
      formRef.current?.querySelector<HTMLInputElement>(`[name="${first.name}"]`)?.focus();
      return;
    }
    setPending(true);
    try {
      const clean = (f: AuthFormField) => (f.type === "password" ? (values[f.name] ?? "") : (values[f.name] ?? "").trim());
      await onSubmit(Object.fromEntries(spec.fields.map((f) => [f.name, clean(f)])));
    } catch (err) {
      const apiErr = err instanceof ApiError ? err : new ApiError(0, "errors.unknown");
      if (!(onError?.(apiErr) ?? false)) {
        setFailure(apiErr);
        // Annonce et place le focus sur le message d'erreur.
        queueMicrotask(() => alertRef.current?.focus());
      }
    } finally {
      setPending(false);
    }
  };

  return (
    <form ref={formRef} className="auth-form" onSubmit={(e) => void submit(e)} noValidate>
      {spec.fields.map((f) => {
        const id = `${uid}-${f.name}`;
        const err = fieldErrors[f.name];
        return (
          <div className="field" key={f.name}>
            <label htmlFor={id}>{t(`${spec.i18n}.fields.${f.name}`)}</label>
            <input
              id={id}
              name={f.name}
              type={f.type}
              autoComplete={f.autoComplete}
              autoCapitalize="none"
              spellCheck={false}
              className={f.mono ? "mono" : undefined}
              value={values[f.name] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
              aria-invalid={err ? true : undefined}
              aria-describedby={err ? `${id}-error` : undefined}
              required
            />
            {err ? (
              <p className="field-error" id={`${id}-error`}>
                <Icon name="warning" />
                <span>{t(err)}</span>
              </p>
            ) : null}
          </div>
        );
      })}
      <div ref={alertRef} tabIndex={-1} role="alert" className="form-alert-slot">
        {failure ? (
          <p className="alert alert-error">
            <Icon name="warning" />
            <span>{errorText(failure.code)}</span>
          </p>
        ) : null}
      </div>
      <button type="submit" className="btn btn-filled" disabled={pending} aria-busy={pending}>
        {t(`${spec.i18n}.submit`)}
      </button>
    </form>
  );
}

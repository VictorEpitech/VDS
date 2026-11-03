import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { Empty, ErrorAlert, QueryStatus } from "../components/feedback";
import { Icon } from "../components/Icon";
import { ExclusiveSetting } from "../features/layouts/ExclusiveSetting";
import { LayoutThumb } from "../features/layouts/LayoutThumb";
import { TEMPLATE_IDS, templateZones, useCreateLayout, useLayoutPage, type Orientation, type TemplateId } from "../features/layouts/layouts";
import { useOrganization } from "../lib/queries";

export function Layouts() {
  const { t } = useTranslation();
  const uid = useId();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [orientationFilter, setOrientationFilter] = useState<Orientation | "">("");
  const layouts = useLayoutPage({ q, orientation: orientationFilter });
  const create = useCreateLayout();
  const org = useOrganization();
  const [open, setOpen] = useState(false);
  const [template, setTemplate] = useState<TemplateId>("full");
  const [orientation, setOrientation] = useState<Orientation>("landscape");
  const [name, setName] = useState("");
  const [missing, setMissing] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);
  useEffect(() => {
    if (open) formRef.current?.querySelector<HTMLElement>("input")?.focus();
  }, [open]);
  const items = layouts.data?.items ?? [];
  const filtered = q.trim() !== "" || orientationFilter !== "";

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const n = name.trim();
    setMissing(n === "");
    if (!n) return;
    create.mutate({ name: n, orientation, zones: templateZones(template, (k) => t(`layouts.templates.${template}.${k}`)), tags: [] }, { onSuccess: (l) => navigate(`/content/layouts/${l.id}`) });
  };

  return (
    <>
      <div className="page-head">
        <h1>{t("layouts.title")}</h1>
        {!open ? (
          <button type="button" className="btn btn-filled" onClick={() => setOpen(true)}>
            <Icon name="add" />
            {t("layouts.create")}
          </button>
        ) : null}
      </div>
      <p className="lead">{t("layouts.intro")}</p>

      {open ? (
        <form ref={formRef} className="card layout-create" onSubmit={submit} noValidate aria-label={t("layouts.create")}>
          <h2>{t("layouts.create")}</h2>
          <div className="field">
            <label htmlFor={`${uid}-name`}>{t("layouts.editor.name")}</label>
            <input id={`${uid}-name`} value={name} maxLength={120} required aria-invalid={missing ? true : undefined} onChange={(e) => setName(e.target.value)} />
            {missing ? (
              <p className="field-error">
                <Icon name="warning" />
                <span>{t("errors.field_required")}</span>
              </p>
            ) : null}
          </div>
          <fieldset className="field">
            <legend>{t("layouts.create_template")}</legend>
            <div className="template-grid" role="radiogroup" aria-label={t("layouts.create_template")}>
              {TEMPLATE_IDS.map((id) => (
                <label key={id} className="template-item">
                  <input type="radio" name={`${uid}-tpl`} value={id} checked={template === id} onChange={() => setTemplate(id)} />
                  <LayoutThumb orientation={orientation} zones={templateZones(id, () => "").map((z) => ({ key: z.key, x: z.x, y: z.y, w: z.w, h: z.h, z: z.z }))} />
                  <span>{t(`layouts.templates.${id}.name`)}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset className="field">
            <legend>{t("layouts.editor.orientation")}</legend>
            <div className="seg" role="radiogroup" aria-label={t("layouts.editor.orientation")}>
              {(["landscape", "portrait"] as const).map((o) => (
                <label key={o} className="seg-item">
                  <input type="radio" name={`${uid}-o`} value={o} checked={orientation === o} onChange={() => setOrientation(o)} />
                  <span>{t(`layouts.orientation.${o}`)}</span>
                </label>
              ))}
            </div>
          </fieldset>
          {create.isError ? <ErrorAlert error={create.error} /> : null}
          <div className="actions">
            <button type="submit" className="btn btn-filled" disabled={create.isPending} aria-busy={create.isPending}>
              <Icon name="add" />
              {t("layouts.create_submit")}
            </button>
            <button type="button" className="btn btn-text" onClick={() => setOpen(false)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      ) : null}

      <section className="section" aria-labelledby={`${uid}-list`}>
        <h2 id={`${uid}-list`}>{t("layouts.list")}</h2>
        <div className="filters" role="search" aria-label={t("layouts.filters.label")}>
          <div className="field">
            <label htmlFor={`${uid}-q`}>{t("layouts.filters.search")}</label>
            <input id={`${uid}-q`} type="search" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor={`${uid}-or`}>{t("layouts.filters.orientation")}</label>
            <select id={`${uid}-or`} value={orientationFilter} onChange={(e) => setOrientationFilter(e.target.value as Orientation | "")}>
              <option value="">{t("layouts.filters.any")}</option>
              <option value="landscape">{t("layouts.orientation.landscape")}</option>
              <option value="portrait">{t("layouts.orientation.portrait")}</option>
            </select>
          </div>
        </div>
        <QueryStatus query={layouts} />
        {layouts.isSuccess && items.length === 0 ? (
          <Empty
            icon="layout"
            action={
              !open && !filtered ? (
                <button type="button" className="btn btn-filled" onClick={() => setOpen(true)}>
                  <Icon name="add" />
                  {t("layouts.empty_action")}
                </button>
              ) : undefined
            }
          >
            {filtered ? t("layouts.no_match") : t("layouts.empty")}
          </Empty>
        ) : null}
        <ul className="layout-cards list-reset">
          {items.map((l) => (
            <li key={l.id} className="card layout-card">
              <LayoutThumb zones={l.zones} orientation={l.orientation} />
              <h3>
                <Link to={`/content/layouts/${l.id}`}>{l.name}</Link>
              </h3>
              <p className="meta">
                {t(`layouts.orientation.${l.orientation}`)} · {t("layouts.zone_count", { count: l.zone_count })} · {t("layouts.usage_count", { count: l.usage_count })}
              </p>
            </li>
          ))}
        </ul>
      </section>

      {org.data ? (
        <section className="section" aria-labelledby={`${uid}-set`}>
          <h2 id={`${uid}-set`}>{t("layouts.exclusive.title")}</h2>
          <p className="meta">{t("layouts.exclusive.intro")}</p>
          <ExclusiveSetting level="organization" id={org.data.id} />
        </section>
      ) : null}
    </>
  );
}

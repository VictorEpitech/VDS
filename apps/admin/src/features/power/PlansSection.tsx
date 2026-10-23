import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { ConfirmAction, Empty, ErrorAlert, QueryStatus } from "../../components/feedback";
import { Icon } from "../../components/Icon";
import { useApi } from "../../lib/api";
import { powerKeys, usePlans, type Plan, type Rule } from "../../lib/queries";
import { RulesEditor } from "../schedule/RulesEditor";
import { ruleProblem } from "../schedule/rules";
import { rulesText } from "./format";

function PlanForm({ plan, onDone }: { plan?: Plan; onDone: () => void }) {
  const { t } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const uid = useId();
  const [name, setName] = useState(plan?.name ?? "");
  const [description, setDescription] = useState(plan?.description ?? "");
  const [rules, setRules] = useState<Rule[]>(plan?.rules ?? []);
  const [invalid, setInvalid] = useState<Set<number>>(new Set());
  const save = useMutation({
    mutationFn: (body: { name: string; description: string | null; rules: Rule[] }) => (plan ? api.patch(`/power/plans/${plan.id}`, body) : api.post("/power/plans", body)),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: powerKeys.plans });
      onDone();
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const bad = new Set<number>();
    rules.forEach((r, i) => {
      if (ruleProblem(r)) bad.add(i);
    });
    setInvalid(bad);
    if (bad.size > 0 || !name.trim()) return;
    save.mutate({ name: name.trim(), description: description.trim() || null, rules });
  };
  const title = plan ? t("power.plans.edit") : t("power.plans.create");
  return (
    <form className="card" onSubmit={submit} noValidate aria-label={title}>
      <h3>{title}</h3>
      <div className="field">
        <label htmlFor={`${uid}-name`}>{t("power.plans.name")}</label>
        <input id={`${uid}-name`} value={name} maxLength={120} required autoComplete="off" onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor={`${uid}-desc`}>{t("power.plans.description")}</label>
        <input id={`${uid}-desc`} value={description} maxLength={500} autoComplete="off" onChange={(e) => setDescription(e.target.value)} />
      </div>
      <RulesEditor rules={rules} onChange={setRules} invalid={invalid} legend={t("power.plans.rules")} emptyHelp={t("power.plans.rules_empty")} help={t("power.plans.rules_help")} />
      {save.isError ? <ErrorAlert error={save.error} /> : null}
      <div className="actions">
        <button type="submit" className="btn btn-filled" disabled={save.isPending || !name.trim()} aria-busy={save.isPending}>
          <Icon name="online" />
          {t("common.save")}
        </button>
        <button type="button" className="btn btn-text" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

export function PlansSection() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const plans = usePlans();
  const [form, setForm] = useState<"new" | Plan | null>(null);
  const remove = useMutation({
    mutationFn: (p: Plan) => api.delete(`/power/plans/${p.id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: powerKeys.plans }),
  });
  return (
    <section className="section" aria-labelledby="plans-h">
      <div className="page-head">
        <h2 id="plans-h">
          <Icon name="power" />
          {t("power.plans.title")}
        </h2>
        <button type="button" className="btn btn-filled" disabled={form !== null} onClick={() => setForm("new")}>
          <Icon name="add" />
          {t("power.plans.create")}
        </button>
      </div>
      <p className="lead">{t("power.plans.intro")}</p>
      <QueryStatus query={plans} />
      {remove.isError ? <ErrorAlert error={remove.error} /> : null}
      {form ? <PlanForm key={form === "new" ? "new" : form.id} plan={form === "new" ? undefined : form} onDone={() => setForm(null)} /> : null}
      {plans.isSuccess && plans.data.length === 0 ? <Empty icon="power">{t("power.plans.empty")}</Empty> : null}
      <ul className="cards list-reset">
        {(plans.data ?? []).map((p) => (
          <li key={p.id} className="card" aria-labelledby={`plan-${p.id}`}>
            <h3 id={`plan-${p.id}`}>
              <Icon name="power" />
              {p.name}
            </h3>
            {p.description ? <p className="meta">{p.description}</p> : null}
            <p>{rulesText(p.rules, i18n.language, t("power.plans.rules_empty_short"))}</p>
            <div className="actions">
              <button type="button" className="btn btn-text" onClick={() => setForm(p)} aria-label={t("power.plans.edit_for", { name: p.name })}>
                {t("schedules.edit")}
              </button>
              <ConfirmAction label={t("schedules.delete.action")} confirmLabel={t("schedules.delete.confirm", { name: p.name })} pending={remove.isPending} onConfirm={() => remove.mutate(p)} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

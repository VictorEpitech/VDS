import { getPowerDrivers } from "@vds/sdk-ts";
import type { PlayerPowerDriver, ScheduleDocument } from "@vds/sdk-ts";
import { plannedPower } from "./plan";
import type { PlannedState } from "./plan";

/** Ordre d'application : CEC d'abord, l'écran noir en dernier (filet de sécurité visible). */
export const PLAYER_CASCADE = ["power.cec", "power.blank"] as const;
export const RETRY_AFTER_MS = 30_000;
export const MAX_RETRIES = 3;

export interface PowerControllerDeps {
  now: () => number;
  getDoc: () => ScheduleDocument | null;
  drivers?: () => PlayerPowerDriver[];
  report: (code: string) => void;
}

/**
 * Exécute le plan d'alimentation du schedule, sans serveur. Appelé à chaque tick du moteur (1 s) :
 * n'agit qu'à un changement d'état voulu ; les pilotes qui ont échoué sont retentés (3 fois, 30 s).
 */
export class PowerController {
  private applied: PlannedState | null = null;
  private pending: Promise<void> | null = null;
  private failures = new Map<string, number>(); // id pilote -> échecs consécutifs
  private retryAt = 0;
  private done = new Set<string>(); // pilotes déjà réussis pour l'état en cours
  private exhausted = new Set<string>(); // pilotes à bout de tentatives pour l'état en cours
  private retrying = false;

  constructor(private readonly deps: PowerControllerDeps) {}

  get state(): PlannedState | null {
    return this.applied;
  }

  tick(): void {
    if (this.pending) return;
    const want = plannedPower(this.deps.getDoc(), this.deps.now());
    if (want !== this.applied) {
      this.applied = want;
      this.failures.clear();
      this.done.clear();
      this.exhausted.clear();
      this.retrying = false;
    } else if (!this.retrying || this.deps.now() < this.retryAt) {
      return;
    }
    this.pending = this.run(want).finally(() => {
      this.pending = null;
    });
  }

  private ordered(): PlayerPowerDriver[] {
    const all = (this.deps.drivers ?? getPowerDrivers)();
    return PLAYER_CASCADE.map((id) => all.find((d) => d.id === id)).filter((d): d is PlayerPowerDriver => !!d);
  }

  private async run(want: PlannedState): Promise<void> {
    const failed = new Set<string>();
    for (const d of this.ordered()) {
      if (this.done.has(d.id) || this.exhausted.has(d.id)) continue;
      try {
        if (!(await d.available())) continue;
        const r = await d.setPower(want);
        if (r.ok) {
          this.done.add(d.id);
          this.failures.delete(d.id);
          continue;
        }
        this.deps.report(r.code ?? "power.failed");
      } catch {
        this.deps.report("power.failed");
      }
      failed.add(d.id);
      const n = (this.failures.get(d.id) ?? 0) + 1;
      this.failures.set(d.id, n);
      if (n >= MAX_RETRIES) {
        this.exhausted.add(d.id);
        failed.delete(d.id);
      }
    }
    this.retrying = failed.size > 0;
    this.retryAt = this.retrying ? this.deps.now() + RETRY_AFTER_MS : 0;
  }
}

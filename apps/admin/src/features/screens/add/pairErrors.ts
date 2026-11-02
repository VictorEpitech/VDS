import { ApiError } from "../../../lib/api";

/** Motif UX d'un échec d'appairage, dérivé du `code` serveur (les codes serveur ne changent pas). */
export type PairMotive = "code_unknown" | "code_expired" | "already_paired" | "too_many_attempts" | "other";

export function pairMotive(error: unknown): PairMotive {
  if (!(error instanceof ApiError)) return "other";
  if (error.status === 429) return "too_many_attempts";
  switch (error.code) {
    case "errors.pairing_code_invalid":
      return "code_unknown";
    case "errors.pairing_code_expired":
      return "code_expired";
    case "errors.pairing_code_claimed":
      return "already_paired";
    default:
      return "other";
  }
}

/** Code d'appairage : sans espaces ni tirets, majuscules (le serveur tolère O/I/L). */
export const normalizeCode = (raw: string) => raw.replace(/[\s-]/g, "").toUpperCase();
export const CODE_MIN = 6;
export const CODE_MAX = 12;
export type CodeState = "empty" | "short" | "ok" | "long";
export function codeState(raw: string): CodeState {
  const n = normalizeCode(raw).length;
  return n === 0 ? "empty" : n < CODE_MIN ? "short" : n > CODE_MAX ? "long" : "ok";
}

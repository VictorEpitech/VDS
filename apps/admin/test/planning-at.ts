/** Réponse simulée de GET /planning/at à partir de l'ancienne forme « programmation active » des tests. */
interface Playing {
  screen_id: string;
  source: string;
  override_id?: string | null;
  schedule_id?: string | null;
  schedule_name?: string | null;
  playlist_id?: string | null;
  playlist_name?: string | null;
  until?: string | null;
  next_change_at?: string | null;
}

const none = { kind: "none", id: null, name: null, target_type: null, target_id: null, target_name: null, inherited: false, priority: null, priority_level: null };

export function atOut(list: Playing[], name = (id: string) => id) {
  const at = new Date().toISOString();
  return {
    at,
    next_cursor: null,
    screens: list.map((p) => ({
      screen_id: p.screen_id,
      screen_name: name(p.screen_id),
      presence: "online",
      next_change_at: p.next_change_at ?? null,
      segment: {
        start: new Date(Date.now() - 3_600_000).toISOString(),
        end: p.until ?? new Date(Date.now() + 3_600_000).toISOString(),
        state: p.source === "override" ? "overridden" : p.source === "schedule" ? "play" : "off",
        content: p.playlist_id ? { kind: "playlist", id: p.playlist_id, name: p.playlist_name ?? "" } : null,
        source: p.source === "none" ? none : { ...none, kind: p.source, id: p.source === "override" ? p.override_id : p.schedule_id, name: p.source === "override" ? p.playlist_name : p.schedule_name },
        reason: p.source === "none" ? "no_candidate" : "only_candidate",
        losers: [],
      },
    })),
  };
}

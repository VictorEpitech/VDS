import type { TFunction } from "i18next";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useLayouts } from "../layouts/layouts";
import { usePlaylists, useScreens, useSchedules, useSites, useMedia } from "../../lib/queries";
import { registerPaletteSource, type PaletteEntry } from "./registry";

/** Entrées fixes : navigation, créations, actions. Le rôle est filtré à l'affichage (`requires`). */
export function coreEntries(t: TFunction): PaletteEntry[] {
  const word = (k: string) => t(k).toLowerCase();
  const nav = (id: string, to: string, key: string, icon: PaletteEntry["icon"], requires?: PaletteEntry["requires"], extra: string[] = []): PaletteEntry => ({
    id: `nav:${id}`, kind: "navigate", labelKey: `palette.go.${key}`, keywords: [word(`nav.${key}`), ...extra], icon, requires, run: (n) => n(to),
  });
  return [
    nav("home", "/", "home", "home"),
    nav("screens", "/screens", "screens", "screen"),
    nav("media", "/content/media", "media", "media"),
    nav("playlists", "/content/playlists", "playlists", "playlist"),
    nav("layouts", "/content/layouts", "layouts", "layout"),
    nav("playback", "/playback", "playback", "schedule"),
    nav("sites", "/sites", "sites", "site", "unrestricted"),
    nav("power", "/power", "power", "power", "unrestricted"),
    nav("notifications", "/notifications", "notifications", "mail", "unrestricted"),
    nav("users", "/users", "users", "user", "admin"),
    nav("help", "/help", "help", "help"),
    { id: "create:screen", kind: "create", labelKey: "palette.create.screen", keywords: [t("nav.add_screen")], icon: "add", requires: "admin", run: (n) => n("/screens/add") },
    { id: "create:android", kind: "create", labelKey: "palette.create.android", keywords: [t("nav.install_android")], icon: "add", requires: "admin", run: (n) => n("/screens/add/android") },
    { id: "create:content", kind: "create", labelKey: "palette.create.content", keywords: [t("nav.import")], icon: "upload", run: (n) => n("/content/media/add") },
    { id: "create:playlist", kind: "create", labelKey: "palette.create.playlist", keywords: [t("nav.playlists")], icon: "playlist", run: (n) => n("/content/playlists") },
    { id: "create:agent", kind: "create", labelKey: "palette.create.agent", keywords: [t("nav.connect_agent")], icon: "agent", requires: "admin", run: (n) => n("/sites/connect") },
    { id: "action:issues", kind: "action", labelKey: "palette.action.issues", keywords: [t("nav.screens")], icon: "warning", run: (n) => n("/screens?filter=issues") },
    { id: "action:onboarding", kind: "action", labelKey: "palette.action.onboarding", keywords: [t("nav.home")], icon: "home", requires: "admin", run: (n) => n("/help") },
  ];
}

/** Données déjà chargées par TanStack Query : écrans, sites, médias, playlists, mises en page, programmations. */
export function usePaletteDataSources() {
  const { t } = useTranslation();
  const screens = useScreens().data;
  const sites = useSites().data;
  const media = useMedia().data;
  const playlists = usePlaylists().data;
  const layouts = useLayouts().data;
  const schedules = useSchedules().data;
  useEffect(() => {
    const item = (kind: string, id: string, name: string, icon: PaletteEntry["icon"], to: string, extra: string[] = []): PaletteEntry => ({
      id: `${kind}:${id}`, kind: "navigate", labelKey: `palette.open.${kind}`, labelParams: { name }, keywords: extra, icon, run: (n) => n(to),
    });
    return registerPaletteSource(() => [
      ...(screens ?? []).filter((s) => !s.archived_at).map((s) => item("screen", s.id, s.name, "screen", `/screens#screen-${s.id}`)),
      ...(sites ?? []).filter((s) => !s.archived_at).map((s) => ({ ...item("site", s.id, s.name, "site", `/sites/${s.id}`, [s.slug]), requires: "unrestricted" as const })),
      ...(media?.items ?? []).map((m) => item("media", m.id, m.title, "media", "/content/media")),
      ...(playlists?.items ?? []).map((p) => item("playlist", p.id, p.name, "playlist", `/content/playlists/${p.id}`)),
      ...(layouts?.items ?? []).map((l) => item("layout", l.id, l.name, "layout", `/content/layouts/${l.id}`)),
      ...(schedules ?? []).map((s) => item("schedule", s.id, s.name, "schedule", "/playback")),
    ]);
  }, [screens, sites, media, playlists, layouts, schedules, t]);
}

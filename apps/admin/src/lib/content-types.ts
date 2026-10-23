import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import type { components, Locale } from "@vds/sdk-ts";
import type { i18n } from "i18next";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useApi } from "./api";

/**
 * Types de contenu : schéma JSON d'élément, noms et traductions des modules `content.*` (même format
 * que les modules tiers), lus depuis `GET /registry/content-types`. Rien n'est embarqué au build.
 */
export interface JsonSchema {
  type?: "string" | "integer" | "number" | "boolean" | "object";
  title?: string;
  description?: string;
  default?: unknown;
  enum?: string[];
  format?: string;
  pattern?: string;
  minimum?: number;
  maximum?: number;
  maxLength?: number;
  required?: string[];
  properties?: Record<string, JsonSchema>;
  "x-vds-level"?: "simple" | "advanced";
  /** Le champ référence un média de ce genre (image, video, document). */
  "x-vds-media"?: "image" | "video" | "document";
}

export interface ContentType {
  id: string;
  name: Record<Locale, string>;
  schema: JsonSchema;
  /** Types MIME de médias que ce type sait lire (vide si aucun média). */
  accepts: string[];
  requiresNetwork: boolean;
}

type ContentTypeOut = components["schemas"]["ContentTypeOut"];

const localized = (v: Record<string, string>, id: string): Record<Locale, string> => ({ fr: v.fr ?? v.en ?? id, en: v.en ?? v.fr ?? id });

function toContentType(c: ContentTypeOut): ContentType {
  return { id: c.id, name: localized(c.name, c.id), schema: c.item_schema as JsonSchema, accepts: c.accepts, requiresNetwork: c.requires_network };
}

/** Traductions d'un module sous son identifiant comme espace de noms i18next. */
export function registerContentI18n(instance: i18n, types: readonly ContentTypeOut[]): void {
  for (const c of types) {
    for (const [lng, bundle] of Object.entries(c.i18n)) instance.addResourceBundle(lng, c.id, bundle, true, true);
  }
}

export interface ContentTypes {
  types: readonly ContentType[];
  /** Types MIME acceptés à l'envoi : l'union de ceux déclarés par les modules de contenu. */
  acceptedMime: readonly string[];
  byId: (id: string) => ContentType | undefined;
  query: UseQueryResult<ContentTypeOut[]>;
}

const NONE: readonly ContentType[] = [];

/** Types de contenu du registre ; les traductions des modules sont enregistrées avant la mise à disposition. */
export function useContentTypes(): ContentTypes {
  const api = useApi();
  const { i18n: instance } = useTranslation();
  const query = useQuery({
    queryKey: ["registry", "content-types"],
    staleTime: 5 * 60_000,
    queryFn: async () => {
      const data = await api.get<ContentTypeOut[]>("/registry/content-types");
      registerContentI18n(instance, data);
      return data;
    },
  });
  return useMemo(() => {
    const types = query.data ? query.data.map(toContentType).sort((a, b) => a.id.localeCompare(b.id)) : NONE;
    return {
      types,
      acceptedMime: [...new Set(types.flatMap((t) => t.accepts))].sort(),
      byId: (id: string) => types.find((c) => c.id === id),
      query,
    };
  }, [query]);
}


import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const MODULES = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../modules");
type Json = { id: string; version: string; name: unknown; description: unknown; content?: { player?: { element?: string }; media?: { accepts?: string[] }; requires_network?: boolean } } & Record<string, unknown>;
const read = (file: string) => (fs.existsSync(file) ? (JSON.parse(fs.readFileSync(file, "utf8")) as Json) : null);

/** Réponse de GET /registry/content-types, construite depuis modules/content.* (même source que le serveur). */
export function contentTypesFixture() {
  return fs
    .readdirSync(MODULES)
    .filter((d) => d.startsWith("content."))
    .map((d) => {
      const dir = path.join(MODULES, d);
      const m = read(path.join(dir, "module.json"))!;
      return {
        id: m.id,
        version: m.version,
        name: m.name,
        description: m.description,
        item_schema: read(path.join(dir, "item.schema.json"))!,
        config_schema: read(path.join(dir, "config.schema.json")),
        i18n: { fr: read(path.join(dir, "i18n", "fr.json")) ?? {}, en: read(path.join(dir, "i18n", "en.json")) ?? {} },
        element: m.content?.player?.element ?? "",
        accepts: m.content?.media?.accepts ?? [],
        requires_network: m.content?.requires_network === true,
      };
    })
    .sort((a, b) => a.id.localeCompare(b.id));
}

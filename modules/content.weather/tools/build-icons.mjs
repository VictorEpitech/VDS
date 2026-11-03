#!/usr/bin/env node
/**
 * Régénère le bloc d'icônes de assets/player.js depuis le dépôt MIT metno/weathericons
 * (dossier `weather/svg`). Les trames PNG d'ombrage intégrées sont retirées (rendu à plat, léger).
 * Usage : node tools/build-icons.mjs <chemin>/weathericons/weather/svg
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const src = process.argv[2];
if (!src) throw new Error("usage: build-icons.mjs <dossier svg>");
const target = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../assets/player.js");
const BLOCK = /<(symbol|radialGradient|linearGradient)\b[^>]*\bid="([^"]+)"[\s\S]*?<\/\1>/g;
const shared = {}; // id -> balisage (identique d'un fichier à l'autre, vérifié)
const icons = {}; // code -> { body, deps }
for (const f of fs.readdirSync(src).sort()) {
  if (!f.endsWith(".svg") || f.includes("polartwilight")) continue; // crépuscule polaire : rendu comme le jour
  const own = {};
  const body = fs
    .readFileSync(path.join(src, f), "utf8")
    .replace(/<image\b[^>]*>(\s*<\/image>)?/g, "")
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(BLOCK, (m, _t, id) => {
      const min = m.replace(/\s+/g, " ").replace(/> </g, "><");
      if (shared[id] !== undefined && shared[id] !== min) throw new Error(`id ${id} différent (${f})`);
      shared[id] = min;
      own[id] = true;
      return "";
    })
    .replace(/<defs>\s*<\/defs>/g, "")
    .replace(/\s+/g, " ")
    .replace(/> </g, "><")
    .replace(/<use\b[^>]*>/g, (u) => u.replace(/ (x|y)="0"/g, "").replace(" width=\"100\" height=\"100\"", ""))
    .trim();
  icons[f.slice(0, -4)] = { body: body.replace(/^<svg[^>]*>/, "").replace(/<\/svg>$/, ""), own: Object.keys(own) };
}
const deps = (ids, seen = new Set()) => {
  for (const id of ids) {
    if (seen.has(id)) continue;
    seen.add(id);
    const refs = [...shared[id].matchAll(/#([\w-]+)/g)].map((m) => m[1]).filter((r) => r in shared);
    deps(refs, seen);
  }
  return seen;
};
const entries = Object.entries(icons).map(([k, v]) => {
  const refs = [...v.body.matchAll(/#([\w-]+)/g)].map((m) => m[1]);
  return [k, [v.body, [...deps([...v.own, ...refs].filter((r) => r in shared))].join(" ")]];
});
const parts = Object.entries(shared).map(([k, v]) => `  ${JSON.stringify(k)}: ${JSON.stringify(v)},`);
const body = `const PARTS = {\n${parts.join("\n")}\n};\n// code -> [corps, identifiants de PARTS utilisés]\nconst ICONS = {\n${entries
  .map(([k, v]) => `  ${JSON.stringify(k)}: ${JSON.stringify(v)},`)
  .join("\n")}\n};`;
const js = fs.readFileSync(target, "utf8");
const out = js.replace(/\/\/ <icons>[\s\S]*\/\/ <\/icons>/, `// <icons>\n${body}\n// </icons>`);
fs.writeFileSync(target, out);
console.log(`${entries.length} icônes, ${out.length} octets`);

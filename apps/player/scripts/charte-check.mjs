#!/usr/bin/env node
/** Charte : CSS du player = tokens --vds-* uniquement (pas de couleur hexadécimale, ombre ni police en dur). */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const src = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src");
const rules = [
  [/#[0-9a-fA-F]{3,8}\b/, "couleur hexadécimale en dur"],
  [/\b(rgb|rgba|hsl|hsla|oklch|hwb)\(/, "couleur fonctionnelle en dur"],
  [/box-shadow\s*:(?!\s*none)/, "ombre portée interdite"],
  [/drop-shadow\(/, "ombre portée interdite"],
  [/font-family\s*:(?!\s*var\(--vds-font-[a-z]+,[^;]*\)\s*;)/, "police hors tokens"],
];

const errors = [];
for (const e of fs.readdirSync(src)) {
  if (!e.endsWith(".css")) continue;
  const text = fs.readFileSync(path.join(src, e), "utf8").replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "));
  text.split("\n").forEach((line, i) => {
    for (const [re, msg] of rules) if (re.test(line)) errors.push(`${e}:${i + 1}: ${msg}`);
  });
}
if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
console.log("charte-check OK");

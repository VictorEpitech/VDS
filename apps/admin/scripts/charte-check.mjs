#!/usr/bin/env node
/** Charte : CSS de l'admin = tokens --vds-* uniquement (pas de couleur, police ni rayon en dur, pas d'ombre). */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const src = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src");
const rules = [
  [/#[0-9a-fA-F]{3,8}\b/, "couleur hexadécimale en dur"],
  [/\b(rgb|rgba|hsl|hsla|oklch|hwb)\(/, "couleur fonctionnelle en dur"],
  [/box-shadow\s*:(?!\s*none)/, "ombre portée interdite"],
  [/drop-shadow\(/, "ombre portée interdite"],
  [/border-radius\s*:(?!\s*(var\(--vds-radius-[a-z]+\)|0)\s*;)/, "rayon hors tokens"],
  [/font-family\s*:(?!\s*(var\(--vds-font-[a-z]+\)|inherit)\s*;)/, "police hors tokens"],
];

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (/\.(css|tsx?)$/.test(e.name) && !/\.test\./.test(e.name)) out.push(p);
  }
  return out;
}

const errors = [];
for (const file of walk(src)) {
  const text = fs.readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "));
  text.split("\n").forEach((line, i) => {
    for (const [re, msg] of rules) if (re.test(line)) errors.push(`${path.relative(src, file)}:${i + 1}: ${msg}`);
  });
}
if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
console.log("charte-check OK");

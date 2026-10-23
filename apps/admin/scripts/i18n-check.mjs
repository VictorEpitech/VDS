#!/usr/bin/env node
/**
 * Échoue si une clé manque dans une langue, si une clé est inutilisée ou utilisée sans exister
 * (admin), ou si les i18n des modules (modules/<id>/i18n/{fr,en}.json) divergent.
 * Les clés dynamiques t(`a.b.${x}`) marquent tout le préfixe `a.b.` comme utilisé.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const admin = path.resolve(here, "..");
const root = process.env.VDS_I18N_ROOT ? path.resolve(process.env.VDS_I18N_ROOT) : admin;
const modulesDir = process.env.VDS_MODULES_DIR ?? path.resolve(admin, "../../modules");

export function flatten(obj, prefix = "") {
  return Object.entries(obj).flatMap(([k, v]) =>
    v && typeof v === "object" ? flatten(v, `${prefix}${k}.`) : [`${prefix}${k}`],
  );
}

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (/\.(ts|tsx)$/.test(e.name) && !/\.(test|spec)\./.test(e.name)) out.push(p);
  }
  return out;
}

export function collectUsage(sources) {
  const exact = new Set();
  const prefixes = new Set();
  const re = /\bt\(\s*(["'`])((?:\\.|(?!\1).)*)\1|i18nKey=(["'])(.*?)\3/g;
  for (const src of sources) {
    for (const m of src.matchAll(re)) {
      const key = m[2] ?? m[4];
      if (key.includes("${")) prefixes.add(key.slice(0, key.indexOf("${")));
      else exact.add(key);
    }
  }
  return { exact, prefixes };
}

export function check({ fr, en, sources }) {
  const errors = [];
  const kf = new Set(flatten(fr));
  const ke = new Set(flatten(en));
  for (const k of kf) if (!ke.has(k)) errors.push(`manquante en EN : ${k}`);
  for (const k of ke) if (!kf.has(k)) errors.push(`manquante en FR : ${k}`);
  const { exact, prefixes } = collectUsage(sources);
  for (const k of exact) if (!kf.has(k)) errors.push(`utilisée mais absente : ${k}`);
  for (const k of kf) {
    const used = exact.has(k) || [...prefixes].some((p) => k.startsWith(p));
    if (!used) errors.push(`inutilisée : ${k}`);
  }
  return errors;
}

export function checkModule(fr, en) {
  const kf = new Set(flatten(fr));
  const ke = new Set(flatten(en));
  return [
    ...[...kf].filter((k) => !ke.has(k)).map((k) => `manquante en EN : ${k}`),
    ...[...ke].filter((k) => !kf.has(k)).map((k) => `manquante en FR : ${k}`),
  ];
}

const readJson = (p) => JSON.parse(fs.readFileSync(p, "utf8"));

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const errors = check({
    fr: readJson(path.join(root, "src/i18n/fr.json")),
    en: readJson(path.join(root, "src/i18n/en.json")),
    sources: walk(path.join(root, "src")).map((f) => fs.readFileSync(f, "utf8")),
  }).map((e) => `admin: ${e}`);
  if (fs.existsSync(modulesDir)) {
    for (const id of fs.readdirSync(modulesDir)) {
      const d = path.join(modulesDir, id, "i18n");
      if (!fs.existsSync(path.join(d, "fr.json")) || !fs.existsSync(path.join(d, "en.json"))) continue;
      const diff = checkModule(readJson(path.join(d, "fr.json")), readJson(path.join(d, "en.json")));
      errors.push(...diff.map((e) => `${id}: ${e}`));
    }
  }
  if (errors.length) {
    console.error(errors.join("\n"));
    process.exit(1);
  }
  console.log("i18n:check OK");
}

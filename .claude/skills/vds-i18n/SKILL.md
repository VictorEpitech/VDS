---
name: vds-i18n
description: Gérer les traductions français / anglais de VDS (admin React avec i18next, modules, écrans système du player). Utiliser dès qu'on ajoute ou modifie un texte visible, un message d'erreur, un libellé de schéma de module ou une date/heure affichée.
---

# Traductions FR / EN

## Règles

- Aucune chaîne visible en dur. Admin : `t("zone.cle")` avec i18next. Modules : clés dans `modules/<id>/i18n/{fr,en}.json`.
- Clés en anglais, hiérarchiques, stables : `screens.status.scheduled_off`, `errors.pairing.code_expired`.
- Le serveur renvoie des **codes** d'erreur, jamais des phrases ; l'admin les traduit (`errors.<code>`).
- Ajouter la clé **dans les deux langues dans le même commit**. Le français est la langue de référence.
- Prévoir +30 % de longueur : aucun libellé ne doit casser la mise en page en EN ni en FR.
- Pluriels via ICU (`{count, plural, one {# écran} other {# écrans}}`).
- Dates et heures : `Intl.DateTimeFormat` avec le fuseau du **site** (Europe/Paris par défaut), format 24 h en FR.
- Utiliser le vocabulaire fixé dans `CLAUDE.md` (Écran/Screen, Mise en page/Layout, Forçage/Override, Hors service prévu/Scheduled off…).

## Vérification

`pnpm i18n:check` (à créer au lot 1) doit échouer si une clé manque dans une langue ou si une clé est inutilisée. L'agent `qa-tests` le lance à chaque fin de lot.

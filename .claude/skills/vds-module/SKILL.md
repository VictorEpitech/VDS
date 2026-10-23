---
name: vds-module
description: Créer ou modifier un module VDS (n'importe quel kind - power-driver, content-type, notifier, trigger, auth-provider, theme, storage, player-adapter) en respectant le contrat de module unique - manifeste module.json, JSON Schema de config avec x-vds-level, événements, permissions, i18n, tests.
---

# Créer un module VDS

Un module intégré et un module tiers ont **exactement le même format**. Les modules intégrés vivent dans `modules/<id>/`.

## Arborescence

```
modules/<id>/
  module.json            # manifeste (template : templates/module.json)
  config.schema.json     # JSON Schema de configuration (template : templates/config.schema.json)
  i18n/fr.json  i18n/en.json
  server/  ou  agent/    # code Python si targets contient server / site-agent
  player/                # web component si kind = content-type
  tests/
  README.md              # rôle, réglages, limites connues
```

## Étapes

1. Choisis l'`id` : `<kind-court>.<nom>` en kebab-case (`power.samsung-ws`, `content.rss`, `notify.mail`).
2. Copie et remplis les templates du dossier `templates/` de ce skill.
3. Déclare **toutes** les permissions utilisées et **tous** les événements écoutés/émis. Le noyau refuse le reste à l'exécution.
4. Config : chaque propriété a `title`, `description` (clés i18n), une valeur par défaut, et `x-vds-level` (`simple` pour ce qu'un non-technicien doit voir, sinon `advanced`). Pas de champ sans défaut sauf s'il est indispensable.
5. Implémente l'interface Python du kind via `packages/sdk-py` (classe de base `vds_sdk.<Kind>Module`). Ne jamais importer un autre module : passe par le bus (`self.bus.publish/subscribe`).
6. Ajoute les traductions FR et EN de tous les libellés du schéma et des erreurs.
7. Tests : chargement du manifeste, validation du schéma, comportement nominal, échec et délai dépassé.
8. Vérifie avec `make test` et `make lint`.

## À ne pas faire

- Pas de logique métier dans le noyau pour « aider » un module : si un besoin manque, ajoute un point d'extension avec l'agent `architecte`.
- Pas d'écran d'admin dédié : l'admin génère le formulaire depuis `config.schema.json`.
- Pas d'accès réseau, disque ou écrans non déclaré dans `permissions`.

Skills spécialisés : `vds-power-driver` (kind power-driver) et `vds-content-type` (kind content-type).

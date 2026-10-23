---
name: vds-ui-charte
description: Appliquer les thèmes graphiques de VDS - charte V-Tech (mode autonome, Material Design 3) et charte Allia (mode hébergé dans le Studio Allia) - à l'admin React, aux écrans système du player et aux gabarits. Utiliser avant de créer ou modifier un composant, un écran, un thème, des couleurs, des icônes ou des animations.
---

# Thèmes VDS : V-Tech et Allia

VDS a deux thèmes, livrés comme modules `kind: theme` :

| Module | Mode | Tokens |
| --- | --- | --- |
| `theme.vtech` | autonome | `tokens.css` (ce dossier) — source `docs/charte-graphique-v-tech.md` |
| `theme.allia` | hébergé Studio Allia | `tokens-allia.css` (ce dossier) — source `docs/integration-studio-allia.md` |

Un module de thème fournit : les tokens CSS, les polices (auto-hébergées), le logo officiel, la bibliothèque d'icônes et les règles de ton.

## Règle d'or

Les composants n'utilisent **que** les variables `--vds-*`. Jamais d'hexadécimal, de police ou de rayon en dur. Changer de thème ne doit modifier aucun composant. Vérifier chaque écran avec les deux thèmes.

## Règles communes

- JetBrains Mono **uniquement** pour les données techniques (IP, code d'appairage, version, pilote, logs, id de module).
- États toujours avec icône + libellé, jamais la couleur seule.
- Contrastes AA, navigation clavier complète.
- Mouvement 200–300 ms, pas d'animation rebondissante, respecter `prefers-reduced-motion`.

## Spécifique V-Tech

MD3 : rayons 28 / 16 px / pilule, élévation tonale sans ombre, padding 24–32 px, Material Symbols Outlined (weight 300). Clair et sombre obligatoires.

## Spécifique Allia (mode hébergé)

- Icônes **lucide** (comme le Studio).
- Ton : vouvoiement, phrases courtes, pas d'emoji, pas de « innovant », « unique », « révolutionnaire » ; dates absolues.
- Logos : uniquement les SVG officiels (`public/brand/*.svg` du Studio), jamais redessinés ni recolorés.
- Utilisable à **375 px**, cibles de 44 px minimum.
- En-tête léger : logo, « Retour au Studio » (`/espace`), utilisateur, déconnexion via le Studio.

## États d'écran

| État | Token | Icône V-Tech | Icône Allia (lucide) |
| --- | --- | --- | --- |
| `online` | `--vds-success` | `check_circle` | `CircleCheck` |
| `scheduled_off` | `--vds-secondary` | `bedtime` | `Moon` |
| `offline` | `--vds-error` | `cloud_off` | `CloudOff` |
| `power_failed` | `--vds-warning` | `power_off` | `PowerOff` |
| `overridden` | `--vds-tertiary` | `push_pin` | `Pin` |
| `pairing` | `--vds-primary` | `link` | `Link` |

## En attente de validation

Couleurs d'état dans les deux thèmes, logo V-Tech, mode sombre Allia.

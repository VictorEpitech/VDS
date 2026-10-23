# Démarrer VDS dans Claude Code

## Installation

1. Crée le dépôt vide `vds/` et copie-y tout le contenu de ce kit (y compris le dossier caché `.claude/`).
2. `git init && git add . && git commit -m "docs: kit de démarrage Claude Code"`
3. Ouvre le dossier dans Claude Code (`claude` dans le terminal, ou l'extension VS Code).
4. Vérifie avec `/agents` que les 7 agents apparaissent, et que les skills `vds-*` sont listés.

## Contenu du kit

| Élément | Rôle |
| --- | --- |
| `CLAUDE.md` | Mémoire du projet : règles, stack, arborescence, vocabulaire |
| `docs/` | Architecture, design, feuille de route, charte V-Tech |
| `.claude/agents/architecte` | Noyau, contrat de module, protocoles (Opus) |
| `.claude/agents/backend` | Serveur FastAPI |
| `.claude/agents/frontend-admin` | Admin React + charte + i18n |
| `.claude/agents/player` | Player web + Android / Tizen / Pi |
| `.claude/agents/agent-site-alimentation` | Agent de site + pilotes d'alimentation |
| `.claude/agents/qa-tests` | Tests et checklists de démo |
| `.claude/agents/revue-securite` | Revue sécurité en lecture seule (Opus) |
| `.claude/skills/vds-lot` | Démarrer / piloter / clore un lot |
| `.claude/skills/vds-module` | Créer un module (+ templates) |
| `.claude/skills/vds-power-driver` | Pilotes d'alimentation |
| `.claude/skills/vds-content-type` | Types de contenu du player |
| `.claude/skills/vds-ui-charte` | Thèmes V-Tech (`tokens.css`) et Allia (`tokens-allia.css`) |
| `.claude/skills/vds-studio-allia` | Mode hébergé dans le Studio Allia : connexion unique, rôles, Dokploy, ajouts côté Studio |
| `docs/integration-studio-allia.md` | Spécification du double mode autonome / hébergé |
| `.claude/skills/vds-i18n` | Traductions FR / EN |
| `.claude/settings.json` | Permissions (uv, pnpm, make, docker compose, git local) et fuseau Europe/Paris |

## Premier message à envoyer

> Lis CLAUDE.md et docs/, dont docs/integration-studio-allia.md. On attaque le lot 1 avec le skill vds-lot. Commence par faire valider par l'agent architecte la structure du monorepo, le contrat de module dans packages/sdk-py (y compris les kinds auth-provider et theme), la gestion de VDS_BASE_PATH et le schéma de base de données, puis propose-moi l'ordre des tâches.

## Conseils

- Démarre une nouvelle session à chaque lot : `CLAUDE.md` et `docs/` portent le contexte.
- Garde `docs/` à jour : c'est la source de vérité pour les agents.
- Pour les outils de développement recommandés à ajouter (serveurs MCP) : GitHub (issues/PR), Playwright (tests du player et de l'admin dans un vrai navigateur), PostgreSQL (lecture de la base en dev).

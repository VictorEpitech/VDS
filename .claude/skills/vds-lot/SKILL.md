---
name: vds-lot
description: Démarrer, piloter ou clore un lot de la feuille de route VDS (docs/roadmap.md). Utiliser quand l'utilisateur dit « on attaque le lot N », « où en est le lot », « on clôt le lot » ou demande la prochaine étape du projet.
---

# Piloter un lot VDS

## Démarrer un lot

1. Lis `docs/roadmap.md` (section du lot) et `CLAUDE.md`.
2. Vérifie que le lot précédent est clos (cases cochées, démo faite). Sinon, signale ce qui reste.
3. Découpe le lot en tâches de taille « une session » avec la liste de tâches, chacune avec :
   - l'agent responsable (`architecte`, `backend`, `frontend-admin`, `player`, `agent-site-alimentation`) ;
   - le critère de fin vérifiable (test, commande, comportement observable).
4. Si une tâche touche le noyau, le contrat de module ou un protocole : fais-la passer d'abord par l'agent `architecte`.
5. Propose l'ordre d'exécution à l'utilisateur, puis enchaîne.

## Pendant le lot

- Délègue chaque tâche à son agent ; lance en parallèle les tâches indépendantes.
- Après chaque tâche : `make lint` et `make test` sur la zone touchée ; coche la case dans `docs/roadmap.md`.
- Toute décision nouvelle → ligne dans `docs/roadmap.md` › Décisions.

## Clore un lot

1. Agent `qa-tests` : tests complets + checklist de démo `docs/demos/lot-N.md`.
2. Agent `revue-securite` : revue ; tout constat « bloquant » doit être corrigé.
3. Démo sur un écran réel du campus (faite par l'utilisateur, à partir de la checklist).
4. Résumé à l'utilisateur : livré, reporté, décisions prises, risques nouveaux.

## Garde-fous

- Ne pas implémenter de module futur (alertes, SSO, conditionnel, tactile, murs, stats) : seulement leurs points d'accroche.
- Lot 2 = jalon clé : un écran TCL réel joue une playlist. Prioriser tout ce qui y mène.

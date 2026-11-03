# VDS — Architecture : refonte UX lots F et G (chantier C)

Décision du 2026-11-03 (architecte, Victor absent : option recommandée la plus simple et sûre). **Sans bump vds-core, sans migration.** Référence : `docs/refonte/ux.md` §2.3, §2.5, §3.

## 1. Identification

- **Lot F — Assistants contenu et agent** (M) : import multi-fichiers avec contrôles, ajout direct à une playlist, assistant « Connecter un agent de site » avec détection.
- **Lot G — Aide contextuelle** (M) : panneau « ? », glossaire en infobulle, palette Ctrl/Cmd+K, page Aide dans « Plus ».

## 2. Décisions

1. **Les deux lots sont livrés cette nuit, F puis G.** Ils sont essentiellement côté admin, réutilisent des API existantes et ne touchent ni le contrat de module ni le player. G ne dépend pas de F.
2. **Import par URL : reporté** (backlog). Il exige un téléchargement serveur de gros fichiers depuis une URL arbitraire (surface SSRF distincte des widgets : 200 Mo, durée longue). Il réutilisera `SafeHttp` du chantier A quand celui-ci sera éprouvé.
3. **Contrôles d'import** : type MIME et poids sont **vérifiés par le serveur** (codes existants de la médiathèque ; on n'ajoute que ceux qui manquent, voir F1). Le ratio et l'orientation sont un **avertissement côté client** (dimensions lues dans le navigateur), jamais un refus.
4. **Ajout direct à une playlist** : appel des API existantes (création de playlist, ajout d'élément) depuis le résumé de l'assistant. Aucun endpoint « import + ajout » composite. Avec le chantier B, la playlist modifiée par un éditeur passe en brouillon (comportement normal, rien de spécifique).
5. **Assistant agent** : réutilise l'enrôlement existant (`architecture-enrolement.md`) et `DetectPanel` ; sondage 3 s de l'état de l'agent (pas de temps réel nouveau, conforme à `ux.md` §4.6). La commande copiable est construite à partir de l'origine publique et de `VDS_BASE_PATH` renvoyés par le serveur, jamais d'URL en dur.
6. **Palette Ctrl/Cmd+K : recherche côté client** sur les listes déjà chargées par TanStack Query (écrans, sites, groupes, médias, playlists, mises en page, programmations) + actions de navigation et de création. Pas d'index serveur en V1 (point d'accroche : `GET /search`). Les actions proposées respectent le rôle (une action interdite n'apparaît pas ; le serveur reste juge).
7. **Aide sans lien externe** : contenus d'aide en clés i18n (`help.<sujet>.title|body`), affichés dans un panneau latéral ; aucune URL de documentation en dur (aucune hypothèse d'hébergeur). Page « Aide » dans Plus : liste des sujets, glossaire, « Rouvrir Premiers pas ».
8. **Ton par thème** : vouvoiement et phrases courtes sous `theme.allia`, neutre sous `theme.vtech`, via un **contexte de ton fourni par le module de thème** (clé i18n suffixée `_formal` utilisée si le thème déclare `tone: "formal"`), jamais par un test sur le mode. Si le module de thème ne déclare pas `tone` aujourd'hui, défaut neutre et ajout du champ dans le manifeste de `theme.allia` (propriété de module, pas de contrat noyau).

## 3. Interfaces (admin, TypeScript)

```ts
// features/help
export type HelpTopic = "screens" | "content" | "playlists" | "layouts" | "schedule"
  | "override" | "power" | "agents" | "publication" | "inheritance";
export function HelpButton(props: { topic: HelpTopic }): JSX.Element;   // icône « ? », ouvre HelpPanel
export function HelpPanel(props: { topic: HelpTopic | null; onClose(): void }): JSX.Element;
export function Term(props: { k: GlossaryKey; children: ReactNode }): JSX.Element; // infobulle focus + toucher
export type GlossaryKey = "override" | "group" | "inheritance" | "schedule" | "layout"
  | "scheduled_off" | "power_driver" | "site_agent" | "draft" | "in_review";

// features/palette
export interface PaletteEntry {
  id: string; kind: "navigate" | "create" | "action";
  labelKey: string; labelParams?: Record<string, string>;
  keywords: string[]; icon: IconName;
  requires?: "admin";
  run(nav: NavigateFunction): void;
}
export function useCommandPalette(): { open(): void; close(): void; isOpen: boolean };
export function registerPaletteSource(src: () => PaletteEntry[]): () => void; // par fonctionnalité

// features/media/import
export interface ImportFileState {
  file: File; status: "pending" | "uploading" | "done" | "error";
  progress: number;                       // 0..1
  errorCode?: string;                     // code serveur traduit (media.*)
  warnings: ("ratio_mismatch" | "low_resolution")[];
  mediaId?: string;
}
export function ImportWizard(props: { targetOrientation?: "landscape" | "portrait" }): JSX.Element;

// features/agents
export function ConnectAgentWizard(props: { siteId: string }): JSX.Element;
```

Composants communs : réutiliser `provisioning/Wizard` et `StepList` (format d'assistant ≤ 4 étapes, fil d'étapes, Retour, résumé, `aria-live`).

## 4. Tâches

### Lot F (en premier)

| # | Tâche | Agent | Critère de fin testable |
| --- | --- | --- | --- |
| F1 | Vérifier les erreurs typées de l'envoi média (type refusé, trop lourd avec `limit`/`size`) ; ajouter seulement les codes manquants, sans changer les existants | builder | Tests API : fichier non accepté → code typé ; fichier trop lourd → code + `limit` et `size` ; aucun test existant modifié |
| F2 | `ImportWizard` : glisser-déposer et sélecteur multi-fichiers, file d'envoi (2 en parallèle), progression par fichier, erreurs traduites avec une action (« Retirer », « Réessayer ») | builder | vitest : 3 fichiers dont 1 refusé → 2 `done`, 1 `error` avec message clair ; navigation clavier complète |
| F3 | Avertissements ratio/orientation et basse résolution (image : `createImageBitmap` ; vidéo : métadonnées `<video>`) | builder | vitest : image 1080×1920 avec cible paysage → `ratio_mismatch` ; aucun blocage de l'envoi |
| F4 | Fin d'assistant « Ajouter à une playlist » (existante ou nouvelle), un clic, via API existantes | builder | vitest : choix d'une playlist → éléments ajoutés dans l'ordre d'envoi ; nouvelle playlist créée avec un nom par défaut |
| F5 | `ConnectAgentWizard` : commande copiable (bloc mono, bouton Copier), attente du premier battement (sondage 3 s, « Agent connecté il y a 3 s »), étape détection (`DetectPanel`, « Ajouter » en un clic), erreur 2 min sans connexion avec message actionnable | builder | vitest avec API simulée : battement reçu → étape suivante ; 2 min sans battement → message « port sortant 443 » ; la commande contient la base `VDS_BASE_PATH` |
| F6 | Points d'entrée : Contenus (« Ajouter un contenu »), Réglages › Sites (« Connecter un agent »), étapes Premiers pas 2 et 4 | builder | Liens présents ; étape Premiers pas ouvre l'assistant |
| F7 | i18n FR/EN de F, état vide et erreurs | grunt | Lint i18n : aucune clé manquante, aucune chaîne en dur |
| F8 | e2e F | qa-tests | Playwright : import 3 fichiers + ajout à une playlist ; assistant agent avec agent simulé ; à 375 px et au clavier ; `VDS_BASE_PATH=/` et `/espace/affichage` |

### Lot G (ensuite)

| # | Tâche | Agent | Critère de fin testable |
| --- | --- | --- | --- |
| G1 | `HelpButton` / `HelpPanel` + contenus des 10 sujets (3 phrases chacun), posés à côté des titres de page | builder | vitest : ouverture/fermeture au clavier (Échap), focus rendu au bouton, `aria-expanded` |
| G2 | `Term` (glossaire) : infobulle au survol, au focus et au toucher, `aria-describedby` ; utilisé sur forçage, groupe, héritage dans les écrans concernés | builder | vitest : accessible au clavier et au toucher ; axe sans violation |
| G3 | Palette Ctrl/Cmd+K : `registerPaletteSource` par fonctionnalité, recherche insensible aux accents, actions filtrées par rôle, icône de recherche sur mobile | builder | vitest : « ecran » trouve « Écran Accueil » ; un éditeur ne voit pas les actions admin ; Entrée exécute, Échap ferme |
| G4 | Page Aide (Plus) : sujets, glossaire, « Rouvrir Premiers pas » ; route sous `VDS_BASE_PATH` | builder | Route accessible, Premiers pas réaffiché après clic |
| G5 | Ton par thème : champ `tone` dans le manifeste de `theme.allia`, variantes `_formal` des textes d'aide | grunt | Avec `VDS_THEME=allia` les textes d'aide vouvoient ; avec `vtech` neutres ; aucun test sur le mode dans le code |
| G6 | i18n FR/EN de G (+30 % de longueur vérifié à 375 px) | grunt | Lint i18n vert ; captures 375 px sans débordement |
| G7 | e2e et accessibilité G | qa-tests | Playwright + axe : aucune violation AA sur Aide, palette, panneau ; deux `VDS_BASE_PATH` |

Avant clôture F+G : agent `reviewer` sur le diff (pas de revue sécurité spécifique : aucune nouvelle surface serveur hors F1).

## 5. Risques

- G5 : si les modules de thème n'exposent aucune métadonnée à l'admin aujourd'hui, prévoir un petit ajout dans le chargement du thème (lecture du manifeste) ; c'est une propriété de module, pas un `if studio`.
- Palette côté client : ne trouve que ce qui est déjà chargé ; acceptable en V1, index serveur en point d'accroche.
- Contenus d'aide : qualité rédactionnelle à relire par Victor.

## 6. Ligne à ajouter dans `docs/roadmap.md` › Décisions

- 2026-11-03 — Refonte UX lots F et G livrés ensemble (F puis G), sans bump vds-core ni migration : import multi-fichiers (MIME et poids vérifiés serveur, ratio = avertissement client), ajout direct à une playlist par API existantes, assistant agent sur l'enrôlement existant et `DetectPanel` (sondage 3 s) ; aide en clés i18n sans lien externe, glossaire `Term`, palette Ctrl/Cmd+K côté client filtrée par rôle ; ton via `tone` du module de thème ; import par URL reporté (réutilisera `SafeHttp` du chantier A). Voir `architecture-ux-fg.md`.

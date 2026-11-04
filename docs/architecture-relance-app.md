# VDS — Architecture : relancer l'application VDS sur une TV (vds-core 1.14.0)

Décision du 2026-11-04 (architecte). Demande de Victor : « sur les télés, quand c'est techniquement possible, un bouton (ainsi qu'une route API) pour relancer l'application VDS dessus ». S'appuie sur `architecture-commandes.md` (lot I, 1.11.0) et `architecture-apk-update.md` (commande d'agent `apk.update`, 1.6.0). **vds-core 1.14.0, additif** (après 1.12.0 widgets et 1.13.0 validation). Migration : une colonne.

## 0. Constat

- La commande player `restart {scope: "app"}` existe déjà (lot I) : Android (activité relancée en lock task, `CmdDispatcher`), Pi (Chromium relancé par systemd via l'assistant local). Web / PWA / Tizen n'annoncent pas `cmd.restart.app` (seul `reload`).
- Limite : ce chemin passe **par le player**. Si l'application est figée, plantée ou fermée (cas réel visé par la demande), il ne reçoit rien et la commande expire.
- L'agent de site sait déjà joindre une TV Android par ADB (`power.adb` : `power.set`, `provision.android`, `update.android`, étape `launch`).

## 1. Décisions

1. **Pas de nouvelle commande player.** La commande publique reste `restart` avec `scope: "app"`. On ajoute un **second exécutant** : l'agent de site, par une nouvelle commande d'agent fermée `app.restart` (même famille que `apk.update` / `power.set`). Une seule action dans l'UI, une seule route, une seule ligne d'historique.
   - Option écartée : commande séparée `restart_app`. Coût : doublon sémantique avec `restart{scope:"app"}`, deuxième jeu de droits, de libellés et de tests, sans gain.
2. **Choix de l'exécutant par le serveur (`via: "auto"`, défaut)** :
   - player en ligne (WS connecté ou heartbeat < 90 s) **et** capacité `cmd.restart.app` ⇒ chemin player (lot I, inchangé) ;
   - sinon, relanceur d'agent disponible (§2) ⇒ commande d'agent `app.restart` ;
   - sinon 422 `restart_app_unavailable` (le bouton n'est de toute façon pas affiché).
   - `via: "agent"` explicite (menu Avancé) pour le cas « player en ligne mais affichage bloqué » ; `via: "player"` explicite pour les tests et l'API.
3. **Exécutants par plateforme** :

| Plateforme | Chemin player (lot I) | Chemin agent (nouveau) | V1 |
| --- | --- | --- | --- |
| Android / Google TV (`player.android`) | `restart-app` du pont `vdsCmd` | `power.adb` capacité `app.restart.android` : `am force-stop app.vds.player` puis `am start -W -n app.vds.player/.MainActivity` (constantes, comme l'étape `launch`) | oui |
| Pi (Chromium kiosque) | assistant local → systemd | aucun : l'agent n'a pas et n'aura pas d'accès SSH au Pi ; les plantages sont couverts par `Restart=always` de `vds-kiosk.service` | chemin player seul |
| Tizen grand public (`power.samsung-ws`) | aucun (`reload` seulement) | **point d'accroche** : capacité réservée `app.restart.tizen` (fermer puis lancer l'app VDS par l'API de la TV, `appId` dans la config du module). Non implémenté tant que non validé sur la TV 2023 réelle (Q1) | non |
| Web / PWA | aucun (`Recharger` suffit et reste distinct) | aucun | non |

4. **Disponibilité calculée par le serveur et exposée par écran**, l'admin **masque** le bouton si `available = false` (règle du lot I : masquer, pas griser).
5. **Droits : admin seul** (comme `restart` au lot I). Les modules ne peuvent toujours pas émettre `restart` (`ScreensApi.send_command` inchangé) ; `app.restart` n'est émise que par le noyau.
6. **Règle 6** : aucune information au public. Côté Android, entre `force-stop` et `start`, le lanceur de la TV peut apparaître ~1 s : risque accepté (même coupure que l'étape `launch` de la mise à jour APK). Device Owner : `MainActivity` réapplique le lock task au démarrage (logique existante). Kiosque doux : activité d'accueil = VDS. Aucun message d'échec, aucune trace à l'écran.

## 2. Contrat vds-core 1.14.0 (additif)

### SDK Python `vds_sdk.agent` (ajout, miroir de `ScreenUpdater`)

```python
class AppRestartRequest(BaseModel):
    request_id: UUID          # = player_command.id
    screen_id: UUID
    platform: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")   # "android" en V1

class AppRestartOutcome(BaseModel):
    ok: bool
    code: str | None = Field(default=None, pattern=r"^[a-z0-9_.]{1,96}$")
    # codes : app.restart.{adb_unreachable, adb_unauthorized, not_installed,
    #         launch_failed, busy, unsupported}

@runtime_checkable
class AppRestarter(Protocol):
    platforms: ClassVar[frozenset[str]]      # capacité manifeste "app.restart.<platform>"
    async def restart_app(self, req: AppRestartRequest) -> AppRestartOutcome: ...

def check_app_restarter(instance: object, capabilities: list[str]) -> None: ...  # miroir de check_updater
```

Le module résout `host`/`port` dans sa propre config résolue de l'écran (comme `power.set` / `apk.update`) ; la requête n'en contient pas. Connexion ADB **sans attente d'invite RSA** (non autorisé ⇒ `adb_unauthorized` immédiat).

### Fil serveur ↔ agent

- Commande d'agent `app.restart`, args = `AppRestartRequest`, résultat = `AppRestartOutcome`. Délai : 30 s.
- Agent : routage vers le module déclarant `app.restart.<platform>` ; **verrou d'hôte partagé** avec `power.set` et `apk.update` (refus `busy` si une mise à jour APK tourne) ; au plus 1 relance / 30 s par appareil (miroir de `MIN_RESTART_GAP_MS`).
- Agent < 1.14 ⇒ `unknown_command` ; le serveur ne déclare le chemin agent disponible que si `hello.core >= 1.14` et `power.adb >= 0.4.0` actif avec `app.restart.android`.

### Arguments de la commande player `restart` (wire + API)

```python
class RestartArgs(BaseModel):
    scope: Literal["app", "device"]
    via: Literal["auto", "player", "agent"] = "auto"   # nouveau ; "agent" interdit avec scope="device"
```

Le player 1.11 ignore `via` (il ne reçoit la commande que si l'exécutant est `player`).

### Disponibilité exposée (`ScreenOut`, ajout)

```python
class RestartAppAvailability(BaseModel):
    available: bool
    via: list[Literal["player", "agent"]]      # exécutants possibles maintenant, ordre de préférence
    reason: Literal["no_capability", "player_offline", "agent_offline", "agent_outdated",
                    "module_disabled", "no_adb_host"] | None   # si available = False

# ScreenOut.restart_app: RestartAppAvailability
```

```ts
// packages/sdk-ts (généré par make api-types)
export interface RestartAppAvailability {
  available: boolean;
  via: ("player" | "agent")[];
  reason: "no_capability" | "player_offline" | "agent_offline" | "agent_outdated"
        | "module_disabled" | "no_adb_host" | null;
}
```

### Événements du bus

Aucun nouvel événement : `player.command_requested@1` / `player.command_completed@1` (lot I §7) couvrent les deux chemins (`name = "restart"`). L'exécutant est visible dans l'API et l'audit, pas dans le payload v1 (inchangé).

### Note de compatibilité 1.14.0

Additif. Modules 1.x inchangés. Player 1.11 inchangé. Agent < 1.14 : chemin player seul (`agent_outdated` si le player est hors ligne). `power.adb` 0.3.0 → 0.4.0 (`core: ">=1.14 <2.0"`, capacité `app.restart.android` ajoutée).

## 3. Serveur (noyau)

- Migration additive : `player_command.executor varchar(8) NOT NULL DEFAULT 'player'` (`'player' | 'agent'`). `PlayerCommandOut` gagne `executor`.
- Route : **la route existante des commandes d'écran** (lot I §4), `POST <base>/api/v1/screens/{screen_id}/commands` avec `{name: "restart", args: {scope: "app", via?}, request_key}`. Rôle admin (403 `cmd.role_required`), cookie + contrôle de même origine, idempotence par `request_key` (inchangée). Batch : barre de sélection existante (file 8 s annulable).
- Chemin agent : la ligne `player_command` est créée (`executor='agent'`, TTL 2 min : pas de relance surprise tardive), la commande d'agent `app.restart` part avec `request_id = player_command.id` ; le résultat de l'agent écrit `status ok|failed` + `code`. Agent hors ligne au moment de l'envoi ⇒ pas de mise en file : 409 `agent_offline`.
- Chemin player : inchangé (`restart_not_observed` après 2 min sans nouveau `boot_id`).
- Audit : entrée de commande existante (lot I §7) avec `executor`. Jamais de sortie shell.
- `ScreenOut.restart_app` calculé (sans requête de plus par écran : agents et modules en cache).
- Toutes les URL sous `VDS_BASE_PATH` ; aucune branche de mode.

## 4. Admin

- Menu Actions de la carte et de la fiche (admin) : « Relancer l'application » visible si `restart_app.available`. Avancé : « Relancer via l'agent de site » visible si `"agent" in via` et le player est en ligne.
- Historique : « Exécutée par l'agent de site » / « par le player ». Codes `app.restart.*` traduits FR/EN.
- Barre groupée : écrans indisponibles exclus et comptés avec leur raison.

## 5. Tests

- Serveur : choix `auto` (player en ligne ⇒ player ; hors ligne + agent ⇒ agent ; rien ⇒ 422), `via` explicite, `agent` + `device` ⇒ 422, 403 non-admin, 403 cross-origin, idempotence, agent hors ligne ⇒ 409, résultat agent ⇒ statut, `restart_app` par raison, sous `/` et `/espace/affichage`.
- `power.adb` (faux ADB) : séquence de constantes exacte, non autorisé ⇒ échec immédiat, verrou partagé avec `apk.update`, anti-rafale 30 s, app absente ⇒ `not_installed`.
- Admin e2e : bouton masqué selon rôle et disponibilité, entrée Avancé, FR/EN, 375 px.
- Démo TCL réelle : app figée ou fermée (`adb shell am force-stop` à la main) ⇒ relance par l'agent, lecture reprise, **kiosque conservé** (Device Owner et kiosque doux), rien d'affiché au public.

## 6. Découpage

| # | Tâche | Agent | Critère de fin |
| --- | --- | --- | --- |
| R0 | Contrat 1.14.0 : `AppRestarter`, `AppRestartRequest/Outcome`, `check_app_restarter`, commande d'agent `app.restart` dans `vds_sdk.wire`, `RestartArgs.via`, `CORE_API_VERSION`, note de compat, `test_compat_114` | builder | `make lint` vert, schémas wire et `wire.generated.ts` regénérés, modules existants chargés |
| R1 | Serveur : migration `executor`, résolution `auto`, envoi/résultat agent, `ScreenOut.restart_app`, `make api-types` | builder | tests §5 serveur verts aux deux `VDS_BASE_PATH` |
| R2 | Agent + `power.adb` 0.4.0 : routage `app.restart`, verrou d'hôte, anti-rafale, commandes ADB constantes | builder | tests faux ADB §5 verts, aucune sortie shell remontée |
| R3 | Admin : actions carte/fiche/Avancé, historique, i18n | builder + grunt (i18n, types) | Playwright §5 vert, `make i18n-check` vert |
| R4 | Tests transverses | qa-tests | couverture §5 complète |
| R5 | Revue | revue-securite | ADB (commandes constantes, pas d'invite RSA laissée), droits admin, rafales, rien au public |
| R6 | Recette TCL réelle, ajout §9 à `docs/demos/commandes.md` | Victor | case cochée kiosque conservé |

## 7. Questions pour Victor

- Q1 Tizen : valider sur la Samsung 2023 que l'API de la TV ferme puis relance l'app VDS (sinon le point d'accroche reste vide).
- Q2 Faut-il exposer aussi « Relancer l'application » à l'éditeur dans son périmètre ? Décision par défaut : non (admin seul, comme `restart`).

## 8. Ligne à ajouter dans `docs/roadmap.md` › Décisions

« 2026-11-04 — Relance de l'application VDS sur TV (architecte, vds-core 1.14.0 additif, une colonne) : pas de nouvelle commande, `restart{scope:"app"}` gagne `via: auto|player|agent` ; second exécutant = agent de site par la commande d'agent `app.restart` (`power.adb` 0.4.0, `am force-stop` + `am start`) quand le player est figé ou hors ligne ; Pi et web : chemin player seul ; Tizen : point d'accroche `app.restart.tizen` ; bouton admin masqué selon `ScreenOut.restart_app`. Voir `docs/architecture-relance-app.md`. »

## 9. Renvoi à ajouter dans `docs/architecture.md`

Dans la section des commandes player / agent de site : « Relance de l'application : chemin player (lot I) ou agent de site (`app.restart`, vds-core 1.14.0), voir `architecture-relance-app.md`. » Ajouter `app.restart.<platform>` à la liste des capacités de module `power-driver`.

# Mise à jour de l'APK player Android depuis l'admin (vds-core 1.6.0)

Statut : décidé le 2026-11-02. S'appuie sur `architecture-provision-android.md` (lot P, 1.3.0) et `architecture-actions-rapides.md` (lot E).

## Décisions

1. **Chemin unique : agent de site + ADB (`power.adb`), comme au provisioning.** L'agent télécharge l'APK au serveur (sortant, jeton `vds_agt_`, route existante `GET <base>/api/v1/agent/apk/android/{version_code}`), vérifie sha256/taille (`ApkSource` existant, cache par sha256 partagé entre écrans du site), puis `cmd package install -r -S <size>` en flux (code existant de l'étape `install`) et relance le player (étape `launch` existante). Aucun port entrant, aucune URL d'APK dans les trames.
   - Option écartée pour V1 : auto-mise à jour par le player (Device Owner + `PackageInstaller`). Coût : nouveau code Kotlin privilégié, invite utilisateur en kiosque doux (visible du public), deuxième chemin à maintenir. À rouvrir si le parc compte des TV sans ADB réseau.
2. **Aucun scan LAN.** L'adresse vient exclusivement de la configuration résolue `power.adb.host`/`port` de l'écran (déjà utilisée par `power.set`). L'admin n'envoie jamais d'adresse. Écran sans `host` ⇒ non éligible (`no_adb_host`).
3. **Version installée = remontée par le player.** Le pont Android ajoute `app_version_code` (`BuildConfig.VERSION_CODE`) à `info()` ; le heartbeat le transmet ; le serveur le stocke. Les APK déjà déployées ne l'envoient pas : état `unknown`, **mise à jour quand même autorisée** (l'étape `inspect` de l'agent lit `versionCode` par `pm` et saute si déjà à jour). Le résultat de l'agent écrit aussi `installed_version_code`, sans attendre le heartbeat, et le marque comme constaté (`screen_device.agent_version_code`). Le code déclaré par l'appareil est indicatif : `up_to_date` (qui bloque le POST) n'est affiché que si la version a été écrite par l'agent et n'a pas changé depuis ; un code déclaré >= disponible sans confirmation reste `unknown` et la mise à jour reste autorisée (l'étape `inspect` de l'agent arbitre).
4. **APK disponible = plus grand `version_code` de `apk.json`** (image CI ou `VDS_APK_DIR`), déjà filtré par `VDS_APK_SIGNER_SHA256` et sha256 recalculé. Ajout : relecture de `apk.json` quand son `mtime` change (contrôle au plus toutes les 60 s), mêmes vérifications ; une instance autonome dépose la nouvelle APK sans redémarrer.
5. **Pas de téléversement d'APK par l'admin** (confirme lot P). Source = CI (`android.yml` → `images.yml`, signataire épinglé) ou dossier `VDS_APK_DIR` monté en lecture seule (`docker/compose.apk.yaml`, `make-apk-manifest.sh`). Raisons : surface d'attaque (fichier exécutable poussé sur des appareils publics), envoi de 200 Mo à travers le proxy Studio, signature vérifiable proprement seulement en CI. L'UI affiche la version disponible et sa provenance, et explique comment en fournir une.
6. **Pas d'endpoint de masse.** Écran, sélection ou « tout le site » (= sélectionner les écrans éligibles du site) passent par le fan-out et la file d'actions du lot E (4 en parallèle, ≤ 200, sondage mutualisé par agent). Action **non réversible** : pas de file d'annulation 8 s, confirmation explicite (« N écrans vont redémarrer leur affichage pendant ~1 min ») comme la suppression.
7. **Commande agent générique, pas de nouvelle table.** Nouvelle commande fermée `apk.update` sur la route existante des commandes d'agent ; le serveur enrichit avec l'`ApkRef` (l'admin n'envoie ni sha ni taille). Suivi par le même `GET .../commands` que `power.set`.
8. **Bump additif vds-core 1.5.0 → 1.6.0** : nouvelle interface SDK, nouvelle capacité, nouvelle commande, nouveau champ heartbeat, nouveaux événements. Rien de retiré. `power.adb` 0.2.0 → 0.3.0 (`core: ">=1.6 <2.0"`).

## Contrat vds-core 1.6.0 (additif)

### SDK Python `vds_sdk.agent.provision` (ajout)

```python
class ApkUpdateRequest(BaseModel):
    request_id: UUID
    screen_id: UUID
    platform: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    apk: ApkRef                      # jamais d'URL

class ApkUpdateOutcome(BaseModel):
    ok: bool
    previous_version_code: int | None = None
    installed_version_code: int | None = None
    code: str | None = Field(default=None, pattern=r"^[a-z0-9_.]{1,96}$")
    # codes : update.android.{already_up_to_date (ok=True), downgrade_refused, adb_unreachable,
    #         adb_unauthorized, apk_unavailable, verify_failed, install_incompatible,
    #         install_failed, launch_failed, busy}

@runtime_checkable
class ScreenUpdater(Protocol):
    platforms: ClassVar[frozenset[str]]   # capacité manifeste "update.<platform>"
    async def update(self, req: ApkUpdateRequest, apk: ApkSource,
                     report: Callable[[StepReport], Awaitable[None]]) -> ApkUpdateOutcome: ...

def check_updater(instance: object, capabilities: list[str]) -> None: ...  # miroir de check_provisioner
```

Le module résout `host`/`port` de l'écran dans sa propre config (comme `power.set`) ; la requête n'en contient pas. Étapes réutilisées : `connect` (sans attente d'invite RSA : non autorisé ⇒ `adb_unauthorized`, jamais d'invite laissée sur un écran public au-delà du délai de connexion), `inspect`, `download`, `verify`, `install`, `launch`. Pas de `device_owner`/`configure`/`inject` : `install -r` du même paquet conserve Device Owner, appairage et activité d'accueil.

### Fil serveur ↔ agent

- Commande `apk.update`, args = `ApkUpdateRequest` ; résultat = `ApkUpdateOutcome`. Délai de commande : 300 s.
- Agent : une seule mise à jour à la fois par appareil (verrou partagé avec `power.set` du même hôte), au plus 2 installations simultanées par site ; un seul téléchargement par sha256.
- Compatibilité : agent < 1.6 ⇒ `unknown_command` ; le serveur ne déclare un écran éligible que si `hello.core >= 1.6` et `power.adb >= 0.3.0` actif avec `update.android`.

### Heartbeat player (additif)

`app_version_code: int | None` (`ge=1, le=2_100_000_000`) dans `POST player/heartbeat`, la trame WS `heartbeat` et `device` de l'appairage. `sdk-ts` : `PlayerHost.info()` gagne `app_version_code?: number`.

### Événements du bus (serveur)

| Nom | Payload v1 | Émetteur |
| --- | --- | --- |
| `screen.apk_updated` | `request_id, screen_id, previous_version_code, version_code` | noyau (résultat agent ok, hors `already_up_to_date`) |
| `screen.apk_update_failed` | `request_id, screen_id, version_code, code` | noyau |

Point d'accroche pour un futur notifier ; aucun abonné en V1.

### Note de compatibilité 1.6.0

Additif. Modules 1.x inchangés. Un agent 1.5 reste fonctionnel (écrans affichés `agent_outdated`). Une APK sans `app_version_code` reste `unknown` et reste mise à jour.

## Serveur (noyau)

- Migrations : `screen_device.app_version_code integer NULL` ; `screen_device.agent_version_code integer NULL` (constaté par l'agent).
- Idempotence : `POST` sérialisé par écran puis par `request_id` (verrous advisory), donc deux POST simultanés de même `request_id` ne créent qu'une commande.
- `GET <base>/api/v1/fleet/apk?site_id=<uuid?>` (rôle **admin**) → `{available: {version_code, version_name, source: "image"|"dir"} | null, screens: list[ScreenApkOut]}`

```python
class ScreenApkOut(BaseModel):
    screen_id: UUID
    installed_version_code: int | None
    installed_version_name: str | None
    state: Literal["up_to_date", "outdated", "unknown", "not_android"]
    updatable: bool
    reason: Literal["agent_offline", "agent_outdated", "module_disabled", "no_adb_host",
                    "apk_unavailable", "not_android", "up_to_date", "update_running"] | None
```

- `POST <base>/api/v1/sites/{site_id}/agents/{agent_id}/commands` accepte `{command: "apk.update", screen_id, request_id}` (rôle **admin**, cookie + contrôle de même origine, idempotent par `request_id`). Refus : 409 `update_running`, 409 `up_to_date` si version connue ≥ disponible, 422 raisons d'éligibilité, 503 `apk_unavailable`.
- Audit : `screen.apk_update.requested` (actor, screen, site, from/to version_code) · `screen.apk_update.finished` (status, code). Jamais de sortie shell.
- Toutes les URL sous `VDS_BASE_PATH` ; aucune branche de mode.

## Agent de site

Routage de `apk.update` vers le module déclarant `update.<platform>` ; `ApkSource` existant ; verrous décrits plus haut ; résultat écrit `installed_version_code`.

## Admin

- Carte écran : version installée (police mono), pastille « Mise à jour disponible » si `outdated`.
- Action « Mettre à jour le player » : carte (unitaire), barre de sélection, et filtre « à mettre à jour » + « tout sélectionner » pour un site. Écrans non éligibles exclus et comptés avec leur raison (`reasonOf`).
- Confirmation explicite, puis file d'actions lot E (sans délai d'annulation), résultat par écran, « Réessayer les échecs ».
- Masqué pour les rôles non admin. Libellés i18n FR/EN avec pluriels. Bloc d'aide si `available = null` (comment fournir une APK : CI ou `VDS_APK_DIR`).

## Player / Android

- `VdsBridge.info()` : `.put("app_version_code", BuildConfig.VERSION_CODE)`.
- Player : relaie `app_version_code` dans le heartbeat si l'hôte le fournit.
- `versionCode` reste `github.run_number` (monotone).

## Critères de test

- Serveur : éligibilité (chaque `reason`), enrichissement `ApkRef` sans URL, 403 non-admin, 403 cross-origin, 409 doublon, idempotence `request_id`, relecture `apk.json` (signataire faux ⇒ rejet), heartbeat borné, sous `/` et `/espace/affichage`.
- Module `power.adb` (faux ADB) : déjà à jour ⇒ ok sans install ; rétrogradation refusée ; `install_incompatible` sans désinstallation ; non autorisé ⇒ échec rapide ; deux écrans ⇒ un seul téléchargement ; commandes shell constantes.
- Player : heartbeat porte `app_version_code`. Admin e2e : pastille, sélection site, confirmation, résultats, FR/EN, deux base paths.
- Démo TCL réelle : Device Owner, kiosque doux et appairage conservés après mise à jour ; écran revient seul.

## Risques

- Coupure d'affichage ~30-90 s pendant l'installation : à faire hors heures d'ouverture (programmation différée = backlog).
- ADB réseau désactivé ou clé révoquée après une mise à jour firmware : échec `adb_unreachable`/`adb_unauthorized`, rejouer l'assistant.
- Changement de clé de signature : Android refuse ; jamais de désinstallation automatique (perdrait Device Owner).
- Bande passante : 200 Mo par site (cache partagé), installations plafonnées à 2.

## Risque connu : signataire

`VDS_APK_SIGNER_SHA256` n'est comparé qu'au `signer_sha256` **déclaré** dans `apk.json` (généré par la CI), pas au certificat réel de l'APK ; l'extraction du certificat est hors périmètre. Un avertissement est journalisé au démarrage si un catalogue existe sans cette variable.

## Hors périmètre (points d'accroche)

Mise à jour programmée ou automatique (le bus porte `screen.apk_updated`), téléversement d'APK par l'UI, canaux (stable/bêta), rétrogradation, Tizen/Pi (capacité `update.<platform>` prévue), auto-mise à jour par le player, mise à jour de l'agent de site lui-même.

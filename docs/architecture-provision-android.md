# Assistant « Ajouter une TV Android » (lot P, vds-core 1.3.0)

Statut : proposé par l'architecte le 26 octobre 2026, à fusionner dans `docs/architecture.md` › « Provisioning d'écran par l'agent ».
Réutilise : `modules/power.adb` (client ADB TCP, clé RSA de l'agent), `docs/architecture-lot3.md` (`remote_command`, trames fiables, commandes fermées), `packaging/android/README.md` (Device Owner, kiosque doux, `ProvisionActivity`), `docs/demos/kiosque-android-tv.md`.

## Décisions

1. **Le noyau reste agnostique de la plateforme.** Il connaît une *session de provisioning d'écran* générique (écran, site, agent, étapes, ticket d'appairage). Tout ce qui est Android (ADB, `pm`, `dpm`, kiosque doux, tutoriel) vit dans le module `power.adb` (0.2.0), qui déclare la capacité `provision.android`.
2. **Pourquoi dans `power.adb` et pas un module `provision.android` séparé** : l'empreinte RSA acceptée sur la TV pendant le provisioning doit être celle que `power.adb` utilise ensuite pour l'alimentation (sinon seconde invite RSA, et les modules ne partagent pas leur `StateStore`). Une seule clé, un seul module. Coût : le module mélange deux capacités ; acceptable, la clé est le vrai couplage.
3. **Bump additif vds-core 1.2.0 → 1.3.0** (nécessaire : nouvelle interface SDK, nouvelle commande fermée, nouvel événement, nouvelle permission). Rien n'est retiré ni modifié.
4. **Commande fermée générique `screen.provision`** avec `platform: "android"` (pas `android.provision`) : l'agent la route vers le module qui déclare `provision.<platform>`. Tizen/Pi futurs = nouvelle capacité, aucun changement de contrat.
5. **APK servie uniquement par le serveur, jamais d'URL** : la commande porte `version_code` + `sha256` + `size` ; l'agent construit lui-même le chemin à partir de son `http_base`. Le serveur ne lit qu'un fichier listé dans son manifeste APK.
6. **Appairage par ticket injecté**, pas par lecture du code affiché : le serveur émet un ticket à usage unique lié à l'écran, l'agent le passe à `ProvisionActivity` ; le player l'échange contre son jeton d'appareil. Le code `ABC-234` classique reste le repli manuel.
7. **Validation automatique** : la session passe `done` quand l'écran du ticket a été appairé **et** a envoyé son premier heartbeat (statut « En ligne »).
8. **Double mode** : aucune branche. L'URL injectée dans la TV = `VDS_PUBLIC_ORIGIN` + `VDS_BASE_PATH` (déjà en config) ; toutes les routes sont sous `<base>`.

## Contrat vds-core 1.3.0 (additif)

### SDK Python `vds_sdk.agent.provision`

```python
ProvisionStepId = Literal[
    "connect", "authorize", "inspect", "download", "verify", "install",
    "device_owner", "soft_kiosk", "configure", "inject", "launch",
]
StepStatus = Literal["pending", "running", "waiting_user", "ok", "skipped", "failed"]

class ApkRef(BaseModel):            # fourni par le serveur, jamais une URL
    package: Literal["app.vds.player"]
    version_code: int = Field(ge=1)
    version_name: str = Field(max_length=32)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=1, le=200 * 1024 * 1024)

class ProvisionRequest(BaseModel):
    provision_id: UUID
    screen_id: UUID
    platform: str                    # "android"
    address: IPv4Address | IPv6Address   # privée/lien local uniquement (validé noyau + module)
    port: int = Field(default=5555, ge=1, le=65535)
    apk: ApkRef
    server_url: AnyHttpUrl           # https obligatoire sauf build lanHttp (refus côté app sinon)
    pairing_ticket: SecretStr        # jamais journalisé
    device_owner: Literal["try", "skip"] = "try"
    soft_kiosk_fallback: bool = True
    from_step: ProvisionStepId | None = None   # reprise après correction (ex. comptes retirés)

class StepReport(BaseModel):
    step: ProvisionStepId
    status: StepStatus
    code: str | None = None          # "provision.android.<raison>", clé i18n du module
    detail: dict[str, str | int | bool] = {}   # aucune donnée sensible (pas de ticket, pas de sortie shell brute)

class ProvisionOutcome(BaseModel):
    ok: bool
    kiosk: Literal["device_owner", "soft", "none"]
    installed_version_code: int | None
    code: str | None = None

class ApkSource(Protocol):           # fourni par le runtime, pas par le module
    async def fetch(self, apk: ApkRef) -> Path: ...   # télécharge + vérifie sha256/taille, cache par sha256

@runtime_checkable
class ScreenProvisioner(Protocol):
    platforms: ClassVar[frozenset[str]]   # {"android"} ; capacité manifeste "provision.android"
    async def provision(
        self,
        req: ProvisionRequest,
        apk: ApkSource,
        report: Callable[[StepReport], Awaitable[None]],
    ) -> ProvisionOutcome: ...
```

### Manifeste

- Capacité `provision.<platform>` (préfixe réservé). Un module qui la déclare doit implémenter `ScreenProvisioner` (vérifié au chargement).
- Permission nouvelle `screens.provision` (installer une app et injecter un ticket sur un appareil du LAN). Affichée à l'activation du module.
- Tutoriel : convention i18n du module `provision.tutorial.<n>.title|body` (n = 1..N, ordre), pas de champ manifeste nouveau. L'admin affiche les clés présentes.

### Fil serveur ↔ agent (`vds_sdk.wire`)

- Commande `screen.provision` (serveur → agent), args = `ProvisionRequest` sans `pairing_ticket` en clair dans `remote_command.args` : le ticket est stocké chiffré (Fernet, clé noyau existante) et déchiffré à l'envoi ; effacé de la ligne à la fin de la session.
- `expires_at` = création + 5 min (contrôlé avant démarrage uniquement) ; durée max d'exécution 10 min côté agent (`provision.timeout`).
- Une seule session active par adresse IP et par agent (verrou agent) ; une seule session active par écran (index partiel serveur).
- Événement fiable agent → serveur `screen.provision_progressed` (payload v1 : `provision_id`, `screen_id`, `StepReport`).
- Résultat final : `cmd.result{status, code, data: ProvisionOutcome}`.
- Compatibilité : agent < 1.3 rejette `unknown_command` ; le serveur ne propose l'assistant que si `hello.core >= 1.3`, agent en ligne et `power.adb >= 0.2.0` actif avec la capacité `provision.android`. Sinon l'admin affiche « Mettez à jour l'agent de site » (et le parcours manuel).

### Événements du bus (serveur)

| Nom | Payload v1 | Émetteur |
| --- | --- | --- |
| `screen.provision_started` | `provision_id, screen_id, site_id, platform, actor_id` | noyau |
| `screen.provision_progressed` | `provision_id, screen_id, step, status, code` | agent (relayé, `origin=agent:<site>`) |
| `screen.provisioned` | `provision_id, screen_id, kiosk, version_code` | noyau (écran en ligne) |
| `screen.provision_failed` | `provision_id, screen_id, step, code` | noyau |

### Note de compatibilité 1.3.0 (livrée en P1)

- Additif : modules 1.0 à 1.2 chargent inchangés ; `CORE_EVENTS_1_3`, `CORE_EVENT_PAYLOAD_1_3`, `AGENT_COMMANDS_1_2` conservés.
- Manifeste : `provision.<platform>` n'est admis que pour `kind: power-driver`, `core` borné à `>=1.3`, permission `screens.provision` et cible `site-agent` ; l'agent vérifie `ScreenProvisioner` au chargement (`check_provisioner`).
- Fil : `screen_id` est porté par `Cmd.screen_id` (absent des `args`) ; `ScreenProvisionArgs` valide adresse LAN et ticket base64url ; un agent 1.2 répond `unknown_command`.

## Étapes Android (module `power.adb`)

Toutes les commandes shell sont des constantes du module ; aucune chaîne venue du serveur n'est concaténée hors arguments validés (`address`, `port`, entier `version_code`, URL et ticket passés en `--es` après échappement shell strict ; ticket = base64url uniquement).

| Étape | Action | Erreurs (`provision.android.*`) |
| --- | --- | --- |
| `connect` | TCP `address:port` | `unreachable` (débogage réseau inactif / IP fausse), `tls_required` (TV en débogage sans fil TLS : activer « Débogage réseau » classique) |
| `authorize` | CNXN/AUTH avec la clé de `power.adb` ; `waiting_user` pendant ≤ 90 s | `unauthorized` (invite refusée ou expirée : « Toujours autoriser ») |
| `inspect` | `getprop` (modèle, SDK), `dumpsys account`, `pm list users`, `pm list packages --show-versioncode app.vds.player`, `dpm list-owners` | `sdk_too_old` (< 24) |
| `download` | `ApkSource.fetch` : `GET <http_base>/api/v1/agent/apk/android/{version_code}`, jeton agent | `apk_unavailable` |
| `verify` | sha256 + taille = `ApkRef` | `apk_mismatch` (fichier supprimé du cache) |
| `install` | `exec:cmd package install -r -S <size>` en flux (extension de `adb.py` : `exec_stream`) ; ignoré si même `version_code` déjà installé | `install_incompatible` (signature différente : désinstaller), `install_failed` |
| `device_owner` | si `try` et aucun compte et un seul utilisateur : `dpm set-device-owner app.vds.player/.AdminReceiver` ; si déjà owner : ok | `accounts_present` (`detail.accounts`, sans noms), `extra_users`, `owner_exists`, `dpm_refused` → `skipped` |
| `soft_kiosk` | si pas de Device Owner et `soft_kiosk_fallback` : `set-home-activity`, `appops SYSTEM_ALERT_WINDOW allow` | `soft_kiosk_failed` (non bloquant) |
| `configure` | `settings put secure attentive_timeout -1` | non bloquant |
| `inject` | `am start -n app.vds.player/.ProvisionActivity --es server_url <url> --es pairing_ticket <t>` | `inject_failed` |
| `launch` | `am start -n app.vds.player/.MainActivity` | `launch_failed` |

Reprise : `from_step` (ex. après retrait des comptes, relancer à `device_owner`). Les étapes déjà `ok` d'une session ne sont pas rejouées sauf `inspect`.

## Serveur (noyau)

### Migration `0005_screen_provision` (numéro à ajuster)

```sql
CREATE TABLE screen_provision (
  id uuid PK, screen_id uuid FK screen ON DELETE CASCADE, site_id uuid FK site, agent_id uuid FK site_agent,
  platform text NOT NULL, address inet NOT NULL, port int NOT NULL DEFAULT 5555,
  status text NOT NULL CHECK (status IN ('queued','running','waiting_user','waiting_online','done','failed','cancelled')),
  steps jsonb NOT NULL DEFAULT '{}',          -- {step: {status, code, at}}
  outcome jsonb, apk_version_code int,
  ticket_hash bytea, ticket_expires_at timestamptz,   -- sha256 ; ticket en clair jamais stocké hors remote_command chiffré
  cmd_id uuid, created_by uuid FK user, created_at timestamptz, finished_at timestamptz);
CREATE UNIQUE INDEX ux_screen_provision_active ON screen_provision (screen_id)
  WHERE status IN ('queued','running','waiting_user','waiting_online');
```

### API (toutes sous `<base>/api/v1`, cookie + contrôle de même origine, rôle **admin** uniquement)

- `GET  /provisioning/capabilities?site_id=` → `{available: bool, reason: "agent_offline"|"agent_outdated"|"module_disabled"|null, platforms: ["android"], apk: {version_name, version_code} | null}`
- `POST /screens/{id}/provision` `{platform:"android", address, port?, device_owner?: "try"|"skip", soft_kiosk_fallback?: bool}` → 201 `ProvisionOut`. Refus : adresse non privée (422), écran déjà appairé (409, sauf `reprovision: true`), session active (409).
- `POST /provisioning/{pid}/retry` `{from_step}` · `POST /provisioning/{pid}/cancel`
- `GET  /provisioning/{pid}` → `ProvisionOut {id, screen_id, status, steps[], outcome, pairing_code_fallback?}` (l'admin interroge toutes les 2 s ; pas de WS admin en V1).
- Player : `POST /player/pair` accepte en plus `{pairing_ticket}` (additif, API REST) : ticket valide, non expiré (10 min), réclamé depuis le réseau de la session (voir Risques), non consommé → jeton d'appareil de l'écran lié, ticket consommé, `screen_provision.status = waiting_online`.
- Agent : `GET /agent/apk/android/{version_code:int}` (jeton agent `vds_agt_`) → fichier, `Content-Type: application/vnd.android.package-archive`, `ETag` = sha256.

### Hébergement de l'APK

- Construite et **signée en CI** (`.github/workflows/android.yml`, secrets GitHub existants), jamais sur le VPS, jamais de keystore dans le dépôt.
- Copiée dans l'image serveur à `/opt/vds/apk/` avec `apk.json` `[{package, version_code, version_name, sha256, size, signer_sha256, file}]` généré par la CI. `VDS_APK_DIR` (optionnel) remplace ce dossier (instance autonome qui veut une APK plus récente) : même format, lu au démarrage.
- Le serveur ne sert que les fichiers listés dans `apk.json` (lookup par `version_code` entier, `file` = nom simple sans `/`, résolu puis vérifié sous le dossier, sha256 recalculé au démarrage ; écart ⇒ APK indisponible + log). Aucun téléversement d'APK par l'admin.
- Version proposée = plus grand `version_code` du manifeste. Android refuse de lui-même une mise à jour signée par une autre clé (`install_incompatible`).

### Audit

`screen.provision.started` (actor, screen, site, adresse, platform, version_code) · `screen.provision.retried` (from_step) · `screen.provision.cancelled` · `screen.provision.finished` (status, kiosk, code). Jamais le ticket, jamais la sortie shell.

## Agent de site (runtime noyau)

- `dispatch` : `screen.provision` → module actif dont la capacité couvre `platform` et qui implémente `ScreenProvisioner` ; sinon `failed/provision.no_provider`.
- `ApkSource` noyau : téléchargement via `http_base` + jeton, écriture atomique dans `VDS_AGENT_DATA_DIR/apk/<sha256>.apk`, vérification avant `rename`, cache 2 versions max.
- Re-validation de l'adresse côté agent : privée RFC 1918 / ULA / lien local uniquement, et jamais l'adresse de l'agent lui-même.
- Aucun port entrant (connexions sortantes ADB vers la TV et HTTPS vers le serveur).

## Android (`packaging/android`)

- `ProvisionActivity` (déjà protégée par `DUMP`) accepte `pairing_ticket` (base64url ≤ 64) : stocké chiffré (EncryptedSharedPreferences), remis **une seule fois** au player web par le pont `VdsNative` (`ticket:claim`), puis effacé. Changement d'URL = purge comme aujourd'hui.
- Player web : au démarrage non appairé, tente `ticket:claim` puis `POST player/pair {pairing_ticket}` ; échec ⇒ parcours code classique. Rien de technique affiché au public.

## Admin (assistant)

Écrans : 1. Nom + site + groupe (crée l'écran) · 2. Tutoriel (clés i18n du module : options développeur, débogage réseau, **cas compte Google : retirer comptes et profils avant, réajouter après**) · 3. Adresse IP (+ « Device Owner : essayer / passer ») · 4. Exécution : liste des étapes avec statut, `waiting_user` en évidence (« Acceptez l'invite sur la TV, cochez Toujours autoriser »), erreur lisible + action (Réessayer à cette étape, Annuler, Passer en kiosque doux) · 5. Attente « En ligne » puis succès ; sans Device Owner : bandeau **kiosque doux** (limites : Accueil/Assistant non bloquables, à réserver aux écrans non publics) + lien procédure Device Owner. Repli permanent : afficher le code d'appairage manuel.
Libellés FR/EN, charte V-Tech, aucun libellé en dur.

## Risques

- TV en débogage sans fil TLS seulement (Android 11+ « Wireless debugging ») : le client ADB n'a pas STLS ⇒ `tls_required`. Mitigation : tutoriel « Débogage réseau » (5555) ; STLS = point d'accroche.
- Firmware imposant une réinitialisation pour retirer le compte principal ⇒ Device Owner impossible sans reset ; l'assistant le dit et propose le kiosque doux.
- ADB TCP en clair sur le LAN : ticket interceptable ⇒ usage unique, 10 min, lié à l'écran, invalidé à la consommation. Mitigations serveur (revue I5) : fenêtre `online_timeout` réduite à 5 min après l'appairage ; un client d'adresse privée (serveur sur le LAN du site) doit avoir l'IP de la TV de la session, sinon 401 sans consommer le ticket (désactivable : `VDS_PROVISION_LAN_CHECK=false`, utile derrière un NAT Docker). **Limite acceptée** : serveur distant, le client est vu avec l'IP publique de sortie du site, non comparable à l'adresse LAN ; un poste du même LAN usurpant la TV (ARP) reste possible. Recours : segmenter le réseau des écrans, vérifier le modèle appairé dans l'admin.
- `screen.provision` est réservée au noyau : la route générique des commandes d'agent la refuse (422 `agents.command_internal`, revue I1).
- Device Owner pose `DISALLOW_DEBUGGING_FEATURES` : après provisioning, ADB coupé ⇒ `power.adb` indisponible hors maintenance. À arbitrer : soit l'app autorise le débogage réseau en Device Owner (risque kiosque), soit l'alimentation passe par `power.cec`/natif. Défaut sûr : on garde la restriction, cascade sans ADB.

## Note d'implémentation serveur (P2)

- Prérequis de configuration : `VDS_SECRET_KEY` (clé Fernet du ticket, dérivée HKDF), `VDS_PUBLIC_ORIGIN`, APK dans `VDS_APK_DIR` (défaut `/opt/vds/apk`). Sinon `capabilities.reason` = `server_unconfigured` ou `apk_unavailable` (raisons additionnelles à la liste de l'architecture).
- Ticket : 43 caractères base64url, sha256 en base (`ticket_hash`, index unique partiel), chiffré Fernet dans `remote_command.args.pairing_ticket_enc`, déchiffré à l'envoi (`command_frame_data`), effacé à la clôture. Chiffré illisible => ticket jamais émis envoyé (le player retombe sur le code manuel).
- `POST /player/pair {pairing_ticket, device}` : sans cookie, exempté du contrôle d'origine (comme `/player/pairings`), limité à 10/min/IP, erreur unique `errors.pairing_ticket_invalid` (401) pour inconnu / expiré / rejoué / session close.
- `done` = ticket consommé (`paired_at`) puis premier heartbeat (REST ou WS) de l'écran ; le résultat `cmd.result` de l'agent ne fait qu'échouer la session ou enregistrer `outcome`. Expiration paresseuse à la lecture (`provision.ticket_expired`, `provision.online_timeout` 5 min).
- Migration additive : colonnes `code`, `paired_at`, `online_at` en plus du schéma proposé.

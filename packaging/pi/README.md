# VDS Player pour Raspberry Pi (Chromium kiosque)

Fichiers à poser par l'image Pi (aucun n'est exécuté ici) :

| Fichier | Destination |
| --- | --- |
| `vds-kiosk.service` | `/etc/systemd/system/` (Chromium `--kiosk`, `Restart=always`) |
| `vds-pi-helper.service`, `helper/vds_pi_helper.py` | `/etc/systemd/system/`, `/opt/vds/` |
| `50-vds.rules` | `/etc/polkit-1/rules.d/` (relance de `vds-kiosk.service` seulement) |
| `51-vds-reboot.rules` | `/etc/polkit-1/rules.d/`, **uniquement si `VDS_PI_ALLOW_REBOOT=1`** (sinon ne pas l'installer) |
| `player.env.example` | `/etc/vds/player.env` |

Commandes du lot I : `cmd.reload`, `cmd.identify`, `cmd.cache.resync` (player web) ; `cmd.restart.app` (relance
systemd de `vds-kiosk.service`) ; `cmd.restart.device` seulement si `VDS_PI_ALLOW_REBOOT=1`. **Pas de capture en V1.**

Sécurité : l'assistant n'écoute que `127.0.0.1:47801`, refuse toute requête dont `Origin` n'est pas
`VDS_PI_ALLOWED_ORIGIN`, exige `Content-Type: application/json` (prévalidation CORS), limite les relances
(app 1/30 s, appareil 1/5 min), ne journalise rien et ne renvoie aucune information technique. Chromium sans
`--remote-debugging-port`. La CSP du player autorise `connect-src http://127.0.0.1:47801`.

Redémarrage de l'appareil : le droit polkit `reboot` est dans un fichier séparé, installé par l'image
seulement quand `VDS_PI_ALLOW_REBOOT=1`. Par défaut aucun processus `vds` (Chromium inclus) ne peut redémarrer
le Pi, même si l'assistant est contourné.

Risque `VDS_PI_ALLOWED_ORIGIN` : l'assistant accepte toute requête portant cette origine. En mode hébergé
(`allia-innovation.fr/espace/affichage`), l'origine est celle du Studio tout entier (`https://allia-innovation.fr`) :
toute page de cette origine (autre application, contenu injecté) pourrait relancer l'app du Pi, et son
redémarrage si activé. Les relances sont limitées (app 1/30 s, appareil 1/5 min) ; garder
`VDS_PI_ALLOW_REBOOT=0` en mode hébergé sauf besoin, et ne jamais afficher de `content.url` de cette origine
non maîtrisée.

Test : `uv run pytest packaging/pi` (logique de décision) ; le reste (systemd, polkit, Chromium) n'est vérifiable que sur un Pi.

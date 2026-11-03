# VDS Player pour Raspberry Pi (Chromium kiosque)

Fichiers à poser par l'image Pi (aucun n'est exécuté ici) :

| Fichier | Destination |
| --- | --- |
| `vds-kiosk.service` | `/etc/systemd/system/` (Chromium `--kiosk`, `Restart=always`) |
| `vds-pi-helper.service`, `helper/vds_pi_helper.py` | `/etc/systemd/system/`, `/opt/vds/` |
| `50-vds.rules` | `/etc/polkit-1/rules.d/` |
| `player.env.example` | `/etc/vds/player.env` |

Commandes du lot I : `cmd.reload`, `cmd.identify`, `cmd.cache.resync` (player web) ; `cmd.restart.app` (relance
systemd de `vds-kiosk.service`) ; `cmd.restart.device` seulement si `VDS_PI_ALLOW_REBOOT=1`. **Pas de capture en V1.**

Sécurité : l'assistant n'écoute que `127.0.0.1:47801`, refuse toute requête dont `Origin` n'est pas
`VDS_PI_ALLOWED_ORIGIN`, exige `Content-Type: application/json` (prévalidation CORS), limite les relances
(app 1/30 s, appareil 1/5 min), ne journalise rien et ne renvoie aucune information technique. Chromium sans
`--remote-debugging-port`. La CSP du player autorise `connect-src http://127.0.0.1:47801`.

Test : `uv run pytest packaging/pi` (logique de décision) ; le reste (systemd, polkit, Chromium) n'est vérifiable que sur un Pi.

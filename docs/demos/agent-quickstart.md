# Démarrage rapide : agent de site + power.samsung-ws

Cible : serveur `http://192.168.1.182:8002` (HTTP LAN, `VDS_BASE_PATH=/`), TV Samsung Tizen 2023. Réglages TV : `docs/drivers/power.samsung-ws.md`.

## Limite : HTTP clair
L'agent refuse `http://` / `ws://` (`agent.server_url_must_be_https`). Le réglage dev explicite est `VDS_AGENT_INSECURE_DEV=1` : opt-in, avertissement au démarrage (`agent.insecure_dev_enabled`), limité aux IP privées ou loopback (un nom ou une IP publique reste refusé). Le jeton circule en clair sur le LAN : réseau de test uniquement. Sans ce réglage, le défaut TLS est inchangé.

## Étapes
1. Admin : Sites > votre site > Agent du site > « Créer l'agent ». Copier le jeton `vds_agt_...` (affiché une seule fois).
2. `cp docker/agent.env.exemple docker/agent.env && chmod 600 docker/agent.env`, puis coller le jeton dans `VDS_AGENT_TOKEN`. `agent.env` ne doit jamais être commité.
3. Admin : activer le module `power.samsung-ws`, puis sur l'écran (Alimentation) renseigner `host` = IP de la TV, `mac` = MAC Ethernet (ou Wi-Fi si la TV l'est). Placer `power.samsung-ws` dans la cascade de l'écran.
4. Lancer :
   - Docker : `docker compose -f docker/compose.agent.yaml --env-file docker/agent.env up -d --build`
   - Sans Docker (depuis la racine du dépôt) : `VDS_SERVER_URL=http://192.168.1.182:8002 VDS_AGENT_TOKEN=<jeton> VDS_AGENT_INSECURE_DEV=1 VDS_AGENT_DATA_DIR=./.agent-data VDS_MODULES_DIR=./modules uv run python -m vds_agent`
5. Vérifier : `docker compose -f docker/compose.agent.yaml logs -f agent` (connexion, pas d'erreur), l'admin affiche l'agent « en ligne » (< 2 min) avec le module `power.samsung-ws`. `ss -ltnp` : aucun port en écoute pour l'agent.
6. Première autorisation : dans l'admin, « Détecter » sur l'écran ; la TV affiche « VDS veut se connecter », accepter dans les ~40 s. Le jeton TV est stocké chiffré dans le volume `agent-data`.
7. Tester dans l'admin : « Détecter » (état `on`), « Éteindre » (veille, état `standby`), « Allumer » (15 à 25 s ; événement `power.changed` avec `power.samsung-ws`). Échec : `power.failed`, voir les logs et `power.verify_delay_s`.

## Dépannage
- Jeton TV périmé ou certificat changé : `docker compose -f docker/compose.agent.yaml --env-file docker/agent.env run --rm agent python -m vds_agent state-reset power.samsung-ws`, puis redétecter.
- Retour au TLS : retirer `VDS_AGENT_INSECURE_DEV`, mettre une URL `https://` (et `VDS_AGENT_CA_FILE` si AC privée).

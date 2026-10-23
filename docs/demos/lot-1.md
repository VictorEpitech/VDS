# Démo lot 1 — Socle

Durée : ~20 min. Aucun secret dans ce document : les mots de passe sont saisis par vous et gardés dans des variables de shell.

## Écarts connus (à annoncer avant la démo)

- `/player/` n'est **pas** servi (lot 2) : l'image embarque un placeholder, le serveur répond 404 sur `<base>/player/`.
- L'admin du lot 1 est un squelette : tableau de bord, liste d'écrans vide, thème, FR/EN. Il n'a **ni écran de connexion ni formulaires** et n'appelle pas encore l'API ; création du premier admin, connexion, site/groupe/écran et configuration se font par l'API (`curl`, étapes 3 à 6).
- `.env.example` n'existe pas : à créer à la main (étape 1).
- Logos provisoires, couleurs d'état à valider (questions ouvertes de la roadmap).
- Non couvert automatiquement : rendu visuel réel (charte, polices), lecture sur un vrai écran, 375 px à l'oeil.

## 0. Prérequis

- Docker + Docker Compose, `curl`, `jq`, un navigateur. Dépôt à la racine : `cd ~/VDS`.

## 1. Préparer `.env` (à la racine, non versionné)

```
printf 'POSTGRES_PASSWORD=%s\nVDS_PORT=8000\n' "$(head -c 18 /dev/urandom | base64 | tr -dc A-Za-z0-9)" > .env
```

- [ ] `.env` est ignoré par git. Pas de `VDS_PUBLIC_ORIGIN` nécessaire en local (contrôle de même origine par `Origin` / `Sec-Fetch-Site`).

## 2. Démarrage à la racine (`VDS_BASE_PATH=/`)

```
docker compose -f docker/compose.yaml --env-file .env up --build -d --wait
curl -si http://localhost:8000/health | head -20
```

- [ ] 200 avec `X-Robots-Tag: noindex`, `X-Frame-Options: DENY`, `Cache-Control: private, no-store`.
- [ ] `http://localhost:8000/` affiche l'admin (thème V-Tech, titre « Tableau de bord »).
- [ ] `http://localhost:8000/config.js` : `basePath` `/`, `theme.id` `theme.vtech`.
- [ ] `http://localhost:8000/modules/theme.vtech/assets/theme.css` : 200.
- [ ] `http://localhost:8000/player/` : 404 (attendu, lot 2).

## 3. Créer le premier admin

```
B=http://localhost:8000          # URL de base (ajouter le préfixe au point 9)
O=http://localhost:8000          # valeur de l'en-tête Origin (sans chemin)
read -rs PW; echo                # saisir un mot de passe (12 caractères ou plus)
# Jeton d'amorçage à usage unique : VDS_BOOTSTRAP_TOKEN s'il est défini, sinon celui écrit une fois
# dans les journaux au démarrage (tant qu'aucun admin n'existe).
docker compose -f docker/compose.yaml logs server | grep -i amorçage
read -rs TOKEN; echo             # coller le jeton
curl -s $B/api/v1/m/auth.local/status | jq .
curl -s -c jar -H "Origin: $O" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg p "$PW" --arg t "$TOKEN" '{email:"admin@vds.local",display_name:"Admin",password:$p,bootstrap_token:$t}')" \
  $B/api/v1/m/auth.local/bootstrap | jq .
```

- [ ] Amorçage : 201, rôle `admin`.
- [ ] Rejouer l'amorçage : refusé (un seul premier admin ; le jeton est invalidé après usage).
- [ ] Jeton absent ou faux : 403 `auth_local.bootstrap_token_invalid`.
- [ ] Sans cookie : `curl -s -o /dev/null -w '%{http_code}\n' $B/api/v1/sites` donne 401.
- [ ] Écriture avec `-H 'Origin: https://evil.test'` : 403 `errors.origin_mismatch`.

Le cookie est `Secure` ; curl l'envoie vers `localhost`. Sinon passer le cookie à la main (`-b "..."`).

## 4. Connexion / déconnexion

```
curl -s -c jar -H "Origin: $O" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg p "$PW" '{email:"admin@vds.local",password:$p}')" $B/api/v1/m/auth.local/login | jq .
curl -s -b jar $B/api/v1/me | jq .
```

- [ ] `me` renvoie l'admin ; mauvais mot de passe : 401 ; échecs répétés : limitation de débit.
- [ ] Déconnexion : `curl -s -b jar -X POST -H "Origin: $O" $B/api/v1/m/auth.local/logout` (204), puis `me` : 401. Se reconnecter.

## 5. Site, groupe, écran

```
A=(-b jar -H "Origin: $O" -H 'Content-Type: application/json')
SITE=$(curl -s "${A[@]}" -d '{"name":"Moulins","slug":"moulins"}' $B/api/v1/sites | jq -r .id)
GRP=$(curl -s "${A[@]}" -d '{"name":"Accueil"}' $B/api/v1/sites/$SITE/groups | jq -r .id)
SCR=$(curl -s "${A[@]}" -d "{\"site_id\":\"$SITE\",\"group_id\":\"$GRP\",\"name\":\"Ecran 1\"}" $B/api/v1/screens | jq -r .id)
curl -s -b jar $B/api/v1/screens | jq .
```

- [ ] Écran créé avec `orientation: landscape`.
- [ ] Recréer le slug `moulins` : 409 `site.slug_taken`.
- [ ] Supprimer un site non vide ou un groupe non vide : 409.

## 6. Configuration héritée avec provenance

```
ORG=$(curl -s -b jar $B/api/v1/organization | jq -r .id)
show(){ curl -s -b jar $B/api/v1/config/screen/$SCR/core | jq -c .values.timezone; }
put(){ curl -s "${A[@]}" -X PUT -d "{\"value\":\"$3\"}" $B/api/v1/config/$1/$2/core/timezone -o /dev/null -w '%{http_code}\n'; }
show                                    # origine : default
put organization $ORG UTC; show         # origine : organization
put site $SITE Europe/London; show      # origine : site (Moulins)
put group $GRP Europe/Berlin; show      # origine : group
put screen $SCR America/Cayenne; show   # origine : screen
for l in screen:$SCR group:$GRP site:$SITE organization:$ORG; do
  curl -s "${A[@]}" -X DELETE $B/api/v1/config/${l%%:*}/${l#*:}/core/timezone -o /dev/null -w '%{http_code}\n'; show; done
```

- [ ] Chaque PUT renvoie 204 ; valeur et `origin` suivent écran > groupe > site > organisation > défaut.
- [ ] Chaque DELETE remonte d'un niveau ; à la fin, retour à `default`.
- [ ] `locale` = `de` (PUT sur `.../core/locale`) refusée en 4xx.
- [ ] Éditeur (`POST $B/api/v1/m/auth.local/users` avec `role: editor`, puis connexion) : lecture OK, écriture 403.
- [ ] Audit : `docker compose -f docker/compose.yaml exec db psql -U vds -c 'select action from audit_log order by id'` liste `auth.bootstrap`, `site.created`, `config.updated`, `config.reset`.

## 7. Bascule de thème V-Tech / Allia

```
docker compose -f docker/compose.yaml --env-file .env down
VDS_THEME=theme.allia docker compose -f docker/compose.yaml --env-file .env up -d --wait
```

- [ ] Rechargement de `/` : primaire bleu Allia (`#0e49be`), logo Allia ; pas de lien « Retour au Studio » en `auth.local` (il vient du fournisseur d'auth).
- [ ] Retour à V-Tech : relancer sans la variable ; thème sombre V-Tech via le réglage système.
- [ ] Les données (site, écran) sont conservées (volume `db-data`).

## 8. FR / EN

- [ ] Sélecteur de langue : bascule immédiate, `<html lang>` change, choix mémorisé après F5.
- [ ] Aucune clé brute visible (`nav.`, `errors.`...) dans les deux langues et les deux thèmes.
- [ ] Automatique : `make i18n-check`.

## 9. Même démo sous `/espace/affichage`

```
docker compose -f docker/compose.yaml --env-file .env down -v        # base vide
docker compose -f docker/compose.yaml -f docker/compose.subpath.yaml --env-file .env up --build -d --wait
```

Refaire 3 à 8 avec `B=http://localhost:8000/espace/affichage` (et `O=http://localhost:8000` inchangé). Pour la bascule d'étape 7, garder les deux `-f`.

- [ ] `/espace/affichage/` affiche l'admin ; `<base href="/espace/affichage/">` dans le source ; logo, polices, icônes sans 404 (onglet Réseau).
- [ ] `http://localhost:8000/` et `http://localhost:8000/health` : 404.
- [ ] `/espace/affichage/health` : 200 avec les en-têtes kiosque.
- [ ] Lien profond `/espace/affichage/screens` puis F5 : la page se recharge.
- [ ] Cookie de session : `Path=/espace/affichage/` (outils développeur).
- [ ] `config.js` : `apiBase` `/espace/affichage/api/v1`.
- [ ] `/espace/affichage/player/` : 404 (lot 2).

## 10. Contrôle automatique final

```
make lint && make test && make e2e
```

Attendu : tout vert. Nettoyage : `docker compose -f docker/compose.yaml down -v`, `rm jar`.

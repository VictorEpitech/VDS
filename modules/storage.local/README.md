# storage.local

Backend `storage` : octets des médias sur disque, racine `VDS_MEDIA_DIR` (défaut `/data/media`).
Clé relative `<2 hex>/<media_id>/<variant>.<ext>`. Écriture atomique (fichier temporaire + renommage),
sha256 calculé pendant l'écriture, clés validées (aucune sortie de la racine).

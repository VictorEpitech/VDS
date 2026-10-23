# content.image

Type de contenu (kind `content-type`, vds-core 1.1).

Image fixe (JPEG, PNG, WebP, GIF). `process_media` lit les dimensions (Pillow, refus au-delà de 100 Mpx) et produit la vignette `thumb`. Élément player `vds-content-image` : `<img>` décodé avant affichage, `fit` contain/cover. Pas de fin naturelle : la durée vient de la playlist.

# content.pdf

Type de contenu (kind `content-type`, vds-core 1.1).

Document PDF. `process_media` rend chaque page en JPEG `page-<n>` avec pypdfium2 (largeur `render_width`, 3840 par défaut ; refus au-delà de `max_pages`). Élément `vds-content-pdf` : pagination toutes les `page_duration_ms`, page suivante décodée d'avance, `vds-ended` après la dernière. Limite : PDF protégés par mot de passe refusés.

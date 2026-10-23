# content.video

Type de contenu (kind `content-type`, vds-core 1.1).

Vidéo (MP4, WebM, QuickTime). `process_media` appelle `ffprobe` (image Docker du serveur ; délai `probe_timeout_s`) pour la durée et les dimensions (rotation prise en compte). Élément `vds-content-video` : `<video muted playsinline>`, son si `audio`, `vds-ended` en fin ; `known_duration_ms` sert de plafond au player. Une seule vidéo décodée à la fois : `stop()` vide la source.

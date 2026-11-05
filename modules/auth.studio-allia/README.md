# auth.studio-allia

Module d'authentification SSO pour le mode hébergé dans le Studio Allia.

## Fonctionnement
- Transmet le cookie `allia_espace` à `STUDIO_SESSION_URL` avec l'en-tête `x-vds-secret`.
- Le Studio vérifie la session et renvoie `{ id, nom, email, role }`.
- Correspondance des rôles :
  - `president`, `direction` -> admin
  - `membre` -> editor
  - `financeur`, inactif, mot de passe provisoire -> refus 403
- Cache en mémoire borné à 60 s par empreinte SHA-256 du cookie.
- Contrôle de même origine (`Origin` / `Sec-Fetch-Site`) sur toute requête d'écriture.
- Zéro accès à `espace-db` : les données du Studio ne sont jamais consultées en base.

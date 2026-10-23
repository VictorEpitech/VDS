# auth.local

Fournisseur d'authentification du mode autonome : comptes VDS, mots de passe argon2id, sessions
par cookie (`Path=<base>/`, HttpOnly, Secure, SameSite=Lax).

Routes (sous `<base>/api/v1/m/auth.local/`) : `GET status`, `POST bootstrap` (premier admin,
une seule fois), `POST login`, `POST logout`, `POST users` (admin), `POST password`.

Tables : `m_auth_local_credential`, `m_auth_local_session` (branche Alembic `auth.local`).
Réglages : durée de session, longueur minimale du mot de passe.
Limites : pas de limitation de débit ni de réinitialisation par e-mail en V1.

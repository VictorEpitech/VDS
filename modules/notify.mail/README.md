# notify.mail

Notifier e-mail : un mail à l'ouverture et un à la résolution des incidents (`incident.opened|resolved`).

- Connexion SMTP **uniquement par l'environnement** : `VDS_SMTP_HOST`, `VDS_SMTP_PORT` (587), `VDS_SMTP_SECURITY` (`starttls|tls`, pas de mode clair), `VDS_SMTP_USERNAME`, `VDS_SMTP_PASSWORD` (secret, jamais en base ni dans l'API ni dans les journaux), `VDS_MAIL_FROM`. Certificat toujours vérifié.
- Sans `VDS_SMTP_HOST` / `VDS_MAIL_FROM` : module `degraded` (`notify.mail.not_configured`) ; les incidents restent en base.
- Réglages admin : `notify_admins`, `extra_recipients`, `kinds`, `send_resolved`.
- Idempotence par `(notification_id, destinataire)` (table `m_notify_mail_delivery`) ; échec transitoire : reprises à 1, 5 et 15 min, échec permanent (auth, TLS) : pas de reprise.
- Mail FR/EN selon la locale du destinataire (administrateurs : locale du compte ; adresses supplémentaires : `fr`), heure locale du site, lien `VDS_PUBLIC_ORIGIN + VDS_BASE_PATH + /admin/<lien>`. Aucun jeton, IP ni trace technique.
- Tests : `tests/fakes.py` (aiosmtpd, STARTTLS/TLS, certificat éphémère).

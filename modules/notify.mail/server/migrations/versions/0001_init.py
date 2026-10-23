"""notify.mail : journal d'envoi idempotent par (notification, destinataire)."""

from alembic import op

revision = "notify_mail_0001"
down_revision = None
branch_labels = ("notify.mail",)
depends_on = "0003_sites_power"  # clé étrangère vers incident uniquement

STATEMENTS = [
    """CREATE TABLE m_notify_mail_delivery (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        notification_id uuid NOT NULL,
        incident_id uuid NOT NULL REFERENCES incident(id) ON DELETE CASCADE,
        transition text NOT NULL,
        recipient text NOT NULL,
        locale text NOT NULL DEFAULT 'fr',
        status text NOT NULL DEFAULT 'pending',
        code text,
        attempts integer NOT NULL DEFAULT 0,
        next_attempt_at timestamptz NOT NULL DEFAULT now(),
        sent_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_m_notify_mail_delivery_status CHECK (status IN ('sent','failed','pending')),
        CONSTRAINT ck_m_notify_mail_delivery_transition CHECK (transition IN ('opened','resolved')),
        CONSTRAINT uq_m_notify_mail_delivery_key UNIQUE (notification_id, recipient))""",
    """CREATE INDEX ix_m_notify_mail_delivery_pending ON m_notify_mail_delivery (next_attempt_at)
        WHERE status = 'pending'""",
    "CREATE INDEX ix_m_notify_mail_delivery_incident ON m_notify_mail_delivery (incident_id)",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS m_notify_mail_delivery")

"""Sessions de provisioning d'écran (additive : une table, aucune colonne existante touchée)."""

from alembic import op

revision = "0005_screen_provision"
down_revision = "0004_incident_ack"
branch_labels = None
depends_on = None

STATEMENTS = [
    """CREATE TABLE screen_provision (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        screen_id uuid NOT NULL REFERENCES screen(id) ON DELETE CASCADE,
        site_id uuid NOT NULL REFERENCES site(id) ON DELETE CASCADE,
        agent_id uuid NOT NULL REFERENCES site_agent(id) ON DELETE CASCADE,
        platform text NOT NULL,
        address inet NOT NULL,
        port integer NOT NULL DEFAULT 5555,
        status text NOT NULL,
        steps jsonb NOT NULL DEFAULT '{}'::jsonb,
        outcome jsonb,
        code text,
        apk_version_code integer,
        ticket_hash bytea,
        ticket_expires_at timestamptz,
        paired_at timestamptz,
        online_at timestamptz,
        cmd_id uuid,
        created_by uuid REFERENCES user_account(id) ON DELETE SET NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        finished_at timestamptz,
        CONSTRAINT ck_screen_provision_status CHECK (status IN
            ('queued','running','waiting_user','waiting_online','done','failed','cancelled')))""",
    """CREATE UNIQUE INDEX ux_screen_provision_active ON screen_provision (screen_id)
        WHERE status IN ('queued','running','waiting_user','waiting_online')""",
    "CREATE INDEX ix_screen_provision_cmd_id ON screen_provision (cmd_id)",
    "CREATE UNIQUE INDEX ux_screen_provision_ticket ON screen_provision (ticket_hash)"
    " WHERE ticket_hash IS NOT NULL",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS screen_provision")

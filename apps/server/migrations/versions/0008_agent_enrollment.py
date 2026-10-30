"""Enrôlement d'agent par code à usage unique (additive : une table, rien d'existant modifié)."""

from alembic import op

revision = "0008_agent_enrollment"
down_revision = "0007_drop_provision_ack"
branch_labels = None
depends_on = None

STATEMENTS = [
    """CREATE TABLE agent_enrollment (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        site_id uuid NOT NULL REFERENCES site(id) ON DELETE CASCADE,
        channel text NOT NULL,
        code_sha256 bytea NOT NULL,
        created_by uuid REFERENCES user_account(id) ON DELETE SET NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        consumed_at timestamptz,
        consumed_ip inet,
        consumed_hostname text,
        agent_id uuid REFERENCES site_agent(id) ON DELETE SET NULL,
        revoked_at timestamptz,
        CONSTRAINT uq_agent_enrollment_code_sha256 UNIQUE (code_sha256),
        CONSTRAINT ck_agent_enrollment_channel CHECK (channel IN ('remote','local')),
        CONSTRAINT ck_agent_enrollment_hostname CHECK (char_length(consumed_hostname) <= 253),
        CONSTRAINT ck_agent_enrollment_exclusive
            CHECK (consumed_at IS NULL OR revoked_at IS NULL))""",
    "CREATE INDEX ix_agent_enrollment_pending ON agent_enrollment (site_id)"
    " WHERE consumed_at IS NULL AND revoked_at IS NULL",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_enrollment")

"""auth.local : échecs de connexion (limitation de débit)."""

from alembic import op

revision = "auth_local_0002"
down_revision = "auth_local_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE m_auth_local_login_attempt (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        kind text NOT NULL CHECK (kind IN ('identifier', 'ip')),
        key text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now())"""
    )
    op.execute(
        "CREATE INDEX ix_m_auth_local_login_attempt_lookup"
        " ON m_auth_local_login_attempt (kind, key, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE m_auth_local_login_attempt")

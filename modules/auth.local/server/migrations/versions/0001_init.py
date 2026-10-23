"""auth.local : identifiants et sessions (tables m_auth_local_*)."""

from alembic import op

revision = "auth_local_0001"
down_revision = None
branch_labels = ("auth.local",)
depends_on = "0001_socle"  # clés étrangères vers le noyau uniquement

STATEMENTS = [
    """CREATE TABLE m_auth_local_credential (
        user_id uuid PRIMARY KEY REFERENCES user_account(id) ON DELETE CASCADE,
        password_hash text NOT NULL,
        must_change boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now())""",
    """CREATE TABLE m_auth_local_session (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES user_account(id) ON DELETE CASCADE,
        token_hash text NOT NULL,
        expires_at timestamptz NOT NULL,
        revoked_at timestamptz,
        last_seen_at timestamptz NOT NULL DEFAULT now(),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT uq_m_auth_local_session_token_hash UNIQUE (token_hash))""",
    "CREATE INDEX ix_m_auth_local_session_user_id ON m_auth_local_session (user_id)",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE m_auth_local_session")
    op.execute("DROP TABLE m_auth_local_credential")

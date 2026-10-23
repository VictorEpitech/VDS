"""auth.local : e-mails des comptes locaux normalisés en minuscules (données uniquement)."""

from alembic import op

revision = "auth_local_0003"
down_revision = "auth_local_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE user_account SET email = lower(email::text)"
        " WHERE auth_provider = 'auth.local' AND email IS NOT NULL"
        " AND email::text <> lower(email::text)"
    )


def downgrade() -> None:
    pass  # normalisation non réversible, sans effet sur le schéma

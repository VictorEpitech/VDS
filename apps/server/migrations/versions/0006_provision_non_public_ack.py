"""Confirmation « écran non public » d'une session de provisioning (additive, réversible)."""

from alembic import op

revision = "0006_provision_non_public_ack"
down_revision = "0005_screen_provision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE screen_provision ADD COLUMN non_public_ack_at timestamptz")
    op.execute(
        "ALTER TABLE screen_provision ADD COLUMN non_public_ack_by uuid"
        " REFERENCES user_account(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE screen_provision DROP COLUMN non_public_ack_by")
    op.execute("ALTER TABLE screen_provision DROP COLUMN non_public_ack_at")

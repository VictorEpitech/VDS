"""Retrait de la confirmation « écran non public » (ADB réseau laissé allumé : décision assumée)."""

from alembic import op

revision = "0007_drop_provision_ack"
down_revision = "0006_provision_non_public_ack"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE screen_provision DROP COLUMN IF EXISTS non_public_ack_by")
    op.execute("ALTER TABLE screen_provision DROP COLUMN IF EXISTS non_public_ack_at")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE screen_provision ADD COLUMN IF NOT EXISTS non_public_ack_at timestamptz"
    )
    op.execute(
        "ALTER TABLE screen_provision ADD COLUMN IF NOT EXISTS non_public_ack_by uuid"
        " REFERENCES user_account(id) ON DELETE SET NULL"
    )

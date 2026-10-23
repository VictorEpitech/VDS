"""Acquittement des incidents (additive : deux colonnes nullables)."""

from alembic import op

revision = "0004_incident_ack"
down_revision = "0003_sites_power"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE incident ADD COLUMN acknowledged_at timestamptz")
    op.execute(
        "ALTER TABLE incident ADD COLUMN acknowledged_by uuid"
        " REFERENCES user_account(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE incident DROP COLUMN IF EXISTS acknowledged_by")
    op.execute("ALTER TABLE incident DROP COLUMN IF EXISTS acknowledged_at")

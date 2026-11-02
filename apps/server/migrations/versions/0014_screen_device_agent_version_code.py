"""screen_device.agent_version_code : versionCode constaté par l'agent (apk.update).

Additive (colonne nullable) et réversible.
"""

from alembic import op

revision = "0014_screen_device_agent_code"
down_revision = "0013_screen_device_apk_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE screen_device ADD COLUMN agent_version_code integer")


def downgrade() -> None:
    op.execute("ALTER TABLE screen_device DROP COLUMN IF EXISTS agent_version_code")

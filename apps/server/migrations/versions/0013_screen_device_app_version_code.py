"""screen_device.app_version_code : versionCode de l'APK player remonté (vds-core 1.6.0).

Additive (colonne nullable) et réversible.
"""

from alembic import op

revision = "0013_screen_device_apk_code"
down_revision = "0012_onboarding_skipped_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE screen_device ADD COLUMN app_version_code integer")


def downgrade() -> None:
    op.execute("ALTER TABLE screen_device DROP COLUMN IF EXISTS app_version_code")

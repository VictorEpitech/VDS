"""Médiathèque : expiration, révision, tags (media, playlist) et file storage_tombstone.

Additive (colonnes avec défaut ou nullables, nouvelle table) et réversible.
"""

from alembic import op

revision = "0016_mediatheque"
down_revision = "0015_playback_override"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE media "
        "ADD COLUMN expires_at timestamptz, "
        "ADD COLUMN expired_at timestamptz, "
        "ADD COLUMN revision integer NOT NULL DEFAULT 1, "
        "ADD COLUMN tags text[] NOT NULL DEFAULT '{}', "
        "ADD CONSTRAINT ck_media_revision CHECK (revision >= 1), "
        "ADD CONSTRAINT ck_media_tags CHECK (cardinality(tags) <= 20)"
    )
    op.execute("CREATE INDEX ix_media_tags ON media USING gin (tags)")
    op.execute(
        "CREATE INDEX ix_media_expires_at ON media (expires_at) "
        "WHERE expires_at IS NOT NULL AND expired_at IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_media_expired_at ON media (expired_at) WHERE expired_at IS NOT NULL"
    )

    op.execute(
        "ALTER TABLE playlist "
        "ADD COLUMN expires_at timestamptz, "
        "ADD COLUMN expired_at timestamptz, "
        "ADD COLUMN tags text[] NOT NULL DEFAULT '{}', "
        "ADD CONSTRAINT ck_playlist_tags CHECK (cardinality(tags) <= 20), "
        "ADD CONSTRAINT ck_playlist_override_no_expiry "
        "CHECK (owner_override_id IS NULL OR expires_at IS NULL)"
    )
    op.execute("CREATE INDEX ix_playlist_tags ON playlist USING gin (tags)")
    op.execute(
        "CREATE INDEX ix_playlist_expires_at ON playlist (expires_at) "
        "WHERE expires_at IS NOT NULL AND expired_at IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_playlist_expired_at ON playlist (expired_at) WHERE expired_at IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE storage_tombstone (
            id bigserial PRIMARY KEY,
            backend text NOT NULL,
            key text NOT NULL,
            reason text NOT NULL,
            media_id uuid,
            not_before timestamptz NOT NULL,
            attempts integer NOT NULL DEFAULT 0,
            last_error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_storage_tombstone_reason
                CHECK (reason IN ('replaced','purged','deleted','aborted')),
            CONSTRAINT uq_storage_tombstone_backend_key UNIQUE (backend, key)
        )
        """
    )
    op.execute("CREATE INDEX ix_storage_tombstone_not_before ON storage_tombstone (not_before)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS storage_tombstone")
    op.execute("DROP INDEX IF EXISTS ix_playlist_expired_at")
    op.execute("DROP INDEX IF EXISTS ix_playlist_expires_at")
    op.execute("DROP INDEX IF EXISTS ix_playlist_tags")
    op.execute(
        "ALTER TABLE playlist "
        "DROP CONSTRAINT IF EXISTS ck_playlist_override_no_expiry, "
        "DROP CONSTRAINT IF EXISTS ck_playlist_tags, "
        "DROP COLUMN IF EXISTS tags, "
        "DROP COLUMN IF EXISTS expired_at, "
        "DROP COLUMN IF EXISTS expires_at"
    )
    op.execute("DROP INDEX IF EXISTS ix_media_expired_at")
    op.execute("DROP INDEX IF EXISTS ix_media_expires_at")
    op.execute("DROP INDEX IF EXISTS ix_media_tags")
    op.execute(
        "ALTER TABLE media "
        "DROP CONSTRAINT IF EXISTS ck_media_tags, "
        "DROP CONSTRAINT IF EXISTS ck_media_revision, "
        "DROP COLUMN IF EXISTS tags, "
        "DROP COLUMN IF EXISTS revision, "
        "DROP COLUMN IF EXISTS expired_at, "
        "DROP COLUMN IF EXISTS expires_at"
    )

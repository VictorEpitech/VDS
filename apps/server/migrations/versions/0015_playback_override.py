"""Forçage avec fin automatique : table playback_overrides, playlist.owner_override_id.

Additive (nouvelle table, colonne nullable) et réversible.
"""

from alembic import op

revision = "0015_playback_override"
down_revision = "0014_screen_device_agent_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE playback_overrides (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name varchar(120) NOT NULL,
            kind varchar(16) NOT NULL,
            playlist_id uuid REFERENCES playlist(id),
            target_type varchar(16) NOT NULL,
            target_id uuid NOT NULL,
            starts_at timestamptz NOT NULL,
            ends_at timestamptz NOT NULL,
            ended_at timestamptz,
            end_reason varchar(16),
            priority integer NOT NULL DEFAULT 0,
            respect_scheduled_off boolean,
            note varchar(500),
            created_by uuid REFERENCES user_account(id) ON DELETE SET NULL,
            created_by_module varchar(120),
            ended_by uuid REFERENCES user_account(id) ON DELETE SET NULL,
            request_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_playback_overrides_kind CHECK (kind IN ('playlist','off')),
            CONSTRAINT ck_playback_overrides_target_type
                CHECK (target_type IN ('organization','site','group','screen')),
            CONSTRAINT ck_playback_overrides_period CHECK (ends_at > starts_at),
            CONSTRAINT ck_playback_overrides_priority CHECK (priority BETWEEN -1000 AND 1000),
            CONSTRAINT ck_playback_overrides_end_reason
                CHECK (end_reason IS NULL OR end_reason IN ('stopped','expired')),
            CONSTRAINT ck_playback_overrides_playlist
                CHECK ((kind = 'off') = (playlist_id IS NULL))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_playback_overrides_target ON playback_overrides (target_type, target_id)"
    )
    op.execute(
        "CREATE INDEX ix_playback_overrides_ends_at ON playback_overrides (ends_at) "
        "WHERE ended_at IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_playback_overrides_request_id ON playback_overrides (request_id) "
        "WHERE request_id IS NOT NULL"
    )
    op.execute(
        "ALTER TABLE playlist ADD COLUMN owner_override_id uuid "
        "REFERENCES playback_overrides(id) ON DELETE CASCADE"
    )
    op.execute(
        "CREATE INDEX ix_playlist_owner_override_id ON playlist (owner_override_id) "
        "WHERE owner_override_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_playlist_owner_override_id")
    op.execute("ALTER TABLE playlist DROP COLUMN IF EXISTS owner_override_id")
    op.execute("DROP TABLE IF EXISTS playback_overrides")

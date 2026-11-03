"""Mises en page : tables layout / layout_zone, cible layout des programmations et forçages.

Additive et réversible : nouvelles tables, colonnes nullables, `schedule.playlist_id` devient
NULLable (XOR avec `layout_id`), `playback_overrides.zone_ids` (NULL = forçage d'écran).
La descente supprime les lignes qui ne peuvent plus exister (cible layout, forçages de zone).
"""

from alembic import op

revision = "0017_mises_en_page"
down_revision = "0016_mediatheque"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE layout (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id uuid NOT NULL REFERENCES organization(id),
            name text NOT NULL,
            orientation text NOT NULL,
            background_color text,
            background_media_id uuid REFERENCES media(id) ON DELETE SET NULL,
            tags text[] NOT NULL DEFAULT '{}',
            revision integer NOT NULL DEFAULT 1,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            created_by uuid REFERENCES user_account(id) ON DELETE SET NULL,
            CONSTRAINT ck_layout_name CHECK (char_length(name) BETWEEN 1 AND 120),
            CONSTRAINT ck_layout_orientation CHECK (orientation IN ('landscape','portrait')),
            CONSTRAINT ck_layout_background_color
                CHECK (background_color IS NULL OR background_color ~ '^#[0-9a-fA-F]{6}$'),
            CONSTRAINT ck_layout_tags CHECK (cardinality(tags) <= 20),
            CONSTRAINT ck_layout_revision CHECK (revision >= 1),
            CONSTRAINT uq_layout_org_id_name UNIQUE (org_id, name)
        )
        """
    )
    op.execute("CREATE INDEX ix_layout_tags ON layout USING gin (tags)")
    op.execute("CREATE INDEX ix_layout_background_media_id ON layout (background_media_id)")
    op.execute(
        """
        CREATE TABLE layout_zone (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            layout_id uuid NOT NULL REFERENCES layout(id) ON DELETE CASCADE,
            key text NOT NULL,
            name text NOT NULL,
            x integer NOT NULL,
            y integer NOT NULL,
            w integer NOT NULL,
            h integer NOT NULL,
            z smallint NOT NULL DEFAULT 0,
            playlist_id uuid REFERENCES playlist(id) ON DELETE SET NULL,
            fit text NOT NULL DEFAULT 'contain',
            default_item_duration_ms integer,
            active_from timestamptz,
            active_until timestamptz,
            CONSTRAINT ck_layout_zone_key CHECK (key ~ '^[a-z0-9_-]{1,32}$'),
            CONSTRAINT ck_layout_zone_name CHECK (char_length(name) BETWEEN 1 AND 80),
            CONSTRAINT ck_layout_zone_geometry CHECK (
                x >= 0 AND y >= 0 AND w > 0 AND h > 0 AND x + w <= 10000 AND y + h <= 10000
            ),
            CONSTRAINT ck_layout_zone_z CHECK (z BETWEEN 0 AND 99),
            CONSTRAINT ck_layout_zone_fit CHECK (fit IN ('contain','cover','stretch')),
            CONSTRAINT ck_layout_zone_duration CHECK (
                default_item_duration_ms IS NULL
                OR default_item_duration_ms BETWEEN 1000 AND 86400000
            ),
            CONSTRAINT ck_layout_zone_window CHECK (
                active_until IS NULL OR active_from IS NULL OR active_until > active_from
            ),
            CONSTRAINT uq_layout_zone_layout_id_key UNIQUE (layout_id, key)
        )
        """
    )
    op.execute("CREATE INDEX ix_layout_zone_layout_id_z ON layout_zone (layout_id, z)")
    op.execute("CREATE INDEX ix_layout_zone_playlist_id ON layout_zone (playlist_id)")

    op.execute(
        "ALTER TABLE schedule "
        "ADD COLUMN layout_id uuid REFERENCES layout(id) ON DELETE RESTRICT, "
        "ALTER COLUMN playlist_id DROP NOT NULL, "
        "ADD CONSTRAINT ck_schedule_content_target CHECK (num_nonnulls(playlist_id, layout_id) = 1)"
    )
    op.execute("CREATE INDEX ix_schedule_layout_id ON schedule (layout_id)")

    op.execute(
        "ALTER TABLE playback_overrides "
        "ADD COLUMN layout_id uuid REFERENCES layout(id) ON DELETE RESTRICT, "
        "ADD COLUMN zone_ids text[], "
        "DROP CONSTRAINT ck_playback_overrides_playlist, "
        "ADD CONSTRAINT ck_playback_overrides_playlist "
        "CHECK ((kind = 'off') = (playlist_id IS NULL AND layout_id IS NULL)), "
        "ADD CONSTRAINT ck_playback_overrides_content_target "
        "CHECK (playlist_id IS NULL OR layout_id IS NULL), "
        "ADD CONSTRAINT ck_playback_overrides_zone_count "
        "CHECK (zone_ids IS NULL OR cardinality(zone_ids) BETWEEN 1 AND 32), "
        "ADD CONSTRAINT ck_playback_overrides_zone_playlist "
        "CHECK (zone_ids IS NULL OR (playlist_id IS NOT NULL AND layout_id IS NULL)), "
        "ADD CONSTRAINT ck_playback_overrides_zone_kind "
        "CHECK (zone_ids IS NULL OR kind = 'playlist')"
    )
    op.execute("CREATE INDEX ix_playback_overrides_layout_id ON playback_overrides (layout_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_playback_overrides_layout_id")
    # forçages de zone et forçages de mise en page : non représentables avant 0017
    op.execute("DELETE FROM playback_overrides WHERE zone_ids IS NOT NULL OR layout_id IS NOT NULL")
    op.execute(
        "ALTER TABLE playback_overrides "
        "DROP CONSTRAINT IF EXISTS ck_playback_overrides_zone_kind, "
        "DROP CONSTRAINT IF EXISTS ck_playback_overrides_zone_playlist, "
        "DROP CONSTRAINT IF EXISTS ck_playback_overrides_zone_count, "
        "DROP CONSTRAINT IF EXISTS ck_playback_overrides_content_target, "
        "DROP CONSTRAINT IF EXISTS ck_playback_overrides_playlist, "
        "ADD CONSTRAINT ck_playback_overrides_playlist "
        "CHECK ((kind = 'off') = (playlist_id IS NULL)), "
        "DROP COLUMN IF EXISTS zone_ids, "
        "DROP COLUMN IF EXISTS layout_id"
    )
    op.execute("DROP INDEX IF EXISTS ix_schedule_layout_id")
    op.execute("DELETE FROM schedule WHERE layout_id IS NOT NULL")
    op.execute(
        "ALTER TABLE schedule "
        "DROP CONSTRAINT IF EXISTS ck_schedule_content_target, "
        "DROP COLUMN IF EXISTS layout_id, "
        "ALTER COLUMN playlist_id SET NOT NULL"
    )
    op.execute("DROP TABLE IF EXISTS layout_zone")
    op.execute("DROP TABLE IF EXISTS layout")

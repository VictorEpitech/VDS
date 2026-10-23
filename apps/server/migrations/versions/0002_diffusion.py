"""Diffusion : appareils, appairage, médias, playlists, programmation (additive)."""

from alembic import op

revision = "0002_diffusion"
down_revision = "0001_socle"
branch_labels = None
depends_on = None

TS = "created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()"
PK = "id uuid PRIMARY KEY DEFAULT gen_random_uuid()"

STATEMENTS = [
    f"""CREATE TABLE screen_device ({PK},
        screen_id uuid NOT NULL REFERENCES screen(id) ON DELETE CASCADE,
        token_hash text NOT NULL, platform text NOT NULL, model text,
        app_version text, paired_at timestamptz NOT NULL DEFAULT now(),
        paired_by uuid REFERENCES user_account(id) ON DELETE SET NULL,
        last_seen_at timestamptz, last_heartbeat jsonb, expires_at timestamptz,
        revoked_at timestamptz,
        revoked_by uuid REFERENCES user_account(id) ON DELETE SET NULL, {TS},
        CONSTRAINT uq_screen_device_token_hash UNIQUE (token_hash))""",
    """CREATE UNIQUE INDEX uq_screen_device_active_screen ON screen_device (screen_id)
        WHERE revoked_at IS NULL""",
    f"""CREATE TABLE pairing_request ({PK},
        code citext NOT NULL, poll_secret_hash text NOT NULL,
        device jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        status text NOT NULL DEFAULT 'pending',
        screen_id uuid REFERENCES screen(id) ON DELETE CASCADE,
        claimed_by uuid REFERENCES user_account(id) ON DELETE SET NULL,
        expires_at timestamptz NOT NULL, {TS},
        CONSTRAINT ck_pairing_request_status
            CHECK (status IN ('pending','claimed','consumed','expired')))""",
    """CREATE UNIQUE INDEX uq_pairing_request_pending_code ON pairing_request (code)
        WHERE status = 'pending'""",
    "CREATE INDEX ix_pairing_request_expires_at ON pairing_request (expires_at)",
    f"""CREATE TABLE media ({PK},
        kind text NOT NULL, title text NOT NULL, original_filename text,
        mime text NOT NULL, bytes bigint NOT NULL DEFAULT 0, sha256 text,
        width integer, height integer, duration_ms integer, page_count integer,
        storage_backend text, storage_key text,
        status text NOT NULL DEFAULT 'processing', error text,
        created_by uuid REFERENCES user_account(id) ON DELETE SET NULL, {TS},
        CONSTRAINT ck_media_kind CHECK (kind IN ('image','video','document')),
        CONSTRAINT ck_media_status CHECK (status IN ('processing','ready','failed')))""",
    "CREATE INDEX ix_media_sha256 ON media (sha256)",
    "CREATE INDEX ix_media_kind ON media (kind)",
    f"""CREATE TABLE media_variant (
        media_id uuid NOT NULL REFERENCES media(id) ON DELETE CASCADE,
        name text NOT NULL, mime text NOT NULL, bytes bigint NOT NULL DEFAULT 0,
        sha256 text, width integer, height integer, storage_key text NOT NULL, {TS},
        PRIMARY KEY (media_id, name))""",
    f"""CREATE TABLE playlist ({PK}, name text NOT NULL, description text,
        created_by uuid REFERENCES user_account(id) ON DELETE SET NULL,
        updated_by uuid REFERENCES user_account(id) ON DELETE SET NULL, {TS})""",
    f"""CREATE TABLE playlist_item ({PK},
        playlist_id uuid NOT NULL REFERENCES playlist(id) ON DELETE CASCADE,
        position integer NOT NULL, content_type text NOT NULL,
        props jsonb NOT NULL DEFAULT '{{}}'::jsonb, duration_ms integer,
        enabled boolean NOT NULL DEFAULT true, {TS},
        CONSTRAINT ck_playlist_item_duration CHECK (duration_ms IS NULL OR duration_ms > 0),
        CONSTRAINT uq_playlist_item_playlist_id_position UNIQUE (playlist_id, position)
            DEFERRABLE INITIALLY DEFERRED)""",
    """CREATE TABLE playlist_item_media (
        item_id uuid NOT NULL REFERENCES playlist_item(id) ON DELETE CASCADE,
        media_id uuid NOT NULL REFERENCES media(id) ON DELETE RESTRICT,
        PRIMARY KEY (item_id, media_id))""",
    "CREATE INDEX ix_playlist_item_media_media_id ON playlist_item_media (media_id)",
    f"""CREATE TABLE schedule ({PK}, name text NOT NULL,
        playlist_id uuid NOT NULL REFERENCES playlist(id) ON DELETE RESTRICT,
        target_type text NOT NULL, target_id uuid NOT NULL,
        priority integer NOT NULL DEFAULT 0,
        starts_on date, ends_on date, enabled boolean NOT NULL DEFAULT true,
        conditions jsonb,
        created_by uuid REFERENCES user_account(id) ON DELETE SET NULL,
        updated_by uuid REFERENCES user_account(id) ON DELETE SET NULL, {TS},
        CONSTRAINT ck_schedule_target_type
            CHECK (target_type IN ('organization','site','group','screen')),
        CONSTRAINT ck_schedule_period CHECK (starts_on IS NULL OR ends_on IS NULL
            OR starts_on <= ends_on))""",
    "CREATE INDEX ix_schedule_target ON schedule (target_type, target_id)",
    "CREATE INDEX ix_schedule_playlist_id ON schedule (playlist_id)",
    # Minutes depuis minuit (24:00 = 1440) : `time` ne sait pas représenter 24:00 côté Python.
    f"""CREATE TABLE schedule_rule ({PK},
        schedule_id uuid NOT NULL REFERENCES schedule(id) ON DELETE CASCADE,
        weekdays smallint NOT NULL, start_minute smallint NOT NULL,
        end_minute smallint NOT NULL, {TS},
        CONSTRAINT ck_schedule_rule_weekdays CHECK (weekdays BETWEEN 1 AND 127),
        CONSTRAINT ck_schedule_rule_range
            CHECK (start_minute >= 0 AND start_minute < end_minute AND end_minute <= 1440))""",
    "CREATE INDEX ix_schedule_rule_schedule_id ON schedule_rule (schedule_id)",
]

TABLES = [
    "schedule_rule",
    "schedule",
    "playlist_item_media",
    "playlist_item",
    "playlist",
    "media_variant",
    "media",
    "pairing_request",
    "screen_device",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")

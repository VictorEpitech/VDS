"""Commandes player et captures d'écran (lot I) : player_command, screenshot.

Additive (deux nouvelles tables, aucune colonne existante touchée) et réversible : la descente
supprime les deux tables (historique des commandes et métadonnées de captures perdus ; les
fichiers image sont purgés par la tâche de rétention, pas par la migration).
"""

from alembic import op

revision = "0019_commandes_player"
down_revision = "0018_perimetre"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE player_command (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            screen_id uuid REFERENCES screen(id) ON DELETE SET NULL,
            device_id uuid REFERENCES screen_device(id) ON DELETE SET NULL,
            name text NOT NULL,
            args jsonb NOT NULL DEFAULT '{}'::jsonb,
            status text NOT NULL DEFAULT 'queued',
            code text,
            request_key text,
            batch_id uuid,
            actor_kind text NOT NULL,
            actor_id text,
            expires_at timestamptz NOT NULL,
            sent_at timestamptz,
            done_at timestamptz,
            result jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_player_command_name
                CHECK (name IN ('reload','restart','identify','screenshot','cache.resync')),
            CONSTRAINT ck_player_command_status CHECK (
                status IN ('queued','sent','ok','failed','expired','rejected','cancelled')
            ),
            CONSTRAINT ck_player_command_actor_kind
                CHECK (actor_kind IN ('user','module','system'))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_player_command_request_key ON player_command "
        "(actor_id, request_key) WHERE request_key IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_player_command_pending ON player_command (device_id, status) "
        "WHERE status IN ('queued','sent')"
    )
    op.execute("CREATE INDEX ix_player_command_screen_id ON player_command (screen_id, created_at)")
    op.execute(
        """
        CREATE TABLE screenshot (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            screen_id uuid NOT NULL REFERENCES screen(id) ON DELETE CASCADE,
            command_id uuid REFERENCES player_command(id) ON DELETE SET NULL,
            trigger text NOT NULL,
            captured_at timestamptz NOT NULL,
            received_at timestamptz NOT NULL DEFAULT now(),
            width integer NOT NULL,
            height integer NOT NULL,
            bytes integer NOT NULL,
            sha256 text NOT NULL,
            storage_key text NOT NULL,
            CONSTRAINT ck_screenshot_trigger CHECK (trigger IN ('on_demand','periodic')),
            CONSTRAINT ck_screenshot_size CHECK (width > 0 AND height > 0 AND bytes > 0),
            CONSTRAINT uq_screenshot_storage_key UNIQUE (storage_key)
        )
        """
    )
    op.execute("CREATE INDEX ix_screenshot_screen_id ON screenshot (screen_id, captured_at DESC)")
    op.execute("CREATE INDEX ix_screenshot_received_at ON screenshot (received_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS screenshot")
    op.execute("DROP TABLE IF EXISTS player_command")

"""Flux de données des widgets (vds-core 1.12.0) : table content_feed.

Additive (une nouvelle table, aucune colonne existante touchée) et réversible : la descente
supprime la table (les données mises en cache sont reconstruites au prochain rafraîchissement).
"""

from alembic import op

revision = "0020_flux_donnees"
down_revision = "0019_commandes_player"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE content_feed (
            id text PRIMARY KEY,
            module_id text NOT NULL,
            request jsonb NOT NULL,
            refresh_s integer NOT NULL,
            data jsonb,
            fetched_at timestamptz,
            status text NOT NULL DEFAULT 'never',
            error_code text,
            fail_count integer NOT NULL DEFAULT 0,
            failing_since timestamptz,
            next_at timestamptz NOT NULL DEFAULT now(),
            last_used_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_content_feed_status CHECK (status IN ('ok','failing','never')),
            CONSTRAINT ck_content_feed_refresh CHECK (refresh_s BETWEEN 300 AND 86400),
            CONSTRAINT ck_content_feed_fail_count CHECK (fail_count >= 0)
        )
        """
    )
    op.execute("CREATE INDEX ix_content_feed_next_at ON content_feed (next_at)")
    op.execute("CREATE INDEX ix_content_feed_module_id ON content_feed (module_id)")
    op.execute("CREATE INDEX ix_content_feed_last_used_at ON content_feed (last_used_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS content_feed")

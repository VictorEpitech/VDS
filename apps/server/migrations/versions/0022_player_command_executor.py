"""Relance de l'application (vds-core 1.14.0) : colonne player_command.executor.

Additive (une colonne avec défaut, aucune colonne existante touchée) et réversible : la descente
supprime la colonne. 'player' : livrée au player (comportement du lot I) ; 'agent' : exécutée par
l'agent de site (commande d'agent `app.restart`).
"""

from alembic import op

revision = "0022_player_command_executor"
down_revision = "0021_publication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE player_command ADD COLUMN executor varchar(8) NOT NULL DEFAULT 'player'"
    )
    op.execute(
        "ALTER TABLE player_command ADD CONSTRAINT ck_player_command_executor"
        " CHECK (executor IN ('player','agent'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE player_command DROP CONSTRAINT IF EXISTS ck_player_command_executor")
    op.execute("ALTER TABLE player_command DROP COLUMN IF EXISTS executor")

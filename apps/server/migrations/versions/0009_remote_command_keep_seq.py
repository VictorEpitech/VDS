"""remote_command.screen_id : SET NULL au lieu de CASCADE.

Supprimer un écran effaçait ses commandes d'agent : le max(seq) régressait, le serveur réattribuait
des seq déjà vus par l'agent (ignorés comme doublons) ou créait des trous (seq_gap). La file d'un
agent est désormais append-only ; seule la référence à l'écran disparaît.
"""

from alembic import op

revision = "0009_remote_command_keep_seq"
down_revision = "0008_agent_enrollment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE remote_command DROP CONSTRAINT remote_command_screen_id_fkey")
    op.execute(
        "ALTER TABLE remote_command ADD CONSTRAINT remote_command_screen_id_fkey "
        "FOREIGN KEY (screen_id) REFERENCES screen(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE remote_command DROP CONSTRAINT remote_command_screen_id_fkey")
    op.execute(
        "ALTER TABLE remote_command ADD CONSTRAINT remote_command_screen_id_fkey "
        "FOREIGN KEY (screen_id) REFERENCES screen(id) ON DELETE CASCADE"
    )

"""État des Premiers pas (additive : une table, une ligne par organisation).

Docs : docs/architecture-onboarding.md. Aucune table existante n'est modifiée.
"""

from alembic import op

revision = "0011_onboarding_state"
down_revision = "0010_discovery_scan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE onboarding_state (
        organization_id uuid PRIMARY KEY REFERENCES organization(id) ON DELETE CASCADE,
        skipped_steps text[] NOT NULL DEFAULT '{}',
        card_hidden_at timestamptz,
        updated_by uuid REFERENCES user_account(id) ON DELETE SET NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now())"""
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS onboarding_state")

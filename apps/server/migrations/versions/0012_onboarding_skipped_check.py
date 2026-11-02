"""Contrainte CHECK sur onboarding_state.skipped_steps (ids d'étapes ignorables connus).

Additive et réversible : `account` n'est pas ignorable, les six autres ids sont stables.
Les lignes existantes sont nettoyées des ids inconnus avant l'ajout de la contrainte.
"""

from alembic import op

revision = "0012_onboarding_skipped_check"
down_revision = "0011_onboarding_state"
branch_labels = None
depends_on = None

IDS = "'site','screen','media','playlist','schedule','power'"


def upgrade() -> None:
    op.execute(
        f"""UPDATE onboarding_state SET skipped_steps = COALESCE(
        (SELECT array_agg(x) FROM unnest(skipped_steps) AS x WHERE x IN ({IDS})), '{{}}')
        WHERE NOT skipped_steps <@ ARRAY[{IDS}]::text[]"""  # noqa: S608 - IDS est une constante littérale
    )
    op.execute(
        f"""ALTER TABLE onboarding_state ADD CONSTRAINT ck_onboarding_skipped_known
        CHECK (skipped_steps <@ ARRAY[{IDS}]::text[])"""
    )


def downgrade() -> None:
    op.execute("ALTER TABLE onboarding_state DROP CONSTRAINT IF EXISTS ck_onboarding_skipped_known")

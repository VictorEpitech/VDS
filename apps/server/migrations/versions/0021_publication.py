"""Validation de publication (vds-core 1.13.0) : table publication.

Additive (une nouvelle table, aucune colonne existante touchée) et réversible : la descente
supprime la table. Les instantanés sont recréés au démarrage (reprise idempotente) à partir de
la copie de travail ; seules les validations en cours sont perdues.
"""

from alembic import op

revision = "0021_publication"
down_revision = "0020_flux_donnees"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE publication (
            entity_type text NOT NULL,
            entity_id uuid NOT NULL,
            state text NOT NULL DEFAULT 'draft',
            working_rev integer NOT NULL DEFAULT 1,
            submitted_rev integer,
            submitted_by uuid,
            submitted_at timestamptz,
            published_rev integer,
            published_by uuid,
            published_at timestamptz,
            snapshot jsonb,
            review_comment text,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_publication PRIMARY KEY (entity_type, entity_id),
            CONSTRAINT ck_publication_entity_type
                CHECK (entity_type IN ('playlist','layout','schedule')),
            CONSTRAINT ck_publication_state CHECK (state IN ('draft','in_review','published')),
            CONSTRAINT ck_publication_working_rev CHECK (working_rev >= 0),
            CONSTRAINT ck_publication_comment
                CHECK (review_comment IS NULL OR char_length(review_comment) <= 500)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_publication_in_review ON publication (updated_at) "
        "WHERE state = 'in_review'"
    )
    op.execute(
        "CREATE INDEX ix_publication_schedule_target ON publication ((snapshot->>'target_id')) "
        "WHERE entity_type = 'schedule'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS publication")

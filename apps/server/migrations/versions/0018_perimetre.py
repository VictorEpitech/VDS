"""Périmètre des éditeurs (lot J) : user_scope, propriétaire des contenus, role_source.

Additive et réversible. Rétrocompatibilité (J-D8) : chaque éditeur déjà présent reçoit le nœud
`organization`, tous les contenus existants restent partagés (propriétaire NULL/NULL). La descente
supprime la table et les colonnes (les périmètres attribués sont perdus, assumé).
"""

from alembic import op

revision = "0018_perimetre"
down_revision = "0017_mises_en_page"
branch_labels = None
depends_on = None

CONTENT_TABLES = ("media", "playlist", "layout")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE user_scope (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES user_account(id) ON DELETE CASCADE,
            organization_id uuid REFERENCES organization(id) ON DELETE CASCADE,
            site_id uuid REFERENCES site(id) ON DELETE CASCADE,
            group_id uuid REFERENCES screen_group(id) ON DELETE CASCADE,
            scope_type text GENERATED ALWAYS AS (
                CASE WHEN group_id IS NOT NULL THEN 'group'
                     WHEN site_id IS NOT NULL THEN 'site' ELSE 'organization' END
            ) STORED,
            scope_id uuid GENERATED ALWAYS AS (COALESCE(group_id, site_id, organization_id)) STORED,
            created_by uuid REFERENCES user_account(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_user_scope_one_scope
                CHECK (num_nonnulls(organization_id, site_id, group_id) = 1),
            CONSTRAINT uq_user_scope_user_id_scope_id UNIQUE (user_id, scope_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_user_scope_user_id ON user_scope (user_id)")
    op.execute(
        "ALTER TABLE user_account ADD COLUMN role_source text NOT NULL DEFAULT 'vds', "
        "ADD CONSTRAINT ck_user_account_role_source CHECK (role_source IN ('vds','provider'))"
    )
    for t in CONTENT_TABLES:
        op.execute(
            f"ALTER TABLE {t} ADD COLUMN owner_site_id uuid REFERENCES site(id) ON DELETE RESTRICT"
        )
        op.execute(f"ALTER TABLE {t} ADD COLUMN owner_group_id uuid")
        op.execute(
            f"ALTER TABLE {t} ADD CONSTRAINT fk_{t}_owner_group "
            "FOREIGN KEY (owner_group_id, owner_site_id) REFERENCES screen_group(id, site_id) "
            "ON DELETE SET NULL (owner_group_id)"
        )
        op.execute(
            f"ALTER TABLE {t} ADD CONSTRAINT ck_{t}_owner "
            "CHECK (owner_group_id IS NULL OR owner_site_id IS NOT NULL)"
        )
        op.execute(f"CREATE INDEX ix_{t}_owner ON {t} (owner_site_id, owner_group_id)")
    op.execute(
        "INSERT INTO user_scope (user_id, organization_id) "
        "SELECT u.id, o.id FROM user_account u CROSS JOIN organization o WHERE u.role = 'editor'"
    )


def downgrade() -> None:
    for t in CONTENT_TABLES:
        op.execute(f"DROP INDEX IF EXISTS ix_{t}_owner")
        op.execute(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS ck_{t}_owner")
        op.execute(f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS fk_{t}_owner_group")
        op.execute(f"ALTER TABLE {t} DROP COLUMN owner_group_id, DROP COLUMN owner_site_id")
    op.execute(
        "ALTER TABLE user_account DROP CONSTRAINT ck_user_account_role_source, "
        "DROP COLUMN role_source"
    )
    op.execute("DROP TABLE user_scope")

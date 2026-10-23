"""Socle : organisation, sites, groupes, écrans, utilisateurs, config, modules, événements."""

from alembic import op

revision = "0001_socle"
down_revision = None
branch_labels = ("core",)
depends_on = None

TS = "created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()"
PK = "id uuid PRIMARY KEY DEFAULT gen_random_uuid()"

STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS citext",
    f"""CREATE TABLE organization ({PK}, name text NOT NULL,
        singleton boolean NOT NULL DEFAULT true,
        CONSTRAINT uq_organization_singleton UNIQUE (singleton),
        CONSTRAINT ck_organization_singleton CHECK (singleton), {TS})""",
    f"""CREATE TABLE site ({PK},
        organization_id uuid NOT NULL REFERENCES organization(id),
        name text NOT NULL, slug text NOT NULL,
        archived_at timestamptz, {TS},
        CONSTRAINT uq_site_slug UNIQUE (slug))""",
    f"""CREATE TABLE screen_group ({PK},
        site_id uuid NOT NULL REFERENCES site(id) ON DELETE RESTRICT,
        name text NOT NULL, {TS},
        CONSTRAINT uq_screen_group_site_id_name UNIQUE (site_id, name),
        CONSTRAINT uq_screen_group_id_site_id UNIQUE (id, site_id))""",
    f"""CREATE TABLE screen ({PK},
        site_id uuid NOT NULL REFERENCES site(id),
        group_id uuid, name text NOT NULL,
        orientation text NOT NULL DEFAULT 'landscape',
        archived_at timestamptz, {TS},
        CONSTRAINT ck_screen_orientation CHECK (orientation IN ('landscape','portrait')),
        CONSTRAINT fk_screen_group_id_site_id FOREIGN KEY (group_id, site_id)
            REFERENCES screen_group(id, site_id))""",
    "CREATE INDEX ix_screen_site_id ON screen (site_id)",
    "CREATE INDEX ix_screen_group_id ON screen (group_id)",
    f"""CREATE TABLE user_account ({PK},
        auth_provider text NOT NULL, external_id text NOT NULL,
        email citext, display_name text NOT NULL,
        role text NOT NULL, locale text,
        is_active boolean NOT NULL DEFAULT true,
        last_seen_at timestamptz, {TS},
        CONSTRAINT uq_user_account_auth_provider_external_id UNIQUE (auth_provider, external_id),
        CONSTRAINT ck_user_account_role CHECK (role IN ('admin','editor')))""",
    "CREATE INDEX ix_user_account_email ON user_account (email)",
    f"""CREATE TABLE config_override ({PK},
        organization_id uuid REFERENCES organization(id) ON DELETE CASCADE,
        site_id uuid REFERENCES site(id) ON DELETE CASCADE,
        group_id uuid REFERENCES screen_group(id) ON DELETE CASCADE,
        screen_id uuid REFERENCES screen(id) ON DELETE CASCADE,
        scope_level integer GENERATED ALWAYS AS (
            CASE WHEN screen_id IS NOT NULL THEN 3 WHEN group_id IS NOT NULL THEN 2
                 WHEN site_id IS NOT NULL THEN 1 ELSE 0 END) STORED,
        scope_id uuid GENERATED ALWAYS AS (
            COALESCE(screen_id, group_id, site_id, organization_id)) STORED,
        namespace text NOT NULL, key text NOT NULL, value jsonb NOT NULL,
        updated_by uuid REFERENCES user_account(id) ON DELETE SET NULL, {TS},
        CONSTRAINT ck_config_override_one_scope
            CHECK (num_nonnulls(organization_id, site_id, group_id, screen_id) = 1),
        CONSTRAINT uq_config_override_scope_id_namespace_key UNIQUE (scope_id, namespace, key))""",
    "CREATE INDEX ix_config_override_namespace_key ON config_override (namespace, key)",
    f"""CREATE TABLE module (id text PRIMARY KEY, version text NOT NULL, kind text NOT NULL,
        source text NOT NULL, state text NOT NULL DEFAULT 'discovered',
        manifest jsonb NOT NULL, config_schema jsonb, checksum text,
        failure_count integer NOT NULL DEFAULT 0, last_error text,
        state_changed_at timestamptz NOT NULL DEFAULT now(), {TS},
        CONSTRAINT ck_module_kind CHECK (kind IN ('player-adapter','power-driver',
            'content-type','trigger','notifier','auth-provider','theme','storage')),
        CONSTRAINT ck_module_source CHECK (source IN ('builtin','installed')),
        CONSTRAINT ck_module_state CHECK (state IN ('discovered','enabled','disabled',
            'quarantined','incompatible')))""",
    f"""CREATE TABLE event_outbox ({PK}, name text NOT NULL, version integer NOT NULL DEFAULT 1,
        source text NOT NULL, origin text, correlation_id text,
        payload jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        occurred_at timestamptz NOT NULL DEFAULT now(), {TS})""",
    "CREATE INDEX ix_event_outbox_occurred_at ON event_outbox (occurred_at)",
    f"""CREATE TABLE event_delivery (
        event_id uuid NOT NULL REFERENCES event_outbox(id) ON DELETE CASCADE,
        subscriber text NOT NULL, status text NOT NULL DEFAULT 'pending',
        attempts integer NOT NULL DEFAULT 0, next_attempt_at timestamptz,
        last_error text, {TS},
        PRIMARY KEY (event_id, subscriber),
        CONSTRAINT ck_event_delivery_status CHECK (status IN ('pending','done','failed')))""",
    """CREATE INDEX ix_event_delivery_next_attempt_at ON event_delivery (next_attempt_at)
        WHERE status = 'pending'""",
    f"""CREATE TABLE audit_log (id bigserial PRIMARY KEY,
        occurred_at timestamptz NOT NULL DEFAULT now(),
        actor_kind text NOT NULL, actor_id text, action text NOT NULL,
        target_type text, target_id text,
        details jsonb NOT NULL DEFAULT '{{}}'::jsonb, {TS},
        CONSTRAINT ck_audit_log_actor_kind
            CHECK (actor_kind IN ('user','module','system','device')))""",
    "CREATE INDEX ix_audit_log_target ON audit_log (target_type, target_id)",
    "CREATE INDEX ix_audit_log_occurred_at ON audit_log (occurred_at)",
]

TABLES = [
    "audit_log",
    "event_delivery",
    "event_outbox",
    "module",
    "config_override",
    "user_account",
    "screen",
    "screen_group",
    "site",
    "organization",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")

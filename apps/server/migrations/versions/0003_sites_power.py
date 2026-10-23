"""Sites, agent de site, commandes distantes, alimentation, incidents (additive)."""

from alembic import op

revision = "0003_sites_power"
down_revision = "0002_diffusion"
branch_labels = None
depends_on = None

TS = "created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()"
PK = "id uuid PRIMARY KEY DEFAULT gen_random_uuid()"
USER = "uuid REFERENCES user_account(id) ON DELETE SET NULL"

STATEMENTS = [
    "ALTER TABLE site ADD COLUMN timezone text NOT NULL DEFAULT 'Europe/Paris'",
    f"""CREATE TABLE site_agent ({PK},
        site_id uuid NOT NULL REFERENCES site(id) ON DELETE CASCADE,
        name text NOT NULL, token_hash text NOT NULL, token_prefix text NOT NULL,
        version text, last_seen_at timestamptz, last_heartbeat jsonb, last_hello jsonb,
        enrolled_by {USER}, rotated_at timestamptz,
        revoked_at timestamptz, revoked_by {USER}, {TS},
        CONSTRAINT uq_site_agent_token_hash UNIQUE (token_hash))""",
    """CREATE UNIQUE INDEX uq_site_agent_active_site ON site_agent (site_id)
        WHERE revoked_at IS NULL""",
    f"""CREATE TABLE remote_command ({PK},
        target_kind text NOT NULL, target_id uuid NOT NULL, seq bigint NOT NULL,
        name text NOT NULL, screen_id uuid REFERENCES screen(id) ON DELETE CASCADE,
        args jsonb NOT NULL DEFAULT '{{}}'::jsonb, status text NOT NULL DEFAULT 'queued',
        code text, result jsonb, correlation_id uuid, created_by {USER},
        created_at timestamptz NOT NULL DEFAULT now(), sent_at timestamptz,
        done_at timestamptz, expires_at timestamptz NOT NULL,
        CONSTRAINT ck_remote_command_target_kind CHECK (target_kind IN ('agent','device')),
        CONSTRAINT ck_remote_command_status
            CHECK (status IN ('queued','sent','ok','failed','expired','rejected')),
        CONSTRAINT uq_remote_command_target_kind_target_id_seq
            UNIQUE (target_kind, target_id, seq))""",
    """CREATE INDEX ix_remote_command_pending ON remote_command (target_kind, target_id, seq)
        WHERE status IN ('queued','sent')""",
    f"""CREATE TABLE power_plan ({PK}, name text NOT NULL, description text,
        created_by {USER}, updated_by {USER}, {TS})""",
    f"""CREATE TABLE power_plan_rule ({PK},
        plan_id uuid NOT NULL REFERENCES power_plan(id) ON DELETE CASCADE,
        weekdays smallint NOT NULL, start_minute smallint NOT NULL,
        end_minute smallint NOT NULL, {TS},
        CONSTRAINT ck_power_plan_rule_weekdays CHECK (weekdays BETWEEN 1 AND 127),
        CONSTRAINT ck_power_plan_rule_range
            CHECK (start_minute >= 0 AND start_minute < end_minute AND end_minute <= 1440))""",
    "CREATE INDEX ix_power_plan_rule_plan_id ON power_plan_rule (plan_id)",
    f"""CREATE TABLE power_exception ({PK},
        scope_level text NOT NULL, scope_id uuid,
        starts_on date NOT NULL, ends_on date NOT NULL,
        start_minute smallint, end_minute smallint,
        state text NOT NULL, label text NOT NULL,
        created_by {USER}, updated_by {USER}, {TS},
        CONSTRAINT ck_power_exception_scope_level
            CHECK (scope_level IN ('organization','site','group','screen')),
        CONSTRAINT ck_power_exception_period CHECK (starts_on <= ends_on),
        CONSTRAINT ck_power_exception_state CHECK (state IN ('on','off')))""",
    """CREATE INDEX ix_power_exception_scope
        ON power_exception (scope_level, scope_id, starts_on, ends_on)""",
    f"""CREATE TABLE power_calendar ({PK}, key text NOT NULL, module_id text NOT NULL,
        name jsonb NOT NULL, refreshed_at timestamptz, {TS},
        CONSTRAINT uq_power_calendar_key UNIQUE (key))""",
    """CREATE TABLE power_calendar_day (
        calendar_id uuid NOT NULL REFERENCES power_calendar(id) ON DELETE CASCADE,
        day date NOT NULL, label text NOT NULL,
        PRIMARY KEY (calendar_id, day))""",
    """CREATE TABLE power_state (
        screen_id uuid PRIMARY KEY REFERENCES screen(id) ON DELETE CASCADE,
        desired text NOT NULL, desired_reason text NOT NULL,
        desired_since timestamptz NOT NULL, observed text NOT NULL DEFAULT 'unknown',
        observed_at timestamptz, driver text, verified boolean NOT NULL DEFAULT false,
        last_code text, updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_power_state_desired CHECK (desired IN ('on','off')),
        CONSTRAINT ck_power_state_observed
            CHECK (observed IN ('on','off','standby','unknown')))""",
    f"""CREATE TABLE power_action ({PK},
        screen_id uuid NOT NULL REFERENCES screen(id) ON DELETE CASCADE,
        request_id uuid, state text NOT NULL, reason text NOT NULL, origin text NOT NULL,
        driver text, ok boolean NOT NULL, verified boolean NOT NULL, code text,
        at timestamptz NOT NULL)""",
    "CREATE INDEX ix_power_action_screen_at ON power_action (screen_id, at DESC)",
    f"""CREATE TABLE incident ({PK}, kind text NOT NULL, severity text NOT NULL,
        subject_type text NOT NULL, subject_id text NOT NULL,
        site_id uuid REFERENCES site(id) ON DELETE CASCADE,
        details jsonb NOT NULL DEFAULT '{{}}'::jsonb,
        opened_at timestamptz NOT NULL, resolved_at timestamptz,
        CONSTRAINT ck_incident_severity CHECK (severity IN ('info','warning','critical')),
        CONSTRAINT ck_incident_subject_type
            CHECK (subject_type IN ('screen','agent','module','site')))""",
    """CREATE UNIQUE INDEX uq_incident_open ON incident (kind, subject_type, subject_id)
        WHERE resolved_at IS NULL""",
]

TABLES = [
    "incident",
    "power_action",
    "power_state",
    "power_calendar_day",
    "power_calendar",
    "power_exception",
    "power_plan_rule",
    "power_plan",
    "remote_command",
    "site_agent",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("ALTER TABLE site DROP COLUMN IF EXISTS timezone")

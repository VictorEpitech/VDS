"""Découverte LAN des écrans : scans et appareils repérés (additive : deux tables).

Numérotée 0010 (la 0009 est `remote_command_keep_seq`). Un seul scan actif par site (index
partiel unique) ; les appareils ne sont conservés que 24 h (purge applicative).
"""

from alembic import op

revision = "0010_discovery_scan"
down_revision = "0009_remote_command_keep_seq"
branch_labels = None
depends_on = None

STATEMENTS = [
    """CREATE TABLE discovery_scan (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        site_id uuid NOT NULL REFERENCES site(id) ON DELETE CASCADE,
        agent_id uuid REFERENCES site_agent(id) ON DELETE SET NULL,
        requested_by uuid REFERENCES user_account(id) ON DELETE SET NULL,
        status text NOT NULL,
        probe_ports boolean NOT NULL,
        error_code text,
        networks cidr[] NOT NULL DEFAULT '{}',
        truncated boolean NOT NULL DEFAULT false,
        capped boolean NOT NULL DEFAULT false,
        cmd_id uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        finished_at timestamptz,
        CONSTRAINT ck_discovery_scan_status
            CHECK (status IN ('pending','running','completed','failed')))""",
    "CREATE UNIQUE INDEX ux_discovery_scan_active ON discovery_scan (site_id)"
    " WHERE status IN ('pending','running')",
    "CREATE INDEX ix_discovery_scan_site_created ON discovery_scan (site_id, created_at DESC)",
    "CREATE INDEX ix_discovery_scan_cmd_id ON discovery_scan (cmd_id)",
    """CREATE TABLE discovery_device (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        scan_id uuid NOT NULL REFERENCES discovery_scan(id) ON DELETE CASCADE,
        ip inet NOT NULL,
        mac macaddr,
        vendor text,
        model text,
        name text,
        platform text NOT NULL,
        protocols text[] NOT NULL DEFAULT '{}',
        suggested_drivers text[] NOT NULL DEFAULT '{}',
        confidence text NOT NULL,
        CONSTRAINT uq_discovery_device_scan_ip UNIQUE (scan_id, ip),
        CONSTRAINT ck_discovery_device_text CHECK (
            char_length(vendor) <= 128 AND char_length(model) <= 128
            AND char_length(name) <= 128))""",
]


KINDS = (
    "'player-adapter','power-driver','content-type','trigger','notifier','auth-provider',"
    "'theme','storage'"
)


def _module_kind(kinds: str) -> None:
    op.execute("ALTER TABLE module DROP CONSTRAINT ck_module_kind")
    op.execute(f"ALTER TABLE module ADD CONSTRAINT ck_module_kind CHECK (kind IN ({kinds}))")


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)
    _module_kind(KINDS + ",'discovery'")  # nouveau kind de module (vds-core 1.5)


def downgrade() -> None:
    op.execute("DELETE FROM module WHERE kind = 'discovery'")
    _module_kind(KINDS)
    op.execute("DROP TABLE IF EXISTS discovery_device")
    op.execute("DROP TABLE IF EXISTS discovery_scan")

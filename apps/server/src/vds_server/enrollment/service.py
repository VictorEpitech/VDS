"""Service d'enrôlement : création du code, révocation, consommation par l'agent.

Le code (`vds_enr_…`, 128 bits) n'est jamais stocké, journalisé, ni mis dans un événement ou un
audit : seuls son sha256 et l'identifiant de l'inscription circulent.
"""

import hashlib
import ipaddress
import secrets
import shlex
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from vds_sdk import Event, EventBus
from vds_sdk.agent.enroll import (
    ENROLL_CODE_PREFIX,
    AgentEnrolled,
    AgentEnrollmentCreated,
    AgentEnrollmentRevoked,
    EnrollRequest,
)

from vds_server.agents.auth import lookup, new_token, token_prefix
from vds_server.agents.queue import notify
from vds_server.core import audit
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import Site
from vds_server.db.models_enrollment import AgentEnrollment
from vds_server.db.models_power import SiteAgent
from vds_server.enrollment import schemas as sc
from vds_server.player.device_auth import hash_secret
from vds_server.settings import Settings

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # base32 de Crockford
CODE_BODY_LEN = 26
REMOTE_TTL_DEFAULT_MIN = 60
LOCAL_TTL = timedelta(minutes=10)
RETENTION = timedelta(days=30)  # conservation des inscriptions expirées avant purge
HOSTNAME_NAME_MAX = 120


def new_code() -> str:
    """`vds_enr_` + 26 caractères base32 Crockford (16 octets aléatoires, 130 bits codés)."""
    n = int.from_bytes(secrets.token_bytes(16), "big") << 2
    body = "".join(ALPHABET[(n >> (5 * i)) & 31] for i in reversed(range(CODE_BODY_LEN)))
    return ENROLL_CODE_PREFIX + body


def hash_code(code: str) -> bytes:
    return hashlib.sha256(code.encode()).digest()


def enrollment_state(row: AgentEnrollment, now: datetime | None = None) -> sc.EnrollmentState:
    if row.revoked_at is not None:
        return "revoked"
    if row.consumed_at is not None:
        return "consumed"
    return "expired" if row.expires_at <= (now or datetime.now(UTC)) else "pending"


def to_out(row: AgentEnrollment) -> sc.EnrollmentOut:
    return sc.EnrollmentOut(
        id=row.id,
        channel=row.channel,  # type: ignore[arg-type]
        created_at=row.created_at,
        expires_at=row.expires_at,
        state=enrollment_state(row),
        consumed_ip=str(row.consumed_ip) if row.consumed_ip is not None else None,
        consumed_hostname=row.consumed_hostname,
        agent_id=row.agent_id,
    )


def _event(name: str, payload: dict[str, Any]) -> Event:
    return Event(name=name, payload=payload, source="core")


def _ip(raw: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address((raw or "").split("%")[0]))
    except ValueError:
        return None


class EnrollmentService:
    def __init__(self, session: AsyncSession, bus: EventBus | None, settings: Settings) -> None:
        self._s = session
        self._bus = bus
        self._settings = settings

    async def _publish(self, events: list[Event]) -> None:
        if self._bus is not None:
            for event in events:
                await self._bus.publish(event)

    async def _site(self, site_id: UUID) -> Site:
        site = await self._s.get(Site, site_id)
        if site is None:
            raise ApiError("site.not_found", 404)
        return site

    # --- côté admin ----------------------------------------------------------
    async def create(
        self,
        site_id: UUID,
        actor: Principal,
        *,
        channel: sc.Channel = "remote",
        ttl_min: int = REMOTE_TTL_DEFAULT_MIN,
    ) -> tuple[AgentEnrollment, str]:
        """Crée une inscription et révoque les précédentes en attente du site (`superseded`)."""
        site = await self._site(site_id)
        if site.archived_at is not None:
            raise ApiError("site.archived", 409)
        now = datetime.now(UTC)
        ttl = LOCAL_TTL if channel == "local" else timedelta(minutes=ttl_min)
        await self._s.execute(
            delete(AgentEnrollment).where(AgentEnrollment.expires_at < now - RETENTION)
        )
        events: list[Event] = []
        pending = (
            await self._s.execute(
                select(AgentEnrollment)
                .where(
                    AgentEnrollment.site_id == site_id,
                    AgentEnrollment.consumed_at.is_(None),
                    AgentEnrollment.revoked_at.is_(None),
                    AgentEnrollment.expires_at > now,
                )
                .with_for_update()
            )
        ).scalars()
        for old in pending:
            old.revoked_at = now
            events.append(self._revoked(old, "superseded", actor))
        code = new_code()
        row = AgentEnrollment(
            site_id=site_id,
            channel=channel,
            code_sha256=hash_code(code),
            created_by=actor.user_id,
            expires_at=now + ttl,
        )
        self._s.add(row)
        await self._s.flush()
        audit.record(
            self._s,
            actor_kind="user",
            actor_id=actor.actor_id,
            action="agent.enrollment.created",
            target_type="agent_enrollment",
            target_id=str(row.id),
            details={"site_id": str(site_id), "channel": channel},
        )
        await self._s.commit()
        events.append(
            _event(
                "agent.enrollment_created",
                AgentEnrollmentCreated(
                    enrollment_id=row.id,
                    site_id=site_id,
                    channel=channel,
                    expires_at=row.expires_at,
                    created_by=actor.user_id,
                ).model_dump(mode="json"),
            )
        )
        await self._publish(events)
        return row, code

    def _revoked(self, row: AgentEnrollment, reason: str, actor: Principal) -> Event:
        audit.record(
            self._s,
            actor_kind="user",
            actor_id=actor.actor_id,
            action="agent.enrollment.revoked",
            target_type="agent_enrollment",
            target_id=str(row.id),
            details={"site_id": str(row.site_id), "reason": reason},
        )
        payload = AgentEnrollmentRevoked(
            enrollment_id=row.id,
            site_id=row.site_id,
            reason=reason,  # type: ignore[arg-type]
        )
        return _event("agent.enrollment_revoked", payload.model_dump(mode="json"))

    async def list_for_site(self, site_id: UUID) -> list[AgentEnrollment]:
        await self._site(site_id)
        stmt = (
            select(AgentEnrollment)
            .where(AgentEnrollment.site_id == site_id)
            .order_by(AgentEnrollment.created_at.desc())
            .limit(50)
        )
        return list((await self._s.execute(stmt)).scalars())

    async def revoke(self, site_id: UUID, enrollment_id: UUID, actor: Principal) -> None:
        row = (
            await self._s.execute(
                select(AgentEnrollment)
                .where(AgentEnrollment.id == enrollment_id, AgentEnrollment.site_id == site_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise ApiError("enroll.not_found", 404)
        if row.consumed_at is not None:
            raise ApiError("enroll.consumed", 409)
        if row.revoked_at is not None:
            return  # idempotent
        row.revoked_at = datetime.now(UTC)
        event = self._revoked(row, "admin", actor)
        await self._s.commit()
        await self._publish([event])

    def created_view(self, row: AgentEnrollment, code: str) -> sc.EnrollmentCreated:
        """Réponse de création distante : code, commandes et fichier à copier (une seule fois)."""
        st = self._settings
        if not st.public_origin:
            raise ApiError("agent.public_origin_missing", 409)
        server_url = st.public_origin.rstrip("/") + st.prefix
        image = st.agent_image or f"vds-agent:{st.version}"
        env = f"VDS_SERVER_URL={server_url}\nVDS_ENROLL_CODE={code}\n"
        run = " ".join(
            [
                "docker run -d --name vds-agent --restart unless-stopped --network host",
                "-v vds-agent-data:/data/agent -e VDS_AGENT_DATA_DIR=/data/agent",
                f"-e VDS_SERVER_URL={shlex.quote(server_url)}",
                f"-e VDS_ENROLL_CODE={shlex.quote(code)}",
                shlex.quote(image),
            ]
        )
        login = None
        if st.agent_pull_user and st.agent_pull_token:
            token = st.agent_pull_token.get_secret_value()
            login = (
                f"echo {shlex.quote(token)} | docker login ghcr.io"
                f" -u {shlex.quote(st.agent_pull_user)} --password-stdin"
            )
        return sc.EnrollmentCreated(
            id=row.id,
            code=code,
            expires_at=row.expires_at,
            server_url=server_url,
            image=image,
            commands=sc.EnrollmentCommands(docker_run=run, docker_login=login),
            files=sc.EnrollmentFiles(agent_env=env),
        )

    # --- côté agent (public) -------------------------------------------------
    async def consume(self, req: EnrollRequest, client_host: str | None) -> sc.EnrollOut:
        now = datetime.now(UTC)
        row = (
            await self._s.execute(
                select(AgentEnrollment)
                .where(AgentEnrollment.code_sha256 == hash_code(req.code))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or row.revoked_at is not None:
            raise ApiError("enroll.invalid", 401)  # même réponse : inconnu ou révoqué
        if row.expires_at <= now:
            raise ApiError("enroll.expired", 410)
        if row.consumed_at is not None:
            raise ApiError("enroll.consumed", 409)
        site = await self._site(row.site_id)
        if site.archived_at is not None:
            raise ApiError("site.archived", 409)

        events: list[Event] = []
        moved_from = await self._revoke_previous(req, row.site_id, now, events)
        token = new_token()
        agent = (
            await self._s.execute(
                select(SiteAgent)
                .where(SiteAgent.site_id == row.site_id, SiteAgent.revoked_at.is_(None))
                .with_for_update()
            )
        ).scalar_one_or_none()
        replaced = agent is not None
        if agent is None:
            agent = SiteAgent(
                site_id=row.site_id,
                name=req.hostname[:HOSTNAME_NAME_MAX],
                token_hash=hash_secret(token),
                token_prefix=token_prefix(token),
                enrolled_by=row.created_by,
            )
            self._s.add(agent)
        else:
            agent.token_hash, agent.token_prefix = hash_secret(token), token_prefix(token)
            agent.rotated_at = now
            events.append(
                _event(
                    "agent.rotated",
                    {"agent_id": str(agent.id), "site_id": str(agent.site_id)},
                )
            )
        try:
            await self._s.flush()
        except IntegrityError as exc:
            await self._s.rollback()
            raise ApiError("agents.already_active", 409) from exc
        if replaced:
            await notify(self._s, "agent", agent.id)  # ferme l'ancienne session en 4411
        row.consumed_at = now
        row.consumed_ip = _ip(client_host)
        row.consumed_hostname = req.hostname
        row.agent_id = agent.id
        audit.record(
            self._s,
            actor_kind="system",
            actor_id=str(agent.id),
            action="agent.enrollment.consumed",
            target_type="agent_enrollment",
            target_id=str(row.id),
            details={
                "site_id": str(row.site_id),
                "channel": row.channel,
                "ip": row.consumed_ip,
                "hostname": req.hostname,
                "replaced": replaced,
                "moved_from_site_id": str(moved_from) if moved_from else None,
            },
        )
        await self._s.commit()
        events.append(
            _event(
                "agent.enrolled",
                AgentEnrolled(
                    agent_id=agent.id,
                    site_id=row.site_id,
                    enrollment_id=row.id,
                    channel=row.channel,  # type: ignore[arg-type]
                    hostname=req.hostname,
                    platform=req.platform,
                    agent_version=req.agent_version,
                    replaced=replaced,
                    moved_from_site_id=moved_from,
                ).model_dump(mode="json"),
            )
        )
        await self._publish(events)
        return sc.EnrollOut(agent_id=agent.id, site_id=row.site_id, token=token)

    async def _revoke_previous(
        self, req: EnrollRequest, target_site: UUID, now: datetime, events: list[Event]
    ) -> UUID | None:
        """Agent déjà enrôlé qui change de site : ancien jeton révoqué (retourne l'ancien site)."""
        if req.previous_token is None:
            return None
        prev = await lookup(self._s, req.previous_token.get_secret_value())
        if prev is None or prev.revoked_at is not None or prev.site_id == target_site:
            return None
        prev.revoked_at = now
        await notify(self._s, "agent", prev.id)  # ferme sa session (4410)
        audit.record(
            self._s,
            actor_kind="system",
            actor_id=str(prev.id),
            action="agent.revoked",
            target_type="site_agent",
            target_id=str(prev.id),
            details={"site_id": str(prev.site_id), "reason": "moved"},
        )
        events.append(
            _event("agent.revoked", {"agent_id": str(prev.id), "site_id": str(prev.site_id)})
        )
        return prev.site_id

"""Calcul des étapes (lecture seule) et upsert de l'état (ignorées, carte masquée)."""

from datetime import UTC, datetime
from typing import Any, get_args
from uuid import UUID

from sqlalchemy import exists, func, or_, select, true
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from vds_server.core import audit
from vds_server.core.auth import Principal
from vds_server.core.errors import ApiError
from vds_server.db.models import (
    Media,
    Organization,
    PairingRequest,
    Playlist,
    PlaylistItem,
    Schedule,
    Screen,
    ScreenDevice,
    Site,
    UserAccount,
)
from vds_server.db.models_onboarding import OnboardingState
from vds_server.db.models_power import PowerException, PowerPlanRule, SiteAgent
from vds_server.diffusion.scope import PerimeterScope
from vds_server.onboarding.schemas import (
    CardOut,
    OnboardingOut,
    ProgressOut,
    StepId,
    StepOut,
    StepStatus,
)
from vds_server.supervision.status import Thresholds

STEPS: tuple[str, ...] = get_args(StepId)
OPTIONAL = frozenset({"power"})
NOT_SKIPPABLE = frozenset({"account"})


async def _count(session: AsyncSession, stmt: Any) -> int:
    return int((await session.execute(stmt)).scalar_one())


def _covered_screens() -> Any:
    """Écrans non archivés couverts par une programmation (cible = org, site, groupe, écran)."""
    return (
        select(Screen.id)
        .where(
            Screen.archived_at.is_(None),
            or_(
                Schedule.target_type == "organization",
                (Schedule.target_type == "site") & (Schedule.target_id == Screen.site_id),
                (Schedule.target_type == "group") & (Schedule.target_id == Screen.group_id),
                (Schedule.target_type == "screen") & (Schedule.target_id == Screen.id),
            ),
        )
        .correlate(Schedule)
    )


async def _organization(session: AsyncSession) -> Organization:
    # Hypothèse : une seule organisation par instance (singleton, cf. docs/architecture.md).
    # Si le multi-organisation arrive, résoudre l'organisation depuis le Principal, pas ici.
    org = (await session.execute(select(Organization))).scalars().first()
    if org is None:
        raise ApiError("onboarding.organization_missing", 409)
    return org


async def compute(
    session: AsyncSession,
    org_id: UUID,
    th: Thresholds,
    now: datetime,
    actor: Principal | None = None,
) -> OnboardingOut:
    """`actor` : les comptes d'un éditeur ne portent que sur son périmètre (lot J)."""
    state = await session.get(OnboardingState, org_id)  # absence de ligne = défauts
    skipped = set(state.skipped_steps) if state else set()
    scope = PerimeterScope(session)
    live_screen: Any = Screen.archived_at.is_(None)
    site_vis: Any = true()
    media_vis: Any = true()
    playlist_vis: Any = true()
    sched_vis: Any = true()
    if actor is not None:
        live_screen = live_screen & scope.screen_filter(actor)
        site_vis = scope.site_filter(actor)
        media_vis = scope.media_filter(actor)
        playlist_vis = scope.playlist_filter(actor)
        sched_vis = scope.target_filter(actor, Schedule.target_type, Schedule.target_id, "read")

    admins = await _count(
        session,
        select(func.count()).where(UserAccount.role == "admin", UserAccount.is_active.is_(True)),
    )
    sites = await _count(session, select(func.count()).where(Site.archived_at.is_(None), site_vis))
    agents = (
        await session.execute(
            select(SiteAgent.last_seen_at)
            .join(Site, Site.id == SiteAgent.site_id)
            .where(SiteAgent.revoked_at.is_(None), Site.archived_at.is_(None))
        )
    ).scalars()
    agent_connected = any(
        t is not None and (now - t).total_seconds() <= th.agent_offline_after_s for t in agents
    )
    paired = await _count(
        session,
        select(func.count(Screen.id.distinct()))
        .join(ScreenDevice, ScreenDevice.screen_id == Screen.id)
        .where(live_screen, ScreenDevice.revoked_at.is_(None)),
    )
    screens = await _count(session, select(func.count()).where(live_screen).select_from(Screen))
    pending = await _count(
        session,
        select(func.count()).where(
            PairingRequest.status == "pending", PairingRequest.expires_at > now
        ),
    )
    media = await _count(session, select(func.count()).select_from(Media).where(media_vis))
    playlists = await _count(
        session, select(func.count()).select_from(Playlist).where(playlist_vis)
    )
    filled = await _count(
        session,
        select(func.count())
        .select_from(Playlist)
        .where(exists().where(PlaylistItem.playlist_id == Playlist.id), playlist_vis),
    )
    schedules = await _count(session, select(func.count()).select_from(Schedule).where(sched_vis))
    covering = await _count(
        session,
        select(func.count())
        .select_from(Schedule)
        .where(exists(_covered_screens()), sched_vis),
    )
    power = await _count(session, select(func.count()).select_from(PowerPlanRule)) + await _count(
        session, select(func.count()).select_from(PowerException)
    )

    raw: dict[str, tuple[StepStatus, int, dict[str, int | bool]]] = {
        "account": ("done" if admins else "todo", admins, {}),
        "site": ("done" if sites else "todo", sites, {"agent_connected": agent_connected}),
        "screen": (
            "done" if paired else "in_progress" if (screens or pending) else "todo",
            paired,
            {"pending_pairing": pending} if pending else {},
        ),
        "media": ("done" if media else "todo", media, {}),
        "playlist": (
            "done" if filled else "in_progress" if playlists else "todo",
            filled,
            {},
        ),
        "schedule": (
            "done" if covering else "in_progress" if schedules else "todo",
            covering,
            {},
        ),
        "power": ("done" if power else "todo", power, {}),
    }
    steps: list[StepOut] = []
    for sid in STEPS:
        status, count, details = raw[sid]
        if status != "done" and sid in skipped and sid not in NOT_SKIPPABLE:
            status = "skipped"
        steps.append(
            StepOut(
                step=sid,  # type: ignore[arg-type]
                status=status,
                optional=sid in OPTIONAL,
                skippable=sid not in NOT_SKIPPABLE,
                count=count,
                details=details,
            )
        )
    required = [s for s in steps if not s.optional]
    resolved = [s for s in required if s.status in ("done", "skipped")]
    pending_steps = [s for s in steps if s.status not in ("done", "skipped")]
    next_step = next((s.step for s in pending_steps if not s.optional), None) or next(
        (s.step for s in pending_steps), None
    )
    hidden_at = state.card_hidden_at if state else None
    return OnboardingOut(
        steps=steps,
        progress=ProgressOut(completed=len(resolved), total=len(required)),
        next_step=next_step,
        complete=len(resolved) == len(required),
        card=CardOut(hidden=hidden_at is not None, hidden_at=hidden_at),
    )


async def _upsert(session: AsyncSession, org_id: UUID, actor: Principal, **values: Any) -> None:
    values = {"updated_by": actor.user_id, "updated_at": datetime.now(UTC)} | values
    await session.execute(
        pg_insert(OnboardingState)
        .values(organization_id=org_id, **values)
        .on_conflict_do_update(index_elements=["organization_id"], set_=values)
    )


async def set_step_skipped(
    session: AsyncSession, step: str, skipped: bool, actor: Principal
) -> UUID:
    if step not in STEPS:
        raise ApiError("onboarding.step_unknown", 404)
    if skipped and step in NOT_SKIPPABLE:
        raise ApiError("onboarding.step_not_skippable", 409)
    org = await _organization(session)
    # Lecture-modification-écriture atomique : la ligne existe (créée sans effet si absente),
    # puis verrouillée jusqu'au commit, sinon deux PUT concurrents s'écraseraient.
    await session.execute(
        pg_insert(OnboardingState)
        .values(organization_id=org.id)
        .on_conflict_do_nothing(index_elements=["organization_id"])
    )
    state = (
        await session.execute(
            select(OnboardingState)
            .where(OnboardingState.organization_id == org.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    current = [s for s in state.skipped_steps if s in STEPS]
    wanted = sorted({*current, step} if skipped else set(current) - {step}, key=STEPS.index)
    if wanted != current:
        await _upsert(session, org.id, actor, skipped_steps=wanted)
        audit.record(
            session,
            actor_kind="user",
            actor_id=actor.actor_id,
            action="onboarding.step_skipped" if skipped else "onboarding.step_reopened",
            target_type="onboarding",
            target_id=str(org.id),
            details={"step": step},
        )
    return org.id


async def set_card_hidden(session: AsyncSession, hidden: bool, actor: Principal) -> UUID:
    org = await _organization(session)
    state = await session.get(OnboardingState, org.id)
    already = state is not None and state.card_hidden_at is not None
    if hidden != already:
        await _upsert(session, org.id, actor, card_hidden_at=datetime.now(UTC) if hidden else None)
        audit.record(
            session,
            actor_kind="user",
            actor_id=actor.actor_id,
            action="onboarding.card_hidden" if hidden else "onboarding.card_shown",
            target_type="onboarding",
            target_id=str(org.id),
        )
    return org.id


async def organization_id(session: AsyncSession) -> UUID:
    return (await _organization(session)).id

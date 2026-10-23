"""auth.local : comptes VDS, mots de passe argon2id, sessions par cookie.

Le module ne dépend que de `vds_sdk` et de la façade `ctx.core` (base, http, auth, users, audit).
"""

import asyncio
import hashlib
import secrets
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from vds_sdk import ModuleContext
from vds_sdk.server import AuthProvider, AuthRequest, Identity

PROVIDER = "auth.local"
COOKIE = "vds_session"
_HASHER = PasswordHasher()
_DUMMY_HASH = _HASHER.hash("dummy-password-for-timing")  # égalise le coût si l'e-mail est inconnu
_BOOTSTRAP_LOCK = 748_113_001
WINDOW_MIN = 15
MAX_PER_IDENTIFIER = 5
MAX_PER_IP = 20


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


EMAIL = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+$")
PASSWORD = Field(min_length=1, max_length=256)


class BootstrapIn(_In):
    email: str = EMAIL
    display_name: str = Field(min_length=1, max_length=120)
    password: str = PASSWORD


class LoginIn(_In):
    email: str = EMAIL
    password: str = PASSWORD
    return_to: str | None = Field(default=None, max_length=2048)


class UserIn(_In):
    email: str = EMAIL
    display_name: str = Field(min_length=1, max_length=120)
    password: str = PASSWORD
    role: str = Field(pattern=r"^(admin|editor)$")


class PasswordIn(_In):
    current: str = PASSWORD
    new: str = PASSWORD


class StatusOut(BaseModel):
    initialized: bool


class LoginOut(BaseModel):
    redirect_to: str


class UserOut(BaseModel):
    id: UUID


def _err(code: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "params": {}})


def _too_many(retry_after: int) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"code": "errors.auth.too_many_attempts", "params": {}},
        headers={"Retry-After": str(max(1, retry_after))},
    )


async def _retry_after(s: Any, kind: str, key: str, limit: int) -> int | None:
    """Secondes avant la fin du blocage si `limit` échecs sont dans la fenêtre, sinon None."""
    rows = (
        await s.execute(
            text(
                "SELECT ceil(extract(epoch FROM created_at + make_interval(mins => :w) - now()))"
                " FROM m_auth_local_login_attempt"
                " WHERE kind = :k AND key = :key"
                " AND created_at > now() - make_interval(mins => :w)"
                " ORDER BY created_at DESC LIMIT :lim"
            ),
            {"k": kind, "key": key, "w": WINDOW_MIN, "lim": limit},
        )
    ).all()
    if len(rows) < limit:
        return None
    return int(rows[-1][0])  # l'échec le plus ancien parmi les `limit` derniers libère le slot


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _hash(password: str) -> str:
    return await asyncio.to_thread(_HASHER.hash, password)


async def _verify(hashed: str, password: str) -> bool:
    def run() -> bool:
        try:
            return _HASHER.verify(hashed, password)
        except (VerificationError, InvalidHashError):
            return False

    return await asyncio.to_thread(run)


class AuthLocal(AuthProvider):
    ctx: Any

    async def setup(self, ctx: ModuleContext) -> None:
        self.ctx = ctx

    # --- interface AuthProvider -----------------------------------------
    async def authenticate(self, request: AuthRequest) -> Identity | None:
        token = request.cookies.get(COOKIE)
        if not token:
            return None
        digest = _digest(token)
        async with self.ctx.core.db.session() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT u.external_id, u.email, u.display_name"
                        " FROM m_auth_local_session m JOIN user_account u ON u.id = m.user_id"
                        " WHERE m.token_hash = :h AND m.revoked_at IS NULL"
                        " AND m.expires_at > now() AND u.auth_provider = :p"
                    ),
                    {"h": digest, "p": PROVIDER},
                )
            ).first()
            if row is None:
                return None
            await s.execute(
                text(
                    "UPDATE m_auth_local_session SET last_seen_at = now()"
                    " WHERE token_hash = :h AND last_seen_at < now() - interval '60 seconds'"
                ),
                {"h": digest},
            )
            await s.commit()
        return Identity(
            provider=PROVIDER, external_id=row[0], email=row[1], display_name=row[2], role=None
        )

    def login_url(self, return_to: str) -> str:
        return f"{self.ctx.core.http.url('/login')}?return_to={quote(return_to, safe='')}"

    def logout_url(self) -> str:
        return self.ctx.core.http.url(f"/api/v1/m/{PROVIDER}/logout")

    # --- logique ---------------------------------------------------------
    async def _settings(self) -> tuple[int, int]:
        cfg = await self.ctx.config.resolve()
        return int(cfg.get("session_ttl_hours", 12)), int(cfg.get("password_min_length", 12))

    async def _open_session(self, s: Any, user_id: UUID, response: Response) -> None:
        ttl_hours, _ = await self._settings()
        token = secrets.token_urlsafe(32)
        await s.execute(
            text(
                "INSERT INTO m_auth_local_session (user_id, token_hash, expires_at)"
                " VALUES (:u, :h, now() + make_interval(hours => :ttl))"
            ),
            {"u": user_id, "h": _digest(token), "ttl": ttl_hours},
        )
        self.ctx.core.http.set_cookie(response, COOKIE, token, max_age=ttl_hours * 3600)

    async def _create_user(
        self, s: Any, email: str, display_name: str, password: str, role: Any, must_change: bool
    ) -> UUID:
        taken = (
            await s.execute(text("SELECT 1 FROM user_account WHERE email = :e"), {"e": email})
        ).first()
        if taken:
            raise ValueError("auth_local.email_taken")
        identity = Identity(
            provider=PROVIDER, external_id=str(uuid4()), email=email, display_name=display_name
        )
        user_id: UUID = await self.ctx.core.users.provision(s, identity, role)
        await s.execute(
            text(
                "INSERT INTO m_auth_local_credential (user_id, password_hash, must_change)"
                " VALUES (:u, :p, :m)"
            ),
            {"u": user_id, "p": await _hash(password), "m": must_change},
        )
        return user_id

    def routes(self) -> APIRouter:
        router = APIRouter(tags=["auth.local"])
        core = self.ctx.core
        admin = core.auth.require("admin")
        principal = core.auth.principal

        @router.get("/status", response_model=StatusOut)
        async def status() -> StatusOut:
            async with core.db.session() as s:
                n = (await s.execute(text("SELECT count(*) FROM m_auth_local_credential"))).scalar()
            return StatusOut(initialized=bool(n))

        @router.post("/bootstrap", status_code=201, response_model=LoginOut)
        async def bootstrap(body: BootstrapIn, response: Response) -> Any:
            _, min_len = await self._settings()
            if len(body.password) < min_len:
                return _err("auth_local.password_too_short", 422)
            async with core.db.session() as s:
                await s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _BOOTSTRAP_LOCK})
                n = (await s.execute(text("SELECT count(*) FROM m_auth_local_credential"))).scalar()
                if n:
                    return _err("auth_local.already_initialized", 409)
                user_id = await self._create_user(
                    s, body.email, body.display_name, body.password, "admin", False
                )
                core.audit.record(
                    s,
                    action="auth.bootstrap",
                    actor_id=str(user_id),
                    target_type="user",
                    target_id=str(user_id),
                )
                await self._open_session(s, user_id, response)
                await s.commit()
            return LoginOut(redirect_to=core.http.safe_return_to(None))

        @router.post("/login", response_model=LoginOut)
        async def login(body: LoginIn, request: Request, response: Response) -> Any:
            ident = body.email.strip().lower()
            ip = core.http.client_ip(request)
            async with core.db.session() as s:
                waits = [
                    w
                    for w in (
                        await _retry_after(s, "identifier", ident, MAX_PER_IDENTIFIER),
                        await _retry_after(s, "ip", ip, MAX_PER_IP),
                    )
                    if w is not None
                ]
                if waits:
                    return _too_many(max(waits))
                row = (
                    await s.execute(
                        text(
                            "SELECT u.id, u.is_active, c.password_hash, c.must_change"
                            " FROM user_account u"
                            " JOIN m_auth_local_credential c ON c.user_id = u.id"
                            " WHERE u.email = :e AND u.auth_provider = :p"
                        ),
                        {"e": body.email, "p": PROVIDER},
                    )
                ).first()
                ok = await _verify(row[2] if row else _DUMMY_HASH, body.password)
                if row is None or not ok or not row[1]:
                    await s.execute(
                        text(
                            "DELETE FROM m_auth_local_login_attempt"
                            " WHERE created_at < now() - make_interval(mins => :w)"
                        ),
                        {"w": WINDOW_MIN},
                    )
                    await s.execute(
                        text(
                            "INSERT INTO m_auth_local_login_attempt (kind, key)"
                            " VALUES ('identifier', :i), ('ip', :p)"
                        ),
                        {"i": ident, "p": ip},
                    )
                    core.audit.record(
                        s, action="auth.login_failed", details={"email": body.email.lower()}
                    )
                    await s.commit()
                    return _err("auth_local.invalid_credentials", 401)
                await s.execute(
                    text(
                        "DELETE FROM m_auth_local_login_attempt"
                        " WHERE kind = 'identifier' AND key = :i"
                    ),
                    {"i": ident},
                )
                await self._open_session(s, row[0], response)
                core.audit.record(
                    s,
                    action="auth.login",
                    actor_id=str(row[0]),
                    target_type="user",
                    target_id=str(row[0]),
                )
                await s.commit()
            return LoginOut(redirect_to=core.http.safe_return_to(body.return_to))

        @router.post("/logout", status_code=204)
        async def logout(request: Request) -> Response:
            token = request.cookies.get(COOKIE)
            response = Response(status_code=204)
            if token:
                async with core.db.session() as s:
                    uid = (
                        await s.execute(
                            text(
                                "UPDATE m_auth_local_session SET revoked_at = now()"
                                " WHERE token_hash = :h AND revoked_at IS NULL RETURNING user_id"
                            ),
                            {"h": _digest(token)},
                        )
                    ).scalar()
                    if uid:
                        core.audit.record(
                            s,
                            action="auth.logout",
                            actor_id=str(uid),
                            target_type="user",
                            target_id=str(uid),
                        )
                    await s.commit()
            core.http.delete_cookie(response, COOKIE)
            return response

        @router.post("/users", status_code=201, response_model=UserOut)
        async def create_user(body: UserIn, actor: Annotated[Any, Depends(admin)]) -> Any:
            _, min_len = await self._settings()
            if len(body.password) < min_len:
                return _err("auth_local.password_too_short", 422)
            async with core.db.session() as s:
                try:
                    user_id = await self._create_user(
                        s, body.email, body.display_name, body.password, body.role, True
                    )
                except ValueError as exc:
                    return _err(str(exc), 409)
                core.audit.record(
                    s,
                    action="user.created",
                    actor_id=actor.actor_id,
                    target_type="user",
                    target_id=str(user_id),
                    details={"role": body.role},
                )
                await s.commit()
            return UserOut(id=user_id)

        @router.post("/password", status_code=204)
        async def change_password(
            body: PasswordIn, who: Annotated[Any, Depends(principal)]
        ) -> Response:
            _, min_len = await self._settings()
            if len(body.new) < min_len:
                return _err("auth_local.password_too_short", 422)  # type: ignore[return-value]
            async with core.db.session() as s:
                current = (
                    await s.execute(
                        text(
                            "SELECT password_hash FROM m_auth_local_credential WHERE user_id = :u"
                        ),
                        {"u": who.user_id},
                    )
                ).scalar()
                if current is None or not await _verify(current, body.current):
                    return _err("auth_local.invalid_credentials", 401)  # type: ignore[return-value]
                await s.execute(
                    text(
                        "UPDATE m_auth_local_credential SET password_hash = :p,"
                        " must_change = false,"
                        " updated_at = now() WHERE user_id = :u"
                    ),
                    {"p": await _hash(body.new), "u": who.user_id},
                )
                core.audit.record(
                    s,
                    action="auth.password_changed",
                    actor_id=who.actor_id,
                    target_type="user",
                    target_id=who.actor_id,
                )
                await s.commit()
            return Response(status_code=204)

        return router

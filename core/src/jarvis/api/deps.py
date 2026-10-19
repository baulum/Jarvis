"""FastAPI dependencies: app state access, authentication and role checks."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, HTTPException, Request, status

from jarvis.app_state import AppState
from jarvis.config import RISK_ORDER
from jarvis.security.auth import Principal, authenticate_token
from jarvis.security.guards import rate_limiter


def get_state(request: Request) -> AppState:
    return request.app.state.jarvis  # type: ignore[no-any-return]


async def current_principal(request: Request) -> Principal:
    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip() if auth.lower().startswith("bearer ") else None
    client = request.client.host if request.client else "?"
    rl = get_state(request).permissions.rate_limits
    if not rate_limiter.check(f"api:{client}", rl.api_requests_per_minute, 60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit")
    principal = await authenticate_token(token)
    if principal is None:
        if not rate_limiter.check(f"authfail:{client}", max(rl.login_attempts_per_hour * 3, 10), 3600):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many failed logins")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthorized", headers={"WWW-Authenticate": "Bearer"})
    state = get_state(request)
    if principal.role not in state.permissions.roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "role not allowed on this API")
    return principal


def require(min_risk: str = "read") -> Callable[..., Coroutine[Any, Any, Principal]]:
    """Require a principal whose role ceiling is at least ``min_risk``."""

    async def dep(request: Request, principal: Principal = Depends(current_principal)) -> Principal:
        state = get_state(request)
        role = state.permissions.roles[principal.role]
        if RISK_ORDER[role.max_risk] < RISK_ORDER[min_risk]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"role {principal.role} not permitted")
        return principal

    return dep


async def require_owner(principal: Principal = Depends(current_principal)) -> Principal:
    if principal.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner only")
    return principal

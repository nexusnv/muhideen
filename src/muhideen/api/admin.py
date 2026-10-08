"""Admin cross-cutting: token auth, audit, shared write lock, error shape.

Task 1 foundation for the token-gated admin surface (spec §1): every
later admin route lives on ``admin_router`` (Bearer-gated + audited by
construction) and holds ``get_write_lock`` around read + merge +
validate + rename. No business routes yet — the router stays empty until
later tasks add config PATCH, playlists, displays, manual-days, media,
and backup/logs endpoints.
"""

from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

ADMIN_WRITES_DISABLED = "admin writes disabled"
"""503 detail when no usable token was read at boot (vs broken-config 503s)."""

INVALID_ADMIN_TOKEN = "invalid admin token"
"""401 detail for missing/mismatched/empty Bearer credentials."""

_bearer = HTTPBearer(auto_error=False)
"""Bearer extractor that yields ``None`` (never the default 403s)."""


def admin_token_file_for_config(config_path: str | Path) -> Path:
    """Single rule for the token file: always ``<config-dir>/admin_token``."""
    return Path(config_path).parent / "admin_token"


def read_admin_token(path: str | Path | None) -> str | None:
    """Read-once token load at boot; missing/blank/unreadable → ``None``.

    A blank file never yields a comparable token (no empty-Bearer
    bypass). Callers keep the result in memory; post-boot file changes
    have no effect until restart.
    """
    if path is None:
        return None
    candidate = Path(path)
    try:
        text = candidate.read_text()
    except OSError as exc:
        logger.warning(
            "admin token file %s unreadable (%s): admin writes disabled",
            candidate,
            exc,
        )
        return None
    token = text.strip()
    if not token:
        logger.warning("admin token file %s is empty: admin writes disabled", candidate)
        return None
    return token


async def require_admin(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """Gate an admin endpoint: disabled-check first, then Bearer compare.

    A request carrying any token while disabled still gets ``503``, never
    ``401``. Missing/mismatched/empty credentials get ``401`` with the
    ``WWW-Authenticate: Bearer`` challenge. The expected token lives on
    ``request.app.state`` (set by ``create_app`` from ``AppDeps``).
    """
    expected: str | None = request.app.state.admin_token
    if not expected:
        raise HTTPException(status_code=503, detail=ADMIN_WRITES_DISABLED)
    provided = credentials.credentials if credentials is not None else ""
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401,
            detail=INVALID_ADMIN_TOKEN,
            headers={"WWW-Authenticate": "Bearer"},
        )


class AdminRoute(APIRoute):
    """Audited admin route: INFO-logs peer/method/path/status, never tokens.

    Only the path is logged (never query or headers), so the Bearer token
    cannot leak into journald. Denials (``require_admin`` 401/503) and
    body-validation 422s are raised as exceptions by the handler, so they
    are logged and re-raised — every gated outcome is audited.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """Wrap the handler with post-response audit logging."""
        handler = super().get_route_handler()

        async def _audited(request: Request) -> Response:
            try:
                response = await handler(request)
            except RequestValidationError:
                _audit_log(request, 422)
                raise
            except HTTPException as exc:
                _audit_log(request, exc.status_code)
                raise
            _audit_log(request, response.status_code)
            return response

        return _audited


def _audit_log(request: Request, status_code: int) -> None:
    """Emit one audit line for a gated request (no tokens, path only)."""
    peer = request.client.host if request.client is not None else "unknown"
    logger.info("%s %s %s -> %s", peer, request.method, request.url.path, status_code)


def get_write_lock(request: Request) -> threading.Lock:
    """Shared process-wide config write lock for one admin request.

    Every admin write holds this (not the per-instance repo locks, which
    do not serialize separate instances) around read + merge + validate
    + rename. ``workers=1`` makes in-process locking sufficient. Write
    endpoints stay sync ``def`` — never hold this inside ``async def``.
    """
    lock: threading.Lock = request.app.state.write_lock
    return lock


def invalid(field: str | Sequence[str], message: str) -> HTTPException:
    """422 for mapped ``ValueError``s (Settings/parse_window/ZoneInfo/...).

    Uses the ``["body", <field>, ...]`` loc convention (nested:
    ``["body", "aladhan", "base_url"]``) so mapped errors share FastAPI's
    ``{detail: [{loc, msg, type}]}`` shape instead of leaking as 500s.
    """
    loc: list[str] = ["body", field] if isinstance(field, str) else ["body", *field]
    return HTTPException(
        status_code=422,
        detail=[{"loc": loc, "msg": message, "type": "value_error"}],
    )


admin_router = APIRouter(route_class=AdminRoute, dependencies=[Depends(require_admin)])
"""Gated admin router (no business routes yet — later tasks extend it)."""

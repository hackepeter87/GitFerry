"""Fail-closed HTTP authentication boundary for GitFerry administration."""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from gitferry.auth import (
    SESSION_LIFETIME,
    AuthPrincipal,
    AuthService,
    CsrfDenied,
    InvalidCredentials,
    LoginRateLimited,
    SessionDenied,
)

CSRF_COOKIE = "gitferry_csrf"
CSRF_HEADER = "x-gitferry-csrf"
MAX_LOGIN_BODY_BYTES = 4096


@dataclass(frozen=True)
class AuthCookieSettings:
    secure: bool = True

    @property
    def session_cookie(self) -> str:
        return "__Host-gitferry_session" if self.secure else "gitferry_session_dev"


class AuthBoundaryError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}


def auth_error_response(error: AuthBoundaryError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"error": {"code": error.code, "message": error.message}},
        headers=error.headers,
    )


def create_auth_router(settings: AuthCookieSettings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])

    @router.get("/status")
    def status(request: Request) -> dict[str, bool]:
        auth = _auth_service(request)
        return {"bootstrapRequired": not auth.bootstrap_completed()}

    @router.post("/login")
    async def login(request: Request) -> JSONResponse:
        if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
            raise AuthBoundaryError(403, "cross_site_request", "Cross-site request denied")
        auth = _auth_service(request)
        if not auth.bootstrap_completed():
            raise AuthBoundaryError(503, "bootstrap_required", "Administrator bootstrap required")
        username, password = await _login_credentials(request)
        client_key = request.client.host if request.client else "<unknown-client>"
        try:
            issued = auth.login(username, password, client_key=client_key)
        except InvalidCredentials as exc:
            raise AuthBoundaryError(401, "invalid_credentials", "Invalid credentials") from exc
        except LoginRateLimited as exc:
            raise AuthBoundaryError(
                429,
                "login_rate_limited",
                "Too many login attempts; try again later",
                headers={"Retry-After": "900"},
            ) from exc
        response = JSONResponse(
            content={
                "authenticated": True,
                "principal": _principal_document(issued.principal),
                "expiresAt": issued.expires_at,
            }
        )
        max_age = int(SESSION_LIFETIME.total_seconds())
        response.set_cookie(
            settings.session_cookie,
            issued.session_token,
            max_age=max_age,
            path="/",
            secure=settings.secure,
            httponly=True,
            samesite="strict",
        )
        response.set_cookie(
            CSRF_COOKIE,
            issued.csrf_token,
            max_age=max_age,
            path="/",
            secure=settings.secure,
            httponly=False,
            samesite="strict",
        )
        return response

    @router.get("/session")
    def session(request: Request) -> dict[str, object]:
        principal = require_authenticated(request, settings)
        return {"authenticated": True, "principal": _principal_document(principal)}

    @router.post("/logout", status_code=204)
    def logout(request: Request) -> Response:
        auth, token, _ = _authenticated_request(request, settings, csrf=True)
        auth.logout(token)
        response = Response(status_code=204)
        response.delete_cookie(
            settings.session_cookie,
            path="/",
            secure=settings.secure,
            httponly=True,
            samesite="strict",
        )
        response.delete_cookie(
            CSRF_COOKIE,
            path="/",
            secure=settings.secure,
            httponly=False,
            samesite="strict",
        )
        response.headers["Clear-Site-Data"] = '"cache", "cookies", "storage"'
        return response

    return router


def admin_csrf_dependency(settings: AuthCookieSettings) -> Callable[[Request], AuthPrincipal]:
    def dependency(request: Request) -> AuthPrincipal:
        _, _, principal = _authenticated_request(request, settings, csrf=True)
        if not principal.is_admin:
            raise AuthBoundaryError(403, "admin_required", "Administrator access required")
        return principal

    return dependency


def require_authenticated(request: Request, settings: AuthCookieSettings) -> AuthPrincipal:
    _, _, principal = _authenticated_request(request, settings, csrf=False)
    return principal


def _authenticated_request(
    request: Request,
    settings: AuthCookieSettings,
    *,
    csrf: bool,
) -> tuple[AuthService, str, AuthPrincipal]:
    auth = _auth_service(request)
    token = request.cookies.get(settings.session_cookie)
    if not token:
        raise AuthBoundaryError(401, "authentication_required", "Authentication required")
    try:
        if csrf:
            header = request.headers.get(CSRF_HEADER, "")
            cookie = request.cookies.get(CSRF_COOKIE, "")
            if not header or not cookie or not hmac.compare_digest(header, cookie):
                raise CsrfDenied("CSRF validation failed")
            principal = auth.verify_csrf(token, header)
        else:
            principal = auth.authenticate(token)
    except CsrfDenied as exc:
        raise AuthBoundaryError(403, "csrf_denied", "CSRF validation failed") from exc
    except SessionDenied as exc:
        raise AuthBoundaryError(401, "authentication_required", "Authentication required") from exc
    return auth, token, principal


def _auth_service(request: Request) -> AuthService:
    auth = getattr(request.app.state, "auth", None)
    if not isinstance(auth, AuthService):
        raise AuthBoundaryError(503, "auth_unavailable", "Authentication is unavailable")
    return auth


async def _login_credentials(request: Request) -> tuple[str, str]:
    declared_length = request.headers.get("content-length")
    if (
        declared_length
        and declared_length.isdigit()
        and int(declared_length) > MAX_LOGIN_BODY_BYTES
    ):
        raise AuthBoundaryError(413, "request_too_large", "Login request is too large")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise AuthBoundaryError(415, "json_required", "JSON request required")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_LOGIN_BODY_BYTES:
            raise AuthBoundaryError(413, "request_too_large", "Login request is too large")
    try:
        document = json.loads(body, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AuthBoundaryError(400, "invalid_request", "Invalid login request") from exc
    if not isinstance(document, dict) or set(document) != {"username", "password"}:
        raise AuthBoundaryError(400, "invalid_request", "Invalid login request")
    username = document["username"]
    password = document["password"]
    if not isinstance(username, str) or not isinstance(password, str):
        raise AuthBoundaryError(400, "invalid_request", "Invalid login request")
    return username, password


def _principal_document(principal: AuthPrincipal) -> dict[str, str]:
    return cast(dict[str, str], {"username": principal.username, "role": principal.role})


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate JSON member")
        document[key] = value
    return document

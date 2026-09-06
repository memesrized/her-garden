"""Single-household OAuth provider using the MCP SDK's protocol handlers.

Inputs are validated OAuth requests and a household password entered in a browser.
Outputs are short-lived access tokens and rotating refresh tokens.
There is no signup: client registration alone never grants garden access.
"""

import hashlib
import hmac
import html
import secrets
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from psycopg.types.json import Jsonb
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from her_garden.config import Settings
from her_garden.store import GardenStore, Record

SCOPE = "garden"


class HouseholdAuth(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """Persist OAuth state in PostgreSQL and require explicit household consent."""

    def __init__(self, store: GardenStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.resource = f"{settings.public_url}/mcp"

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """Load a dynamically registered client."""
        record = await self._get("client", client_id)
        return OAuthClientInformationFull.model_validate(record) if record else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """Accept ChatGPT and local-client callbacks; registration grants no access."""
        for redirect in client_info.redirect_uris or []:
            url = urlsplit(str(redirect))
            chatgpt_callback = (
                url.scheme == "https"
                and url.netloc == "chatgpt.com"
                and (
                    url.path.startswith("/connector/oauth/")
                    or url.path == "/connector_platform_oauth_redirect"
                )
                and not url.fragment
                and not url.query
            )
            loopback_callback = (
                url.scheme == "http"
                and url.hostname in {"127.0.0.1", "::1"}
                and url.path == "/callback"
                and not url.username
                and not url.password
                and not url.fragment
                and not url.query
            )
            if not (chatgpt_callback or loopback_callback):
                raise RegistrationError(
                    "invalid_redirect_uri", "Only ChatGPT or local loopback callbacks are allowed"
                )
        if not client_info.redirect_uris or not client_info.client_id:
            raise RegistrationError("invalid_client_metadata", "Client and redirect URI required")
        await self._put(
            "client", client_info.client_id, client_info.model_dump(mode="json"), 10 * 365 * 86400
        )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Save a short-lived request and redirect to the household consent form."""
        if params.resource and params.resource != self.resource:
            raise AuthorizeError("invalid_request", "Unknown resource")
        if not params.scopes:
            params = params.model_copy(update={"scopes": [SCOPE]})
        elif params.scopes != [SCOPE]:
            raise AuthorizeError("invalid_scope", "The garden scope is required")
        if len(params.code_challenge) != 43:
            raise AuthorizeError("invalid_request", "S256 PKCE challenge required")
        flow = secrets.token_urlsafe(32)
        await self._put(
            "flow",
            flow,
            {"client_id": client.client_id, "params": params.model_dump(mode="json")},
            600,
        )
        return f"{self.settings.public_url}/login?flow={flow}"

    async def handle_login(self, request: Request) -> Response:
        """Show explicit consent and exchange the password for a single-use code."""
        headers = {
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": (
                "default-src 'none'; form-action 'self'; frame-ancestors 'none'"
            ),
        }
        if request.method == "GET":
            flow = request.query_params.get("flow", "")
            if not await self._get("flow", flow):
                return HTMLResponse("Link expired. Reconnect from ChatGPT.", 400, headers=headers)
            csrf = secrets.token_urlsafe(32)
            response: Response = HTMLResponse(
                '<!doctype html><html lang="en"><meta name="viewport" content="width=device-width">'
                "<title>Her Garden — connect</title><h1>Connect your garden to ChatGPT</h1>"
                "<p>Allow ChatGPT to read and record your household plants and supplies.</p>"
                '<form method="post"><input type="hidden" name="flow" value="'
                f'{html.escape(flow, quote=True)}"><input type="hidden" name="csrf" value="{csrf}">'
                '<label>Household password <input type="password" name="password" '
                'required autocomplete="current-password" maxlength="256"></label>'
                '<button type="submit">Allow access</button></form></html>',
                headers=headers,
            )
            response.set_cookie(
                "garden_csrf",
                csrf,
                secure=self.settings.public_url.startswith("https"),
                httponly=True,
                samesite="lax",
                max_age=600,
                path="/garden/login",
            )
            return response
        form = await request.form()
        csrf = str(form.get("csrf", ""))
        if not csrf or not hmac.compare_digest(csrf, request.cookies.get("garden_csrf", "")):
            return HTMLResponse("Invalid login form. Reconnect from ChatGPT.", 403, headers=headers)
        origin = request.headers.get("origin")
        expected = self.settings.public_url.removesuffix("/garden")
        if origin and origin != expected:
            return HTMLResponse("Invalid origin", 403, headers=headers)
        if not await self._allow_login():
            return HTMLResponse("Too many attempts. Try again in a minute.", 429, headers=headers)
        password = str(form.get("password", ""))
        salt, digest = self.settings.household_password_hash.get_secret_value().split(":")
        supplied = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        if len(password) > 256 or not hmac.compare_digest(supplied.hex(), digest):
            return HTMLResponse("Incorrect password. Go back and try again.", 401, headers=headers)
        record = await self._get("flow", str(form.get("flow", "")), consume=True)
        if not record:
            return HTMLResponse("Link expired. Reconnect from ChatGPT.", 400, headers=headers)
        params = AuthorizationParams.model_validate(record["params"])
        code = secrets.token_urlsafe(32)
        authorization = AuthorizationCode(
            code="",
            client_id=record["client_id"],
            scopes=[SCOPE],
            expires_at=time.time() + 120,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=self.resource,
            subject="household",
        )
        await self._put("code", code, authorization.model_dump(mode="json"), 120)
        response = RedirectResponse(
            construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state),
            303,
            headers=headers,
        )
        response.delete_cookie("garden_csrf", path="/garden/login")
        return response

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        """Load a code; the SDK verifies client, redirect URI, expiry and PKCE."""
        record = await self._get("code", authorization_code)
        return (
            AuthorizationCode.model_validate({**record, "code": authorization_code})
            if record
            else None
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        """Consume codes atomically so concurrent exchanges cannot reuse them."""
        if not await self._get("code", authorization_code.code, consume=True):
            raise TokenError("invalid_grant", "Code already used or expired")
        return await self._issue(client.client_id or "", authorization_code.scopes)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        """Retrieve a refresh token without exposing its stored hash."""
        record = await self._get("refresh", refresh_token)
        return RefreshToken.model_validate({**record, "token": refresh_token}) if record else None

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        """Rotate the token pair on each refresh."""
        record = await self._get("refresh", refresh_token.token, consume=True)
        if not record:
            raise TokenError("invalid_grant", "Refresh token already used or expired")
        await self._delete_key("access", record["access_hash"])
        return await self._issue(client.client_id or "", scopes)

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Accept only unexpired tokens issued for this exact garden endpoint."""
        record = await self._get("access", token)
        if not record or record.get("resource") != self.resource:
            return None
        return AccessToken.model_validate({**record, "token": token})

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Revoke both halves of a token pair."""
        kind = "access" if isinstance(token, AccessToken) else "refresh"
        record = await self._get(kind, token.token, consume=True)
        if record:
            other = "refresh" if kind == "access" else "access"
            await self._delete_key(other, record[f"{other}_hash"])

    async def _issue(self, client_id: str, scopes: list[str]) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        common: Record = {"client_id": client_id, "scopes": scopes, "subject": "household"}
        await self._put(
            "access",
            access,
            {
                **common,
                "resource": self.resource,
                "expires_at": int(time.time()) + 3600,
                "refresh_hash": token_hash(refresh),
            },
            3600,
        )
        await self._put(
            "refresh",
            refresh,
            {
                **common,
                "access_hash": token_hash(access),
                "expires_at": int(time.time()) + 30 * 86400,
            },
            30 * 86400,
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=3600,
            refresh_token=refresh,
            scope=" ".join(scopes),
        )

    async def _put(self, kind: str, key: str, value: Record, ttl: int) -> None:
        async with self.store.pool.connection() as conn:
            await conn.execute("DELETE FROM oauth_records WHERE expires_at < now()")
            await conn.execute(
                "INSERT INTO oauth_records VALUES (%s,%s,%s,%s) "
                "ON CONFLICT(kind,key) DO UPDATE SET value=excluded.value, "
                "expires_at=excluded.expires_at",
                (kind, token_hash(key), Jsonb(value), datetime.now(UTC) + timedelta(seconds=ttl)),
            )

    async def _get(self, kind: str, key: str, consume: bool = False) -> Record | None:
        async with self.store.pool.connection() as conn:
            if consume:
                cursor = await conn.execute(
                    "DELETE FROM oauth_records WHERE kind=%s AND key=%s "
                    "AND expires_at > now() RETURNING value",
                    (kind, token_hash(key)),
                )
            else:
                cursor = await conn.execute(
                    "SELECT value FROM oauth_records WHERE kind=%s AND key=%s "
                    "AND expires_at > now()",
                    (kind, token_hash(key)),
                )
            row = await cursor.fetchone()
        return row["value"] if row else None

    async def _delete_key(self, kind: str, hashed_key: str) -> None:
        async with self.store.pool.connection() as conn:
            await conn.execute(
                "DELETE FROM oauth_records WHERE kind=%s AND key=%s", (kind, hashed_key)
            )

    async def _allow_login(self) -> bool:
        async with self.store.pool.connection() as conn:
            row = await (
                await conn.execute(
                    "INSERT INTO oauth_records VALUES "
                    "('attempt',%s,'1',now() + interval '2 minutes') "
                    "ON CONFLICT(kind,key) DO UPDATE SET "
                    "value=to_jsonb((oauth_records.value::text)::int+1) "
                    "RETURNING value",
                    (str(int(time.time()) // 60),),
                )
            ).fetchone()
        return bool(row and row["value"] <= 10)


def token_hash(value: str) -> str:
    """Hash high-entropy opaque credentials before storing lookup keys."""
    return hashlib.sha256(value.encode()).hexdigest()

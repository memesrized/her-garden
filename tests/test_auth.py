"""Exercise real SDK OAuth routes including PKCE, consent, replay and token rotation."""

import base64
import hashlib
import re
import secrets
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from pydantic import SecretStr

from her_garden.config import Settings
from her_garden.server import create_app
from her_garden.store import GardenStore

PASSWORD = "integration-test-household-password"


def settings(store: GardenStore) -> Settings:
    """Build test-only credentials and use the fixture's isolated schema."""
    salt = b"0" * 16
    digest = hashlib.scrypt(PASSWORD.encode(), salt=salt, n=16384, r=8, p=1).hex()
    return Settings(
        database_url=SecretStr(store.database_url),
        household_password_hash=SecretStr(f"{salt.hex()}:{digest}"),
    )


async def test_oauth_and_authenticated_mcp(store: GardenStore) -> None:
    app = create_app(settings(store))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8002"
        ) as client:
            unauthorized = await client.post("/garden/mcp", json={})
            assert unauthorized.status_code == 401
            assert (
                "/.well-known/oauth-protected-resource/garden/mcp"
                in unauthorized.headers["www-authenticate"]
            )
            metadata = (await client.get("/.well-known/oauth-authorization-server/garden")).json()
            assert metadata["authorization_endpoint"] == "http://localhost:8002/garden/authorize"
            assert metadata["code_challenge_methods_supported"] == ["S256"]
            bad = await client.post(
                "/garden/register",
                json={
                    "redirect_uris": ["https://attacker.example/callback"],
                },
            )
            assert bad.status_code == 400
            unsafe_loopback = await client.post(
                "/garden/register",
                json={
                    "redirect_uris": ["http://localhost:58128/callback"],
                },
            )
            assert unsafe_loopback.status_code == 400
            loopback = await client.post(
                "/garden/register",
                json={
                    "redirect_uris": ["http://127.0.0.1:58128/callback"],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "token_endpoint_auth_method": "client_secret_post",
                    "scope": "garden",
                },
            )
            assert loopback.status_code == 201, loopback.text
            loopback_client = loopback.json()
            codex_authorize = await client.get(
                "/garden/authorize",
                params={
                    "client_id": loopback_client["client_id"],
                    "response_type": "code",
                    "redirect_uri": "http://127.0.0.1:58128/callback",
                    "code_challenge": "A" * 43,
                    "code_challenge_method": "S256",
                    "state": "codex-test-state",
                },
            )
            assert codex_authorize.status_code in {302, 303, 307}
            assert urlsplit(codex_authorize.headers["location"]).path == "/garden/login"
            registration = await client.post(
                "/garden/register",
                json={
                    "redirect_uris": ["https://chatgpt.com/connector/oauth/test"],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "token_endpoint_auth_method": "client_secret_post",
                    "scope": "garden",
                },
            )
            assert registration.status_code == 201, registration.text
            registered = registration.json()
            verifier = secrets.token_urlsafe(48)
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                .decode()
                .rstrip("=")
            )
            params = {
                "client_id": registered["client_id"],
                "response_type": "code",
                "redirect_uri": "https://chatgpt.com/connector/oauth/test",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "garden",
                "state": "test-state",
                "resource": "http://localhost:8002/garden/mcp",
            }
            denied = await client.get(
                "/garden/authorize", params={**params, "resource": "https://wrong.example"}
            )
            assert "error=" in denied.headers.get("location", "") or denied.status_code == 400
            authorize = await client.get("/garden/authorize", params=params)
            assert authorize.status_code in {302, 303, 307}
            login = await client.get(authorize.headers["location"])
            flow = parse_qs(urlsplit(authorize.headers["location"]).query)["flow"][0]
            csrf_match = re.search('name="csrf" value="([^"]+)"', login.text)
            assert csrf_match
            csrf = csrf_match.group(1)
            no_csrf = await client.post("/garden/login", data={"flow": flow, "password": PASSWORD})
            assert no_csrf.status_code == 403
            wrong = await client.post(
                "/garden/login", data={"flow": flow, "csrf": csrf, "password": "wrong"}
            )
            assert wrong.status_code == 401
            consent = await client.post(
                "/garden/login", data={"flow": flow, "csrf": csrf, "password": PASSWORD}
            )
            assert consent.status_code == 303, consent.text
            callback = parse_qs(urlsplit(consent.headers["location"]).query)
            assert callback["state"] == ["test-state"]
            exchange = {
                "grant_type": "authorization_code",
                "code": callback["code"][0],
                "redirect_uri": params["redirect_uri"],
                "code_verifier": verifier,
                "client_id": registered["client_id"],
                "client_secret": registered["client_secret"],
            }
            wrong_pkce = await client.post(
                "/garden/token", data={**exchange, "code_verifier": "bad"}
            )
            assert wrong_pkce.status_code == 400
            token = await client.post("/garden/token", data=exchange)
            assert token.status_code == 200, token.text
            tokens = token.json()
            replay = await client.post("/garden/token", data=exchange)
            assert replay.status_code == 400
            headers = {
                "Authorization": f"Bearer {tokens['access_token']}",
                "Accept": "application/json, text/event-stream",
            }
            initialized = await client.post(
                "/garden/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "integration", "version": "1"},
                    },
                },
            )
            assert initialized.status_code == 200, initialized.text
            listed = await client.post(
                "/garden/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                },
            )
            assert len(listed.json()["result"]["tools"]) == 9
            call = await client.post(
                "/garden/mcp",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "list_plants", "arguments": {}},
                },
            )
            assert not call.json()["result"].get("isError"), call.text
            refresh_data = {
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": registered["client_id"],
                "client_secret": registered["client_secret"],
            }
            refreshed = await client.post("/garden/token", data=refresh_data)
            assert refreshed.status_code == 200, refreshed.text
            assert (await client.post("/garden/token", data=refresh_data)).status_code == 400
            assert (await client.post("/garden/mcp", headers=headers, json={})).status_code == 401
            new_tokens: dict[str, Any] = refreshed.json()
            revoke = await client.post(
                "/garden/revoke",
                data={
                    "token": new_tokens["refresh_token"],
                    "client_id": registered["client_id"],
                    "client_secret": registered["client_secret"],
                },
            )
            assert revoke.status_code == 200
            assert await app.state.auth.load_access_token(new_tokens["access_token"]) is None

# Copyright 2026 Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""FastMCP client helpers with persistent OAuth token storage."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

from fastmcp import Client
from fastmcp.client.auth.oauth import OAuth
from key_value.aio._utils.sanitization import AlwaysHashStrategy
from key_value.aio.protocols import AsyncKeyValue
from key_value.aio.stores.filetree import FileTreeStore

GOOGLE_ADS_SCOPE = "https://www.googleapis.com/auth/adwords"
GOOGLE_ADS_MCP_OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    GOOGLE_ADS_SCOPE,
]

CLIENT_URL_ENV = "GOOGLE_ADS_MCP_CLIENT_URL"
LEGACY_CLIENT_URL_ENV = "GOOGLE_ADS_MCP_URL"
SERVER_BASE_URL_ENV = "GOOGLE_ADS_MCP_BASE_URL"
TOKEN_STORE_ENV = "GOOGLE_ADS_MCP_CLIENT_TOKEN_STORE"
CALLBACK_PORT_ENV = "GOOGLE_ADS_MCP_CLIENT_CALLBACK_PORT"
CALLBACK_TIMEOUT_ENV = "GOOGLE_ADS_MCP_CLIENT_CALLBACK_TIMEOUT"


def default_mcp_url() -> str:
    """Return the MCP endpoint URL used by local FastMCP client scripts."""
    explicit_url = os.environ.get(CLIENT_URL_ENV) or os.environ.get(
        LEGACY_CLIENT_URL_ENV
    )
    if explicit_url:
        return explicit_url

    base_url = os.environ.get(SERVER_BASE_URL_ENV)
    if base_url:
        return f"{base_url.rstrip('/')}/mcp"

    return "http://localhost:8080/mcp"


def default_token_store_dir() -> Path:
    """Return the persistent OAuth token store directory."""
    configured_dir = os.environ.get(TOKEN_STORE_ENV)
    if configured_dir:
        return Path(configured_dir).expanduser()

    if sys.platform == "darwin":
        return (
            Path.home() / "Library" / "Application Support" / "google-ads-mcp" / "oauth"
        )

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "google-ads-mcp" / "oauth"

    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home) / "google-ads-mcp" / "oauth"

    return Path.home() / ".local" / "state" / "google-ads-mcp" / "oauth"


def _prepare_private_directory(path: Path) -> Path:
    path = path.expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)

    if os.name != "nt":
        path.chmod(0o700)

    return path


def _env_int(name: str) -> int | None:
    value = os.environ.get(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc


def _env_float(name: str) -> float | None:
    value = os.environ.get(name)
    if not value:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc


def build_oauth_token_store(
    token_store_dir: str | Path | None = None,
) -> AsyncKeyValue:
    """Build a persistent AsyncKeyValue store for FastMCP OAuth tokens.

    FastMCP stores token records with URL-derived keys such as
    ``https://example.com/mcp/tokens``. The FileTreeStore below hashes both
    collection names and keys before writing filenames, so access tokens are not
    stored under readable URL/path-derived filenames.
    """
    data_directory = _prepare_private_directory(
        Path(token_store_dir) if token_store_dir else default_token_store_dir()
    )

    return FileTreeStore(
        data_directory=data_directory,
        key_sanitization_strategy=AlwaysHashStrategy(hash_length=64),
        collection_sanitization_strategy=AlwaysHashStrategy(hash_length=64),
    )


def create_google_ads_oauth(
    mcp_url: str | None = None,
    *,
    scopes: Sequence[str] | str | None = None,
    token_storage: AsyncKeyValue | None = None,
    token_store_dir: str | Path | None = None,
    callback_port: int | None = None,
    callback_host: str = "localhost",
    callback_timeout: float | None = None,
    client_name: str = "Google Ads MCP FastMCP Client",
) -> OAuth:
    """Create a FastMCP OAuth provider backed by persistent local storage."""
    if token_storage is None:
        token_storage = build_oauth_token_store(token_store_dir)

    if callback_port is None:
        callback_port = _env_int(CALLBACK_PORT_ENV)

    if callback_timeout is None:
        callback_timeout = _env_float(CALLBACK_TIMEOUT_ENV) or 300.0

    return OAuth(
        mcp_url=mcp_url or default_mcp_url(),
        scopes=scopes or GOOGLE_ADS_MCP_OAUTH_SCOPES,
        client_name=client_name,
        token_storage=token_storage,
        callback_port=callback_port,
        callback_host=callback_host,
        callback_timeout=callback_timeout,
    )


def create_google_ads_mcp_client(
    mcp_url: str | None = None,
    *,
    scopes: Sequence[str] | str | None = None,
    token_storage: AsyncKeyValue | None = None,
    token_store_dir: str | Path | None = None,
    callback_port: int | None = None,
    callback_host: str = "localhost",
    callback_timeout: float | None = None,
    client_name: str = "Google Ads MCP FastMCP Client",
) -> Client:
    """Create a FastMCP client that reuses OAuth tokens across script runs."""
    oauth = create_google_ads_oauth(
        mcp_url=mcp_url,
        scopes=scopes,
        token_storage=token_storage,
        token_store_dir=token_store_dir,
        callback_port=callback_port,
        callback_host=callback_host,
        callback_timeout=callback_timeout,
        client_name=client_name,
    )
    return Client(mcp_url or default_mcp_url(), auth=oauth, name=client_name)

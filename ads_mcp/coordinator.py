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

"""Module declaring the singleton MCP instance.

The singleton allows other modules to register their tools with the same MCP
server using `@mcp.tool` annotations, thereby 'coordinating' the bootstrapping
of the server.
"""

import os
from typing import Iterable

from cryptography.fernet import Fernet
from fastmcp import FastMCP
from fastmcp.server.auth.oauth_proxy.models import ProxyDCRClient
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.utilities.auth import parse_scopes
from key_value.aio.stores.redis import RedisStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from key_value.aio.wrappers.prefix_collections import PrefixCollectionsWrapper
from mcp.server.auth.provider import TokenError
from pydantic import AnyUrl

_CLIENT_ID = os.environ.get("GOOGLE_ADS_MCP_OAUTH_CLIENT_ID")
_CLIENT_SECRET = os.environ.get("GOOGLE_ADS_MCP_OAUTH_CLIENT_SECRET")
_BASE_URL = os.environ.get("GOOGLE_ADS_MCP_BASE_URL", "http://localhost:8080")
_JWT_SIGNING_KEY = os.environ.get("JWT_SIGNING_KEY")
_STORAGE_ENCRYPTION_KEY = os.environ.get("STORAGE_ENCRYPTION_KEY")
_REDIS_URL = os.environ.get("REDIS_URL")
_ALLOW_MISSING_OAUTH_CLIENTS_ENV = "GOOGLE_ADS_MCP_ALLOW_MISSING_OAUTH_CLIENTS"
_ALLOWED_REDIRECT_URIS_ENV = "GOOGLE_ADS_MCP_ALLOWED_REDIRECT_URIS"
_GOOGLE_ADS_SCOPE = "https://www.googleapis.com/auth/adwords"
_REQUIRED_GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    _GOOGLE_ADS_SCOPE,
]

_GOOGLE_SCOPE_ALIASES = {
    "email": "https://www.googleapis.com/auth/userinfo.email",
    "profile": "https://www.googleapis.com/auth/userinfo.profile",
}

_DEFAULT_CLIENT_REDIRECT_URI_PATTERNS = [
    "https://chatgpt.com/connector/oauth/*",
    "https://chat.openai.com/connector/oauth/*",
    "http://localhost:*/*",
    "http://127.0.0.1:*/*",
]


def _normalize_google_scope(scope: str) -> str:
    return _GOOGLE_SCOPE_ALIASES.get(scope, scope)


def _missing_required_google_scopes(
    required_scopes: Iterable[str],
    granted_scope_value: str | None,
    requested_scopes: Iterable[str],
) -> list[str]:
    granted_scopes = parse_scopes(granted_scope_value or "") or list(requested_scopes)
    normalized_granted = {_normalize_google_scope(scope) for scope in granted_scopes}
    return [
        scope
        for scope in required_scopes
        if _normalize_google_scope(scope) not in normalized_granted
    ]


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _client_redirect_uri_patterns() -> list[str]:
    """Allowed redirect URI patterns for DCR and missing-client recovery."""
    return (
        _split_csv(os.environ.get(_ALLOWED_REDIRECT_URIS_ENV))
        or _DEFAULT_CLIENT_REDIRECT_URI_PATTERNS
    )


def _allow_missing_oauth_clients() -> bool:
    """Whether to synthesize lost DCR clients for constrained redirects."""
    value = os.environ.get(_ALLOW_MISSING_OAUTH_CLIENTS_ENV, "true").lower()
    return value not in {"0", "false", "no", "off"}


class GoogleAdsProvider(GoogleProvider):
    """Google OAuth provider that rejects partial grants for Google Ads access."""

    async def get_client(self, client_id: str):
        client = await super().get_client(client_id)
        if client is not None or not _allow_missing_oauth_clients():
            return client

        return ProxyDCRClient(
            client_id=client_id,
            client_secret=None,
            redirect_uris=[AnyUrl("http://localhost")],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope=" ".join(_REQUIRED_GOOGLE_SCOPES),
            token_endpoint_auth_method="none",
            allowed_redirect_uri_patterns=_client_redirect_uri_patterns(),
            allow_unregistered_redirect_uris=True,
        )

    async def exchange_authorization_code(self, client, authorization_code):
        code_model = await self._code_store.get(key=authorization_code.code)
        if code_model:
            missing_scopes = _missing_required_google_scopes(
                required_scopes=self.required_scopes,
                granted_scope_value=code_model.idp_tokens.get("scope"),
                requested_scopes=authorization_code.scopes,
            )
            if missing_scopes:
                await self._code_store.delete(key=authorization_code.code)
                raise TokenError(
                    "invalid_scope",
                    "Google did not grant all required scopes. Reconnect and "
                    "approve Google Ads access. Missing scopes: "
                    + ", ".join(missing_scopes),
                )

        return await super().exchange_authorization_code(client, authorization_code)


def _build_client_storage():
    """Create encrypted persistent OAuth storage when Redis is configured."""
    if not (_REDIS_URL and _STORAGE_ENCRYPTION_KEY):
        return None
    return FernetEncryptionWrapper(
        key_value=PrefixCollectionsWrapper(
            key_value=RedisStore(url=_REDIS_URL),
            prefix="google-ads-mcp-oauth",
        ),
        fernet=Fernet(_STORAGE_ENCRYPTION_KEY.encode()),
    )


if _CLIENT_ID and _CLIENT_SECRET:
    storage = _build_client_storage()
    auth = GoogleAdsProvider(
        client_id=_CLIENT_ID,
        client_secret=_CLIENT_SECRET,
        base_url=_BASE_URL,
        jwt_signing_key=_JWT_SIGNING_KEY,
        client_storage=storage,
        required_scopes=_REQUIRED_GOOGLE_SCOPES,
        allowed_client_redirect_uris=_client_redirect_uri_patterns(),
        extra_authorize_params={"include_granted_scopes": "true"},
    )
    mcp = FastMCP("Google Ads Server", auth=auth)
else:
    mcp = FastMCP("Google Ads Server")


def initialize_and_mount_tools(parent_mcp: FastMCP) -> None:
    """Loads the tools configuration and dynamically mounts the tools sub-servers."""
    from ads_mcp.config import ToolsConfig
    import importlib
    import pkgutil
    import ads_mcp.tools as tools_pkg

    # Map of category name -> FastMCP sub-server
    sub_servers = {}

    # Discover and dynamically load all tool modules
    for _, module_name, _ in pkgutil.iter_modules(tools_pkg.__path__):
        full_module_name = f"ads_mcp.tools.{module_name}"
        module = importlib.import_module(full_module_name)

        # Find any FastMCP instances defined in the module
        for attr_name in dir(module):
            attr_val = getattr(module, attr_name)
            if isinstance(attr_val, FastMCP):
                category = attr_val.name
                sub_servers[category] = attr_val

    config = ToolsConfig.load()

    for category, sub_mcp in sub_servers.items():
        if not config.is_namespace_enabled(category):
            continue

        # Filter disabled tools inside the sub-server before mounting
        tool_names = []
        for key, val in sub_mcp.local_provider._components.items():
            if key.startswith("tool:"):
                tool_names.append(val.name)

        for name in tool_names:
            if not config.is_tool_enabled(category, name):
                sub_mcp.local_provider.remove_tool(name)

        # Determine prefix/namespace
        namespace_prefix = config.get_namespace_prefix(category)

        # Mount the sub-server
        parent_mcp.mount(sub_mcp, namespace=namespace_prefix or None)


# Automatically initialize and mount tools upon import
initialize_and_mount_tools(mcp)

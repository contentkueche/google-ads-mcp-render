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

"""Entry point for the MCP server."""

from ads_mcp.coordinator import mcp
from starlette.requests import Request
from starlette.responses import JSONResponse

# The following imports are necessary to register the resources with the `mcp`
# object, even though they are not directly used in this file.
# Tools are loaded dynamically via reflection in coordinator.py.
# The `# noqa: F401` comment tells the linter to ignore the "unused import"
# warning.
from ads_mcp.resources import (
    discovery,
    metrics,
    release_notes,
    segments,
)  # noqa: F401


import os
from urllib.parse import urlsplit


def _split_env_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _public_base_url() -> str:
    return os.environ.get("GOOGLE_ADS_MCP_BASE_URL", "http://localhost:8080").rstrip(
        "/"
    )


def _http_allowed_hosts() -> list[str]:
    hosts = _split_env_list(os.environ.get("GOOGLE_ADS_MCP_ALLOWED_HOSTS"))
    parsed = urlsplit(_public_base_url())
    if parsed.hostname and parsed.hostname not in hosts:
        hosts.append(parsed.hostname)
    return hosts


def _http_allowed_origins() -> list[str]:
    origins = _split_env_list(os.environ.get("GOOGLE_ADS_MCP_ALLOWED_ORIGINS"))
    base_url = _public_base_url()
    parsed = urlsplit(base_url)
    if parsed.scheme and parsed.hostname and base_url not in origins:
        origins.append(base_url)
    return origins


def _oauth_resource_metadata() -> dict[str, object]:
    base_url = _public_base_url()
    return {
        "resource": f"{base_url}/mcp",
        "authorization_servers": [f"{base_url}/"],
        "scopes_supported": [
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/adwords",
        ],
        "bearer_methods_supported": ["header"],
    }


@mcp.custom_route("/.well-known/oauth-protected-resource", methods=["GET"])
async def oauth_protected_resource_root(_request: Request) -> JSONResponse:
    """Expose protected resource metadata at the RFC root path for MCP clients."""
    return JSONResponse(_oauth_resource_metadata())


def run_server() -> None:
    _CLIENT_ID = os.environ.get("GOOGLE_ADS_MCP_OAUTH_CLIENT_ID")
    _CLIENT_SECRET = os.environ.get("GOOGLE_ADS_MCP_OAUTH_CLIENT_SECRET")
    port = int(os.environ.get("PORT", "8080"))

    if _CLIENT_ID and _CLIENT_SECRET:
        mcp.run(
            transport="streamable-http",
            port=port,
            host="0.0.0.0",
            allowed_hosts=_http_allowed_hosts(),
            allowed_origins=_http_allowed_origins(),
        )
    else:
        mcp.run()


if __name__ == "__main__":
    run_server()
